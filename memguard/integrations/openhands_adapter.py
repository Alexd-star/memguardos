from __future__ import annotations

from importlib import import_module, metadata
from typing import Any, Callable, Dict, Optional, Tuple
import json
import sys

from memguard.integrations.events import AgentInteractionEvent
from memguard.models import utc_now


OPENHANDS_VERSION = "1.28.1"


class OpenHandsEventAdapter:
    def __init__(
        self,
        session_id: str,
        agent_version: str = OPENHANDS_VERSION,
        project_root: str = "",
        task_type: str = "",
        on_unknown: Optional[Callable[[Any], None]] = None,
    ):
        self.session_id = session_id
        self.agent_version = agent_version
        self.project_root = project_root
        self.task_type = task_type
        self.on_unknown = on_unknown
        self.sequence = 0
        self.current_turn = 0
        self.tool_turns: Dict[str, str] = {}
        self.internal_feedback = set()

    def register_internal_feedback(self, content: str) -> None:
        normalized = " ".join(str(content).split())
        if normalized:
            self.internal_feedback.add(normalized)

    def convert(self, raw_event: Any) -> Optional[AgentInteractionEvent]:
        event_name = raw_event.__class__.__name__
        if event_name == "MessageEvent":
            return self._message_event(raw_event)
        if event_name == "ActionEvent":
            return self._action_event(raw_event)
        if event_name == "ObservationEvent":
            return self._observation_event(raw_event)
        if event_name in {"AgentErrorEvent", "ConversationErrorEvent"}:
            return self._error_event(raw_event)
        if event_name == "HookExecutionEvent":
            return self._hook_event(raw_event)
        if self.on_unknown:
            self.on_unknown(raw_event)
        return None

    def _message_event(self, raw_event: Any) -> AgentInteractionEvent:
        message = raw_event.llm_message
        role = str(getattr(message, "role", "assistant"))
        source = _source_name(getattr(raw_event, "source", ""))
        content = _content_text(getattr(message, "content", []))
        normalized_content = " ".join(content.split())
        is_registered_internal = normalized_content in self.internal_feedback
        if is_registered_internal:
            self.internal_feedback.discard(normalized_content)
        is_real_user = (
            role == "user"
            and source == "user"
            and not is_registered_internal
            and not _is_openhands_internal_feedback(content)
        )
        if is_real_user:
            self.current_turn += 1
        turn_id = self._turn_id()
        if role == "user" and not is_real_user:
            role = "system"
            event_type = "system_message"
        else:
            event_type = "user_message" if role == "user" else "assistant_message"
        return self._event(
            raw_event,
            event_type=event_type,
            role=role if role in {"user", "assistant", "tool", "system"} else "assistant",
            content=content,
            turn_id=turn_id,
        )

    def _action_event(self, raw_event: Any) -> AgentInteractionEvent:
        tool_call_id = str(getattr(raw_event, "tool_call_id", ""))
        turn_id = self._turn_id()
        if tool_call_id:
            self.tool_turns[tool_call_id] = turn_id
        raw_arguments = getattr(getattr(raw_event, "tool_call", None), "arguments", "{}")
        try:
            tool_args = json.loads(raw_arguments) if isinstance(raw_arguments, str) else dict(raw_arguments or {})
        except (json.JSONDecodeError, TypeError, ValueError):
            tool_args = {"raw_arguments": str(raw_arguments)}
        if tool_call_id:
            tool_args["tool_call_id"] = tool_call_id
        return self._event(
            raw_event,
            event_type="tool_call",
            role="assistant",
            content=_content_text(getattr(raw_event, "thought", [])),
            tool_name=str(getattr(raw_event, "tool_name", "")),
            tool_args=tool_args,
            turn_id=turn_id,
        )

    def _observation_event(self, raw_event: Any) -> AgentInteractionEvent:
        observation = getattr(raw_event, "observation", None)
        tool_call_id = str(getattr(raw_event, "tool_call_id", ""))
        is_error = bool(getattr(observation, "is_error", False))
        observed_exit_code = getattr(observation, "exit_code", None)
        exit_code = int(observed_exit_code) if observed_exit_code is not None else (1 if is_error else 0)
        return self._event(
            raw_event,
            event_type="tool_result",
            role="tool",
            tool_name=str(getattr(raw_event, "tool_name", "")),
            tool_args={"tool_call_id": tool_call_id} if tool_call_id else {},
            tool_result=_content_text(getattr(observation, "content", [])),
            exit_code=exit_code,
            turn_id=self.tool_turns.get(tool_call_id, self._turn_id()),
        )

    def _error_event(self, raw_event: Any) -> AgentInteractionEvent:
        return self._event(
            raw_event,
            event_type="task_error",
            role="assistant",
            content=str(getattr(raw_event, "error", "")),
            tool_name=str(getattr(raw_event, "tool_name", "")),
            exit_code=1,
            turn_id=self._turn_id(),
        )

    def _hook_event(self, raw_event: Any) -> Optional[AgentInteractionEvent]:
        hook_type = str(getattr(raw_event, "hook_event_type", ""))
        if hook_type == "PreToolUse":
            blocked = bool(getattr(raw_event, "blocked", False))
            raw_reason = str(getattr(raw_event, "reason", "") or "")
            raw_error = str(getattr(raw_event, "error", "") or "")
            raw_stdout = str(getattr(raw_event, "stdout", "") or "")
            policy = {}
            for candidate in (raw_stdout, raw_reason, raw_error):
                policy.update(_parse_hook_policy(candidate))
            reason = str(
                policy.get("reason")
                or raw_reason
                or raw_error
                or raw_stdout
                or "OpenHands pre-tool policy decision"
            )
            blocked = bool(blocked or policy.get("decision") == "deny")
            return self._event(
                raw_event,
                event_type="policy_decision",
                role="system",
                content=reason,
                tool_name=str(getattr(raw_event, "tool_name", "") or ""),
                tool_args=policy,
                exit_code=int(getattr(raw_event, "exit_code", 2 if blocked else 0)),
                turn_id=self._turn_id(),
            )
        event_type = {"SessionStart": "session_start", "SessionEnd": "session_end"}.get(hook_type)
        if event_type is None:
            if self.on_unknown:
                self.on_unknown(raw_event)
            return None
        return self._event(
            raw_event,
            event_type=event_type,
            role="system",
            content=str(getattr(raw_event, "additional_context", "") or ""),
            turn_id=self._turn_id(),
        )

    def _event(self, raw_event: Any, **values: Any) -> AgentInteractionEvent:
        self.sequence += 1
        raw_id = str(getattr(raw_event, "id", "event-%s" % self.sequence))
        return AgentInteractionEvent(
            agent_name="openhands",
            agent_version=self.agent_version,
            session_id=self.session_id,
            turn_id=str(values.pop("turn_id")),
            sequence=self.sequence,
            timestamp=str(getattr(raw_event, "timestamp", "") or utc_now()),
            source_uri="openhands://%s/%s" % (self.session_id, raw_id),
            event_id=raw_id,
            project_root=self.project_root,
            task_type=self.task_type,
            **values,
        )

    def _turn_id(self) -> str:
        return "turn-%s" % max(1, self.current_turn)


