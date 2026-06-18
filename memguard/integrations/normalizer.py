from __future__ import annotations

from typing import Any, Dict, Iterable, List

from memguard.integrations.events import AgentInteractionEvent


SENSITIVE_KEY_MARKERS = {
    "api_key",
    "apikey",
    "authorization",
    "credential",
    "credentials",
    "password",
    "passwd",
    "private_key",
    "secret",
    "token",
}


def normalize_tool_args(value: Dict[str, Any]) -> Dict[str, Any]:
    if not isinstance(value, dict):
        raise ValueError("tool_args must be an object")
    return _normalize_mapping(value)


def normalize_event(event: AgentInteractionEvent) -> AgentInteractionEvent:
    payload = event.to_dict()
    payload["tool_args"] = normalize_tool_args(event.tool_args)
    payload["provenance_hash"] = ""
    return AgentInteractionEvent.from_dict(payload)


def normalize_events(events: Iterable[AgentInteractionEvent]) -> List[AgentInteractionEvent]:
    normalized = [normalize_event(event) for event in events]
    return sorted(
        normalized,
        key=lambda event: (
            event.agent_name,
            event.session_id,
            event.sequence,
            event.turn_id,
            event.event_id,
        ),
    )


def _normalize_mapping(value: Dict[str, Any]) -> Dict[str, Any]:
    result: Dict[str, Any] = {}
    for key, item in sorted(value.items(), key=lambda pair: str(pair[0])):
        clean_key = str(key)
        if _is_sensitive_key(clean_key):
            result[clean_key] = "[REDACTED]"
        else:
            result[clean_key] = _normalize_nested(item)
    return result


def _normalize_nested(value: Any) -> Any:
    if isinstance(value, dict):
        return _normalize_mapping(value)
    if isinstance(value, (list, tuple)):
        return [_normalize_nested(item) for item in value]
    if isinstance(value, str):
        return " ".join(value.replace("\r\n", "\n").replace("\r", "\n").split())
    if value is None or isinstance(value, (bool, int, float)):
        return value
    return str(value)


def _is_sensitive_key(key: str) -> bool:
    normalized = key.strip().lower().replace("-", "_")
    return normalized in SENSITIVE_KEY_MARKERS or any(
        normalized.endswith("_" + marker) for marker in SENSITIVE_KEY_MARKERS
    )
