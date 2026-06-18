from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any, Dict, Optional
import json

from memguard.models import new_id, stable_hash, utc_now


ALLOWED_EVENT_TYPES = {
    "user_message",
    "assistant_message",
    "system_message",
    "tool_call",
    "tool_result",
    "user_feedback",
    "task_complete",
    "task_error",
    "session_start",
    "session_end",
}
ALLOWED_ROLES = {"user", "assistant", "tool", "system"}


@dataclass(frozen=True)
class AgentInteractionEvent:
    agent_name: str
    agent_version: str
    session_id: str
    turn_id: str
    sequence: int
    event_type: str
    role: str
    content: str = ""
    tool_name: str = ""
    tool_args: Dict[str, Any] = field(default_factory=dict)
    tool_result: str = ""
    exit_code: Optional[int] = None
    state_before: str = ""
    state_after: str = ""
    user_feedback: str = ""
    project_root: str = ""
    task_type: str = ""
    timestamp: str = field(default_factory=utc_now)
    source_uri: str = ""
    schema_version: str = "1.0"
    event_id: str = field(default_factory=lambda: new_id("evt"))
    provenance_hash: str = ""

    def __post_init__(self) -> None:
        self._validate()
        for name in (
            "agent_name",
            "agent_version",
            "session_id",
            "turn_id",
            "event_type",
            "role",
            "content",
            "tool_name",
            "tool_result",
            "state_before",
            "state_after",
            "user_feedback",
            "project_root",
            "task_type",
            "source_uri",
        ):
            object.__setattr__(self, name, _normalize_text(str(getattr(self, name))))
        object.__setattr__(self, "tool_args", _normalize_value(self.tool_args))
        expected_hash = stable_hash(_canonical_json(self._hash_payload()))
        if self.provenance_hash and self.provenance_hash != expected_hash:
            raise ValueError("provenance_hash does not match normalized event payload")
        object.__setattr__(self, "provenance_hash", expected_hash)

    def _validate(self) -> None:
        if not str(self.agent_name).strip():
            raise ValueError("agent_name is required")
        if not str(self.session_id).strip():
            raise ValueError("session_id is required")
        if not str(self.turn_id).strip():
            raise ValueError("turn_id is required")
        if self.event_type not in ALLOWED_EVENT_TYPES:
            raise ValueError("unsupported event_type: %s" % self.event_type)
        if self.role not in ALLOWED_ROLES:
            raise ValueError("unsupported role: %s" % self.role)
        if isinstance(self.sequence, bool) or not isinstance(self.sequence, int) or self.sequence < 0:
            raise ValueError("sequence must be a non-negative integer")
        if self.exit_code is not None and (isinstance(self.exit_code, bool) or not isinstance(self.exit_code, int)):
            raise ValueError("exit_code must be an integer or null")
        if not isinstance(self.tool_args, dict):
            raise ValueError("tool_args must be an object")

    def _hash_payload(self) -> Dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "agent_name": self.agent_name,
            "agent_version": self.agent_version,
            "session_id": self.session_id,
            "turn_id": self.turn_id,
            "sequence": self.sequence,
            "event_type": self.event_type,
            "role": self.role,
            "content": self.content,
            "tool_name": self.tool_name,
            "tool_args": self.tool_args,
            "tool_result": self.tool_result,
            "exit_code": self.exit_code,
            "state_before": self.state_before,
            "state_after": self.state_after,
            "user_feedback": self.user_feedback,
            "project_root": self.project_root,
            "task_type": self.task_type,
            "source_uri": self.source_uri,
        }

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)

    def to_json(self) -> str:
        return _canonical_json(self.to_dict())

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "AgentInteractionEvent":
        if not isinstance(data, dict):
            raise ValueError("event payload must be an object")
        return cls(**data)

    @classmethod
    def from_json(cls, payload: str) -> "AgentInteractionEvent":
        data = json.loads(payload)
        if not isinstance(data, dict):
            raise ValueError("event payload must be an object")
        return cls.from_dict(data)


def _normalize_text(value: str) -> str:
    return " ".join(value.replace("\r\n", "\n").replace("\r", "\n").split())


def _normalize_value(value: Any) -> Any:
    if isinstance(value, str):
        return _normalize_text(value)
    if isinstance(value, dict):
        return {str(key): _normalize_value(item) for key, item in sorted(value.items(), key=lambda pair: str(pair[0]))}
    if isinstance(value, (list, tuple)):
        return [_normalize_value(item) for item in value]
    if value is None or isinstance(value, (bool, int, float)):
        return value
    return _normalize_text(str(value))


def _canonical_json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
