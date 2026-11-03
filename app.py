"""Yu-Gi-Oh! Vinted Lister - desktop app (pywebview UI over the same engine as the CLI)."""
from __future__ import annotations

import base64
import collections
import io
import json
import os
import shutil
import subprocess
import sys
import threading
import time
from pathlib import Path

import webview
from webview.dom import DOMEventHandler

import paths
import vinted_lister as v
import updater
from engine import CSV_LOCK, Engine, Settings, update_row
from version import __version__

APP_TITLE = "Yu-Gi-Oh! Vinted Lister"
RES_DIR = Path(getattr(sys, "_MEIPASS", Path(__file__).parent))
CHROME = Path(os.environ.get("PROGRAMFILES", r"C:\Program Files")) / "Google/Chrome/Application/chrome.exe"

SITES = ["hr", "de", "at", "fr", "be", "nl", "lu", "it", "es", "pt", "pl", "cz", "sk", "lt", "lv", "ee",
         "hu", "ro", "fi", "se", "dk", "gr", "ie", "si", "co.uk"]
CONDITIONS = ["New with tags", "New without tags", "Very good", "Good", "Satisfactory"]


class Log:
    """Collects log lines (from the engine and from print()) for the UI."""
    def __init__(self):
        self.lines: collections.deque[tuple[int, str, str]] = collections.deque(maxlen=500)
        self.n = 0
        self._buf = ""
        self.lock = threading.Lock()

    def add(self, text: str) -> None:
        for line in str(text).splitlines():
            if line.strip():
                with self.lock:
                    self.n += 1
                    self.lines.append((self.n, time.strftime("%H:%M:%S"), line.rstrip()))

    # file-like, so print() from vinted_lister ends up here too
    def write(self, s: str) -> None:
        self._buf += s
        while "\n" in self._buf:
            line, self._buf = self._buf.split("\n", 1)
            self.add(line)

    def flush(self) -> None:
        pass

    def since(self, n: int) -> list:
        with self.lock:
            return [l for l in self.lines if l[0] > n]


def load_settings() -> Settings:
    data = {}
    if paths.SETTINGS.exists():
        try:
            data = json.loads(paths.SETTINGS.read_text(encoding="utf-8"))
        except Exception:
            pass
    s = Settings(folder=str(paths.DEFAULT_CARDS))
    for k, val in data.items():
        if hasattr(s, k):
            setattr(s, k, val)
    return s


