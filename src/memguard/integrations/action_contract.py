"""Deterministic memory-to-action preflight for external Agents.

The contract deliberately does not execute tools.  It converts applicable
memory and a proposed tool call into a small, auditable decision that an Agent
adapter can enforce before execution.
"""

from __future__ import annotations

import re
from typing import Dict, Mapping

from memguard.integrations.openhands_memory_hook import (
    high_risk_marker,
    shell_command_executables,
    violates_code_search_preference,
    violates_test_runner_preference,
)


_CONFIRMABLE_COMMAND = re.compile(
    r"(?:^|[;&|]\s*)(?:sudo\s+)?(?:env\s+)?(?:command\s+)?"
    r"(?:rm\s+-(?:[A-Za-z]*[rf][A-Za-z]*)\b|"
    r"systemctl\s+(?:restart|stop|disable|mask|kill)\b|"
    r"(?:reboot|shutdown|poweroff)\b|"
    r"(?:apt|apt-get)\s+(?:install|remove|purge)\b|"
    r"(?:chmod|chown)\s+)",
    re.IGNORECASE,
)


def evaluate_action(
    *,
    tool_name: str,
    tool_input: Mapping[str, object],
    memories: list[Mapping[str, object]],
) -> Dict[str, object]:
    """Evaluate one proposed action without running it.

    ``redirect`` is used for a recoverable preference mismatch, while
    ``confirm`` is reserved for a risky operation covered by an explicit
    confirmation policy.  Known destructive signatures remain ``deny``.
    """

    clean_tool = str(tool_name or "").strip()
    payload = dict(tool_input or {})
    command = " ".join(str(payload.get("command") or "").split())
    preferences = {
        str(item.get("preference_topic") or "").strip(): str(item.get("preference_value") or "").strip()
        for item in memories
        if item.get("preference_topic") and item.get("preference_value")
    }
    evidence = [
        {
            "memory_id": str(item.get("memory_id") or ""),
            "version_id": str(item.get("version_id") or ""),
            "topic": str(item.get("preference_topic") or ""),
        }
        for item in memories
        if item.get("memory_id")
    ]

    if clean_tool == "terminal":
        marker = high_risk_marker(payload)
        if marker:
            return _decision(
                "deny",
                "high_risk",
                "检测到受保护的高风险命令，MemGuardOS 在执行前拒绝该操作。",
                evidence,
                blocked_marker=marker,
            )
        if command and _CONFIRMABLE_COMMAND.search(command):
            policy = preferences.get("high_risk_confirmation")
            if policy == "required":
                return _decision(
                    "confirm",
                    "high_risk_confirmation",
                    "当前记忆要求高风险操作先取得用户确认。",
                    evidence,
                    confirmation_required=True,
                )

        preferred_search = preferences.get("code_search_tool")
        if preferred_search and violates_code_search_preference(command, preferred_search):
            suggestion = _replace_search_tool(command, preferred_search)
            return _decision(
                "redirect",
                "preference_enforcement",
                "拟执行的代码搜索工具与当前偏好不一致，建议改用 %s。" % preferred_search,
                evidence,
                preference_topic="code_search_tool",
                expected_value=preferred_search,
                suggested_command=suggestion,
            )
        preferred_runner = preferences.get("test_runner_command")
        if preferred_runner and violates_test_runner_preference(command, preferred_runner):
            return _decision(
                "redirect",
                "preference_enforcement",
                "拟执行的测试入口与当前偏好不一致，建议改用 %s。" % preferred_runner,
                evidence,
                preference_topic="test_runner_command",
                expected_value=preferred_runner,
                suggested_command=preferred_runner,
            )

    return _decision("allow", "no_conflict", "未发现适用的记忆冲突或安全阻断条件。", evidence)


def _decision(decision: str, category: str, reason: str, evidence: list[dict], **details: object) -> Dict[str, object]:
    result: Dict[str, object] = {
        "decision": decision,
        "policy_category": category,
        "reason": reason,
        "evidence": evidence,
        "continue": decision == "allow",
    }
    result.update(details)
    return result


def _replace_search_tool(command: str, preferred: str) -> str:
    """Return a conservative display-only suggestion, never an execution string."""

    if preferred not in {"rg", "grep"}:
        return command
    tokens = shell_command_executables(command)
    if preferred == "rg" and any(item in {"grep", "egrep", "fgrep", "git grep"} for item in tokens):
        return re.sub(r"\b(?:git\s+grep|egrep|fgrep|grep)\b", "rg", command, count=1)
    if preferred == "grep" and "rg" in tokens:
        return re.sub(r"\brg\b", "grep", command, count=1)
    return command
