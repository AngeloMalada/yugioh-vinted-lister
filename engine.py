"""Background worker: stay connected to Chrome and turn new card photos into Vinted drafts.

Used by both the desktop app (app.py) and `vinted_lister.py --watch`.
"""
from __future__ import annotations

import random
import threading
import time
from dataclasses import dataclass
from pathlib import Path

import vinted_lister as v

# names.csv is touched by the worker and by the app's table edits.
CSV_LOCK = threading.RLock()


@dataclass
class Settings:
    folder: str
    site: str = "https://www.vinted.hr"
    price: str = "1"
    condition: str = "New without tags"
    delay: float = 15.0
    cdp: str | None = None


class Engine:
    def __init__(self, settings: Settings, log=print):
        self.s = settings
        self.log = log
        self.stop_event = threading.Event()
        self.thread: threading.Thread | None = None
        self.status = "stopped"       # stopped | connecting | running | stopping | error
        self.current: str | None = None  # image being drafted right now
        self.fails: dict[str, int] = {}

    # -- control ---------------------------------------------------------------
    def start(self) -> None:
        if self.thread and self.thread.is_alive():
            return
        self.stop_event.clear()
        self.thread = threading.Thread(target=self._run_safe, daemon=True)
        self.thread.start()

    def stop(self) -> None:
        if self.thread and self.thread.is_alive():
            self.status = "stopping"
            self.stop_event.set()

    def running(self) -> bool:
        return bool(self.thread and self.thread.is_alive())

    def _sleep(self, seconds: float) -> bool:
        """Sleep, but wake up early on Stop. Returns False if we should stop."""
        return not self.stop_event.wait(max(0.0, seconds))

    # -- worker ----------------------------------------------------------------
    def _run_safe(self) -> None:
        try:
            self._run()
            self.status = "stopped"
        except SystemExit as e:  # connect() reports problems via sys.exit(message)
            self.log(str(e))
            self.status = "error"
        except Exception as e:
            self.log(f"Stopped because of an error: {e}")
            self.status = "error"
        finally:
            self.current = None

    def _run(self) -> None:
        s = self.s
        folder = Path(s.folder)
        folder.mkdir(parents=True, exist_ok=True)
        from card_names import _load_db
        _load_db()

        self.status = "connecting"
        pw, browser, ctx = v.connect(s.cdp)
        page = ctx.new_page()
        self.status = "running"
        done = v.load_done()
        held_shown: set[str] = set()
        last_post = 0.0
        args = _Args(s)
        self.log(f"Connected. Watching {folder} - new photos become drafts on {s.site}.")
        try:
            while not self.stop_event.is_set():
                if not browser.is_connected():
                    self.log("Chrome connection closed (Chrome restarted?). Press Start again.")
                    self.status = "error"
                    return
                with CSV_LOCK:
                    rows = v.extract_all(folder, watch=True)
                todo = None
                for r in rows:
                    img = r["image"]
                    if img in done or self.fails.get(img, 0) >= 2 or not Path(img).exists():
                        continue
                    if v.needs_review(r):
                        key = f"{img}|{r.get('name')}|{r.get('check')}"
                        if key not in held_shown and r.get("name", "").strip() and r.get("check") != "skipped":
                            held_shown.add(key)
                            self.log(f"  HELD {Path(img).name}: '{r['name']}' [{r.get('check')}] - check it and press Approve")
                        continue
                    todo = r
                    break
                if todo is None:
                    self._sleep(5)
                    continue

                gap = s.delay * random.uniform(1, 2.5)
                if random.random() < 0.15 and last_post:
                    gap += random.uniform(30, 120)  # occasional longer break
                wait = gap - (time.time() - last_post)
                if wait > 0:
                    self.log(f"      (waiting {wait:.0f}s)")
                    if not self._sleep(wait):
                        break
                with CSV_LOCK:  # re-read: you may have edited this card while we waited
                    fresh = {r["image"]: r for r in v.read_csv()}.get(todo["image"])
                if not fresh or v.needs_review(fresh) or not fresh.get("name", "").strip():
                    continue
                if page.is_closed():
                    page = ctx.new_page()
                self.current = fresh["image"]
                ok = v.post_one(page, fresh, args, done, "->")
                self.current = None
                if not ok:
                    self.fails[fresh["image"]] = self.fails.get(fresh["image"], 0) + 1
                    if self.fails[fresh["image"]] >= 2:
                        self.log(f"     giving up on {Path(fresh['image']).name} (use Retry after fixing it)")
                last_post = time.time()
        finally:
            try:
                if not page.is_closed():
                    page.close()
            except Exception:
                pass
            pw.stop()  # disconnects only - Chrome stays open
            self.log("Disconnected from Chrome.")


class _Args:
    """Adapter so vinted_lister.post_one() can read settings like argparse args."""
    def __init__(self, s: Settings):
        self.site, self.price, self.condition = s.site.rstrip("/"), s.price, s.condition


# -- names.csv helpers for the app --------------------------------------------
def update_row(image: str, **fields) -> None:
    with CSV_LOCK:
        rows = v.read_csv() if v.NAMES_CSV.exists() else []
        for r in rows:
            if r["image"] == image:
                r.update(fields)
        v.write_csv(rows)