def _content_text(content: Any) -> str:
    if isinstance(content, str):
        return content
    parts = []
    for item in content or []:
        text = getattr(item, "text", None)
        if text:
            parts.append(str(text))
    return "\n".join(parts)


def _source_name(source: Any) -> str:
    value = getattr(source, "value", source)
    clean = str(value).strip().lower()
    if "." in clean:
        clean = clean.rsplit(".", 1)[-1]
    return clean


def _is_openhands_internal_feedback(content: str) -> bool:
    normalized = " ".join(str(content).split())
    return normalized in {
        "Your last response did not include a function call or a message. "
        "Please use a tool to proceed with the task.",
        "You have 1000 weighted tokens left",
    }


def _parse_hook_policy(value: str) -> Dict[str, Any]:
    text = str(value or "").strip()
    try:
        payload = json.loads(text)
    except (json.JSONDecodeError, TypeError, ValueError):
        return {}
    return payload if isinstance(payload, dict) else {}


def detect_openhands_runtime(
    python_version: Optional[Tuple[int, int, int]] = None,
    version_getter: Optional[Callable[[str], str]] = None,
    module_loader: Optional[Callable[[str], Any]] = None,
) -> Dict[str, object]:
    version_info = python_version or tuple(sys.version_info[:3])
    get_version = version_getter or metadata.version
    load_module = module_loader or import_module
    python_compatible = version_info >= (3, 12, 0)
    base = {
        "required_version": OPENHANDS_VERSION,
        "python_version": ".".join(str(part) for part in version_info),
        "python_compatible": python_compatible,
        "installed": False,
        "available": False,
        "sdk_version": "",
        "tools_version": "",
        "versions_match": False,
        "symbols": {
            "LLM": False,
            "Agent": False,
            "Conversation": False,
            "TerminalTool": False,
        },
    }
    if not python_compatible:
        base["install_hint"] = "OpenHands SDK 1.28.1 requires Python 3.12 or newer."
        return base

    try:
        sdk_version = get_version("openhands-sdk")
        tools_version = get_version("openhands-tools")
    except (metadata.PackageNotFoundError, ModuleNotFoundError, KeyError):
        base["install_hint"] = (
            "Install MemGuardOS with the optional dependency: "
            "pip install 'memguardos[agent-openhands]' or pip install -e '.[agent-openhands]'"
        )
        return base

    base["installed"] = True
    base["sdk_version"] = sdk_version
    base["tools_version"] = tools_version
    base["versions_match"] = sdk_version == tools_version == OPENHANDS_VERSION
    try:
        sdk = load_module("openhands.sdk")
        tools = load_module("openhands.tools")
    except (ImportError, ModuleNotFoundError) as exc:
        base["import_error"] = str(exc)
        base["install_hint"] = "Reinstall matching openhands-sdk and openhands-tools packages."
        return base

    symbols = {
        "LLM": hasattr(sdk, "LLM"),
        "Agent": hasattr(sdk, "Agent"),
        "Conversation": hasattr(sdk, "Conversation"),
        "TerminalTool": hasattr(tools, "TerminalTool"),
    }
    base["symbols"] = symbols
    base["available"] = bool(base["versions_match"] and all(symbols.values()))
    if base["available"]:
        base["install_hint"] = ""
    elif not base["versions_match"]:
        base["install_hint"] = "Install matching openhands-sdk==1.28.1 and openhands-tools==1.28.1."
    else:
        base["install_hint"] = "Installed OpenHands packages do not expose the required SDK symbols."
    return base
