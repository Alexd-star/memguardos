from __future__ import annotations

import os
import re
import sqlite3
from pathlib import Path
from typing import Dict, List


DEFAULT_NAMESPACE = "default"
_NAMESPACE_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,63}$")
_WINDOWS_RESERVED_NAMES = {
    "CON",
    "PRN",
    "AUX",
    "NUL",
    "COM1",
    "COM2",
    "COM3",
    "COM4",
    "COM5",
    "COM6",
    "COM7",
    "COM8",
    "COM9",
    "LPT1",
    "LPT2",
    "LPT3",
    "LPT4",
    "LPT5",
    "LPT6",
    "LPT7",
    "LPT8",
    "LPT9",
}


def get_home() -> Path:
    configured = os.environ.get("MEMGUARD_HOME")
    if configured:
        return Path(configured).expanduser().resolve()
    return Path.home() / ".memguardos"


def ensure_home() -> Path:
    home = get_home()
    home.mkdir(parents=True, exist_ok=True)
    return home


def normalize_namespace(namespace: str | None = None) -> str:
    raw = namespace
    if raw is None:
        raw = os.environ.get("MEMGUARD_NAMESPACE", DEFAULT_NAMESPACE)
    normalized = (raw or DEFAULT_NAMESPACE).strip()
    if normalized in {"", DEFAULT_NAMESPACE}:
        return DEFAULT_NAMESPACE
    if not _NAMESPACE_PATTERN.fullmatch(normalized):
        raise ValueError(
            "invalid MEMGUARD namespace %r: use 1-64 ASCII letters, digits, dot, underscore or dash, starting with a letter or digit" % normalized
        )
    if normalized.split(".", 1)[0].upper() in _WINDOWS_RESERVED_NAMES:
        raise ValueError("invalid MEMGUARD namespace %r: reserved device name" % normalized)
    return normalized


def namespace_home(namespace: str | None = None, *, create: bool = True) -> Path:
    namespace = normalize_namespace(namespace)
    home = ensure_home() if create else get_home()
    if namespace == DEFAULT_NAMESPACE:
        root = home
    else:
        root = home / "namespaces" / namespace
    if create:
        root.mkdir(parents=True, exist_ok=True)
    return root


def default_store_path(namespace: str | None = None, *, create: bool = True) -> Path:
    return namespace_home(namespace, create=create) / "memory.segment"


def default_db_path(namespace: str | None = None, *, create: bool = True) -> Path:
    return namespace_home(namespace, create=create) / "memguard.sqlite3"


def custom_patterns_path(namespace: str | None = None, *, create: bool = True) -> Path:
    return namespace_home(namespace, create=create) / "sensitive_patterns.json"


def namespace_status(namespace: str | None = None, *, create: bool = False) -> Dict[str, object]:
    namespace = normalize_namespace(namespace)
    root = namespace_home(namespace, create=create)
    store_path = root / "memory.segment"
    db_path = root / "memguard.sqlite3"
    patterns_path = root / "sensitive_patterns.json"
    status: Dict[str, object] = {
        "namespace": namespace,
        "is_default": namespace == DEFAULT_NAMESPACE,
        "home": str(get_home()),
        "home_exists": get_home().exists(),
        "namespace_home": str(root),
        "namespace_home_exists": root.exists(),
        "store_path": str(store_path),
        "db_path": str(db_path),
        "custom_patterns_path": str(patterns_path),
        "store_exists": store_path.exists(),
        "db_exists": db_path.exists(),
        "custom_patterns_exists": patterns_path.exists(),
        "store_bytes": store_path.stat().st_size if store_path.exists() else 0,
        "db_bytes": db_path.stat().st_size if db_path.exists() else 0,
        "record_count": 0,
        "live_record_count": 0,
        "audit_event_count": 0,
    }
    if db_path.exists():
        status.update(_sqlite_status_counts(db_path))
    return status


def list_namespaces() -> List[Dict[str, object]]:
    home = get_home()
    namespaces = [DEFAULT_NAMESPACE]
    namespace_root = home / "namespaces"
    if namespace_root.exists():
        for child in namespace_root.iterdir():
            if child.is_dir():
                try:
                    namespaces.append(normalize_namespace(child.name))
                except ValueError:
                    continue
    return [namespace_status(name, create=False) for name in sorted(set(namespaces))]


def _sqlite_status_counts(db_path: Path) -> Dict[str, object]:
    try:
        conn = sqlite3.connect("%s?mode=ro" % db_path.resolve().as_uri(), uri=True)
        try:
            return {
                "record_count": _table_count(conn, "memory", include_deleted=True),
                "live_record_count": _table_count(conn, "memory", include_deleted=False),
                "audit_event_count": _table_count(conn, "audit", include_deleted=True),
            }
        finally:
            conn.close()
    except sqlite3.Error as exc:
        return {"db_read_error": str(exc)}


def _table_count(conn: sqlite3.Connection, table: str, *, include_deleted: bool) -> int:
    exists = conn.execute("SELECT name FROM sqlite_master WHERE type='table' AND name=?", (table,)).fetchone()
    if not exists:
        return 0
    if table == "memory" and not include_deleted:
        return int(conn.execute("SELECT COUNT(*) FROM memory WHERE deleted = 0").fetchone()[0])
    return int(conn.execute("SELECT COUNT(*) FROM %s" % table).fetchone()[0])
