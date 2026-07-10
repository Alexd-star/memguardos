from __future__ import annotations

from pathlib import Path
from typing import Dict
import json
import shutil
import subprocess

from memguard.sensitivity.detector import SensitiveDetector
from memguard.service import MemGuardService


TEXT_SUFFIXES = {
    ".txt",
    ".md",
    ".log",
    ".json",
    ".yaml",
    ".yml",
    ".conf",
    ".ini",
    ".service",
    ".desktop",
    ".sh",
    ".py",
    ".cpp",
    ".h",
    ".c",
    ".toml",
    ".csv",
    ".xml",
    ".html",
    ".css",
    ".js",
    ".ts",
    ".sql",
    ".env",
    ".example",
}


def scan_peony_file(
    path: str | Path,
    service: MemGuardService,
    *,
    ingest: bool = False,
    notify: bool = False,
    max_bytes: int = 1024 * 1024,
) -> Dict[str, object]:
    target = Path(path).expanduser()
    report = _base_report(target)
    if not target.exists():
        report["recommended_action"] = "missing_path"
        report["warnings"].append("路径不存在，未写入文件内容。")
        _maybe_notify(report, notify)
        return report

    report["exists"] = True
    if target.is_dir():
        report["file_kind"] = "directory"
        report["recommended_action"] = "audit_only"
        return _finalize_with_optional_audit(report, service, ingest=ingest, notify=notify)
    if not target.is_file():
        report["file_kind"] = "special"
        report["recommended_action"] = "audit_only"
        report["warnings"].append("特殊文件不会直接读取，只记录 OS 事件。")
        return _finalize_with_optional_audit(report, service, ingest=ingest, notify=notify)

    size = target.stat().st_size
    report["size_bytes"] = size
    report["file_kind"] = "file"
    report["eligible_for_text_import"] = _is_text_candidate(target, size, max_bytes)
    if not report["eligible_for_text_import"]:
        report["recommended_action"] = "audit_only"
        if size <= 0:
            report["warnings"].append("空文件不会进入记忆。")
        elif size > max_bytes:
            report["warnings"].append("文件超过桌面导入上限，只记录 OS 事件。")
        else:
            report["warnings"].append("文件类型不在安全文本导入白名单内。")
        return _finalize_with_optional_audit(report, service, ingest=ingest, notify=notify)

    text = target.read_text(encoding="utf-8", errors="replace")
    report["preview"] = " ".join(text.split())[:240]
    sensitivity = SensitiveDetector().classify(text, str(target))
    report["sensitivity"] = sensitivity.__dict__
    if sensitivity.level == "high":
        report["recommended_action"] = "block_sensitive"
        report["warnings"].append("检测到高风险敏感内容，默认不写入长期记忆。")
        return _finalize_with_optional_audit(report, service, ingest=ingest, notify=notify)

    report["recommended_action"] = "import_text"
    if ingest:
        result = service.ingest_text(text, source_kind="peony_file", source_uri=str(target))
        report["ingest"] = _ingest_summary(result, attempted=True)
    return _finalize_with_optional_audit(report, service, ingest=ingest, notify=notify)


def _base_report(target: Path) -> Dict[str, object]:
    return {
        "schema": "memguardos-peony-scan-report-v1",
        "path": str(target),
        "exists": False,
        "file_kind": "unknown",
        "size_bytes": 0,
        "eligible_for_text_import": False,
        "recommended_action": "audit_only",
        "sensitivity": {"level": "none", "labels": [], "redacted": False},
        "preview": "",
        "warnings": [],
        "audit_event": {"attempted": False, "stored": False},
        "ingest": {"attempted": False, "stored": False},
        "verification": {"audit_chain_ok": None},
    }


def _is_text_candidate(path: Path, size: int, max_bytes: int) -> bool:
    return (path.suffix.lower() in TEXT_SUFFIXES or path.name.lower() in TEXT_SUFFIXES) and 0 < size <= max_bytes


def _finalize_with_optional_audit(report: Dict[str, object], service: MemGuardService, *, ingest: bool, notify: bool) -> Dict[str, object]:
    if ingest:
        event = {
            "event_type": "peony_context_menu",
            "path": report["path"],
            "process": "peony",
            "action": "inspect",
            "recommended_action": report["recommended_action"],
        }
        result = service.ingest_os_event(json.dumps(event, ensure_ascii=False))
        report["audit_event"] = {
            "attempted": True,
            "stored": bool(result.get("stored")),
            "reason": result.get("reason", ""),
        }
        report["verification"]["audit_chain_ok"] = bool(service.verify_audit().get("ok"))
    _maybe_notify(report, notify)
    return report


def _ingest_summary(result: Dict[str, object], *, attempted: bool) -> Dict[str, object]:
    summary = {
        "attempted": attempted,
        "stored": bool(result.get("stored")),
        "reason": result.get("reason", ""),
        "feedback": result.get("feedback"),
        "sensitive_notice": result.get("sensitive_notice"),
        "audit_event_id": result.get("audit_event_id", ""),
    }
    record = result.get("record")
    if record is not None:
        summary.update(
            {
                "memory_id": record.memory_id,
                "type": record.type,
                "subtype": record.subtype,
                "tier": record.tier,
            }
        )
    return summary


def _maybe_notify(report: Dict[str, object], notify: bool) -> None:
    if not notify or not shutil.which("notify-send"):
        return
    title = "MemGuardOS 文件扫描"
    action = str(report["recommended_action"])
    if action == "import_text" and report["ingest"]["stored"]:
        body = "文本内容已导入记忆，并完成审计链校验。"
    elif action == "block_sensitive":
        body = "检测到高风险敏感内容，已阻止写入记忆。"
    elif action == "audit_only":
        body = "已记录文件选择事件；目录、大文件或非文本文件未读取内容。"
    else:
        body = "扫描完成，请查看 JSON 报告。"
    notify_desktop(title, body)


def notify_desktop(title: str, body: str) -> bool:
    executable = shutil.which("notify-send")
    if not executable:
        return False
    completed = subprocess.run(
        [executable, "--app-name=MemGuardOS", title, body],
        check=False,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    return completed.returncode == 0
