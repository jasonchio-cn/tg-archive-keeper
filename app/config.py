"""Configuration management."""

import os
from pathlib import Path
from dotenv import load_dotenv

load_dotenv()

# Telegram Bot
_BOT_TOKEN = os.getenv("BOT_TOKEN")
if not _BOT_TOKEN:
    raise ValueError("BOT_TOKEN is required in environment variables")

BOT_TOKEN: str = _BOT_TOKEN

# Bot polling/network stability settings
# - BOT_GETUPDATES_TIMEOUT: Telegram getUpdates long-poll wait time (seconds)
# - BOT_*_TIMEOUT: HTTP client timeouts (seconds)
# - BOT_POLL_INTERVAL: sleep between polling requests (seconds)
BOT_POLL_INTERVAL = float(os.getenv("BOT_POLL_INTERVAL", "1.0"))
BOT_GETUPDATES_TIMEOUT = int(os.getenv("BOT_GETUPDATES_TIMEOUT", "30"))

BOT_CONNECT_TIMEOUT = float(os.getenv("BOT_CONNECT_TIMEOUT", "10"))
# Keep this >= BOT_GETUPDATES_TIMEOUT + a buffer, and high enough for large downloads.
BOT_READ_TIMEOUT = float(os.getenv("BOT_READ_TIMEOUT", "90"))
BOT_WRITE_TIMEOUT = float(os.getenv("BOT_WRITE_TIMEOUT", "30"))
BOT_POOL_TIMEOUT = float(os.getenv("BOT_POOL_TIMEOUT", "5"))

# Download settings
MAX_CONCURRENT_DOWNLOADS = int(os.getenv("MAX_CONCURRENT_DOWNLOADS", "4"))

# Paths
DB_PATH = Path(os.getenv("DB_PATH", "/data/task_db/app.db"))
LOG_PATH = Path(os.getenv("LOG_PATH", "/data/logs"))
FILES_PATH = Path(os.getenv("FILES_PATH", "/data/files"))
NOTES_PATH = Path(os.getenv("NOTES_PATH", "/data/notes"))
TASK_DB_PATH = DB_PATH.parent

# WebDAV settings (optional)
WEBDAV_URL = os.getenv("WEBDAV_URL", "")
WEBDAV_USERNAME = os.getenv("WEBDAV_USERNAME", "")
WEBDAV_PASSWORD = os.getenv("WEBDAV_PASSWORD", "")
WEBDAV_ENABLED = bool(WEBDAV_URL and WEBDAV_USERNAME and WEBDAV_PASSWORD)

# Storage mode: local, webdav, or both (comma-separated)
_STORAGE_MODE = os.getenv("STORAGE_MODE", "local").lower()
STORAGE_MODES = [m.strip() for m in _STORAGE_MODE.split(",") if m.strip()]

SAVE_TO_LOCAL = "local" in STORAGE_MODES
SAVE_TO_WEBDAV = "webdav" in STORAGE_MODES

if SAVE_TO_WEBDAV and not WEBDAV_ENABLED:
    SAVE_TO_WEBDAV = False
    if not SAVE_TO_LOCAL:
        SAVE_TO_LOCAL = True

if not SAVE_TO_LOCAL and not SAVE_TO_WEBDAV:
    SAVE_TO_LOCAL = True

# Ensure directories exist
if SAVE_TO_LOCAL:
    for path in [TASK_DB_PATH, LOG_PATH, FILES_PATH, NOTES_PATH]:
        path.mkdir(parents=True, exist_ok=True)
else:
    for path in [TASK_DB_PATH, LOG_PATH, NOTES_PATH]:
        path.mkdir(parents=True, exist_ok=True)
