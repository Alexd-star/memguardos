#!/usr/bin/env python3
from __future__ import annotations

import argparse
from pathlib import Path
import json
import os

from memguard.config import namespace_home
from memguard.integrations.openhands_adapter import detect_openhands_runtime
from memguard.integrations.openhands_runner import (
    OpenHandsMemoryRunner,
    create_openhands_conversation,
)
from memguard.service import MemGuardService


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="在专用工作区运行接入 MemGuardOS 的真实 OpenHands Agent"
    )
    parser.add_argument("--task", default="")
    parser.add_argument("--session-id", required=True)
    parser.add_argument("--workspace", required=True)
    parser.add_argument("--state-dir", default="")
    parser.add_argument("--project-root", default="")
    parser.add_argument("--task-type", default="code_assistance")
    parser.add_argument("--namespace", default="default")
    parser.add_argument("--model", default=os.environ.get("OPENHANDS_MODEL", ""))
    parser.add_argument("--base-url", default=os.environ.get("OPENHANDS_BASE_URL", ""))
    parser.add_argument("--api-key-env", default="OPENHANDS_API_KEY")
    parser.add_argument("--tool-profile", choices=["default", "terminal"], default="default")
    parser.add_argument(
        "--dry-init",
        action="store_true",
        help="只初始化真实 OpenHands Agent、工具和 Hook，不向模型发送请求",
    )
    return parser


def main() -> int:
    args = build_parser().parse_args()
    runtime = detect_openhands_runtime()
    if not runtime["available"]:
        print(json.dumps({"ok": False, "runtime": runtime}, ensure_ascii=False, indent=2))
        return 2

    workspace = Path(args.workspace).expanduser().resolve()
    state_dir = (
        Path(args.state_dir).expanduser().resolve()
        if args.state_dir
        else namespace_home(args.namespace) / "agent-runs" / args.session_id
    )
    api_key = os.environ.get(args.api_key_env, "")
    service = MemGuardService(namespace=args.namespace)
    try:
        runner = OpenHandsMemoryRunner(
            service=service,
            session_id=args.session_id,
            workspace=workspace,
            state_dir=state_dir,
            project_root=args.project_root or str(workspace),
            task_type=args.task_type,
            model=args.model,
            api_key=api_key,
            base_url=args.base_url,
            tool_profile=args.tool_profile,
        )
        if args.dry_init:
            workspace.mkdir(parents=True, exist_ok=True)
            bundle = runner.prepare_context(args.task or "OpenHands 运行环境初始化检查")
            conversation = create_openhands_conversation(
                callback=runner.on_event,
                workspace=workspace,
                state_dir=state_dir,
                context_file=Path(str(bundle["context_file"])),
                session_id=args.session_id,
                model=args.model,
                api_key=api_key,
                base_url=args.base_url,
                tool_profile=args.tool_profile,
            )
            try:
                tools = [str(tool.name) for tool in conversation.agent.tools]
                result = {
                    "ok": True,
                    "mode": "dry_init",
                    "runtime": runtime,
                    "session_id": args.session_id,
                    "trace_id": bundle["trace_id"],
                    "workspace": str(workspace),
                    "state_dir": str(state_dir),
                    "tools": tools,
                    "model": args.model,
                    "api_key_configured": bool(api_key),
                }
            finally:
                conversation.close()
        else:
            if not args.task.strip():
                raise ValueError("--task is required unless --dry-init is used")
            result = runner.run(args.task)
            result["runtime"] = runtime
            result["model"] = args.model
            result["api_key_configured"] = bool(api_key)
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return 0 if result.get("ok") else 1
    finally:
        service.close()


if __name__ == "__main__":
    raise SystemExit(main())
