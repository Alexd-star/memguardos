"""Collect a reproducible, isolated evidence record for session consolidation.

The collector never touches the operator's real MEMGUARD_HOME.  It is intended
to run on the development machine or on openKylin after deployment and emits
only structured results that can be attached to the evidence bundle.
"""

from __future__ import annotations

import argparse
import json
import platform
import subprocess
import sys
import tempfile
from pathlib import Path

from memguard.service import MemGuardService


def git_revision() -> str:
    try:
        return subprocess.check_output(
            ["git", "rev-parse", "HEAD"], text=True, stderr=subprocess.DEVNULL
        ).strip()
    except (OSError, subprocess.CalledProcessError):
        return "unknown"


def collect() -> dict[str, object]:
    with tempfile.TemporaryDirectory(prefix="memguardos-consolidation-") as temp_dir:
        root = Path(temp_dir)
        service = MemGuardService(root / "memory.segment", root / "meta.sqlite3", namespace="consolidation-evidence")
        preference = service.ingest_text(
            "以后搜索项目代码优先使用 rg，并先给出可复制命令。",
            source_kind="manual",
            tier="session",
            retention_session_id="demo-session",
        )["record"]
        knowledge = service.ingest_text(
            "项目演示结束后再清理临时输出目录。",
            source_kind="manual",
            tier="session",
            retention_session_id="demo-session",
        )["record"]
        working = service.ingest_text(
            "本轮任务的临时文件位于 output/demo。",
            source_kind="manual",
            tier="working",
            retention_session_id="demo-session",
        )["record"]

        plan = service.build_consolidation_plan("demo-session")
        by_id = {item["memory_id"]: item for item in plan["candidates"]}
        preview = service.apply_consolidation_plan(plan)
        applied = service.apply_consolidation_plan(
            plan,
            selected_memory_ids=[preference.memory_id],
            confirm=True,
        )
        stale = service.apply_consolidation_plan(plan, confirm=True)

        latest_preference = service.store.get_latest(preference.memory_id)
        latest_knowledge = service.store.get_latest(knowledge.memory_id)
        latest_working = service.store.get_latest(working.memory_id)
        payload = {
            "schema": "memguardos-consolidation-evidence-v1",
            "revision": git_revision(),
            "platform": platform.platform(),
            "python": sys.version.split()[0],
            "plan": {
                "plan_id": plan.get("plan_id"),
                "summary": plan.get("summary"),
                "candidates": [
                    {
                        "memory_id": item["memory_id"],
                        "recommended_action": item["recommended_action"],
                        "reason": item["reason"],
                        "version_id": item["version_id"],
                    }
                    for item in plan["candidates"]
                ],
            },
            "checks": {
                "explicit_preference_promote": by_id[preference.memory_id]["recommended_action"] == "promote",
                "unreinforced_knowledge_retained": by_id[knowledge.memory_id]["recommended_action"] == "retain_session",
                "working_memory_reviewed": by_id[working.memory_id]["recommended_action"] == "evict_review",
                "preview_requires_confirmation": preview.get("reason") == "confirmation_required",
                "selected_promotion_applied": applied.get("applied") is True
                and latest_preference is not None
                and latest_preference.tier == "long_term",
                "unselected_knowledge_preserved": latest_knowledge is not None
                and latest_knowledge.tier == "session",
                "working_memory_preserved_until_review": latest_working is not None
                and latest_working.tier == "working",
                "old_plan_rejected_by_cas": stale.get("reason") == "stale_consolidation_plan",
            },
            "results": {
                "preview": preview,
                "applied": applied,
                "stale_plan": stale,
            },
        }
        payload["passed"] = all(payload["checks"].values())
        service.close()
        return payload


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, help="write JSON evidence to this path")
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
