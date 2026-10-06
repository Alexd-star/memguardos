from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable, Dict, Iterable, Mapping, Optional, Tuple
import json
import re

from memguard.integrations.events import AgentInteractionEvent, ALLOWED_EVENT_TYPES, ALLOWED_ROLES
from memguard.models import stable_hash


CONTEXT_STAGES: Tuple[str, ...] = (
    "planning",
    "tool_selection",
    "response_generation",
)


@dataclass(frozen=True)
class AdapterCapabilities:
    """Capabilities advertised by one Agent event adapter.

    The registry describes integration boundaries only. It does not grant an
    adapter permission to bypass the service's sensitivity, scope, or
    forgetting checks.
    """

    name: str
    version: str
    description: str
    event_types: Tuple[str, ...]
    context_stages: Tuple[str, ...] = CONTEXT_STAGES
    supports_event_ingest: bool = True
    supports_context: bool = True
    supports_checkpoints: bool = False

    def to_dict(self) -> Dict[str, object]:
        return {
            "name": self.name,
            "version": self.version,
            "description": self.description,
            "event_types": list(self.event_types),
            "context_stages": list(self.context_stages),
            "supports_event_ingest": self.supports_event_ingest,
            "supports_context": self.supports_context,
            "supports_checkpoints": self.supports_checkpoints,
        }


