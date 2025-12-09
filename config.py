"""
Configuration for Telegram client.

Values are loaded from environment variables and an optional .env file.
Fill in your own `api_id` and `api_hash` from https://my.telegram.org.
"""
import os
from pathlib import Path


def load_env_file(path: str = ".env") -> None:
    """Simple .env loader to avoid external deps."""
    env_path = Path(path)
    if not env_path.exists():
        return
    for raw in env_path.read_text().splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        key = key.strip()
        value = value.strip().strip('"').strip("'")
        # do not override existing environment variables
        os.environ.setdefault(key, value)


load_env_file()

# Prefer environment variables so secrets are not committed to VCS.
api_id = int(os.getenv("TG_API_ID", "0"))  # set TG_API_ID in .env
api_hash = os.getenv("TG_API_HASH", "")    # set TG_API_HASH in .env
session_name = os.getenv("TG_SESSION_NAME", "tg_assistant_session")

# Path to the SQLite database file.
db_path = os.getenv("TG_DB_PATH", "messages.db")

