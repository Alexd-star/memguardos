from __future__ import annotations

from dataclasses import dataclass, field
from typing import List
import re

from memguard.integrations.events import AgentInteractionEvent


@dataclass(frozen=True)
class PreferenceObservation:
    topic: str
    value: str
    polarity: str
    scope: str
    explicit: bool
    confidence: float
    evidence_event_id: str


@dataclass(frozen=True)
class InteractionExtractionResult:
    remember_allowed: bool
    reason: str = ""
    observations: List[PreferenceObservation] = field(default_factory=list)


def extract_interaction_preferences(event: AgentInteractionEvent) -> InteractionExtractionResult:
    if event.role != "user" or event.event_type not in {"user_message", "user_feedback"}:
        return InteractionExtractionResult(remember_allowed=True)

    text = " ".join(part for part in (event.content, event.user_feedback) if part).strip()
    if _requests_no_memory(text):
        return InteractionExtractionResult(
            remember_allowed=False,
            reason="user_requested_no_memory",
        )

    # 提问不是偏好："默认用 rg 还是 grep 好？"是在征求建议，不是在下达长期习惯。
    if _is_question(text):
        return InteractionExtractionResult(remember_allowed=True, reason="question_not_preference")

    observations: List[PreferenceObservation] = []
    lowered = text.lower()
    # 一次性指令（"这次用 grep"）仍作为隐式证据保留，用于跨会话形成习惯，
    # 但不得作为显式声明单条晋升为稳定偏好。
    one_time = _is_one_time_instruction(text)

    if _has_one_time_tool_exception(text, lowered):
        observations.append(_observation(event, "code_search_tool", "grep", scope="session"))
        observations.append(_observation(event, "code_search_tool", "rg", scope="global"))
    elif _prefers_rg(text, lowered):
        observations.append(
            _observation(
                event,
                "code_search_tool",
                "rg",
                scope=_scope_for_text(text, lowered),
                explicit=_explicit_tool_preference(text, lowered),
            )
        )
    elif _prefers_grep(text, lowered):
        observations.append(
            _observation(
                event,
                "code_search_tool",
                "grep",
                scope=_scope_for_text(text, lowered),
                explicit=_explicit_tool_preference(text, lowered),
            )
        )

    if _prefers_python_pytest(text, lowered):
        observations.append(
            _observation(
                event,
                "test_runner_command",
                "python -m pytest",
                scope=_scope_for_text(text, lowered),
                explicit=_explicit_command_preference(text, lowered),
            )
        )

    service_log_command = _preferred_service_log_command(text)
    if service_log_command:
        observations.append(
            _observation(
                event,
                "service_log_tool",
                service_log_command,
                scope=_scope_for_text(text, lowered),
                explicit=_explicit_command_preference(text, lowered),
            )
        )

    if _command_before_explanation(text, lowered):
        observations.append(
            _observation(
                event,
                "response_order",
                "command_then_explanation",
                scope=_scope_for_text(text, lowered),
            )
        )
    if _prefers_chinese(text, lowered):
        observations.append(
            _observation(
                event,
                "response_language",
                "zh-CN",
                scope=_scope_for_text(text, lowered),
            )
        )
    if _prefers_concise(text, lowered):
        observations.append(
            _observation(
                event,
                "response_verbosity",
                "concise",
                scope=_scope_for_text(text, lowered),
            )
        )
    if _requires_risk_confirmation(text, lowered):
        observations.append(
            _observation(
                event,
                "high_risk_confirmation",
                "required",
                scope=_scope_for_text(text, lowered),
            )
        )

    observations = [
        PreferenceObservation(
            topic=item.topic,
            value=item.value,
            polarity="negative" if _value_is_negated(lowered, item.value) else item.polarity,
            scope=item.scope,
            explicit=item.explicit and not one_time,
            confidence=min(item.confidence, 0.72) if one_time else item.confidence,
            evidence_event_id=item.evidence_event_id,
        )
        for item in observations
    ]
    return InteractionExtractionResult(
        remember_allowed=True,
        observations=_dedupe_observations(observations),
        reason="one_time_instruction_recorded_as_implicit" if one_time and observations else "",
    )


