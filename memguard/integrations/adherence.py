from __future__ import annotations

from pathlib import Path
from typing import Dict, Optional
import json
import re
import shlex


def detect_adherence_issues(
    stages: Dict[str, object],
    events: list[Dict[str, object]],
) -> list[Dict[str, str]]:
    preferences = selected_preferences(stages)
    commands = successful_terminal_commands(events)
    issues = []
    preferred_search = preferences.get("code_search_tool", "")
    if preferred_search and not any(
        command_executable(command) == preferred_search for command in commands
    ):
        issues.append(
            {
                "topic": "code_search_tool",
                "expected_value": preferred_search,
                "reason": "未观察到成功执行的当前偏好工具命令",
            }
        )
    if preferences.get("response_order") == "command_then_explanation":
        successful_commands = successful_terminal_commands(events)
        final_output = final_visible_output(events)
        if successful_commands and not any(
            command in final_output
            or command_executable(command) in final_output
            for command in successful_commands
        ):
            issues.append(
                {
                    "topic": "response_order",
                    "expected_value": "command_then_explanation",
                    "reason": "最终回答未先呈现已执行或建议的命令",
                }
            )
    return issues


def build_adherence_repair_message(
    issues: list[Dict[str, str]],
    events: list[Dict[str, object]],
) -> str:
    lines = [
        "[MemGuardOS 合规修复]",
        "上一轮没有完整遵循已生效的用户记忆。请只修复以下问题，"
        "不要声称这是用户的新偏好，也不要重复已经成功完成的无关操作。",
    ]
    for issue in issues:
        if issue["topic"] == "code_search_tool":
            preferred = issue["expected_value"]
            lines.append(
                "- 代码文本搜索必须使用 `%s`。请用 `%s -n '目标符号' .` "
                "完成当前任务，不要改用 grep。" % (preferred, preferred)
            )
        elif issue["topic"] == "response_order":
            commands = successful_terminal_commands(events)
            command = commands[-1] if commands else "相关命令"
            lines.append(
                "- 重新给出最终回答，第一行先写命令 `%s`，然后用中文简要解释。"
                % command
            )
    return "\n".join(lines)


def selected_preferences(stages: Dict[str, object]) -> Dict[str, str]:
    values = {}
    for stage in ("planning", "tool_selection", "response_generation"):
        result = dict(stages.get(stage) or {})
        for memory in result.get("memories") or []:
            topic = str(memory.get("preference_topic") or "").strip()
            value = str(memory.get("preference_value") or "").strip()
            if topic and value:
                values[topic] = value
    return values


def terminal_commands(events: list[Dict[str, object]]) -> list[str]:
    commands = []
    for event in events:
        if str(event.get("event_type") or "") != "tool_call":
            continue
        if str(event.get("tool_name") or "") not in {"", "terminal"}:
            continue
        command = " ".join(
            str(dict(event.get("tool_args") or {}).get("command") or "").split()
        )
        if command:
            commands.append(command)
    return commands


def successful_terminal_commands(
    events: list[Dict[str, object]],
) -> list[str]:
    commands = []
    pending = []
    pending_policy = {}
    for event in events:
        event_type = str(event.get("event_type") or "")
        if event_type == "policy_decision":
            policy = dict(event.get("tool_args") or {})
            denied = (
                str(policy.get("decision") or "").lower() == "deny"
                or int(event.get("exit_code") or 0) == 2
            )
            pending_policy = {"denied": denied}
        elif event_type == "tool_call":
            if str(event.get("tool_name") or "") not in {"", "terminal"}:
                pending_policy = {}
                continue
            command = " ".join(
                str(dict(event.get("tool_args") or {}).get("command") or "").split()
            )
            if command and not pending_policy.get("denied"):
                pending.append(command)
            pending_policy = {}
        elif event_type == "tool_result" and pending:
            if int(event.get("exit_code") or 0) == 0:
                commands.append(pending.pop(0))
            else:
                pending.pop(0)
    return commands


