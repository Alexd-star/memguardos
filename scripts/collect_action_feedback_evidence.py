"""Collect isolated evidence for the Agent action outcome feedback ledger."""

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
        return subprocess.check_output(["git", "rev-parse", "HEAD"], text=True, stderr=subprocess.DEVNULL).strip()
    except (OSError, subprocess.CalledProcessError):
        return "unknown"


def collect() -> dict[str, object]:
    with tempfile.TemporaryDirectory(prefix="memguardos-action-feedback-") as temp_dir:
        root = Path(temp_dir)
        project = str(root / "project")
        service = MemGuardService(root / "memory.segment", root / "meta.sqlite3", namespace="action-feedback-evidence")
        service.ingest_interaction(AgentInteractionEvent(
            agent_name="evidence-agent", agent_version="1.0", session_id="feedback-session",
            turn_id="turn-1", sequence=1, event_type="user_message", role="user",
            content="以后搜索项目代码优先使用 rg。",
            project_root=project, task_type="code_search", source_uri="evidence://action-feedback/1",
            event_id="event-action-feedback-1",
        ))
        redirect = service.preflight_agent_action(
            "搜索项目代码", tool_name="terminal", tool_input={"command": "grep -n target ."},
            agent_name="evidence-agent", session_id="feedback-session", project_root=project,
            task_type="code_search",
        )
        followed = service.record_agent_action_outcome(
            redirect, tool_input={"command": "rg -n target ."}, exit_code=0,
            idempotency_key="evidence-followed-1",
        )
        duplicate = service.record_agent_action_outcome(
            redirect, tool_input={"command": "rg -n target ."}, exit_code=0,
            idempotency_key="evidence-followed-1",
        )
        deviated = service.record_agent_action_outcome(
            redirect, tool_input={"command": "grep -n target ."}, exit_code=0,
            idempotency_key="evidence-deviated-1",
        )
        memory_id = str((redirect.get("evidence") or [{}])[0].get("memory_id") or "")
        stats = service.meta.action_outcome_stats(memory_id)
        service.update_memory(memory_id, "以后搜索项目代码优先使用 rg，并先检查工作区。")
        stale = service.record_agent_action_outcome(
            redirect, tool_input={"command": "rg -n target ."}, exit_code=0,
            idempotency_key="evidence-stale-1",
        )
        payload = {
            "schema": "memguardos-action-feedback-evidence-v1",
            "revision": revision(),
            "platform": platform.platform(),
            "python": sys.version.split()[0],
            "preflight": {
                "decision": redirect.get("decision"),
                "policy_category": redirect.get("policy_category"),
                "evidence_count": len(redirect.get("evidence") or []),
            },
            "outcomes": {
                "followed": {"outcome": followed.get("outcome"), "stored": followed.get("stored", 0)},
                "deviated": {"outcome": deviated.get("outcome"), "stored": deviated.get("stored", 0)},
                "duplicate": {"stored": duplicate.get("stored", 0), "duplicates": duplicate.get("duplicates", 0)},
                "stats": stats,
                "stale_receipt": {"ok": stale.get("ok"), "reason": stale.get("reason")},
            },
            "checks": {
                "preflight_redirects_conflict": redirect.get("decision") == "redirect",
                "successful_action_recorded": followed.get("outcome") == "success" and followed.get("stored", 0) > 0,
                "deviation_not_success": deviated.get("outcome") == "deviation" and not deviated.get("reinforced_memory_ids"),
                "retry_is_idempotent": duplicate.get("stored") == 0 and duplicate.get("duplicates", 0) > 0,
                "stats_count_both_outcomes": stats.get("success_count") == 1 and stats.get("deviation_count") == 1,
                "stale_receipt_rejected": stale.get("reason") == "stale_memory_context" and stale.get("stored") == 0,
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
    rendered = json.dumps(payload, ensure_ascii=False, indent=2) + "\n"
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(rendered, encoding="utf-8")
    print(rendered, end="")
    return 0 if payload["passed"] else 2


if __name__ == "__main__":
    raise SystemExit(main())
