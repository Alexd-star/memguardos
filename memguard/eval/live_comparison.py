"""Live "with memory vs without memory" comparison on the user's real namespace.

Why this exists: the older ``run_feedback_replay`` always emitted the same
hardcoded events, so it could never show a genuine difference. This module runs
the same task twice against a deterministic policy whose behaviour is a real
function of the injected context, so any difference between the two runs is
caused by the memories that actually exist in the namespace.

Honesty contract, mirrored in the payload:

* ``measured`` values are computed from the running system (which memories were
  injected, how many context tokens they cost, which preferences applied, what
  the naive baseline would have done).
* ``modelled`` values are consequences under an explicitly stated assumption
  (a user who does not get their preference honoured spends one extra turn
  restating it). They are labelled as such and never presented as observations.
* Nothing here claims a language model executed anything. A real model-driven
  comparison is a separate, clearly labelled path.
"""

from __future__ import annotations

from typing import Dict, List, Tuple

STAGES = ("planning", "tool_selection", "response_generation")

# 无记忆基线：一个没有任何用户上下文的 Agent 会采用的通用默认动作。
NAIVE_DEFAULTS: Dict[str, str] = {
    "code_search_tool": "grep",
    "test_runner_command": "pytest",
    "service_log_tool": "journalctl -xe",
    "response_order": "explanation_then_command",
    "response_language": "auto",
    "response_verbosity": "verbose",
    "high_risk_confirmation": "not_required",
}

TOPIC_LABELS: Dict[str, str] = {
    "code_search_tool": "代码搜索工具",
    "test_runner_command": "测试运行命令",
    "service_log_tool": "服务日志命令",
    "response_order": "回答顺序",
    "response_language": "回答语言",
    "response_verbosity": "回答详细度",
    "high_risk_confirmation": "高风险确认",
}

VALUE_LABELS: Dict[str, str] = {
    "command_then_explanation": "先给命令再解释",
    "explanation_then_command": "先解释再给命令",
    "concise": "简洁",
    "verbose": "详细",
    "required": "必须先确认",
    "not_required": "不额外确认",
    "zh-CN": "中文",
    "auto": "未指定",
}

# 任务关键词 → 该任务实际会用到的偏好维度。
TASK_TOPIC_HINTS: Tuple[Tuple[Tuple[str, ...], str], ...] = (
    (("搜索", "查找", "定位", "grep", "search", "find", "代码"), "code_search_tool"),
    (("测试", "test", "pytest", "用例"), "test_runner_command"),
    (("日志", "log", "journal", "服务状态"), "service_log_tool"),
    (("删除", "清理", "重启", "卸载", "rm ", "drop"), "high_risk_confirmation"),
)

ALWAYS_RELEVANT_TOPICS = ("response_order", "response_language", "response_verbosity")


def label_topic(topic: str) -> str:
    return TOPIC_LABELS.get(topic, topic or "其他偏好")


def label_value(value: str) -> str:
    return VALUE_LABELS.get(value, value or "未指定")


def relevant_topics(task: str) -> List[str]:
    """Which preference dimensions this task can actually exercise."""

    lowered = (task or "").lower()
    topics: List[str] = []
    for keywords, topic in TASK_TOPIC_HINTS:
        if any(keyword.lower() in lowered for keyword in keywords):
            topics.append(topic)
    topics.extend(ALWAYS_RELEVANT_TOPICS)
    seen: set = set()
    ordered: List[str] = []
    for topic in topics:
        if topic not in seen:
            seen.add(topic)
            ordered.append(topic)
    return ordered


def _command_for(topic: str, value: str, task: str) -> str:
    target = _search_target(task)
    if topic == "code_search_tool":
        return "%s -n \"%s\" ." % (value, target) if value != "grep" else 'grep -Rn "%s" .' % target
    if topic == "test_runner_command":
        return value
    if topic == "service_log_tool":
        return value
    return ""


def _search_target(task: str) -> str:
    cleaned = (task or "").strip()
    for marker in ("查找", "搜索", "定位", "的定义", "在哪里", "实现位置"):
        cleaned = cleaned.replace(marker, " ")
    tokens = [token for token in cleaned.split() if token]
    for token in tokens:
        if any(char.isalpha() for char in token) and all(ord(char) < 128 for char in token):
            return token
    return tokens[0] if tokens else "keyword"


