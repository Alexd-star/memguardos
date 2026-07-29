#!/usr/bin/env python3
from __future__ import annotations

import argparse
from pathlib import Path
import json
import re
import shlex
import sys


HIGH_RISK_MARKERS = (
    "rm -rf /",
    "mkfs.",
    "dd if=",
    ":(){:|:&};:",
    "shutdown",
    "reboot",
)

HIGH_RISK_COMMAND_PATTERNS = (
    (
        re.compile(
            r"(?:^|[;&|]\s*)(?:sudo\s+)?(?:env\s+)?(?:command\s+)?"
            r"rm\s+-[A-Za-z]*r[A-Za-z]*f[A-Za-z]*\b",
            re.IGNORECASE,
        ),
        "rm -rf",
    ),
    (
        re.compile(
            r"(?:^|[;&|]\s*)(?:sudo\s+)?(?:env\s+)?(?:command\s+)?"
            r"systemctl\s+(?:restart|stop|disable|mask|kill|poweroff)\b",
            re.IGNORECASE,
        ),
        "systemctl restart",
    ),
)


def selected_preferences(payload):
    preferences = {}
    for stage in ("planning", "tool_selection", "response_generation"):
        result = dict((payload.get("stages") or {}).get(stage) or {})
        for memory in result.get("memories") or []:
            topic = str(memory.get("preference_topic") or "").strip()
            value = str(memory.get("preference_value") or "").strip()
            if topic and value:
                preferences[topic] = value
    return preferences


def deny(reason, category, **details):
    payload = {
        "decision": "deny",
        "reason": reason,
        "policy_category": category,
        "continue": False,
    }
    payload.update(details)
    print(json.dumps(payload, ensure_ascii=False))
    return 2


def violates_code_search_preference(command, preferred_tool):
    executables = _shell_command_executables(command)
    if preferred_tool == "rg":
        return any(item in {"grep", "egrep", "fgrep", "git grep"} for item in executables)
    if preferred_tool == "grep":
        return "rg" in executables
    return False


def _shell_command_executables(command):
    normalized = " ".join(str(command or "").strip().split())
    if not normalized:
        return []
    executables = []
    for segment in re.split(r"(?:&&|\|\||[;|])", normalized):
        try:
            tokens = shlex.split(segment.strip())
        except ValueError:
            tokens = segment.strip().split()
        while tokens:
            name = Path(tokens[0]).name
            if name == "sudo":
                tokens.pop(0)
                while tokens and tokens[0].startswith("-"):
                    tokens.pop(0)
                continue
            if name == "env":
                tokens.pop(0)
                while tokens and (tokens[0].startswith("-") or "=" in tokens[0]):
                    tokens.pop(0)
                continue
            if name == "command":
                tokens.pop(0)
                continue
            if name in {"time", "nice", "nohup", "timeout"}:
                tokens.pop(0)
                while tokens and tokens[0].startswith("-"):
                    tokens.pop(0)
                if name == "timeout" and tokens:
                    tokens.pop(0)
                continue
            break
        if not tokens:
            continue
        executable = Path(tokens[0]).name
        if executable in {"bash", "sh", "dash", "zsh"}:
            command_flag = next(
                (
                    index
                    for index, token in enumerate(tokens[1:], start=1)
                    if token.startswith("-") and "c" in token[1:]
                ),
                -1,
            )
            if command_flag >= 0 and command_flag + 1 < len(tokens):
                executables.extend(
                    _shell_command_executables(tokens[command_flag + 1])
                )
                continue
        if executable == "git" and len(tokens) > 1 and tokens[1] == "grep":
            executable = "git grep"
        executables.append(executable)
        if executable == "xargs":
            candidates = [item for item in tokens[1:] if not item.startswith("-")]
            if candidates:
                executables.append(Path(candidates[0]).name)
        if executable == "find" and "-exec" in tokens:
            index = tokens.index("-exec")
            if index + 1 < len(tokens):
                executables.append(Path(tokens[index + 1]).name)
    return executables


def violates_test_runner_preference(command, preferred_command):
    normalized = " ".join(str(command).strip().split())
    if not normalized:
        return False
    if preferred_command != "python -m pytest":
        return False
    return bool(
        re.search(
            r"(^|(?:\|\||&&|[|;])\s*)(?:sudo\s+)?"
            r"(?:env\s+)?(?:command\s+)?pytest\b",
            normalized,
        )
    )


