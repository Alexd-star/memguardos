from typing import Dict, Optional
import json
import shutil
import subprocess


class JournaldLogger:
    """Best-effort journald bridge.

    On openKylin/Linux with systemd, events are mirrored into journald via
    `systemd-cat`. On Windows or minimal containers it silently falls back to
    SQLite audit only.
    """

    def __init__(self, identifier: str = "memguardd"):
        self.identifier = identifier
        self.systemd_cat = shutil.which("systemd-cat")

    def available(self) -> bool:
        return bool(self.systemd_cat)

    def log(self, event_type: str, memory_id: Optional[str], detail: Dict[str, object]) -> None:
        if not self.systemd_cat:
            return
        payload = {
            "event_type": event_type,
            "memory_id": memory_id,
            "detail": detail,
        }
        try:
            subprocess.run(
                [self.systemd_cat, "-t", self.identifier, "-p", "info"],
                input=json.dumps(payload, ensure_ascii=False, sort_keys=True),
                text=True,
                timeout=2,
                check=False,
            )
        except (OSError, subprocess.SubprocessError):
            return

