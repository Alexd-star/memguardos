from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Iterable, List
import re


NOISE_PREFIXES = ("clear", "history", "exit", "pwd", "ls", "cd ")
TOOL_HINTS = ("rg", "grep", "pytest", "git", "systemctl", "journalctl", "ssh", "scp", "pip", "python", "make")
RISK_HINTS = ("rm ", "sudo", "chmod", "chown", "dd ", "mkfs", "git reset", "private", "token", "key")


@dataclass
class BehaviorEvent:
    command: str
    source: str
    index: int

    def to_memory_text(self) -> str:
        category = "high_risk" if any(hint in self.command for hint in RISK_HINTS) else "routine"
        tool = self.command.split()[0] if self.command.split() else "unknown"
        return (
            "User behavior: shell_command tool=%s category=%s source=%s index=%s command=%s"
            % (tool, category, self.source, self.index, self.command)
        )


def load_shell_history(path: Path, limit: int = 200) -> List[BehaviorEvent]:
    lines = path.read_text(encoding="utf-8", errors="replace").splitlines()
    return parse_shell_history(lines[-limit:], source=str(path))


def parse_shell_history(lines: Iterable[str], source: str = "shell_history") -> List[BehaviorEvent]:
    events: List[BehaviorEvent] = []
    seen = set()
    for index, raw in enumerate(lines, start=1):
        command = normalize_history_line(raw)
        if not command or command in seen:
            continue
        if is_noise_command(command):
            continue
        if not looks_memory_worthy(command):
            continue
        seen.add(command)
        events.append(BehaviorEvent(command=command, source=source, index=index))
    return events


def normalize_history_line(raw: str) -> str:
    line = raw.strip()
    if not line:
        return ""
    if line.startswith(": ") and ";" in line:
        line = line.split(";", 1)[1].strip()
    line = re.sub(r"^\s*\d+\s+", "", line)
    return " ".join(line.split())


def is_noise_command(command: str) -> bool:
    lowered = command.lower()
    return any(lowered == prefix or lowered.startswith(prefix) for prefix in NOISE_PREFIXES)


def looks_memory_worthy(command: str) -> bool:
    lowered = command.lower()
    return any(hint in lowered for hint in TOOL_HINTS + RISK_HINTS)