def final_visible_output(events: list[Dict[str, object]]) -> str:
    outputs = []
    for event in events:
        event_type = str(event.get("event_type") or "")
        if event_type in {"assistant_message", "task_complete"}:
            outputs.append(str(event.get("content") or ""))
        elif (
            event_type == "tool_result"
            and str(event.get("tool_name") or "") == "finish"
        ):
            outputs.append(str(event.get("tool_result") or ""))
    return outputs[-1] if outputs else ""


def procedural_memory_evidence(stages: Dict[str, object]) -> str:
    for stage in ("planning", "tool_selection", "response_generation"):
        result = dict(stages.get(stage) or {})
        subtypes = {
            str(memory.get("subtype") or "")
            for memory in result.get("memories") or []
        }
        if "procedural_trace" not in subtypes:
            continue
        context = str(result.get("context") or "")
        matches = re.findall(
            r"Tool result:\s*command=.*?\s+status=success\b.*?"
            r"stdout_summary=(.*?)\s+stderr_summary=",
            context,
        )
        for value in matches:
            evidence = " ".join(value.split()).strip()
            if evidence:
                return evidence[:1000]
    return ""


def command_executable(command: str) -> str:
    clean = str(command).strip()
    if not clean:
        return ""
    try:
        parts = shlex.split(clean)
    except ValueError:
        parts = clean.split()
    while parts and parts[0] in {"sudo", "env", "command"}:
        parts.pop(0)
    return Path(parts[0]).name if parts else ""


def parse_structured_tool_intent(
    content: str,
    *,
    preferred_tool: str,
) -> Optional[Dict[str, object]]:
    try:
        payload = json.loads(str(content or "").strip())
    except (json.JSONDecodeError, TypeError, ValueError):
        return None
    if not isinstance(payload, dict):
        return None
    candidate = payload
    function = payload.get("function")
    if isinstance(function, dict):
        if str(function.get("name") or "") not in {"terminal", "function_name"}:
            return None
        arguments = function.get("arguments")
        if isinstance(arguments, str):
            try:
                arguments = json.loads(arguments)
            except (json.JSONDecodeError, TypeError, ValueError):
                return None
        if not isinstance(arguments, dict):
            return None
        candidate = arguments
    command = " ".join(str(candidate.get("command") or "").split())
    if not command or len(command) > 2048:
        return None
    if any(marker in command for marker in (";", "|", ">", "<", "`", "\n", "\r")):
        return None
    try:
        argv = shlex.split(command)
    except ValueError:
        return None
    if not argv or argv[0] != preferred_tool:
        return None
    if preferred_tool not in {"rg", "grep"}:
        return None
    if len(argv) < 3 or argv[-1] != ".":
        return None
    allowed_options = {
        "rg": {
            "-n",
            "--line-number",
            "-S",
            "--smart-case",
            "-i",
            "--ignore-case",
            "-F",
            "--fixed-strings",
            "-w",
            "--word-regexp",
        },
        "grep": {
            "-n",
            "--line-number",
            "-i",
            "--ignore-case",
            "-F",
            "--fixed-strings",
            "-w",
            "--word-regexp",
            "-R",
            "-r",
            "--recursive",
        },
    }
    patterns = []
    for argument in argv[1:-1]:
        if argument.startswith("-"):
            if argument not in allowed_options[preferred_tool]:
                return None
            continue
        patterns.append(argument)
    if len(patterns) != 1 or len(patterns[0]) > 256:
        return None
    for argument in argv[1:]:
        path = Path(argument)
        if path.is_absolute() or ".." in path.parts:
            return None
    timeout = candidate.get("timeout", 10)
    try:
        timeout_value = max(1, min(int(timeout), 15))
    except (TypeError, ValueError):
        timeout_value = 10
    return {
        "command": command,
        "argv": argv,
        "timeout": timeout_value,
    }