def build_comparison(
    *,
    task: str,
    contexts: Dict[str, Dict[str, object]],
    selected_preferences: List[Dict[str, object]],
    full_history_tokens: int,
) -> Dict[str, object]:
    """Compose the with/without comparison from already-collected real data.

    ``contexts`` are real ``build_agent_context`` results, ``selected_preferences``
    is the real preference selection, and ``full_history_tokens`` is the measured
    cost of the naive "paste everything" alternative.
    """

    task_topics = relevant_topics(task)
    applied: Dict[str, Dict[str, object]] = {}
    for preference in selected_preferences:
        topic = str(preference.get("topic") or preference.get("preference_topic") or "")
        if topic in task_topics:
            applied[topic] = {
                "topic": topic,
                "value": str(preference.get("value") or preference.get("preference_value") or ""),
                "scope": str(preference.get("preference_scope") or preference.get("scope") or "global"),
                "memory_id": str(preference.get("memory_id") or preference.get("promoted_memory_id") or ""),
            }

    injected_memories: List[Dict[str, object]] = []
    seen_ids: set = set()
    for stage in STAGES:
        for memory in (contexts.get(stage) or {}).get("memories", []) or []:
            memory_id = str(memory.get("memory_id") or "")
            if not memory_id or memory_id in seen_ids:
                continue
            seen_ids.add(memory_id)
            injected_memories.append(
                {
                    "memory_id": memory_id,
                    "stage": stage,
                    "topic": str(memory.get("preference_topic") or ""),
                    "value": str(memory.get("preference_value") or ""),
                    "type": str(memory.get("type") or ""),
                    "selection_reason": str(memory.get("selection_reason") or ""),
                    "estimated_tokens": int(memory.get("estimated_tokens") or 0),
                }
            )

    # 每轮注入成本取"单个阶段的最大值"：一次调用只使用一个阶段的上下文，
    # 把三个阶段相加会重复计算同一条偏好，与"一次性携带全部历史"不可比。
    stage_tokens = {
        stage: int(((contexts.get(stage) or {}).get("budget") or {}).get("estimated_context_tokens") or 0)
        for stage in STAGES
    }
    memguard_tokens = max(stage_tokens.values()) if stage_tokens else 0

    differences: List[Dict[str, object]] = []
    for topic in task_topics:
        naive_value = NAIVE_DEFAULTS.get(topic, "")
        record = applied.get(topic)
        memory_value = record["value"] if record else naive_value
        changed = bool(record) and memory_value != naive_value
        differences.append(
            {
                "topic": topic,
                "topic_label": label_topic(topic),
                "without_memory": naive_value,
                "without_memory_label": label_value(naive_value),
                "with_memory": memory_value,
                "with_memory_label": label_value(memory_value),
                "changed": changed,
                "memory_id": record["memory_id"] if record else "",
                "scope": record["scope"] if record else "",
                "naive_command": _command_for(topic, naive_value, task),
                "memory_command": _command_for(topic, memory_value, task),
            }
        )

    changed_count = sum(1 for item in differences if item["changed"])
    token_saving = full_history_tokens - memguard_tokens

    measured = {
        "task": task,
        "injected_memory_count": len(injected_memories),
        "injected_memories": injected_memories,
        "applied_preference_count": changed_count,
        "differences": differences,
        "memguard_context_tokens": memguard_tokens,
        "stage_context_tokens": stage_tokens,
        "full_history_tokens": full_history_tokens,
        "token_saving_vs_full_history": token_saving,
        "token_reduction_ratio": (
            round(token_saving / full_history_tokens, 4) if full_history_tokens > 0 else None
        ),
        "context_cost_note": _context_cost_note(memguard_tokens, full_history_tokens),
    }
    modelled = {
        "assumption": (
            "若某项偏好未被自动提供，用户需要额外一轮对话重新说明该偏好；"
            "每项未生效偏好计一次纠正轮次。"
        ),
        "extra_turns_without_memory": changed_count,
        "corrections_avoided": changed_count,
    }
    return {
        "schema": "memguardos-live-comparison-v1",
        "measured": measured,
        "modelled": modelled,
        "verdict": _verdict(changed_count, len(injected_memories), token_saving),
        "truthful_note": (
            "本对比在当前记忆空间上真实构建了两次上下文：无记忆基线不注入任何记忆，"
            "带记忆组注入真实命中的记忆。差异由实际存在的记忆决定。"
            "measured 为系统实测值；modelled 为在上述假设下的推论，不是模型执行观测。"
        ),
    }


def _context_cost_note(memguard_tokens: int, full_history_tokens: int) -> str:
    """State the token comparison honestly, including when memory costs more."""

    if full_history_tokens <= 0:
        return "当前还没有可比较的历史交互，上下文成本对比暂不适用。"
    if memguard_tokens < full_history_tokens:
        return (
            "本轮注入 %d tokens，低于携带全部历史所需的 %d tokens；"
            "历史越长，这一差距越大。" % (memguard_tokens, full_history_tokens)
        )
    return (
        "当前历史较短（%d tokens），受管注入（%d tokens）尚未体现成本优势："
        "记忆的价值此时体现在偏好自动生效，而非省 token。"
        "随着交互历史增长，携带全部历史的成本会持续上升，而注入成本受 token 预算约束保持稳定。"
        % (full_history_tokens, memguard_tokens)
    )


def _verdict(changed_count: int, injected_count: int, token_saving: int) -> Dict[str, object]:
    if changed_count > 0:
        headline = "记忆改变了本次任务的执行方式"
        detail = "有 %d 项偏好被自动应用，Agent 无需再次被告知。" % changed_count
        state = "ok"
    elif injected_count > 0:
        headline = "记忆已提供背景，但本次任务未改变默认动作"
        detail = "注入了 %d 条相关记忆，其取值与默认动作一致。" % injected_count
        state = "info"
    else:
        headline = "当前没有与该任务相关的记忆"
        detail = "先通过 Agent 交互或手动添加形成偏好，再运行对比会看到差异。"
        state = "warn"
    return {
        "headline": headline,
        "detail": detail,
        "state": state,
        "token_saving": token_saving,
    }
