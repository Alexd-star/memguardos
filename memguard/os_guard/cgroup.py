from pathlib import Path
from typing import Dict, Optional
import os
import platform


def cgroup_status(
    *, cgroup_root: Path = Path("/sys/fs/cgroup"), proc_root: Path = Path("/proc"),
) -> Dict[str, object]:
    """Return best-effort cgroup v2 status.

    The function is intentionally safe on non-Linux development machines. On
    openKylin/Linux it reports cgroup membership plus memory/CPU files when
    visible to the current process.
    """

    membership = _read_optional(proc_root / "self/cgroup")
    current = cgroup_root.resolve()
    for line in (membership or "").splitlines():
        if line.startswith("0::"):
            candidate = (cgroup_root / line[3:].lstrip("/")).resolve()
            if candidate.is_relative_to(cgroup_root.resolve()):
                current = candidate
            break
    status: Dict[str, object] = {
        "platform": platform.system(),
        "is_linux": platform.system().lower() == "linux",
        "cgroup_v2_mounted": (cgroup_root / "cgroup.controllers").exists(),
        "proc_cgroup": membership,
        "cgroup_path": str(current),
        "memory_current": _read_optional(current / "memory.current"),
        "memory_max": _read_optional(current / "memory.max"),
        "memory_high": _read_optional(current / "memory.high"),
        "cpu_max": _read_optional(current / "cpu.max"),
        "pid": os.getpid(),
    }
    # Ancestors can impose tighter budgets than this leaf cgroup.
    ratios = []
    node = current
    while node.is_relative_to(cgroup_root.resolve()):
        usage = _read_optional(node / "memory.current")
        limits = [_read_optional(node / name) for name in ("memory.max", "memory.high")]
        if usage and usage.isdigit():
            for limit in limits:
                if limit and limit.isdigit() and int(limit) > 0:
                    ratios.append(int(usage) / int(limit))
        if node == cgroup_root.resolve():
            break
        node = node.parent
    status["memory_budget_ratio"] = max(ratios) if ratios else None
    pressures = {}
    for resource in ("cpu", "memory", "io"):
        local = _psi_some_avg10(_read_optional(current / (resource + ".pressure")))
        system = _psi_some_avg10(_read_optional(proc_root / "pressure" / resource))
        readings = [value for value in (local, system) if value is not None]
        pressures[resource] = max(readings) if readings else None
    status["pressure_some_avg10"] = pressures
    return status


def _psi_some_avg10(raw: Optional[str]) -> Optional[float]:
    for line in (raw or "").splitlines():
        fields = line.split()
        if fields and fields[0] == "some":
            for field in fields[1:]:
                if field.startswith("avg10="):
                    try:
                        value = float(field.split("=", 1)[1])
                        return value if 0 <= value <= 100 else None
                    except ValueError:
                        return None
    return None


def _read_optional(path: Path) -> Optional[str]:
    try:
        if path.exists():
            return path.read_text(encoding="utf-8").strip()
    except OSError:
        return None
    return None
