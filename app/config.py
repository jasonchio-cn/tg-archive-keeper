"""Configuration management."""

import os
from pathlib import Path
from dotenv import load_dotenv

# Load environment variables
load_dotenv()

# Telegram Bot
_BOT_TOKEN = os.getenv("BOT_TOKEN")
_USER_ID = os.getenv("USER_ID")

# Validate required config
if not _BOT_TOKEN:
    raise ValueError("BOT_TOKEN is required in environment variables")

BOT_TOKEN: str = _BOT_TOKEN
USER_ID: str = _USER_ID or ""

# File size threshold (20MB default)
FILE_SIZE_THRESHOLD = int(os.getenv("FILE_SIZE_THRESHOLD", "20971520"))

# Worker settings
MAX_ATTEMPTS = int(os.getenv("MAX_ATTEMPTS", "8"))
STALE_JOB_MINUTES = int(os.getenv("STALE_JOB_MINUTES", "30"))

# Paths
DB_PATH = Path(os.getenv("DB_PATH", "/data/app.db"))
LOG_PATH = Path(os.getenv("LOG_PATH", "/data/logs"))
FILES_PATH = Path(os.getenv("FILES_PATH", "/data/files"))
NOTES_PATH = Path(os.getenv("NOTES_PATH", "/data/notes"))

# Ensure directories exist
for path in [DB_PATH.parent, LOG_PATH, FILES_PATH, NOTES_PATH]:
    path.mkdir(parents=True, exist_ok=True)
