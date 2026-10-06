"""Collect deterministic evidence for value/risk/byte-aware memory curation."""

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
    with tempfile.TemporaryDirectory(prefix="memguardos-memory-budget-") as temp_dir:
        root = Path(temp_dir)
        service = MemGuardService(root / "memory.segment", root / "meta.sqlite3", namespace="memory-budget-evidence")
        protected = service.ingest_text(
            "以后回答系统问题时先给出可复制命令。", source_kind="manual", tier="long_term"
        )["record"]
        cold = service.ingest_text(
            "本次演示产生的临时构建缓存位于 /srv/demo/cache。", source_kind="manual", tier="long_term"
        )["record"]
        useful = service.ingest_text(
            "openKylin 的 MemGuardOS 服务由 systemd 管理，Agent 通过统一接口获取经过治理的上下文。",
            source_kind="manual", tier="long_term",
        )["record"]
        service.meta.conn.execute(
            "UPDATE memory SET updated_at = ?, last_hit_at = '', hit_count = 0 WHERE memory_id IN (?, ?)",
            (OLD, cold.memory_id, protected.memory_id),
        )
        service.meta.record_memory_hits([useful.memory_id, useful.memory_id])
        service.meta.conn.commit()

        first = service.build_memory_budget_plan(10**9, as_of=AS_OF)
        protected_bytes = next(
            item["storage_bytes"]
            for item in first["candidates"]
            if item["memory_id"] == protected.memory_id
        )
        plan = service.build_memory_budget_plan(protected_bytes, as_of=AS_OF)
        selected = plan["demotion_ids"][:1]
        preview = service.apply_memory_budget_plan(
            plan,
            selected_memory_ids=selected,
            target_session_id="budget-evidence-session",
        )
        applied = service.apply_memory_budget_plan(
            plan,
            selected_memory_ids=selected,
            target_session_id="budget-evidence-session",
            confirm=True,
        )
        protected_current = service.store.get_latest(protected.memory_id)
        demoted_current = service.store.get_latest(selected[0]) if selected else None
        payload = {
            "schema": "memguardos-memory-budget-evidence-v1",
            "revision": revision(),
            "platform": platform.platform(),
            "python": sys.version.split()[0],
            "plan": {
                "plan_id": plan.get("plan_id"),
                "summary": plan.get("summary"),
                "selected_memory_ids": selected,
                "protected_memory_id": protected.memory_id,
                "candidate_value_scores": [
                    {
                        "memory_id": item.get("memory_id"),
                        "value_score": item.get("value_score"),
                        "risk_score": item.get("risk_score"),
                        "value_per_byte": item.get("value_per_byte"),
                        "recommended_action": item.get("recommended_action"),
                    }
                    for item in plan.get("candidates", [])
                ],
            },
            "apply": {
                "preview_reason": preview.get("reason"),
                "applied": applied.get("applied"),
                "bytes_reclaimed": applied.get("bytes_reclaimed", 0),
                "protected_tier": protected_current.tier if protected_current else "",
                "demoted_tier": demoted_current.tier if demoted_current else "",
            },
            "checks": {
                "plan_has_value_risk_byte_scores": all(
                    key in item for item in plan.get("candidates", [])
                    for key in ("value_score", "risk_score", "storage_bytes", "value_per_byte")
                ),
                "protected_preference_retained": (
                    protected_current is not None and protected_current.tier == "long_term"
                ),
                "preview_requires_confirmation": preview.get("reason") == "confirmation_required",
                "approved_action_is_reversible_demotion": (
                    applied.get("applied") is True
                    and demoted_current is not None
                    and demoted_current.tier == "session"
                ),
                "budget_plan_reaches_target": bool(plan.get("summary", {}).get("target_reached")),
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
