#!/usr/bin/env python3
from __future__ import annotations

import argparse
from pathlib import Path
import json
import re
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
    normalized = " ".join(str(command).strip().split())
    if not normalized:
        return False
    if preferred_tool == "rg":
        return bool(
            re.search(
                r"(^|(?:\|\||&&|[|;])\s*)(?:sudo\s+)?"
                r"(?:grep|egrep|fgrep|git\s+grep)\b",
                normalized,
            )
        )
    if preferred_tool == "grep":
        return bool(
            re.search(
                r"(^|(?:\|\||&&|[|;])\s*)(?:sudo\s+)?rg\b",
                normalized,
            )
        )
    return False


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
    blocked_marker = high_risk_marker(tool_input)
    if blocked_marker:
        return deny(
            "MemGuardOS blocked a high-risk tool command: %s" % blocked_marker,
            "high_risk",
            blocked_marker=blocked_marker,
        )
    preferences = selected_preferences(payload)
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
    print(json.dumps({"decision": "allow", "continue": True}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