class GenericAgentEventAdapter:
    """Map provider-neutral JSON events into the canonical AgentEvent contract.

    This adapter deliberately accepts only a small, documented set of aliases.
    Unknown event kinds fail closed instead of being silently treated as a
    user message. The resulting canonical event still passes through the same
    normalizer and service-level sensitivity policy as OpenHands events.
    """

    capabilities = AdapterCapabilities(
        name="generic-json",
        version="1.0",
        description="Provider-neutral JSON/JSONL Agent event adapter",
        event_types=tuple(sorted(ALLOWED_EVENT_TYPES)),
        supports_checkpoints=False,
    )

    _EVENT_ALIASES = {
        "message": "message",
        "chat_message": "message",
        "user": "user_message",
        "assistant": "assistant_message",
        "action": "tool_call",
        "function_call": "tool_call",
        "command": "tool_call",
        "observation": "tool_result",
        "tool_output": "tool_result",
        "feedback": "user_feedback",
        "complete": "task_complete",
        "success": "task_complete",
        "failure": "task_error",
        "error": "task_error",
        "policy": "policy_decision",
        "session_begin": "session_start",
        "session_finish": "session_end",
    }

    def __init__(
        self,
        *,
        agent_name: str = "generic-agent",
        agent_version: str = "unknown",
        session_id: str = "",
        project_root: str = "",
        task_type: str = "",
    ) -> None:
        self.agent_name = str(agent_name).strip() or "generic-agent"
        self.agent_version = str(agent_version).strip() or "unknown"
        self.session_id = str(session_id).strip()
        self.project_root = str(project_root or "")
        self.task_type = str(task_type or "")
        self.sequence = 0

    def convert(self, raw_event: Mapping[str, Any]) -> AgentInteractionEvent:
        if not isinstance(raw_event, Mapping):
            raise ValueError("agent event must be a JSON object")
        payload = dict(raw_event)
        nested = payload.get("event")
        if isinstance(nested, Mapping):
            payload = {**dict(nested), **{key: value for key, value in payload.items() if key != "event"}}

        raw_type = str(payload.get("event_type") or payload.get("type") or "").strip().lower()
        event_type = self._canonical_event_type(raw_type, payload)
        role = self._canonical_role(payload.get("role"), event_type)
        self.sequence = self._sequence(payload.get("sequence"))
        session_id = str(payload.get("session_id") or self.session_id).strip()
        if not session_id:
            raise ValueError("session_id is required for generic Agent events")
        turn_id = str(payload.get("turn_id") or "turn-%s" % max(1, self.sequence)).strip()
        event_id = str(payload.get("event_id") or payload.get("id") or "").strip()
        if not event_id:
            event_id = "evt_%s" % stable_hash(
                json.dumps(
                    {
                        "agent_name": str(payload.get("agent_name") or self.agent_name),
                        "agent_version": str(payload.get("agent_version") or self.agent_version),
                        "session_id": session_id,
                        "turn_id": turn_id,
                        "sequence": self.sequence,
                        "event_type": event_type,
                        "role": role,
                        "content": _text(payload, "content", "text", "message", "thought", "error"),
                        "tool_name": _text(payload, "tool_name", "tool", "function", "command_name"),
                        "tool_args": _mapping(payload.get("tool_args", payload.get("arguments", payload.get("args", {})))),
                        "tool_result": _text(payload, "tool_result", "output", "stdout", "observation"),
                    },
                    ensure_ascii=False,
                    sort_keys=True,
                    separators=(",", ":"),
                )
            )[:24]
        if not event_id:
            raise ValueError("event_id must not be empty")

        return AgentInteractionEvent(
            agent_name=str(payload.get("agent_name") or self.agent_name).strip(),
            agent_version=str(payload.get("agent_version") or self.agent_version).strip(),
            session_id=session_id,
            turn_id=turn_id,
            sequence=self.sequence,
            event_type=event_type,
            role=role,
            content=_text(payload, "content", "text", "message", "thought", "error"),
            tool_name=_text(payload, "tool_name", "tool", "function", "command_name"),
            tool_args=_mapping(payload.get("tool_args", payload.get("arguments", payload.get("args", {})))),
            tool_result=_text(payload, "tool_result", "output", "stdout", "observation"),
            exit_code=_exit_code(payload.get("exit_code", payload.get("status_code"))),
            state_before=_text(payload, "state_before"),
            state_after=_text(payload, "state_after"),
            user_feedback=_text(payload, "user_feedback", "feedback"),
            project_root=str(payload.get("project_root") or self.project_root),
            task_type=str(payload.get("task_type") or self.task_type),
            timestamp=str(payload.get("timestamp") or ""),
            source_uri=str(payload.get("source_uri") or "agent://%s/%s" % (session_id, event_id)),
            schema_version=str(payload.get("schema_version") or "1.0"),
            event_id=event_id,
            # The canonical event recomputes its hash after normalization.
            provenance_hash="",
        )

    def convert_many(self, raw_events: Iterable[Mapping[str, Any]]) -> list[AgentInteractionEvent]:
        converted = []
        for index, raw_event in enumerate(raw_events, start=1):
            try:
                converted.append(self.convert(raw_event))
            except ValueError as exc:
                raise ValueError("invalid generic Agent event at item %s: %s" % (index, exc)) from exc
        return converted

    def _canonical_event_type(self, raw_type: str, payload: Mapping[str, Any]) -> str:
        canonical = self._EVENT_ALIASES.get(raw_type, raw_type)
        if canonical == "message":
            role = str(payload.get("role") or "assistant").strip().lower()
            canonical = (
                "user_message"
                if role == "user"
                else "system_message"
                if role in {"system", "developer", "environment", "hook"}
                else "assistant_message"
            )
        if canonical not in ALLOWED_EVENT_TYPES:
            raise ValueError("unsupported event_type: %s" % (raw_type or "missing"))
        return canonical

    @staticmethod
    def _canonical_role(raw_role: Any, event_type: str) -> str:
        role = str(raw_role or "").strip().lower()
        if role in {"developer", "environment", "hook"}:
            role = "system"
        elif role in {"function", "function_result", "observation"}:
            role = "tool"
        if role not in ALLOWED_ROLES:
            role = {
                "user_message": "user",
                "user_feedback": "user",
                "tool_call": "assistant",
                "tool_result": "tool",
                "policy_decision": "system",
                "session_start": "system",
                "session_end": "system",
                "task_error": "assistant",
                "task_complete": "assistant",
            }.get(event_type, "assistant")
        return role

    def _sequence(self, value: Any) -> int:
        if value is None or str(value).strip() == "":
            self.sequence += 1
            return self.sequence
        if isinstance(value, bool):
            raise ValueError("sequence must be an integer")
        try:
            sequence = int(value)
        except (TypeError, ValueError) as exc:
            raise ValueError("sequence must be an integer") from exc
        if sequence < 0:
            raise ValueError("sequence must be non-negative")
        self.sequence = sequence
        return sequence