def _observation(
    event: AgentInteractionEvent,
    topic: str,
    value: str,
    scope: str = "global",
    explicit: bool = True,
) -> PreferenceObservation:
    return PreferenceObservation(
        topic=topic,
        value=value,
        polarity="positive",
        scope=scope,
        explicit=explicit,
        confidence=0.98 if explicit else 0.72,
        evidence_event_id=event.event_id,
    )


# 只收明确的疑问标记。像"还是"这类词在"下次还是用 rg"里是延续语气而非提问，
# 误判会把正常偏好整条丢掉。
_QUESTION_MARKERS = ("吗", "呢", "是不是", "要不要", "哪个", "好不好", "该不该")


def _is_question(text: str) -> bool:
    stripped = text.strip()
    if not stripped:
        return False
    lowered = stripped.lower()
    if stripped.endswith(("?", "？")):
        return True
    if any(marker in stripped for marker in _QUESTION_MARKERS):
        # 「以后默认用 rg」不是提问；只有缺少持久化副词时才按提问处理。
        return not _has_persistent_marker(stripped, lowered)
    return lowered.startswith(("should i", "which ", "what ", "how ", "why ", "do you", "can you"))


def _has_persistent_marker(text: str, lowered: str) -> bool:
    markers = ("以后", "默认", "每次", "总是", "一律", "统一", "长期", "今后", "下次", "仍然", "继续", "优先", "首选")
    return any(marker in text for marker in markers) or any(
        marker in lowered for marker in ("always", "by default", "from now on", "prefer")
    )


def _is_one_time_instruction(text: str) -> bool:
    lowered = text.lower()
    if _has_persistent_marker(text, lowered):
        return False
    # 一次性任务的判定以"明确的一次性时间/范围词"为准，不能仅凭"先看/帮我"这类
    # 祈使动词，否则"排查服务日志时先看 journalctl"这类操作习惯会被误拦。
    one_time = ("这次", "本次", "仅此一次", "临时", "现在", "马上", "立刻", "今晚", "今天之内", "just this once", "right now")
    return any(marker in text for marker in one_time)


_NEGATION_MARKERS = ("不要", "别再", "别用", "不用", "不再", "换掉", "停止使用", "don't use", "do not use", "stop using")


def _value_is_negated(lowered: str, value: str) -> bool:
    """Negation applies per value: in「不要用 grep，改用 rg」only grep is negated."""

    value_lower = value.lower()
    position = lowered.find(value_lower)
    while position >= 0:
        window = lowered[max(0, position - 12) : position]
        if any(marker in window for marker in _NEGATION_MARKERS):
            return True
        position = lowered.find(value_lower, position + 1)
    return False


def _requests_no_memory(text: str) -> bool:
    lowered = text.lower()
    return any(
        marker in lowered
        for marker in (
            "不要记住",
            "不要保存这条",
            "别记住",
            "do not remember",
            "don't remember",
            "do not store",
        )
    )


def _has_one_time_tool_exception(text: str, lowered: str) -> bool:
    return (
        any(marker in text for marker in ("这次", "仅本次", "本轮"))
        and _mentions_tool(lowered, "grep")
        and any(marker in text for marker in ("下次", "以后", "仍然", "还是"))
        and _mentions_tool(lowered, "rg")
    )


def _mentions_tool(lowered: str, tool: str) -> bool:
    """Word-boundary match so 'rg' does not fire on 'large', 'target' or 'org'."""

    return bool(re.search(r"(?<![A-Za-z0-9_])%s(?![A-Za-z0-9_])" % re.escape(tool), lowered))


def _prefers_rg(text: str, lowered: str) -> bool:
    return _mentions_tool(lowered, "rg") and any(
        marker in lowered
        for marker in ("优先", "首选", "默认", "prefer", "use rg", "使用 rg", "用 rg")
    )


def _prefers_grep(text: str, lowered: str) -> bool:
    return _mentions_tool(lowered, "grep") and any(
        marker in lowered
        for marker in ("优先", "首选", "默认", "prefer", "use grep", "使用 grep", "用 grep")
    )


