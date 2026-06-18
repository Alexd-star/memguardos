from __future__ import annotations

from dataclasses import dataclass, field
from typing import List

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

    observations: List[PreferenceObservation] = []
    lowered = text.lower()

    if _has_one_time_tool_exception(text, lowered):
        observations.append(_observation(event, "code_search_tool", "grep", scope="session"))
        observations.append(_observation(event, "code_search_tool", "rg", scope="global"))
    elif _prefers_rg(text, lowered):
        observations.append(_observation(event, "code_search_tool", "rg"))
    elif _prefers_grep(text, lowered):
        observations.append(_observation(event, "code_search_tool", "grep"))

    if _command_before_explanation(text, lowered):
        observations.append(_observation(event, "response_order", "command_then_explanation"))
    if _prefers_chinese(text, lowered):
        observations.append(_observation(event, "response_language", "zh-CN"))
    if _prefers_concise(text, lowered):
        observations.append(_observation(event, "response_verbosity", "concise"))
    if _requires_risk_confirmation(text, lowered):
        observations.append(_observation(event, "high_risk_confirmation", "required"))

    return InteractionExtractionResult(
        remember_allowed=True,
        observations=_dedupe_observations(observations),
    )


def _observation(
    event: AgentInteractionEvent,
    topic: str,
    value: str,
    scope: str = "global",
) -> PreferenceObservation:
    return PreferenceObservation(
        topic=topic,
        value=value,
        polarity="positive",
        scope=scope,
        explicit=True,
        confidence=0.98,
        evidence_event_id=event.event_id,
    )


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
        and "grep" in lowered
        and any(marker in text for marker in ("下次", "以后", "仍然", "还是"))
        and "rg" in lowered
    )


def _prefers_rg(text: str, lowered: str) -> bool:
    return "rg" in lowered and any(
        marker in lowered
        for marker in ("优先", "首选", "默认", "prefer", "use rg", "使用 rg", "用 rg")
    )


def _prefers_grep(text: str, lowered: str) -> bool:
    return "grep" in lowered and any(
        marker in lowered
        for marker in ("优先", "首选", "默认", "prefer", "use grep", "使用 grep", "用 grep")
    )


def _command_before_explanation(text: str, lowered: str) -> bool:
    command_marker = any(marker in text for marker in ("先给命令", "命令放前面", "先输出命令"))
    explanation_marker = any(marker in text for marker in ("再解释", "然后解释", "后解释"))
    english = "command first" in lowered or "commands first" in lowered
    return (command_marker and explanation_marker) or english


def _prefers_chinese(text: str, lowered: str) -> bool:
    return any(marker in text for marker in ("用中文回答", "中文回复", "默认中文")) or "answer in chinese" in lowered


def _prefers_concise(text: str, lowered: str) -> bool:
    return any(marker in text for marker in ("回答简洁", "尽量简洁", "只给关键")) or "be concise" in lowered


def _requires_risk_confirmation(text: str, lowered: str) -> bool:
    risky = any(marker in text for marker in ("删除", "覆盖", "高风险", "导出密钥"))
    confirmation = any(marker in text for marker in ("先确认", "询问我", "征得同意", "必须确认"))
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