def high_risk_marker(tool_input):
    serialized = json.dumps(tool_input, ensure_ascii=False).lower()
    blocked_marker = next((marker for marker in HIGH_RISK_MARKERS if marker in serialized), "")
    if blocked_marker:
        return blocked_marker
    command = " ".join(str((tool_input or {}).get("command") or "").split())
    for pattern, marker in HIGH_RISK_COMMAND_PATTERNS:
        if pattern.search(command):
            return marker
    return ""


def finish_high_risk_violation_marker(tool_input, preferences):
    if preferences.get("high_risk_confirmation") != "required":
        return ""
    text = json.dumps(tool_input or {}, ensure_ascii=False).lower()
    risk_markers = (
        "高风险",
        "危险",
        "删除",
        "rm",
        "systemctl",
        "reboot",
        "shutdown",
    )
    has_risk = any(marker in text for marker in risk_markers)
    false_confirmation_markers = (
        "已获得用户确认",
        "您已确认",
        "用户已确认",
        "already confirmed",
        "confirmed by user",
    )
    if has_risk and any(marker in text for marker in false_confirmation_markers):
        return "finish_false_confirmation"
    safe_refusal_markers = (
        "未确认",
        "等待确认",
        "需要确认",
        "请确认",
        "不能直接执行",
        "不能执行",
        "无法执行",
        "拒绝执行",
        "will not execute",
        "won't execute",
        "cannot execute",
        "not execute",
        "confirm first",
        "requires confirmation",
    )
    if any(marker in text for marker in safe_refusal_markers):
        return ""
    completion_markers = (
        "执行完成",
        "已执行",
        "完成",
        "done",
        "executed",
        "completed",
        "confirmed",
        "removed",
        "deleted",
    )
    if has_risk and any(marker in text for marker in completion_markers):
        return "finish_false_completion"
    return ""


def parse_args():
    parser = argparse.ArgumentParser()
    parser.add_argument("--context-file", required=True)
    parser.add_argument("--guard-tool", action="store_true")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    payload = json.loads(Path(args.context_file).read_text(encoding="utf-8"))
    if not args.guard_tool:
        print(
            json.dumps(
                {
                    "decision": "allow",
                    "additionalContext": payload.get("additional_context", ""),
                    "continue": True,
                },
                ensure_ascii=False,
            )
        )
        return 0

    hook_input = json.load(sys.stdin)
    tool_name = str(hook_input.get("tool_name") or "")
    allowed_tools = [str(item) for item in payload.get("allowed_tools") or []]
    if allowed_tools and tool_name not in allowed_tools:
        return deny(
            "Tool %s is not allowed by the active MemGuardOS profile." % tool_name,
            "tool_profile",
            observed_tool=tool_name,
        )
    tool_input = hook_input.get("tool_input") or {}
    preferences = selected_preferences(payload)
    finish_violation = (
        finish_high_risk_violation_marker(tool_input, preferences)
        if tool_name == "finish"
        else ""
    )
    if finish_violation:
        return deny(
            "MemGuardOS blocked a high-risk finish message that claimed execution "
            "without an observed confirmation gate.",
            "high_risk",
            blocked_marker=finish_violation,
        )
    blocked_marker = high_risk_marker(tool_input)
    if blocked_marker:
        return deny(
            "MemGuardOS blocked a high-risk tool command: %s" % blocked_marker,
            "high_risk",
            blocked_marker=blocked_marker,
        )
    preferred_search = preferences.get("code_search_tool", "")
    command = str(tool_input.get("command") or "")
    if (
        tool_name == "terminal"
        and preferred_search
        and violates_code_search_preference(command, preferred_search)
    ):
        return deny(
            "MemGuardOS requires the active code-search preference `%s`; "
            "revise the command and retry." % preferred_search,
            "preference_enforcement",
            preference_topic="code_search_tool",
            expected_value=preferred_search,
            observed_command=command,
        )
    preferred_test_runner = preferences.get("test_runner_command", "")
    if (
        tool_name == "terminal"
        and preferred_test_runner
        and violates_test_runner_preference(command, preferred_test_runner)
    ):
        return deny(
            "MemGuardOS requires the active Python test runner preference `%s`; "
            "revise the command and retry." % preferred_test_runner,
            "preference_enforcement",
            preference_topic="test_runner_command",
            expected_value=preferred_test_runner,
            observed_command=command,
        )
    print(json.dumps({"decision": "allow", "continue": True}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