@dataclass(frozen=True)
class AdapterRegistration:
    name: str
    factory: Callable[..., Any]
    capabilities: AdapterCapabilities


class AgentAdapterRegistry:
    """Small explicit registry for provider adapters.

    Registration is process-local. It is an integration catalog, not a plugin
    execution mechanism; callers still choose which adapter to instantiate.
    """

    _NAME = re.compile(r"^[a-z][a-z0-9._-]{0,63}$")

    def __init__(self) -> None:
        self._registrations: Dict[str, AdapterRegistration] = {}

    def register(
        self,
        name: str,
        factory: Callable[..., Any],
        capabilities: AdapterCapabilities,
        *,
        replace: bool = False,
    ) -> None:
        clean_name = str(name).strip().lower()
        if not self._NAME.fullmatch(clean_name):
            raise ValueError("adapter name must use lowercase ASCII identifiers")
        if not callable(factory):
            raise ValueError("adapter factory must be callable")
        if clean_name in self._registrations and not replace:
            raise ValueError("adapter already registered: %s" % clean_name)
        if capabilities.name != clean_name:
            raise ValueError("capability name must match adapter name")
        self._registrations[clean_name] = AdapterRegistration(clean_name, factory, capabilities)

    def create(self, name: str, **kwargs: Any) -> Any:
        clean_name = str(name).strip().lower()
        registration = self._registrations.get(clean_name)
        if registration is None:
            raise ValueError("unknown Agent adapter: %s" % clean_name)
        return registration.factory(**kwargs)

    def describe(self) -> list[Dict[str, object]]:
        return [
            registration.capabilities.to_dict()
            for registration in sorted(self._registrations.values(), key=lambda item: item.name)
        ]

    def names(self) -> Tuple[str, ...]:
        return tuple(sorted(self._registrations))


def builtin_agent_adapter_registry() -> AgentAdapterRegistry:
    """Return a fresh registry containing the supported first-party adapters."""

    registry = AgentAdapterRegistry()
    registry.register(
        "generic-json",
        GenericAgentEventAdapter,
        GenericAgentEventAdapter.capabilities,
    )

    # Lazy import keeps the core event contract usable without OpenHands SDK
    # dependencies and avoids importing provider-specific code at startup.
    from memguard.integrations.openhands_adapter import OpenHandsEventAdapter, OPENHANDS_VERSION

    openhands_capabilities = AdapterCapabilities(
        name="openhands",
        version=OPENHANDS_VERSION,
        description="OpenHands SDK event adapter",
        event_types=tuple(sorted(ALLOWED_EVENT_TYPES)),
        supports_checkpoints=True,
    )
    registry.register(
        "openhands",
        OpenHandsEventAdapter,
        openhands_capabilities,
    )
    return registry


def _text(payload: Mapping[str, Any], *names: str) -> str:
    for name in names:
        value = payload.get(name)
        if value is not None and not isinstance(value, (dict, list, tuple)):
            return str(value)
    return ""


def _mapping(value: Any) -> Dict[str, Any]:
    if value is None:
        return {}
    if isinstance(value, Mapping):
        return dict(value)
    if isinstance(value, str):
        return {"raw_arguments": value}
    raise ValueError("tool_args must be an object or string")


def _exit_code(value: Any) -> Optional[int]:
    if value is None or value == "":
        return None
    if isinstance(value, bool):
        raise ValueError("exit_code must be an integer or null")
    try:
        parsed = int(value)
    except (TypeError, ValueError) as exc:
        raise ValueError("exit_code must be an integer or null") from exc
    return parsed


__all__ = [
    "AdapterCapabilities",
    "AdapterRegistration",
    "AgentAdapterRegistry",
    "GenericAgentEventAdapter",
    "builtin_agent_adapter_registry",
]
