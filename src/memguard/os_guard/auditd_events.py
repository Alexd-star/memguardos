from __future__ import annotations

from dataclasses import dataclass
from typing import Dict, Iterable, List
import re


AUDIT_ID_RE = re.compile(r"msg=audit\([^:]+:(\d+)\)")
KEY_RE = re.compile(r'key="([^"]+)"')
PATH_RE = re.compile(r'name="([^"]+)"')
EXE_RE = re.compile(r'exe="([^"]+)"')
COMM_RE = re.compile(r'comm="([^"]+)"')
SYSCALL_RE = re.compile(r"SYSCALL=([A-Za-z0-9_]+)")


@dataclass
class AuditdAccessEvent:
    audit_id: str
    path: str
    process: str = ""
    action: str = "open"
    key: str = "memguard_sensitive"

    def to_os_event_json(self) -> Dict[str, str]:
        return {
            "event_type": "auditd_access",
            "path": self.path,
            "process": self.process,
            "action": self.action,
            "audit_id": self.audit_id,
            "key": self.key,
        }


def parse_auditd_events(lines: Iterable[str], key: str = "memguard_sensitive") -> List[AuditdAccessEvent]:
    groups: Dict[str, Dict[str, object]] = {}
    for line in lines:
        event_id = _match(AUDIT_ID_RE, line)
        if not event_id:
            continue
        group = groups.setdefault(event_id, {"paths": [], "process": "", "action": "open", "keys": set()})
        matched_key = _match(KEY_RE, line)
        if matched_key:
            group["keys"].add(matched_key)
        matched_path = _match(PATH_RE, line)
        if matched_path:
            group["paths"].append(matched_path)
        matched_exe = _match(EXE_RE, line)
        matched_comm = _match(COMM_RE, line)
        if matched_exe:
            group["process"] = matched_exe
        elif matched_comm and not group.get("process"):
            group["process"] = matched_comm
        syscall = _match(SYSCALL_RE, line)
        if syscall:
            group["action"] = _action_from_syscall(syscall)

    events: List[AuditdAccessEvent] = []
    seen = set()
    for event_id, group in groups.items():
        if key not in group["keys"]:
            continue
        for path in group["paths"]:
            marker = (event_id, path)
            if marker in seen:
                continue
            seen.add(marker)
            events.append(
                AuditdAccessEvent(
                    audit_id=event_id,
                    path=str(path),
                    process=str(group.get("process") or ""),
                    action=str(group.get("action") or "open"),
                    key=key,
                )
            )
    return events


def _match(pattern: re.Pattern, text: str) -> str:
    match = pattern.search(text)
    return match.group(1) if match else ""


def _action_from_syscall(syscall: str) -> str:
    lowered = syscall.lower()
    if "write" in lowered or "unlink" in lowered or "rename" in lowered:
        return "write"
    if "open" in lowered or "read" in lowered or "stat" in lowered:
        return "open"
    return lowered
