from pathlib import Path
from typing import Dict, Optional
import platform
import shutil
import sqlite3
import sys

from memguard.config import default_db_path, default_store_path, get_home, namespace_status, normalize_namespace
from memguard.os_guard.cgroup import cgroup_status
from memguard.retrieval.kylin_sdk import kylin_sdk_status


def doctor_report(namespace: Optional[str] = None) -> Dict[str, object]:
    namespace = normalize_namespace(namespace)
    return {
        "python": sys.version.split()[0],
        "platform": platform.platform(),
        "home": str(get_home()),
        "namespace": namespace,
        "namespace_status": namespace_status(namespace),
        "store_path": str(default_store_path(namespace, create=False)),
        "db_path": str(default_db_path(namespace, create=False)),
        "sqlite_version": sqlite3.sqlite_version,
        "commands": {
            "systemctl": shutil.which("systemctl"),
            "journalctl": shutil.which("journalctl"),
            "systemd-cat": shutil.which("systemd-cat"),
            "auditctl": shutil.which("auditctl"),
            "bpftrace": shutil.which("bpftrace"),
        },
        "paths": {
            "proc": Path("/proc").exists(),
            "sys_fs_cgroup": Path("/sys/fs/cgroup").exists(),
        },
        "cgroup": cgroup_status(),
        "kylin_ai_sdk": kylin_sdk_status(probe_embedding=False),
    }
