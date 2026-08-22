import os
import sys
from pathlib import Path

try:
    from dotenv import load_dotenv
    _has_dotenv = True
except ImportError:
    _has_dotenv = False

# Base Directory
BASE_DIR = Path(__file__).resolve().parent

# Load .env file
ENV_FILE = BASE_DIR / ".env"
if _has_dotenv:
    if ENV_FILE.exists():
        load_dotenv(dotenv_path=ENV_FILE)
    else:
        load_dotenv()
elif ENV_FILE.exists():
    # Simple fallback parser for .env if python-dotenv is not yet installed
    with open(ENV_FILE, "r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if line and not line.startswith("#") and "=" in line:
                key, val = line.split("=", 1)
                os.environ.setdefault(key.strip(), val.strip())


# Telegram Settings
TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "").strip()
_raw_admin_id = os.getenv("ADMIN_CHAT_ID", "").strip()
ADMIN_CHAT_ID = int(_raw_admin_id) if _raw_admin_id.isdigit() or (_raw_admin_id.startswith("-") and _raw_admin_id[1:].isdigit()) else None

# Target Accounts
TARGET_USERNAMES = [
    u.strip() for u in os.getenv("TARGET_USERNAME", "").split(",") if u.strip()
]

# File & Directory Paths
DATA_DIR = BASE_DIR / os.getenv("DATA_DIR", "data")
LOG_DIR = BASE_DIR / os.getenv("LOG_DIR", "logs")
DB_PATH = DATA_DIR / "scraper.db"
SESSION_FILE_PATH = BASE_DIR / os.getenv("SESSION_FILE_PATH", "session.json")
LOCK_FILE_PATH = BASE_DIR / "scraper.lock"

# Log files
CRON_LOG_PATH = LOG_DIR / "cron.log"
BOT_LOG_PATH = LOG_DIR / "bot.log"
GIT_LOG_PATH = LOG_DIR / "git.log"

# Operation Parameters
JITTER_MAX_SECONDS = int(os.getenv("JITTER_MAX_SECONDS", "60"))
REQUEST_TIMEOUT = int(os.getenv("REQUEST_TIMEOUT", "15"))

# Ensure required directories exist
DATA_DIR.mkdir(parents=True, exist_ok=True)
LOG_DIR.mkdir(parents=True, exist_ok=True)


def validate_config(require_telegram: bool = True, require_target: bool = True) -> list[str]:
    """Validate core configurations and return a list of error messages."""
    errors = []
    if require_telegram:
        if not TELEGRAM_BOT_TOKEN:
            errors.append("TELEGRAM_BOT_TOKEN is missing in .env")
        if ADMIN_CHAT_ID is None:
            errors.append("ADMIN_CHAT_ID is missing or invalid in .env")
    if require_target:
        if not TARGET_USERNAMES:
            errors.append("TARGET_USERNAME is missing in .env")
    return errors
