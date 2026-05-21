from pathlib import Path
from typing import Dict, Optional
import os
import platform


def cgroup_status() -> Dict[str, object]:
    """Return best-effort cgroup v2 status.

    The function is intentionally safe on non-Linux development machines. On
    openKylin/Linux it reports cgroup membership plus memory/CPU files when
    visible to the current process.
    """

    status: Dict[str, object] = {
        "platform": platform.system(),
        "is_linux": platform.system().lower() == "linux",
        "cgroup_v2_mounted": Path("/sys/fs/cgroup/cgroup.controllers").exists(),
        "proc_cgroup": _read_optional(Path("/proc/self/cgroup")),
        "memory_current": _read_optional(Path("/sys/fs/cgroup/memory.current")),
        "memory_max": _read_optional(Path("/sys/fs/cgroup/memory.max")),
        "cpu_max": _read_optional(Path("/sys/fs/cgroup/cpu.max")),
        "pid": os.getpid(),
    }
    return status


def _read_optional(path: Path) -> Optional[str]:
    try:
        if path.exists():
            return path.read_text(encoding="utf-8").strip()
    except OSError:
        return None
    return None

