#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import sys

from memguard.integrations.events import AgentInteractionEvent
from memguard.integrations.openhands_runner import OpenHandsMemoryRunner
from memguard.models import new_id
from memguard.service import MemGuardService


SCENARIOS = {
    "grep": "以后在项目中搜索代码必须使用 grep。回答时先给命令，再解释结果。",
    "rg": "以后在项目中搜索代码必须使用 rg。回答时先给命令，再解释结果。",
    "neutral": "",
}


def _setting(env_name: str, file_name: str, *, secret: bool = False) -> str:
    value = os.environ.get(env_name, "").strip()
    if value:
        return value
    path = Path.home() / ".config" / "memguardos" / file_name
    if not path.is_file():
        return ""
    value = path.read_text(encoding="utf-8").strip()
    if secret and path.stat().st_mode & 0o077:
        raise PermissionError(f"{path} must not be readable by group or other users")
    return value


def _seed_preference(service: MemGuardService, scenario: str) -> None:
    content = SCENARIOS[scenario]
    if not content:
        return
    session_id = f"matrix-seed-{scenario}"
    service.ingest_interaction(
        AgentInteractionEvent(
            event_id=new_id("evt"),
            agent_name="openhands",
            agent_version="1.28.1",
            session_id=session_id,
            turn_id="turn-1",
            sequence=1,
            event_type="user_message",
            role="user",
            content=content,
            source_uri=f"openhands://{session_id}/user-preference",
        )
    )


def _run_scenario(
    scenario: str,
    *,
    workspace: Path,
    namespace_prefix: str,
    model: str,
    api_key: str,
    base_url: str,
    max_iterations: int,
) -> dict[str, object]:
    namespace = f"{namespace_prefix}-{scenario}"
    service = MemGuardService(namespace=namespace)
    session_id = f"matrix-run-{scenario}"
    try:
        _seed_preference(service, scenario)
        runner = OpenHandsMemoryRunner(
            service=service,
            session_id=session_id,
            workspace=workspace,
            state_dir=Path.home()
            / ".local"
            / "state"
            / "memguardos"
            / "agent-matrix"
            / namespace,
            project_root=str(workspace),
            task_type="code_assistance",
            model=model,
            api_key=api_key,
            base_url=base_url,
            max_iterations=max_iterations,
            tool_profile="terminal",
            allow_in_place_workspace=True,
        )
        result = runner.run(
            "请在当前项目中查找 forget_agent_interactions 的定义位置，并报告文件路径。"
        )
    finally:
        service.close()

    successful_commands = [
        str(item.get("command") or "")
        for item in result.get("execution_trace", [])
        if item.get("event_type") == "tool_call"
        and item.get("tool_name") == "terminal"
        and item.get("status") == "success"
    ]
    return {
        "scenario": scenario,
        "ok": bool(result.get("ok")),
        "task_completion": result.get("task_completion", {}),
        "adherence": result.get("adherence", {}),
        "hook_health": result.get("hook_health", {}),
        "successful_commands": successful_commands,
        "event_summary": result.get("event_summary", {}),
        "preference_effect_proofs": result.get("preference_effect_proofs", []),
    }


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="使用真实模型验证有 grep 偏好、有 rg 偏好和无偏好三类 Agent 场景"
    )
    parser.add_argument("--workspace", default=str(Path.cwd()))
    parser.add_argument("--namespace-prefix", default="real-agent-matrix")
    parser.add_argument("--scenarios", default="grep,rg,neutral")
    parser.add_argument("--max-iterations", type=int, default=6)
    return parser


def main() -> int:
    args = build_parser().parse_args()
    workspace = Path(args.workspace).expanduser().resolve()
    scenarios = [item.strip() for item in args.scenarios.split(",") if item.strip()]
    unknown = [item for item in scenarios if item not in SCENARIOS]
    if unknown:
        raise ValueError("unknown scenarios: %s" % ", ".join(unknown))

    model = _setting("OPENHANDS_MODEL", "openhands_model")
    api_key = _setting("OPENHANDS_API_KEY", "openhands_api_key", secret=True)
    base_url = _setting("OPENHANDS_BASE_URL", "openhands_base_url")
    if not model or not api_key:
        print(json.dumps({"ok": False, "reason": "model credentials are not configured"}))
        return 2

    results = []
    for scenario in scenarios:
        print(f"[real-agent-matrix] running {scenario}...", file=sys.stderr)
        try:
            results.append(
                _run_scenario(
                    scenario,
                    workspace=workspace,
                    namespace_prefix=args.namespace_prefix,
                    model=model,
                    api_key=api_key,
                    base_url=base_url,
                    max_iterations=args.max_iterations,
                )
            )
        except Exception as exc:
            results.append(
                {
                    "scenario": scenario,
                    "ok": False,
                    "error": f"{type(exc).__name__}: {exc}",
                }
            )
    payload = {"ok": all(item.get("ok") for item in results), "results": results}
    print(json.dumps(payload, ensure_ascii=False, indent=2))
    return 0 if payload["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