def repair_failed_preferred_tool_intent(
    events: list[Dict[str, object]],
    *,
    preferred_tool: str,
) -> Optional[Dict[str, object]]:
    failed_commands = []
    pending_command = ""
    pending_denied = False
    for event in events:
        event_type = str(event.get("event_type") or "")
        if event_type == "policy_decision":
            policy = dict(event.get("tool_args") or {})
            pending_denied = (
                str(policy.get("decision") or "").lower() == "deny"
                or int(event.get("exit_code") or 0) == 2
            )
        elif event_type == "tool_call":
            if str(event.get("tool_name") or "") not in {"", "terminal"}:
                pending_command = ""
                pending_denied = False
                continue
            command = " ".join(
                str(dict(event.get("tool_args") or {}).get("command") or "").split()
            )
            pending_command = "" if pending_denied else command
            pending_denied = False
        elif event_type == "tool_result" and pending_command:
            if int(event.get("exit_code") or 0) == 2:
                failed_commands.append(pending_command)
            pending_command = ""
    for command in reversed(failed_commands):
        repaired = _canonical_search_command(command, preferred_tool)
        if repaired is not None:
            return repaired
    return None


def recover_misrouted_preferred_tool_intent(
    events: list[Dict[str, object]],
    *,
    preferred_tool: str,
) -> Optional[Dict[str, object]]:
    """Recover safe search commands sent to a non-terminal OpenHands tool name."""
    for event in reversed(events):
        if str(event.get("event_type") or "") != "tool_call":
            continue
        if str(event.get("tool_name") or "") in {"", "terminal"}:
            continue
        command = " ".join(
            str(dict(event.get("tool_args") or {}).get("command") or "").split()
        )
        if not command:
            continue
        intent = parse_structured_tool_intent(
            json.dumps({"command": command}),
            preferred_tool=preferred_tool,
        )
        if intent is not None:
            return intent
    return None


def _canonical_search_command(
    command: str,
    preferred_tool: str,
) -> Optional[Dict[str, object]]:
    if preferred_tool not in {"rg", "grep"}:
        return None
    if any(marker in command for marker in (";", "|", ">", "<", "`", "\n", "\r")):
        return None
    try:
        argv = shlex.split(command)
    except ValueError:
        return None
    if not argv or argv[0] != preferred_tool:
        return None
    no_value_options = {
        "-n",
        "--line-number",
        "-S",
        "--smart-case",
        "-i",
        "--ignore-case",
        "-F",
        "--fixed-strings",
        "-w",
        "--word-regexp",
        "-R",
        "-r",
        "--recursive",
        "--files",
    }
    values = []
    ignore_case = False
    for argument in argv[1:]:
        if argument.startswith("-"):
            if argument not in no_value_options:
                return None
            if argument in {"-i", "--ignore-case"}:
                ignore_case = True
            continue
        if argument == ".":
            continue
        values.append(argument)
    if not values:
        return None
    pattern = values[0]
    if len(pattern) > 256:
        return None
    pattern_path = Path(pattern)
    if pattern_path.is_absolute() or ".." in pattern_path.parts:
        return None
    repaired_argv = [preferred_tool]
    if preferred_tool == "grep":
        repaired_argv.append("-R")
    repaired_argv.append("-n")
    if ignore_case:
        repaired_argv.append("-i")
    repaired_argv.extend([pattern, "."])
    return {
        "command": shlex.join(repaired_argv),
        "argv": repaired_argv,
        "timeout": 10,
    }


def recoverable_tool_schema_error_count(
    events: list[Dict[str, object]],
) -> int:
    return sum(
        1
        for event in events
        if str(event.get("event_type") or "") == "task_error"
        and is_tool_schema_error(str(event.get("content") or ""))
    )


def is_tool_schema_error(value: str) -> bool:
    lowered = str(value or "").lower()
    return "tool" in lowered and any(
        marker in lowered
        for marker in ("not found", "function call", "invalid function", "unknown tool")
    )
