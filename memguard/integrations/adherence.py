from __future__ import annotations

from pathlib import Path
from typing import Dict, Optional
import json
import os
import re
import shlex
import sys


def detect_adherence_issues(
    stages: Dict[str, object],
    events: list[Dict[str, object]],
    *,
    task: str = "",
) -> list[Dict[str, str]]:
    preferences = selected_preferences(stages)
    commands = successful_terminal_commands(events)
    issues = []
    preferred_search = preferences.get("code_search_tool", "")
    observed_search_tools = [
        command_executable(command)
        for command in commands
        if command_executable(command) in {"rg", "grep"}
    ]
    conflicting_search_tools = [
        tool for tool in observed_search_tools if tool != preferred_search
    ]
    if preferred_search and code_search_preference_applicable(task) and (
        preferred_search not in observed_search_tools or conflicting_search_tools
    ):
        issues.append(
            {
                "topic": "code_search_tool",
                "expected_value": preferred_search,
                "reason": (
                    "观察到与当前偏好冲突的代码搜索工具：%s"
                    % "、".join(dict.fromkeys(conflicting_search_tools))
                    if conflicting_search_tools
                    else "未观察到成功执行的当前偏好工具命令"
                ),
            }
        )
    final_output = final_visible_output(events)
    preferred_test_runner = preferences.get("test_runner_command", "")
    if preferred_test_runner and not any(
        command.startswith(preferred_test_runner) for command in commands
    ) and preferred_test_runner not in final_output:
        issues.append(
            {
                "topic": "test_runner_command",
                "expected_value": preferred_test_runner,
                "reason": "未观察到用户偏好的 Python 测试入口",
            }
        )
    preferred_service_log = preferences.get("service_log_tool", "")
    if preferred_service_log and not any(
        command.startswith(preferred_service_log) for command in commands
    ) and preferred_service_log not in final_output:
        issues.append(
            {
                "topic": "service_log_tool",
                "expected_value": preferred_service_log,
                "reason": "未观察到用户偏好的服务日志命令",
            }
        )
    if preferences.get("response_order") == "command_then_explanation":
        successful_commands = successful_terminal_commands(events)
        if successful_commands and not response_starts_with_command_or_conclusion(
            final_output,
            successful_commands,
        ):
            issues.append(
                {
                    "topic": "response_order",
                    "expected_value": "command_then_explanation",
                    "reason": "最终回答未先呈现已执行或建议的命令",
                }
            )
    return issues