class Api:
    """Methods callable from the page as window.pywebview.api.<name>()."""

    def __init__(self, log: Log):
        self._log = log
        self._settings = load_settings()
        Path(self._settings.folder).mkdir(parents=True, exist_ok=True)
        self._engine = Engine(self._settings, log=log.add)
        self._window: webview.Window | None = None
        self._thumbs: dict[str, str] = {}
        self._scanning = False
        threading.Thread(target=self._scanner, daemon=True).start()
        self._update: dict = {"available": False, "current": __version__}
        self._update_progress: float | None = None
        threading.Thread(target=self._update_checker, daemon=True).start()

    # -- background OCR: read new photos even before Chrome is connected ----------
    def _scanner(self) -> None:
        from card_names import _load_db
        try:
            _load_db()
        except Exception as e:
            self._log.add(f"Could not load the card database: {e}")
        while True:
            try:
                folder = Path(self._settings.folder)
                if folder.exists():
                    self._scanning = True
                    with CSV_LOCK:
                        v.extract_all(folder, watch=True)
            except Exception as e:
                self._log.add(f"Reading photos failed: {e}")
            finally:
                self._scanning = False
            time.sleep(2)

    # -- state for the UI -----------------------------------------------------------
    def get_state(self, log_since: int = 0) -> dict:
        folder = Path(self._settings.folder)
        with CSV_LOCK:
            rows = v.read_csv() if v.NAMES_CSV.exists() else []
        done = v.load_done()
        known = {r["image"] for r in rows}
        cards = []
        for r in rows:
            img = r["image"]
            if not Path(img).exists() or Path(img).parent.resolve() != folder.resolve():
                continue
            check = r.get("check", "")
            if img == self._engine.current:
                status = "posting"
            elif img in done:
                status = "drafted"
            elif check == "skipped":
                status = "skipped"
            elif self._engine.fails.get(img, 0) >= 2:
                status = "failed"
            elif v.needs_review(r):
                status = "review"
            else:
                status = "queued"
            cards.append({"id": img, "file": Path(img).name, "name": r.get("name", ""),
                          "set_name": r.get("set_name", ""), "set_code": r.get("set_code", ""),
                          "check": check, "status": status, "mtime": Path(img).stat().st_mtime})
        pending = [{"id": str(p.resolve()), "file": p.name} for p in v.find_images(folder)
                   if str(p.resolve()) not in known] if folder.exists() else []
        cards.sort(key=lambda c: c["mtime"], reverse=True)
        return {
            "engine": self._engine.status if self._engine.running() or self._engine.status == "error" else "stopped",
            "cards": cards,
            "pending": pending,
            "log": self._log.since(log_since),
            "settings": vars(self._settings),
            "update": {**self._update, "progress": self._update_progress,
                       "can_install": updater.can_self_update()},
        }

    # -- updates ------------------------------------------------------------------
    def _update_checker(self) -> None:
        time.sleep(3)
        while True:
            info = updater.check()
            if info.get("available") and not self._update.get("available"):
                self._log.add(f"Update available: v{info['latest']} (you have v{__version__}).")
            self._update = info
            time.sleep(6 * 3600)

    def check_update(self) -> dict:
        """Manual 'Check for updates' button."""
        info = updater.check()
        if not info.get("error") or not self._update.get("available"):
            self._update = info  # don't hide a known update just because this check failed
        if info.get("available"):
            self._log.add(f"Update available: v{info['latest']} (you have v{__version__}).")
        elif not info.get("error"):
            self._log.add(f"Checked for updates: v{__version__} is the latest.")
        return info

    def install_update(self) -> str:
        info = self._update
        if not info.get("available") or not info.get("asset"):
            return "No update available."
        if not updater.can_self_update():
            return "Running from source - pull the latest code with git instead."
        if self._update_progress is not None:
            return "Already downloading."
        def work():
            try:
                self._engine.stop()
                self._update_progress = 0.0
                self._log.add(f"Downloading v{info['latest']}...")
                setup = updater.download(info["asset"], lambda f: setattr(self, "_update_progress", f))
                if self._engine.thread:
                    self._engine.thread.join(timeout=120)  # let the current card finish
                self._log.add("Installing update - the app will restart.")
                updater.install_and_restart(setup)
                time.sleep(0.5)
                self._window.destroy()
            except Exception as e:
                self._update_progress = None
                self._log.add(f"Update failed: {e}")
        threading.Thread(target=work, daemon=True).start()
        return "ok"

    def get_options(self) -> dict:
        return {"sites": SITES, "conditions": CONDITIONS, "data_dir": str(paths.DATA_DIR), "version": __version__}

    def thumb(self, image: str) -> str:
        if image in self._thumbs:
            return self._thumbs[image]
        from PIL import Image, ImageOps
        try:
            with Image.open(image) as im:
                im = ImageOps.exif_transpose(im).convert("RGB")
                im.thumbnail((420, 560))
                buf = io.BytesIO()
                im.save(buf, "JPEG", quality=82)
            uri = "data:image/jpeg;base64," + base64.b64encode(buf.getvalue()).decode()
        except Exception:
            uri = ""
        self._thumbs[image] = uri
        return uri

    # -- adding photos -------------------------------------------------------------
    def add_paths(self, items: list[str]) -> int:
        folder = Path(self._settings.folder)
        folder.mkdir(parents=True, exist_ok=True)
        files: list[Path] = []
        for it in items:
            p = Path(it)
            if p.is_dir():
                files += [f for f in sorted(p.iterdir()) if f.suffix.lower() in v.IMAGE_EXTS]
            elif p.suffix.lower() in v.IMAGE_EXTS:
                files.append(p)
        added = 0
        for f in files:
            if f.parent.resolve() == folder.resolve():
                continue  # already in the cards folder
            dest = folder / f.name
            i = 1
            while dest.exists():
                dest = folder / f"{f.stem}_{i}{f.suffix}"
                i += 1
            shutil.copy2(f, dest)
            os.utime(dest)  # mark as new so it's picked up (and sorted to the top)
            added += 1
        if added:
            self._log.add(f"Added {added} photo(s).")
        return added

    def browse(self) -> int:
        types = ("Images (*.jpg;*.jpeg;*.png;*.webp;*.heic;*.bmp)", "All files (*.*)")
        picked = self._window.create_file_dialog(webview.FileDialog.OPEN, allow_multiple=True, file_types=types)
        return self.add_paths(list(picked or []))

    def choose_folder(self) -> str:
        picked = self._window.create_file_dialog(webview.FileDialog.FOLDER, directory=self._settings.folder)
        if picked:
            self.save_settings({"folder": picked[0] if isinstance(picked, (list, tuple)) else picked})
        return self._settings.folder

    def open_url(self, url: str) -> None:
        if url.startswith("https://github.com/"):
            os.startfile(url)

    def open_folder(self) -> None:
        os.startfile(self._settings.folder)

    # -- settings / control -----------------------------------------------------
    def save_settings(self, data: dict) -> dict:
        for k in ("folder", "site", "price", "condition", "delay"):
            if k in data and data[k] not in (None, ""):
                setattr(self._settings, k, float(data[k]) if k == "delay" else str(data[k]))
        Path(self._settings.folder).mkdir(parents=True, exist_ok=True)
        paths.SETTINGS.write_text(json.dumps(vars(self._settings), indent=1), encoding="utf-8")
        return vars(self._settings)

    def start(self) -> None:
        self._engine.start()

    def stop(self) -> None:
        self._engine.stop()

    def open_chrome_setup(self) -> None:
        exe = CHROME if CHROME.exists() else Path(os.environ.get("LOCALAPPDATA", "")) / "Google/Chrome/Application/chrome.exe"
        if exe.exists():
            subprocess.Popen([str(exe), "chrome://inspect/#remote-debugging"])

    # -- card actions -----------------------------------------------------------
    def save_card(self, image: str, name: str, set_name: str = "", set_code: str = "") -> None:
        update_row(image, name=name.strip(), set_name=set_name.strip(), set_code=set_code.strip(), check="ok")
        self._engine.fails.pop(image, None)

    def approve(self, image: str) -> None:
        update_row(image, check="ok")
        self._engine.fails.pop(image, None)

    def skip(self, image: str) -> None:
        update_row(image, check="skipped")

    def unskip(self, image: str) -> None:
        update_row(image, check="ok")

    def retry(self, image: str) -> None:
        self._engine.fails.pop(image, None)
        done = v.load_done()
        if image in done:  # "draft again"
            done.discard(image)
            v.DONE_FILE.write_text(json.dumps(sorted(done), indent=1))

    def suggest(self, query: str) -> list[str]:
        """Card-name autocomplete for the edit box."""
        from rapidfuzz import fuzz, process
        import card_names as c
        c._load_db()
        q = query.strip()
        if len(q) < 2:
            return []
        hits = process.extract(q.upper(), c._upper, scorer=fuzz.WRatio, limit=8)
        return [c._names[i] for _, _, i in hits]

    def sets_for(self, name: str) -> list[list[str]]:
        import card_names as c
        c._load_db()
        seen, out = set(), []
        for code, set_name in c._db.get(name, []):
            if code not in seen:
                seen.add(code)
                out.append([code, set_name])
        return out


def main() -> None:
    log = Log()
    sys.stdout = log if sys.stdout is None or getattr(sys, "frozen", False) else _Tee(sys.stdout, log)
    api = Api(log)
    window = webview.create_window(
        APP_TITLE, url=str(RES_DIR / "ui" / "index.html"), js_api=api,
        width=1320, height=880, min_size=(980, 640), background_color="#0f1117",
    )
    api._window = window

    def on_drop(event):
        files = event.get("dataTransfer", {}).get("files", [])
        full = [f.get("pywebviewFullPath") for f in files if f.get("pywebviewFullPath")]
        if full:
            api.add_paths(full)

    def on_loaded():
        window.dom.document.events.drop += DOMEventHandler(on_drop, prevent_default=True, stop_propagation=True)

    window.events.loaded += on_loaded
    window.events.closing += lambda: api._engine.stop()
    webview.start(debug=os.environ.get("LISTER_DEBUG") == "1")


class _Tee:
    def __init__(self, a, b):
        self.a, self.b = a, b

    def write(self, s):
        self.a.write(s)
        self.b.write(s)

    def flush(self):
        self.a.flush()


if __name__ == "__main__":
    main()
