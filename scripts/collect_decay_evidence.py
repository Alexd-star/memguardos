"""Collect isolated evidence for reversible long-term-memory decay."""

from __future__ import annotations

import argparse
import json
import os
import platform
import subprocess
import sys
import tempfile
from pathlib import Path

from memguard.service import MemGuardService


AS_OF = "2026-01-31T00:00:00+00:00"
OLD = "2025-01-01T00:00:00+00:00"


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


def collect() -> dict[str, object]:
    with tempfile.TemporaryDirectory(prefix="memguardos-decay-") as temp_dir:
        root = Path(temp_dir)
        service = MemGuardService(root / "memory.segment", root / "meta.sqlite3", namespace="decay-evidence")

        def old_memory(text: str):
            record = service.ingest_text(text, source_kind="manual", tier="long_term")["record"]
            service.meta.conn.execute(
                "UPDATE memory SET updated_at = ?, last_hit_at = '', hit_count = 0 WHERE memory_id = ?",
                (OLD, record.memory_id),
            )
            service.meta.conn.commit()
            return record

        cold = old_memory("项目 Alpha 的构建目录位于 /srv/alpha/build。")
        explicit = old_memory("以后回答系统问题时先给出可复制命令。")
        reinforced = old_memory("openKylin 服务使用 systemd 管理。")
        service.meta.record_memory_hits([reinforced.memory_id])

        plan = service.build_decay_plan(as_of=AS_OF, max_age_days=30, score_threshold=0.35)
        by_id = {item["memory_id"]: item for item in plan["candidates"]}
        preview = service.apply_decay_plan(
            plan,
            selected_memory_ids=[cold.memory_id],
            target_session_id="maintenance-2026-01",
        )
        applied = service.apply_decay_plan(
            plan,
            selected_memory_ids=[cold.memory_id],
            target_session_id="maintenance-2026-01",
            confirm=True,
        )
        stale = service.apply_decay_plan(
            plan,
            selected_memory_ids=[cold.memory_id],
            target_session_id="maintenance-2026-01",
            confirm=True,
        )
        latest_cold = service.store.get_latest(cold.memory_id)
        latest_explicit = service.store.get_latest(explicit.memory_id)
        latest_reinforced = service.store.get_latest(reinforced.memory_id)
        payload = {
            "schema": "memguardos-decay-evidence-v1",
            "revision": revision(),
            "platform": platform.platform(),
            "python": sys.version.split()[0],
            "plan": {
                "plan_id": plan.get("plan_id"),
                "summary": plan.get("summary"),
                "candidates": [
                    {
                        "memory_id": item["memory_id"],
                        "recommended_action": item["recommended_action"],
                        "decay_score": item["decay_score"],
                        "age_days": item["age_days"],
                        "reason": item["reason"],
                    }
                    for item in plan["candidates"]
                ],
            },
            "checks": {
                "cold_memory_demoted": by_id[cold.memory_id]["recommended_action"] == "demote_to_session",
                "explicit_preference_protected": by_id[explicit.memory_id]["recommended_action"] == "retain_explicit_preference",
                "reinforced_memory_protected": by_id[reinforced.memory_id]["recommended_action"] == "retain_reinforced",
                "preview_requires_confirmation": preview.get("reason") == "confirmation_required",
                "demotion_is_reversible": applied.get("applied") is True
                and latest_cold is not None
                and latest_cold.tier == "session"
                and latest_cold.retention_session_id == "maintenance-2026-01",
                "protected_records_remain_long_term": latest_explicit is not None
                and latest_explicit.tier == "long_term"
                and latest_reinforced is not None
                and latest_reinforced.tier == "long_term",
                "old_plan_rejected_by_cas": stale.get("reason") == "stale_decay_plan",
            },
            "results": {"preview": preview, "applied": applied, "stale_plan": stale},
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