def code_search_preference_applicable(task: str) -> bool:
    """Return whether a code-text search preference applies to this task."""

    text = str(task or "").strip().lower()
    if not text:
        return True
    code_markers = (
        "代码",
        "源码",
        "函数",
        "定义",
        "符号",
        "调用",
        "引用",
        "文本内容",
        "关键字",
        "code",
        "source",
        "function",
        "definition",
        "symbol",
        "reference",
    )
    filesystem_markers = (
        "文件夹",
        "目录在哪",
        "文件在哪",
        "路径在哪",
        "定位目录",
        "定位文件",
        "folder",
        "directory",
        "file path",
        "locate file",
    )
    if any(marker in text for marker in code_markers):
        return True
    if any(marker in text for marker in filesystem_markers):
        return False
    return True


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
            alternative = "grep" if preferred == "rg" else "rg"
            lines.append(
                "- 代码文本搜索必须使用 `%s`。请用 `%s -n '目标符号' .` "
                "完成当前任务，不要改用 `%s`。" % (preferred, preferred, alternative)
            )
        elif issue["topic"] == "response_order":
            commands = successful_terminal_commands(events)
            command = commands[-1] if commands else "相关命令"
            lines.append(
                "- 重新给出最终回答，第一行先写命令 `%s`，然后用中文简要解释。"
                % command
            )
        elif issue["topic"] == "test_runner_command":
            lines.append(
                "- Python 测试入口必须使用 `%s`。请直接给出或执行该入口，"
                "不要使用裸 `pytest`。" % issue["expected_value"]
            )
        elif issue["topic"] == "service_log_tool":
            lines.append(
                "- 用户服务日志排查必须优先使用 `%s`。请直接给出或执行该命令。"
                % issue["expected_value"]
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
    pending: Dict[str, list[str]] = {}
    pending_policy: Dict[str, Dict[str, bool]] = {}
    for event in events:
        event_type = str(event.get("event_type") or "")
        tool_name = str(event.get("tool_name") or "terminal")
        if event_type == "policy_decision":
            policy = dict(event.get("tool_args") or {})
            denied = (
                str(policy.get("decision") or "").lower() == "deny"
                or int(event.get("exit_code") or 0) == 2
            )
            pending_policy[tool_name] = {"denied": denied}
        elif event_type == "tool_call":
            if tool_name != "terminal":
                pending_policy.pop(tool_name, None)
                continue
            command = " ".join(
                str(dict(event.get("tool_args") or {}).get("command") or "").split()
            )
            policy = pending_policy.pop(tool_name, {})
            if command and not policy.get("denied"):
                pending.setdefault(tool_name, []).append(command)
        elif event_type == "tool_result":
            queue = pending.get(tool_name) or []
            if queue:
                command = queue.pop(0)
                if int(event.get("exit_code") or 0) == 0:
                    commands.append(command)
    return commands


def final_visible_output(events: list[Dict[str, object]]) -> str:
    outputs = []
    for event in events:
        event_type = str(event.get("event_type") or "")
        if event_type in {"assistant_message", "task_complete"}:
            outputs.append(str(event.get("display_content", event.get("content")) or ""))
        elif (
            event_type == "tool_result"
            and str(event.get("tool_name") or "") == "finish"
        ):
            outputs.append(str(event.get("display_tool_result", event.get("tool_result")) or ""))
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


def response_starts_with_command_or_conclusion(
    output: str,
    commands: list[str] | tuple[str, ...] = (),
) -> bool:
    clean = str(output or "").lstrip()
    fence = re.match(r"^(?:```|~~~)[A-Za-z0-9_+-]*[ \t]*\n", clean)
    if fence:
        clean = clean[fence.end():].lstrip()
    if clean.startswith(("命令", "结论", "Command", "Conclusion", "$")):
        return True
    first_line = clean.splitlines()[0].strip() if clean else ""
    for command in commands:
        executable = command_executable(command)
        if first_line.startswith(command):
            return True
        if executable and first_line.startswith(
            (executable + " ", "`" + executable + " ", "$ " + executable + " ")
        ):
            return True
    return False


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


def parse_inline_search_intent(
    content: str,
    *,
    preferred_tool: str,
) -> Optional[Dict[str, object]]:
    candidates = []
    text = str(content or "")
    candidates.extend(match.group(1).strip() for match in re.finditer(r"`([^`\n]+)`", text))
    for line in text.splitlines():
        line = line.strip()
        if preferred_tool and preferred_tool in line:
            candidates.append(line)
    for candidate in candidates:
        cleaned = candidate.strip()
        if cleaned.startswith("$"):
            cleaned = cleaned[1:].strip()
        if not cleaned.startswith(preferred_tool + " "):
            continue
        intent = parse_structured_tool_intent(
            json.dumps({"command": cleaned}),
            preferred_tool=preferred_tool,
        )
        if intent is not None:
            return intent
    return None


def parse_natural_search_intent(
    content: str,
    *,
    preferred_tool: str,
) -> Optional[Dict[str, object]]:
    if preferred_tool not in {"rg", "grep"}:
        return None
    text = str(content or "")
    if preferred_tool not in text and not re.search(
        r"(检索|定位|查找|搜索|search|find|locate)",
        text,
        flags=re.IGNORECASE,
    ):
        return None
    terms = []
    patterns = [
        r"`([^`\n]+)`",
        r"['\"]([A-Za-z_][A-Za-z0-9_./:-]{1,128})['\"]",
    ]
    for pattern in patterns:
        for match in re.finditer(pattern, text):
            candidate = match.group(1).strip()
            if candidate == preferred_tool:
                continue
            if _safe_search_term(candidate):
                terms.append(candidate)
    if not terms:
        return None
    term = terms[0]
    command = f"{preferred_tool} -n {shlex.quote(term)} ."
    return {
        "command": command,
        "argv": [preferred_tool, "-n", term, "."],
        "timeout": 10,
    }


def _safe_search_term(value: str) -> bool:
    text = str(value or "")
    if not text or len(text) > 128:
        return False
    if any(marker in text for marker in (";", "|", ">", "<", "`", "\n", "\r")):
        return False
    if text.startswith("-"):
        return False
    path = Path(text)
    if path.is_absolute() or ".." in path.parts:
        return False
    return bool(re.fullmatch(r"[A-Za-z_][A-Za-z0-9_./:-]{1,128}", text))


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
            if int(event.get("exit_code") or 0) != 0:
                failed_commands.append(pending_command)
            pending_command = ""
    for command in reversed(failed_commands):
        repaired = _canonical_search_command(command, preferred_tool)
        if repaired is not None:
            return repaired
    return None


def recover_failed_test_runner_intent(
    events: list[Dict[str, object]],
    *,
    preferred_command: str,
    workspace: Path,
) -> Optional[Dict[str, object]]:
    if " ".join(str(preferred_command or "").split()) != "python -m pytest":
        return None
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
            if int(event.get("exit_code") or 0) != 0:
                failed_commands.append(pending_command)
            pending_command = ""
    for command in reversed(failed_commands):
        recovered = _canonical_pytest_command(command, workspace=workspace)
        if recovered is not None:
            return recovered
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


def _canonical_pytest_command(
    command: str,
    *,
    workspace: Path,
) -> Optional[Dict[str, object]]:
    if any(marker in command for marker in (";", "|", ">", "<", "`", "\n", "\r")):
        return None
    try:
        argv = shlex.split(command)
    except ValueError:
        return None
    if not argv:
        return None
    rest = []
    executable = command_executable(command)
    if executable == "pytest":
        rest = argv[1:]
    elif executable in {"python", "python3"} and len(argv) >= 3 and argv[1:3] == [
        "-m",
        "pytest",
    ]:
        rest = argv[3:]
    else:
        return None

    root = Path(workspace).resolve()
    paths = []
    for argument in rest:
        if argument.startswith("-"):
            continue
        candidate = Path(argument)
        if candidate.is_absolute() or ".." in candidate.parts:
            continue
        resolved = (root / candidate).resolve()
        try:
            resolved.relative_to(root)
        except ValueError:
            continue
        if resolved.exists():
            paths.append(str(candidate).replace("\\", "/").rstrip("/") or ".")

    if not paths:
        tests_dir = root / "tests"
        if tests_dir.is_dir():
            paths = ["tests"]
        else:
            paths = ["."]
    paths = _dedupe_preserve_order(paths)
    command_parts = ["python", "-m", "pytest"] + paths
    return {
        "command": shlex.join(command_parts),
        "argv": [_pytest_python_executable(root), "-m", "pytest"] + paths,
        "timeout": 30,
    }


def _pytest_python_executable(workspace: Path) -> str:
    explicit = str(os.environ.get("MEMGUARD_PYTEST_PYTHON") or "").strip()
    if explicit and Path(explicit).exists():
        return str(Path(explicit).absolute())
    roots = [Path.cwd()]
    workspace_root = Path(workspace).resolve()
    roots.extend([workspace_root, *workspace_root.parents])
    seen = set()
    for root in roots:
        key = str(root)
        if key in seen:
            continue
        seen.add(key)
        for candidate in (
            root / ".venv" / "bin" / "python",
            root / ".venv" / "Scripts" / "python.exe",
        ):
            if candidate.exists():
                return str(candidate.absolute())
    return sys.executable


def _dedupe_preserve_order(values: list[str]) -> list[str]:
    seen = set()
    result = []
    for value in values:
        if value in seen:
            continue
        seen.add(value)
        result.append(value)
    return result


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
