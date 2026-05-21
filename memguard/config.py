from pathlib import Path
import os


def get_home() -> Path:
    configured = os.environ.get("MEMGUARD_HOME")
    if configured:
        return Path(configured).expanduser().resolve()
    return Path.home() / ".memguardos"


def ensure_home() -> Path:
    home = get_home()
    home.mkdir(parents=True, exist_ok=True)
    return home


def default_store_path() -> Path:
    return ensure_home() / "memory.segment"


def default_db_path() -> Path:
    return ensure_home() / "memguard.sqlite3"


def custom_patterns_path() -> Path:
    return ensure_home() / "sensitive_patterns.json"
