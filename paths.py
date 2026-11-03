"""Where the app keeps its files.

Running from source: next to the code (as before).
Installed app (PyInstaller build): %APPDATA%\\YugiohVintedLister, since Program Files isn't writable.
"""
import os
import sys
from pathlib import Path

APP_NAME = "YugiohVintedLister"
FROZEN = getattr(sys, "frozen", False)

if FROZEN:
    DATA_DIR = Path(os.environ.get("APPDATA", Path.home())) / APP_NAME
else:
    DATA_DIR = Path(__file__).parent
DATA_DIR.mkdir(parents=True, exist_ok=True)

NAMES_CSV = DATA_DIR / "names.csv"
DONE_FILE = DATA_DIR / "done.json"
SETTINGS = DATA_DIR / "settings.json"
DB_CACHE = DATA_DIR / "cards_db_v3.json"
UPLOAD_CACHE = DATA_DIR / ".upload_cache"
DEFAULT_CARDS = (Path.home() / "Pictures" / "Yugioh cards") if FROZEN else Path(__file__).parent / "cards"
