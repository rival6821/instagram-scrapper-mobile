import json
import logging
import os
import re
from datetime import datetime
from pathlib import Path
from typing import Any, Optional
from config import SESSION_FILE_PATH

logger = logging.getLogger(__name__)


def load_session(path: Path = SESSION_FILE_PATH) -> dict[str, Any]:
    """
    Load session data from session.json.
    Returns a dictionary containing session information.
    """
    if not path.exists():
        return {}
    try:
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
            if isinstance(data, dict):
                return data
            elif isinstance(data, str):
                return {"sessionid": data.strip()}
            return {}
    except Exception as e:
        logger.error(f"Failed to read session file from {path}: {e}")
        return {}


def get_sessionid(path: Path = SESSION_FILE_PATH) -> Optional[str]:
    """Extract and validate sessionid string from session file."""
    data = load_session(path)
    sessionid = data.get("sessionid", "").strip()
    return sessionid if sessionid else None


def validate_sessionid_format(sessionid: str) -> bool:
    """
    Validate that the sessionid string conforms to expected Instagram session format.
    Instagram sessionids typically contain user_id%3A... or are url-encoded strings (at least 15 chars).
    """
    if not sessionid or not isinstance(sessionid, str):
        return False
    sessionid = sessionid.strip()
    if len(sessionid) < 10:
        return False
    # Allow alphanumeric, %, :, _, -, .
    if not re.match(r"^[A-Za-z0-9%:\-_.]+$", sessionid):
        return False
    return True


def save_session_atomic(
    sessionid: str,
    extra_data: Optional[dict[str, Any]] = None,
    path: Path = SESSION_FILE_PATH
) -> bool:
    """
    Atomically write sessionid and metadata to session.json using a temp file and rename.
    """
    try:
        sessionid = sessionid.strip()
        payload = {
            "sessionid": sessionid,
            "updated_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        }
        if extra_data:
            payload.update(extra_data)

        temp_path = path.with_suffix(".json.tmp")
        with open(temp_path, "w", encoding="utf-8") as f:
            json.dump(payload, f, ensure_ascii=False, indent=2)
            f.flush()
            os.fsync(f.fileno())

        # Atomic replace
        os.replace(temp_path, path)
        logger.info(f"Session successfully updated atomically at {path}")
        return True
    except Exception as e:
        logger.error(f"Failed to save session atomically: {e}")
        temp_path = path.with_suffix(".json.tmp")
        if temp_path.exists():
            try:
                temp_path.unlink()
            except Exception:
                pass
        return False
