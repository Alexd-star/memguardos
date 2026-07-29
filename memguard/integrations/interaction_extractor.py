from __future__ import annotations

from dataclasses import dataclass, field
from typing import List
import re

from memguard.integrations.events import AgentInteractionEvent
from memguard.natural_language import looks_like_question


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

    one_time_tool_exception = _has_one_time_tool_exception(text, lowered)
    if one_time_tool_exception:
        observations.append(_observation(event, "code_search_tool", "grep", scope="session"))
        observations.append(_observation(event, "code_search_tool", "rg", scope="global"))
    else:
        # A correction may mention both values ("不要再用 grep，改用 rg").
        # Keep the positive and negative observations separate so the service can
        # retire only the old value and activate the replacement.
        if _prefers_rg(text, lowered):
            observations.append(
                _observation(
                    event,
                    "code_search_tool",
                    "rg",
                    scope=_scope_for_text(text, lowered),
                    explicit=_explicit_tool_preference(text, lowered),
                )
            )
        if _prefers_grep(text, lowered):
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
                polarity=(
                    "negative"
                    if _preference_is_negated(
                        text,
                        ("先给", "先输出", "先列", "命令放前面", "步骤放前面", "结论放前面"),
                    )
                    else "positive"
                ),
            )
        )
    if _prefers_chinese(text, lowered):
        observations.append(
            _observation(
                event,
                "response_language",
                "zh-CN",
                scope=_scope_for_text(text, lowered),
                polarity="negative" if _preference_is_negated(text, ("中文",)) else "positive",
            )
        )
    if _prefers_concise(text, lowered):
        observations.append(
            _observation(
                event,
                "response_verbosity",
                "concise",
                scope=_scope_for_text(text, lowered),
                polarity=(
                    "negative"
                    if _preference_is_negated(text, ("简洁", "简短", "精简"))
                    else "positive"
                ),
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
            explicit=item.explicit if item.scope == "session" else item.explicit and not one_time,
            confidence=(
                item.confidence
                if item.scope == "session"
                else (min(item.confidence, 0.72) if one_time else item.confidence)
            ),
            evidence_event_id=item.evidence_event_id,
        )
        for item in observations
    ]
    if one_time and not one_time_tool_exception:
        # A one-time instruction must apply immediately in its session, while
        # repeated choices across sessions are also useful implicit evidence of
        # a possible long-term habit. Keep those two meanings as separate rows.
        observations.extend(
            PreferenceObservation(
                topic=item.topic,
                value=item.value,
                polarity=item.polarity,
                scope="global",
                explicit=False,
                confidence=min(item.confidence, 0.72),
                evidence_event_id=item.evidence_event_id,
            )
            for item in list(observations)
            if item.scope == "session" and item.topic in {
                "code_search_tool",
                "test_runner_command",
                "service_log_tool",
            }
        )
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
    polarity: str = "positive",
) -> PreferenceObservation:
    return PreferenceObservation(
        topic=topic,
        value=value,
        polarity=polarity,
        scope=scope,
        explicit=explicit,
        confidence=0.98 if explicit else 0.72,
        evidence_event_id=event.event_id,
    )


def _is_question(text: str) -> bool:
    return looks_like_question(text)


def _is_one_time_instruction(text: str) -> bool:
    # 一次性任务的判定以"明确的一次性时间/范围词"为准，不能仅凭"先看/帮我"这类
    # 祈使动词，否则"排查服务日志时先看 journalctl"这类操作习惯会被误拦。
    one_time = ("这次", "本次", "仅此一次", "临时", "现在", "马上", "立刻", "今晚", "今天之内", "just this once", "right now")
    if any(marker in text for marker in one_time):
        return True
    return False


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
        for marker in (
            "优先",
            "首选",
            "默认",
            "改用",
            "换成",
            "继续用",
            "还是用",
            "记住",
            "搜索用",
            "查代码用",
            "prefer",
            "use rg",
            "使用 rg",
            "用 rg",
        )
    )


def _prefers_grep(text: str, lowered: str) -> bool:
    return _mentions_tool(lowered, "grep") and any(
        marker in lowered
        for marker in (
            "优先",
            "首选",
            "默认",
            "改用",
            "换成",
            "继续用",
            "还是用",
            "记住",
            "搜索用",
            "查代码用",
            "prefer",
            "use grep",
            "使用 grep",
            "用 grep",
        )
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
    return any(
        marker in text
        for marker in (
            "以后",
            "今后",
            "默认",
            "优先",
            "首选",
            "每次",
            "总是",
            "统一",
            "一律",
            "改用",
            "换成",
            "继续用",
            "不要再用",
            "别再用",
            "不再用",
            "停止使用",
            "记住",
            "本轮",
            "本次",
            "这次",
            "临时",
            "本次会话",
        )
    ) or any(
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
    if any(marker in text for marker in ("本轮", "本次", "这次", "临时", "本次会话", "当前会话", "这个会话")):
        return "session"
    if any(marker in text for marker in ("当前项目", "这个项目", "本项目")):
        return "project"
    if any(marker in text for marker in ("这类任务", "此类任务", "这种任务")):
        return "task_type"
    if any(marker in text for marker in ("这个 Agent", "当前 Agent", "该 Agent")) or "this agent" in lowered:
        return "agent"
    return "global"


def _command_before_explanation(text: str, lowered: str) -> bool:
    compact = re.sub(r"\s+", "", text)
    command_marker = bool(
        re.search(
            r"(?:先(?:给|输出|写|列出?|告诉我|展示)[^，。；;]{0,12}"
            r"(?:可复制|可执行)?(?:命令|步骤|操作方法|怎么做|结论)|"
            r"(?:命令|步骤|操作方法|结论)(?:放|写|列)[^，。；;]{0,6}前面)",
            compact,
        )
    )
    explanation_marker = bool(
        re.search(
            r"(?:再|然后|随后|最后)[^，。；;]{0,8}"
            r"(?:解释|说明|分析|讲|给出)(?:原因|理由|结果|细节|依据|含义)?",
            compact,
        )
    )
    english = "command first" in lowered or "commands first" in lowered
    return (command_marker and explanation_marker) or english


def _prefers_chinese(text: str, lowered: str) -> bool:
    return any(
        marker in text
        for marker in (
            "用中文回答",
            "使用中文回答",
            "用中文",
            "使用中文",
            "中文回复",
            "用中文回复",
            "默认中文",
            "回答用中文",
            "请说中文",
        )
    ) or "answer in chinese" in lowered


def _prefers_concise(text: str, lowered: str) -> bool:
    return any(
        marker in text
        for marker in (
            "回答简洁",
            "尽量简洁",
            "简短回答",
            "回答简短",
            "精简回答",
            "只给关键",
            "简洁列出",
            "简洁回答",
            "不要写长篇",
            "不要长篇",
        )
    ) or "be concise" in lowered


def _preference_is_negated(text: str, anchors: tuple[str, ...]) -> bool:
    """Detect a local withdrawal without treating unrelated negation as global.

    Examples: ``以后不要用中文回答`` and ``不必先给命令再解释``.  The
    bounded window avoids letting a later phrase such as ``不要删除文件`` negate
    an earlier response-style preference in the same message.
    """

    compact = re.sub(r"\s+", "", text)
    for anchor in anchors:
        start = compact.find(anchor)
        while start >= 0:
            window = compact[max(0, start - 8) : start]
            if any(
                marker in window
                for marker in ("不要", "别再", "别", "不用", "不必", "无需", "取消", "不再")
            ):
                return True
            start = compact.find(anchor, start + 1)
    return False


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
