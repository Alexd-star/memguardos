"""Shared natural-language decisions for every product entry point.

This module deliberately decides *intent and policy*, not final memory content.
Sensitive detection remains local and mandatory, while semantic extraction may
use deterministic rules plus an optional schema-validated enhancer.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass
import re


@dataclass(frozen=True)
class NaturalActionDecision:
    action: str
    payload: str = ""
    confidence: float = 1.0
    requires_confirmation: bool = False
    reason: str = ""

    def to_dict(self) -> dict[str, object]:
        return asdict(self)


def interpret_user_input(text: str) -> NaturalActionDecision:
    clean = " ".join(str(text or "").strip().split())
    compact = clean.replace(" ", "")
    if not clean:
        return NaturalActionDecision("empty", confidence=1.0, reason="empty_input")
    if looks_like_forget_request(clean):
        return NaturalActionDecision(
            "forget",
            clean,
            confidence=0.98,
            requires_confirmation=True,
            reason="explicit_memory_erasure_request",
        )
    if compact in {
        "查看记忆",
        "看看记忆",
        "我记住了什么",
        "你记得什么",
        "有哪些记忆",
        "显示最近记忆",
    } or re.fullmatch(r"(?:查看|显示|列出|列举|看看)(?:我的|当前)?(?:全部)?记忆(?:列表|内容)?", compact):
        return NaturalActionDecision("memory_list", reason="explicit_memory_list_request")
    if compact in {
        "查看偏好",
        "我的偏好",
        "我的偏好有哪些",
        "你记住了哪些偏好",
        "显示当前偏好",
    } or re.fullmatch(r"(?:查看|显示|列出|列举|看看)(?:我的|当前)?(?:全部)?偏好(?:设置|列表|内容)?", compact):
        return NaturalActionDecision("preference_list", reason="explicit_preference_list_request")
    if compact in {
        "查看事实冲突",
        "查看待核对事实",
        "待核对事实",
        "有哪些事实冲突",
        "有哪些待核对内容",
        "显示待核对内容",
    } or re.fullmatch(r"(?:查看|显示|列出|列举|看看)(?:当前)?(?:事实冲突|冲突事实|待核对事实|事实核对)(?:列表|内容)?", compact):
        return NaturalActionDecision("fact_conflict_list", reason="explicit_fact_conflict_request")
    if compact in {"查看状态", "系统状态", "记忆状态", "服务正常吗"} or re.fullmatch(
        r"(?:查看|显示|检查)(?:当前)?(?:系统|记忆|服务)?(?:运行|健康)?状态", compact
    ):
        return NaturalActionDecision("status", reason="explicit_status_request")
    imperative = _strip_polite_prefixes(clean)
    search_match = re.match(
        r"^(?:搜索|查找|检索|查询|查)(?:一下)?(?:关于)?(.+?)(?:的)?记忆$",
        imperative,
    )
    if search_match:
        return NaturalActionDecision(
            "search",
            search_match.group(1).strip(" ：:"),
            reason="explicit_memory_search_request",
        )
    search_prefix = re.match(
        r"^(?:搜索记忆|查找记忆|检索记忆)[：:\s]+(.+)$",
        imperative,
    )
    if search_prefix:
        return NaturalActionDecision(
            "search",
            search_prefix.group(1).strip(),
            reason="explicit_memory_search_request",
        )
    search_description = re.match(
        r"^(?:搜索|查找|检索|查询|查)(?:一下)?(?:关于)?(.+?)(?:相关)?(?:信息|内容)$",
        imperative,
    )
    if search_description:
        return NaturalActionDecision(
            "search",
            search_description.group(1).strip(" ：:"),
            reason="explicit_memory_search_request",
        )
    run_match = re.match(
        r"^(?:运行|执行)(?:一下|一个)?(?:任务)?[：:\s]+(.+)$",
        imperative,
    )
    if run_match and run_match.group(1).strip():
        return NaturalActionDecision(
            "run",
            run_match.group(1).strip(),
            reason="explicit_agent_task_request",
        )
    for prefix in ("让 Agent：", "让Agent：", "让智能体：", "让智能体:"):
        if imperative.startswith(prefix) and imperative[len(prefix):].strip():
            return NaturalActionDecision(
                "run",
                imperative[len(prefix):].strip(),
                reason="explicit_agent_task_request",
            )
    if looks_like_question(clean):
        return NaturalActionDecision(
            "ambiguous",
            clean,
            confidence=0.0,
            requires_confirmation=True,
            reason="question_is_not_a_memory_write_request",
        )
    remember_prefixes = (
        "记住",
        "请记住",
        "帮我记住",
        "请记下",
        "帮我记下",
        "记录一下",
        "请记录",
        "帮我记录",
        "保存一下",
        "请保存",
        "帮我保存",
        "存一下",
        "加入记忆",
        "保存为记忆",
        "添加记忆",
        "知识记忆：",
        "偏好记忆：",
        "流程记忆：",
        "操作经验：",
        "纠正一下",
        "更新偏好",
        "以后",
        "今后",
        "默认",
    )
    if imperative.startswith(remember_prefixes) or clean.endswith(("记下来", "保存为记忆", "加入记忆")):
        return NaturalActionDecision("remember", clean, reason="explicit_memory_write_request")
    return NaturalActionDecision(
        "ambiguous",
        clean,
        confidence=0.0,
        requires_confirmation=True,
        reason="task_or_memory_intent_is_ambiguous",
    )


def looks_like_forget_request(text: str) -> bool:
    normalized = " ".join(str(text or "").strip().split())
    if not normalized:
        return False
    imperative = _strip_polite_prefixes(normalized)
    for prefix in ("我想", "我希望", "我要"):
        if imperative.startswith(prefix):
            imperative = imperative[len(prefix):].lstrip()
            break
    if imperative.startswith(("忘掉", "忘记", "遗忘")):
        # “我忘记了 grep 的参数” describes the user's state; it is not an
        # erasure command and must remain an ordinary task or memory candidate.
        return not imperative.startswith(("忘记了", "忘了"))
    if imperative.startswith(("删除", "清除")):
        if any(marker in imperative for marker in ("怎么", "如何", "为什么", "实现", "代码", "函数", "逻辑")):
            return False
        return any(marker in imperative for marker in ("记忆", "偏好", "习惯", "历史记录"))
    if re.search(r"(?:记忆|偏好|习惯|历史记录).*(?:删掉|删除|清除|忘掉)$", imperative):
        return not any(marker in imperative for marker in ("怎么", "如何", "为什么", "实现"))
    return False


def looks_like_question(text: str) -> bool:
    stripped = str(text or "").strip()
    if not stripped:
        return False
    lowered = stripped.lower()
    if stripped.endswith(("?", "？")):
        return True
    if any(
        marker in stripped
        for marker in ("吗", "呢", "是不是", "要不要", "哪个", "好不好", "该不该")
    ):
        return True
    return lowered.startswith(
        ("should i", "which ", "what ", "how ", "why ", "do you", "can you")
    )


def _strip_polite_prefixes(text: str) -> str:
    clean = str(text or "").strip()
    prefixes = (
        "麻烦你帮我",
        "麻烦帮我",
        "可以帮我",
        "能不能帮我",
        "请你帮我",
        "请帮我",
        "麻烦你",
        "麻烦",
        "请你",
        "帮我",
        "请",
    )
    changed = True
    while changed:
        changed = False
        for prefix in prefixes:
            if clean.startswith(prefix):
                clean = clean[len(prefix):].lstrip(" ，,:：")
                changed = True
                break
    if clean.startswith(("把", "将")):
        clean = clean[1:].lstrip()
    return clean


def infer_retention_tier(text: str) -> str:
    compact = re.sub(r"\s+", "", str(text or "")).lower()
    if (
        "临时" in compact
        and any(marker in compact for marker in ("本次会话", "当前会话", "这次会话", "这次对话", "本轮"))
    ) or any(
        marker in compact
        for marker in (
            "只保留到本次演示结束",
            "保留到本次演示结束",
            "本次演示结束后删除",
            "这次演示结束后删除",
            "演示结束后删除",
            "演示完就删除",
            "演示完就删",
            "只在本次会话",
            "只在这次会话",
            "只在这次对话",
            "仅在本次会话",
            "本次会话结束后删除",
            "当前会话结束后删除",
            "只保留在当前会话",
            "本轮结束后删除",
            "会话结束就删除",
            "会话结束就删",
            "不用长期保存",
            "临时记一下",
            "临时记住",
            "这次先记住",
        )
    ):
        return "session"
    if any(
        marker in compact
        for marker in (
            "只在当前任务",
            "仅供当前任务",
            "本次任务结束后删除",
            "当前任务结束后删除",
            "临时工作记忆",
        )
    ):
        return "working"
    if any(
        marker in compact
        for marker in (
            "长期保存",
            "保存为长期记忆",
            "长期记住",
            "以后都记住",
            "永久保存",
            "一直保留",
        )
    ):
        return "long_term"
    return ""


def explicitly_requests_no_store(text: str) -> bool:
    compact = re.sub(r"\s+", "", str(text or "")).lower()
    return compact.startswith(("不要保存：", "别保存：", "不用记住：", "不要记住：")) or any(
        marker in compact
        for marker in (
            "不要记住这条",
            "别记住这条",
            "不要保存这条",
            "这条不要保存",
            "本次不要保存",
            "不要记住以下内容",
            "不要保存以下内容",
            "不要把这条内容记住",
            "不要把本次内容保存",
        )
    )
