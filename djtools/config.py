"""Paths and persisted settings."""
import json
import os
from pathlib import Path

APP_DIR = Path.home() / "Library" / "Application Support" / "DJTools"
BACKUP_DIR = APP_DIR / "backups"
CACHE_PATH = APP_DIR / "cache.db"
SETTINGS_PATH = APP_DIR / "settings.json"
DEFAULT_RB_DB = Path.home() / "Library" / "Pioneer" / "rekordbox" / "master.db"

AUDIO_EXTS = {".mp3", ".flac", ".wav", ".aif", ".aiff", ".m4a", ".aac", ".ogg"}
BACKUPS_KEPT = 20

# The four My Tag columns rekordbox offers, named after what the user asked for.
SUGGESTED_COLUMNS = ["Genre", "Mood", "Set position", "Favorite"]
FAVORITE = "Favorite"


class Settings:
    def __init__(self):
        APP_DIR.mkdir(parents=True, exist_ok=True)
        self._data = {}
        if SETTINGS_PATH.exists():
            try:
                self._data = json.loads(SETTINGS_PATH.read_text())
            except (OSError, ValueError):
                self._data = {}

    def _save(self):
        SETTINGS_PATH.write_text(json.dumps(self._data, indent=2))

    def get(self, name, default=None):
        return self._data.get(name, default)

    def set(self, name, value):
        self._data[name] = value
        self._save()

    @property
    def library_root(self):
        return self._data.get("library_root")

    @library_root.setter
    def library_root(self, value):
        self._data["library_root"] = value
        self._save()

    @property
    def rekordbox_db(self) -> Path:
        # DJTOOLS_RB_DB points the app at a copy of master.db for testing.
        override = os.environ.get("DJTOOLS_RB_DB") or self._data.get("rekordbox_db")
        return Path(override) if override else DEFAULT_RB_DB
