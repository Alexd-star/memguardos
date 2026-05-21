from dataclasses import dataclass
from typing import Dict
import json
import re


@dataclass
class OSEvent:
    event_type: str
    path: str
    process: str = ""
    action: str = "read"
    raw: str = ""

    def to_memory_text(self) -> str:
        return "OS event: %s action=%s path=%s process=%s" % (
            self.event_type,
            self.action,
            self.path,
            self.process or "unknown",
        )


def parse_os_event(raw: str) -> OSEvent:
    """Parse a tiny JSON/audit/fanotify-style event.

    This is a portable ingestion shim. On Linux it can receive lines collected
    from auditd/fanotify/eBPF wrappers; on Windows tests can feed the same JSON
    shape without kernel dependencies.
    """

    stripped = raw.strip()
    if stripped.startswith("{"):
        data: Dict[str, str] = json.loads(stripped)
        return OSEvent(
            event_type=str(data.get("event_type", "os_access")),
            path=str(data.get("path", "")),
            process=str(data.get("process", data.get("exe", ""))),
            action=str(data.get("action", "read")),
            raw=raw,
        )

    path_match = re.search(r'(?:name|path)=["\']?([^"\'\s]+)', raw)
    exe_match = re.search(r'exe=["\']?([^"\'\s]+)', raw)
    action = "read"
    if "open" in raw.lower():
        action = "open"
    if "write" in raw.lower():
        action = "write"
    return OSEvent(
        event_type="auditd_access",
        path=path_match.group(1) if path_match else "",
        process=exe_match.group(1) if exe_match else "",
        action=action,
        raw=raw,
    )