def _prefers_python_pytest(text: str, lowered: str) -> bool:
    return bool(re.search(r"\bpython\s+-m\s+pytest\b", lowered)) and (
        any(
            marker in text
            for marker in (
                "以后",
                "优先",
                "默认",
                "不要直接",
                "别直接",
                "请使用",
                "请用",
                "必须",
            )
        )
        or any(
            marker in lowered
            for marker in (
                "prefer",
                "always",
                "by default",
                "use python -m pytest",
                "run tests with python -m pytest",
            )
        )
    )


def _preferred_service_log_command(text: str) -> str:
    match = re.search(
        r"\bjournalctl\s+--user\s+-u\s+([A-Za-z0-9_.@-]+(?:\.service)?)",
        text,
        flags=re.IGNORECASE,
    )
    if not match:
        return ""
    unit = match.group(1).strip("，。；;,")
    if not unit:
        return ""
    return "journalctl --user -u %s" % unit


def _explicit_tool_preference(text: str, lowered: str) -> bool:
    return any(marker in text for marker in ("以后", "默认", "优先", "首选", "每次", "总是", "本轮", "本次会话")) or any(
        marker in lowered for marker in ("prefer", "always", "by default")
    )


def _explicit_command_preference(text: str, lowered: str) -> bool:
    return any(
        marker in text
        for marker in (
            "以后",
            "默认",
            "优先",
            "首选",
            "必须",
            "请使用",
            "请用",
            "先看",
            "先查",
            "继续先",
            "还是用",
        )
    ) or any(marker in lowered for marker in ("prefer", "always", "by default"))


def _scope_for_text(text: str, lowered: str) -> str:
    if any(marker in text for marker in ("本轮", "本次会话", "这个会话")):
        return "session"
    if any(marker in text for marker in ("当前项目", "这个项目", "本项目")):
        return "project"
    if any(marker in text for marker in ("这类任务", "此类任务", "这种任务")):
        return "task_type"
    if any(marker in text for marker in ("这个 Agent", "当前 Agent", "该 Agent")) or "this agent" in lowered:
        return "agent"
    return "global"


def _command_before_explanation(text: str, lowered: str) -> bool:
    command_marker = any(
        marker in text
        for marker in ("先给命令", "命令放前面", "先输出命令", "先命令", "先写命令", "先列命令")
    )
    explanation_marker = any(
        marker in text
        for marker in ("再解释", "然后解释", "后解释", "再说明", "然后说明", "后说明", "再讲原因", "再给原因")
    )
    english = "command first" in lowered or "commands first" in lowered
    return (command_marker and explanation_marker) or english


def _prefers_chinese(text: str, lowered: str) -> bool:
    return any(marker in text for marker in ("用中文回答", "中文回复", "默认中文")) or "answer in chinese" in lowered


def _prefers_concise(text: str, lowered: str) -> bool:
    return any(
        marker in text
        for marker in (
            "回答简洁",
            "尽量简洁",
            "只给关键",
            "简洁列出",
            "简洁回答",
            "不要写长篇",
            "不要长篇",
        )
    ) or "be concise" in lowered


def _requires_risk_confirmation(text: str, lowered: str) -> bool:
    risky = any(
        marker in text
        for marker in (
            "删除",
            "覆盖",
            "高风险",
            "导出密钥",
            "rm",
            "systemctl disable",
            "重启服务",
        )
    )
    confirmation = any(
        marker in text
        for marker in (
            "先确认",
            "询问我",
            "征得同意",
            "必须确认",
            "等待确认",
            "说明风险",
        )
    )
    english = "confirm before" in lowered and any(marker in lowered for marker in ("delete", "overwrite", "destructive"))
    return (risky and confirmation) or english


def _dedupe_observations(observations: List[PreferenceObservation]) -> List[PreferenceObservation]:
    seen = set()
    result = []
    for observation in observations:
        key = (observation.topic, observation.value, observation.scope, observation.polarity)
        if key in seen:
            continue
        seen.add(key)
        result.append(observation)
    return result
