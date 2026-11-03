"""Self-update from GitHub Releases.

A release is made by pushing a tag like v1.2.0; GitHub Actions builds the installer and attaches it
(see .github/workflows/release.yml). The app compares that tag with its own version.
"""
from __future__ import annotations

import os
import subprocess
import sys
import tempfile
from pathlib import Path

import requests

from version import REPO, __version__

API = f"https://api.github.com/repos/{REPO}/releases/latest"
HEADERS = {"Accept": "application/vnd.github+json", "User-Agent": "yugioh-vinted-lister-updater"}


def _ver(tag: str) -> tuple[int, ...]:
    parts = []
    for p in tag.lstrip("vV").split("."):
        num = "".join(ch for ch in p if ch.isdigit())
        parts.append(int(num) if num else 0)
    return tuple(parts + [0] * (3 - len(parts)))


def check() -> dict:
    """{'available': bool, 'current', 'latest', 'notes', 'url', 'asset'} - never raises."""
    info = {"available": False, "current": __version__, "latest": None, "notes": "", "url": "", "asset": None}
    try:
        r = requests.get(API, headers=HEADERS, timeout=10)
        if r.status_code != 200:
            info["error"] = f"GitHub said {r.status_code}"
            return info
        rel = r.json()
        asset = next((a for a in rel.get("assets", []) if a["name"].lower().endswith("setup.exe")), None)
        info.update(latest=rel.get("tag_name", "").lstrip("vV"), notes=(rel.get("body") or "")[:2000],
                    url=rel.get("html_url", ""), asset=asset and asset["browser_download_url"])
        info["available"] = bool(asset) and _ver(rel.get("tag_name", "0")) > _ver(__version__)
    except Exception as e:
        info["error"] = str(e)
    return info


def can_self_update() -> bool:
    """Only the installed app updates itself (not when running from source)."""
    return getattr(sys, "frozen", False)


def download(url: str, progress=None) -> Path:
    dest = Path(tempfile.gettempdir()) / "YugiohVintedLister-Setup.exe"
    with requests.get(url, headers={"User-Agent": HEADERS["User-Agent"]}, stream=True, timeout=60) as r:
        r.raise_for_status()
        total = int(r.headers.get("content-length") or 0)
        done = 0
        with open(dest, "wb") as f:
            for chunk in r.iter_content(1 << 16):
                f.write(chunk)
                done += len(chunk)
                if progress and total:
                    progress(done / total)
    return dest


def install_and_restart(setup: Path) -> None:
    """Run the installer silently once this app has exited, then start the new version."""
    exe = Path(sys.executable)
    script = Path(tempfile.gettempdir()) / "YugiohVintedLister-update.cmd"
    script.write_text(
        "@echo off\r\n"
        "ping -n 3 127.0.0.1 >nul\r\n"  # give the app a moment to exit
        f'start "" /wait "{setup}" /VERYSILENT /SUPPRESSMSGBOXES /NORESTART /CLOSEAPPLICATIONS\r\n'
        f'start "" "{exe}"\r\n',
        encoding="mbcs",  # cmd.exe reads the ANSI code page
    )
    flags = 0x00000200 | 0x08000000  # CREATE_NEW_PROCESS_GROUP | CREATE_NO_WINDOW (survives our exit, no console flash)
    subprocess.Popen(["cmd", "/c", str(script)], creationflags=flags, close_fds=True,
                     cwd=tempfile.gettempdir())
