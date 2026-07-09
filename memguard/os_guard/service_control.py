from __future__ import annotations

from pathlib import Path
from typing import Dict
import shutil
import subprocess


def service_resource_status(
    unit: str = "memguardd.service",
    cgroup_root: Path = Path("/sys/fs/cgroup"),
) -> Dict[str, object]:
    """Return resource controls for a systemd service, not the caller process."""

    properties = _systemctl_show(unit)
    control_group = str(properties.get("ControlGroup", "")).strip()
    cgroup_v2 = (cgroup_root / "cgroup.controllers").exists()
    group_path = _safe_group_path(cgroup_root, control_group)
    active = properties.get("ActiveState") == "active"
    loaded = properties.get("LoadState") == "loaded"
    result: Dict[str, object] = {
        "unit": unit,
        "available": loaded,
        "active": active,
        "cgroup_v2": cgroup_v2,
        "service_managed": bool(cgroup_v2 and loaded and control_group and group_path is not None),
        "control_group": control_group,
        "memory_current": _read_optional(group_path / "memory.current" if group_path else None),
        "memory_max": _read_optional(group_path / "memory.max" if group_path else None),
        "cpu_max": _read_optional(group_path / "cpu.max" if group_path else None),
        "cpu_stat": _read_key_values(group_path / "cpu.stat" if group_path else None),
        "memory_max_property": properties.get("MemoryMax", ""),
        "cpu_quota_per_sec_usec": properties.get("CPUQuotaPerSecUSec", ""),
    }
    if not shutil.which("systemctl"):
        result["diagnosis_code"] = "systemctl_unavailable"
        result["advice"] = "当前系统没有 systemctl；核心功能仍可运行，但无法验证服务级资源治理。"
    elif not loaded:
        result["diagnosis_code"] = "service_not_loaded"
        result["advice"] = "memguardd.service 尚未安装，请先安装核心包或刷新 systemd 单元。"
    elif not cgroup_v2:
        result["diagnosis_code"] = "cgroup_v2_unavailable"
        result["advice"] = "系统未挂载 cgroup v2，无法读取服务资源限制。"
    elif not control_group or group_path is None:
        result["diagnosis_code"] = "control_group_unavailable"
        result["advice"] = "systemd 未返回有效 ControlGroup；请启动 memguardd.service 后刷新。"
    else:
        result["diagnosis_code"] = "ready" if active else "service_inactive"
        result["advice"] = "服务级 cgroup 资源状态正常。" if active else "服务已安装但未运行，可在控制中心启动。"
    return result


def _systemctl_show(unit: str) -> Dict[str, str]:
    systemctl = shutil.which("systemctl")
    if not systemctl:
        return {}
    try:
        completed = subprocess.run(
            [
                systemctl,
                "show",
                unit,
                "--property=LoadState,ActiveState,ControlGroup,MemoryMax,CPUQuotaPerSecUSec",
                "--no-pager",
            ],
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            text=True,
            timeout=5,
            check=False,
        )
    except (OSError, subprocess.SubprocessError):
        return {}
    result: Dict[str, str] = {}
    for line in completed.stdout.splitlines():
        if "=" not in line:
            continue
        key, value = line.split("=", 1)
        result[key] = value
    return result


def _safe_group_path(root: Path, control_group: str) -> Path | None:
    if not control_group.startswith("/"):
        return None
    root_resolved = root.resolve()
    candidate = (root / control_group.lstrip("/")).resolve()
    try:
        candidate.relative_to(root_resolved)
    except ValueError:
        return None
    return candidate


def _read_optional(path: Path | None) -> str | None:
    if path is None:
        return None
    try:
        return path.read_text(encoding="utf-8").strip() if path.exists() else None
    except OSError:
        return None


def _read_key_values(path: Path | None) -> Dict[str, int | str]:
    text = _read_optional(path)
    result: Dict[str, int | str] = {}
    if not text:
        return result
    for line in text.splitlines():
        parts = line.split(None, 1)
        if len(parts) != 2:
            continue
        key, value = parts
        try:
            result[key] = int(value)
        except ValueError:
            result[key] = value
    return result
