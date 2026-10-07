"""Provider-neutral classification of an Agent action after preflight.

The feedback record deliberately contains no command plaintext.  A caller can
prove which memory influenced an action and whether the action followed that
memory, while the sensitive command and its output remain in the Agent's own
execution boundary.
"""

from __future__ import annotations

from typing import Any, Dict, Optional


def _exit_code(tool_result: Any, explicit: Optional[int]) -> Optional[int]:
    if explicit is not None:
        return int(explicit)
    if isinstance(tool_result, dict) and tool_result.get("exit_code") is not None:
        try:
            return int(tool_result["exit_code"])
        except (TypeError, ValueError):
            return None
    return None


def classify_action_outcome(
    preflight: Dict[str, Any],
    *,
    exit_code: Optional[int] = None,
    tool_result: Any = None,
    task_status: str = "",
    actual_tool_name: str = "",
    actual_tool_input: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    """Classify execution without pretending that MemGuardOS ran the tool.

    ``deviation`` is intentionally distinct from ``failure``: a command can
    succeed while violating the remembered tool preference.  Such a result
    must not reinforce the preference.
    """

    decision = str(preflight.get("decision") or "").strip().lower()
    expected_tool = str(preflight.get("tool_name") or "").strip()
    actual_tool = str(actual_tool_name or expected_tool).strip()
    code = _exit_code(tool_result, exit_code)
    status = str(task_status or "").strip().lower()
    executed = code is not None or status in {"success", "completed", "failed", "failure", "error"}
    expected_value = str(preflight.get("expected_value") or "").strip()
    actual_command = ""
    if isinstance(actual_tool_input, dict):
        actual_command = str(actual_tool_input.get("command") or "").strip()
    suggested_command = str(preflight.get("suggested_command") or "").strip()
    if decision in {"deny", "confirm"} and not executed:
        outcome = "blocked"
        adherence = "followed" if decision == "deny" else "not_applicable"
        reason = "行动在执行前被记忆治理策略拦截，未产生工具执行结果。"
    elif not executed:
        outcome = "unknown"
        adherence = "not_applicable"
        reason = "尚未收到可判定的退出码或任务状态，暂不强化任何记忆。"
    elif code is not None and code != 0:
        outcome = "failure"
        adherence = "followed" if actual_tool == expected_tool else "violated"
        reason = "工具已执行但返回非零退出码，结果不会作为成功经验强化。"
    elif expected_value and actual_command:
        executable_matches = actual_command.split()[0].strip("'\"") == expected_value
        if not executable_matches:
            outcome = "deviation"
            adherence = "violated"
            reason = "工具执行成功，但实际命令偏离了记忆中的首选工具。"
        else:
            outcome = "success"
            adherence = "followed"
            reason = "工具执行成功，且实际命令遵循了记忆中的首选工具。"
    elif expected_tool and actual_tool != expected_tool:
        outcome = "deviation"
        adherence = "violated"
        reason = "工具执行成功，但实际工具名称与行动预检不一致。"
    elif suggested_command and actual_command and actual_command != suggested_command:
        outcome = "deviation"
        adherence = "violated"
        reason = "工具执行成功，但实际命令偏离了记忆提供的建议命令。"
    else:
        outcome = "success"
        adherence = "followed"
        reason = "工具执行成功，且实际行动遵循了本次预检采用的记忆证据。"
    return {
        "outcome": outcome,
        "adherence": adherence,
        "reason": reason,
        "decision": decision,
        "tool_name": actual_tool or expected_tool,
        "exit_code": code,
        "reinforce": outcome == "success" and adherence == "followed",
    }
