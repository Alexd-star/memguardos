from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List
import json


@dataclass
class ToolResult:
    command: str
    cwd: str = ""
    exit_code: int = 0
    stdout: str = ""
    stderr: str = ""
    duration_ms: float = 0.0
    generated_files: List[str] = field(default_factory=list)
    tool_name: str = ""
    state_before: str = ""
    state_after: str = ""
    lesson: str = ""

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "ToolResult":
        return cls(
            command=str(data.get("command", "")),
            cwd=str(data.get("cwd", "")),
            exit_code=int(data.get("exit_code", data.get("returncode", 0))),
            stdout=str(data.get("stdout", "")),
            stderr=str(data.get("stderr", "")),
            duration_ms=float(data.get("duration_ms", data.get("duration", 0.0))),
            generated_files=[str(item) for item in data.get("generated_files", [])],
            tool_name=str(data.get("tool_name", "")),
            state_before=str(data.get("state_before", "")),
            state_after=str(data.get("state_after", "")),
            lesson=str(data.get("lesson", "")),
        )

    @classmethod
    def from_json_file(cls, path: Path) -> "ToolResult":
        data = json.loads(path.read_text(encoding="utf-8"))
        return cls.from_dict(data)

    def to_memory_text(self) -> str:
        status = "success" if self.exit_code == 0 else "failure"
        stdout_summary = summarize_stream(self.stdout)
        stderr_summary = summarize_stream(self.stderr)
        files = ", ".join(self.generated_files[:8])
        parts = [
            "Tool result: command=%s status=%s exit_code=%s cwd=%s duration_ms=%.3f "
            "stdout_summary=%s stderr_summary=%s generated_files=%s"
            % (
                self.command,
                status,
                self.exit_code,
                self.cwd,
                self.duration_ms,
                stdout_summary,
                stderr_summary,
                files,
            )
        ]
        if self.state_before or self.state_after or self.lesson:
            parts.append(
                "procedural_memory=state_action_result state_before=%s action=%s result=%s state_after=%s lesson=%s"
                % (
                    summarize_stream(self.state_before, limit=180),
                    summarize_stream(self.command, limit=180),
                    status,
                    summarize_stream(self.state_after, limit=180),
                    summarize_stream(self.lesson, limit=220),
                )
            )
        return " ".join(parts)


def summarize_stream(text: str, limit: int = 240) -> str:
    clean = " ".join(text.strip().split())
    if len(clean) <= limit:
        return clean
    return clean[: limit - 3] + "..."
