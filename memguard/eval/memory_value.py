from __future__ import annotations

from collections import Counter
from statistics import mean
from typing import Dict, Iterable, List, Optional


GROUP_NAMES = ("no_memory", "full_history", "memguard")


def normalize_command(command: str) -> str:
    return " ".join(str(command or "").split())


def summarize_group(group: Dict[str, object]) -> Dict[str, object]:
    raw_calls = list(group.get("tool_calls") or [])
    normalized_commands: List[str] = []
    failed_command_count = 0
    blocked_high_risk_count = 0
    for raw_call in raw_calls:
        call = dict(raw_call) if isinstance(raw_call, dict) else {"command": str(raw_call)}
        command = normalize_command(str(call.get("command") or ""))
        if command:
            normalized_commands.append(command)
        blocked = bool(call.get("blocked_by_policy"))
        if blocked:
            blocked_high_risk_count += 1
        elif _as_int(call.get("exit_code"), 0) != 0:
            failed_command_count += 1

    counts = Counter(normalized_commands)
    repeated_command_count = sum(max(0, count - 1) for count in counts.values())
    expected_tool = str(group.get("expected_tool") or "").strip()
    first_command = normalized_commands[0] if normalized_commands else ""
    first_tool = first_command.split(" ", 1)[0] if first_command else ""
    return {
        "task_completed": bool(group.get("task_completed")),
        "first_tool_correct": bool(expected_tool and first_tool == expected_tool),
        "interaction_turns": max(0, _as_int(group.get("interaction_turns"), 0)),
        "tool_call_count": len(raw_calls),
        "failed_command_count": failed_command_count,
        "repeated_command_count": repeated_command_count,
        "blocked_high_risk_count": blocked_high_risk_count,
        "prompt_tokens": max(0, _as_int(group.get("prompt_tokens"), 0)),
    }


def evaluate_paired_case(case: Dict[str, object]) -> Dict[str, object]:
    raw_groups = dict(case.get("groups") or {})
    groups = {
        name: summarize_group(dict(raw_groups.get(name) or {}))
        for name in GROUP_NAMES
    }
    no_memory = groups["no_memory"]
    full_history = groups["full_history"]
    memguard = groups["memguard"]
    full_history_tokens = int(full_history["prompt_tokens"])
    token_delta = int(memguard["prompt_tokens"]) - full_history_tokens
    token_reduction_ratio: Optional[float] = None
    if full_history_tokens > 0:
        token_reduction_ratio = round(
            (full_history_tokens - int(memguard["prompt_tokens"])) / full_history_tokens,
            6,
        )
    turn_delta = int(memguard["interaction_turns"]) - int(no_memory["interaction_turns"])
    return {
        "case_id": str(case.get("case_id") or ""),
        "category": str(case.get("category") or ""),
        "title": str(case.get("title") or ""),
        "groups": groups,
        "deltas": {
            "completion_vs_no_memory": int(bool(memguard["task_completed"]))
            - int(bool(no_memory["task_completed"])),
            "tool_accuracy_vs_no_memory": int(bool(memguard["first_tool_correct"]))
            - int(bool(no_memory["first_tool_correct"])),
            "interaction_turns_vs_no_memory": turn_delta,
            "interaction_efficiency_improved": bool(
                memguard["task_completed"]
                and turn_delta < 0
            ),
            "prompt_tokens_vs_full_history": token_delta,
            "prompt_token_reduction_ratio": token_reduction_ratio,
            "failed_commands_vs_no_memory": int(memguard["failed_command_count"])
            - int(no_memory["failed_command_count"]),
            "repeated_commands_vs_no_memory": int(memguard["repeated_command_count"])
            - int(no_memory["repeated_command_count"]),
        },
    }


def aggregate_paired_results(
    results: Iterable[Dict[str, object]],
) -> Dict[str, object]:
    cases = list(results)
    groups: Dict[str, Dict[str, object]] = {}
    for name in GROUP_NAMES:
        summaries = [dict(case["groups"][name]) for case in cases]
        groups[name] = {
            "task_completion_rate": _bool_rate(summaries, "task_completed"),
            "first_tool_accuracy": _eligible_bool_rate(
                summaries, "first_tool_correct", "tool_call_count"
            ),
            "mean_interaction_turns": _mean_metric(summaries, "interaction_turns"),
            "mean_prompt_tokens": _mean_metric(summaries, "prompt_tokens"),
            "mean_failed_commands": _mean_metric(summaries, "failed_command_count"),
            "mean_repeated_commands": _mean_metric(
                summaries, "repeated_command_count"
            ),
        }
    delta_keys = (
        "completion_vs_no_memory",
        "tool_accuracy_vs_no_memory",
        "interaction_turns_vs_no_memory",
        "prompt_tokens_vs_full_history",
        "prompt_token_reduction_ratio",
        "failed_commands_vs_no_memory",
        "repeated_commands_vs_no_memory",
    )
    mean_deltas = {}
    for key in delta_keys:
        values = [
            case["deltas"][key]
            for case in cases
            if case["deltas"].get(key) is not None
        ]
        mean_deltas[key] = round(mean(values), 6) if values else None
    return {
        "case_count": len(cases),
        "groups": groups,
        "mean_deltas": mean_deltas,
    }


def _as_int(value: object, default: int) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def _bool_rate(items: List[Dict[str, object]], key: str) -> float:
    if not items:
        return 0.0
    return round(sum(1 for item in items if item.get(key)) / len(items), 6)


def _eligible_bool_rate(
    items: List[Dict[str, object]], key: str, eligibility_key: str
) -> float:
    eligible = [item for item in items if _as_int(item.get(eligibility_key), 0) > 0]
    return _bool_rate(eligible, key)


def _mean_metric(items: List[Dict[str, object]], key: str) -> float:
    if not items:
        return 0.0
    return round(mean(float(item.get(key) or 0) for item in items), 6)
