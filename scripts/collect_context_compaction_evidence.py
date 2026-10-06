"""Collect isolated evidence for stage-aware Agent context representations."""

from __future__ import annotations

import argparse
import json
import os
import platform
import subprocess
import sys
import tempfile
from pathlib import Path

from memguard.integrations.events import AgentInteractionEvent
from memguard.service import MemGuardService


def revision() -> str:
    supplied = os.environ.get("MEMGUARD_SOURCE_REVISION", "").strip()
    if supplied:
        return supplied
    try:
        return subprocess.check_output(
            ["git", "rev-parse", "HEAD"], text=True, stderr=subprocess.DEVNULL
        ).strip()
    except (OSError, subprocess.CalledProcessError):
        return "unknown"


def preference_event() -> AgentInteractionEvent:
    return AgentInteractionEvent(
        agent_name="evidence-agent",
        agent_version="1.0",
        session_id="context-evidence-session",
        turn_id="turn-1",
        sequence=1,
        event_type="user_message",
        role="user",
        content="以后搜索项目代码优先使用 rg。回答时先给出可复制命令。",
        project_root="/tmp/context-evidence-project",
        task_type="code_search",
        source_uri="evidence://context-preference",
        event_id="event-context-preference",
    )


def collect() -> dict[str, object]:
    with tempfile.TemporaryDirectory(prefix="memguardos-context-") as temp_dir:
        root = Path(temp_dir)
        service = MemGuardService(
            root / "memory.segment", root / "meta.sqlite3", namespace="context-evidence"
        )
        service.ingest_text(
            "项目 Alpha 构建需要先完成依赖检查和测试，然后再生成发布包。",
            source_kind="manual",
            project_root="/tmp/context-evidence-project",
        )
        service.ingest_interaction(preference_event())
        planning = service.build_agent_context(
            "查找项目 Alpha 构建流程",
            stage="planning",
            agent_name="evidence-agent",
            session_id="context-evidence-session",
            project_root="/tmp/context-evidence-project",
            task_type="code_assistance",
            token_budget=500,
        )
        tool_selection = service.build_agent_context(
            "搜索项目代码",
            stage="tool_selection",
            agent_name="evidence-agent",
            session_id="context-evidence-session",
            project_root="/tmp/context-evidence-project",
            task_type="code_search",
            token_budget=500,
        )
        full = service.build_agent_context(
            "查找项目 Alpha 构建流程",
            stage="planning",
            agent_name="evidence-agent",
            session_id="context-evidence-session",
            project_root="/tmp/context-evidence-project",
            task_type="code_assistance",
            token_budget=1200,
            compaction="full",
        )
        planning_representations = {
            item.get("representation") for item in planning.get("memories") or []
        }
        payload = {
            "schema": "memguardos-context-compaction-evidence-v1",
            "revision": revision(),
            "platform": platform.platform(),
            "python": sys.version.split()[0],
            "stages": {
                "planning": {
                    "memory_count": planning.get("memory_count", 0),
                    "representations": planning.get("budget", {}).get("representation_counts", {}),
                    "estimated_saved_tokens": planning.get("budget", {}).get("estimated_saved_tokens", 0),
                    "receipt": planning.get("context_receipt", {}),
                },
                "tool_selection": {
                    "memory_count": tool_selection.get("memory_count", 0),
                    "representations": tool_selection.get("budget", {}).get("representation_counts", {}),
                    "directive_present": "rg" in tool_selection.get("context", ""),
                    "receipt": tool_selection.get("context_receipt", {}),
                },
                "full_review": {
                    "memory_count": full.get("memory_count", 0),
                    "representations": full.get("budget", {}).get("representation_counts", {}),
                },
            },
            "checks": {
                "planning_uses_compact_knowledge": "summary" in planning_representations,
                "tool_selection_preserves_preference_directive": "rg" in tool_selection.get("context", ""),
                "receipt_binds_representations": bool((planning.get("context_receipt") or {}).get("representations")),
                "full_review_available": "full" in (full.get("budget", {}).get("representation_counts", {}) or {}),
                "adaptive_saves_or_equal_tokens": planning.get("budget", {}).get("estimated_saved_tokens", -1) >= 0,
            },
        }
        payload["passed"] = all(payload["checks"].values())
        service.close()
        return payload


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    payload = collect()
    rendered = json.dumps(payload, ensure_ascii=False, indent=2, default=str) + "\n"
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(rendered, encoding="utf-8")
    print(rendered, end="")
    return 0 if payload["passed"] else 2


if __name__ == "__main__":
    raise SystemExit(main())
