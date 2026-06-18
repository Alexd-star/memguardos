#!/usr/bin/env python3
from __future__ import annotations

import argparse
from pathlib import Path
import json
import sys


HIGH_RISK_MARKERS = (
    "rm -rf /",
    "mkfs.",
    "dd if=",
    ":(){:|:&};:",
    "shutdown",
    "reboot",
)


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
    tool_input = hook_input.get("tool_input") or {}
    serialized = json.dumps(tool_input, ensure_ascii=False).lower()
    blocked_marker = next((marker for marker in HIGH_RISK_MARKERS if marker in serialized), "")
    if blocked_marker:
        print(
            json.dumps(
                {
                    "decision": "deny",
                    "reason": "MemGuardOS blocked a high-risk tool command: %s" % blocked_marker,
                    "continue": False,
                },
                ensure_ascii=False,
            )
        )
        return 2
    print(json.dumps({"decision": "allow", "continue": True}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
