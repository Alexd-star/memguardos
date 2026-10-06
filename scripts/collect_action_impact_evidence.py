"""Collect deterministic evidence for memory-conditioned action quality."""

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


def _outcome(record, index: int, result: str) -> dict[str, object]:
    return {
        "idempotency_key": "impact-evidence-%s-%s" % (record.memory_id, index),
        "memory_id": record.memory_id,
        "version_id": record.version.version_id,
        "decision": "redirect",
        "outcome": result,
        "adherence": "followed" if result == "success" else "violated",
        "tool_name": "terminal",
        "command_hash": "redacted-%s" % index,
        "agent_name": "evidence-agent",
        "session_id": "action-impact-session",
        "project_root": "/tmp/memguardos-demo-project",
        "task_type": "code_search",
        "context_fingerprint": "impact-context-%s" % index,
        "observed_at": "2026-10-07T12:00:%02d+00:00" % index,
    }


def collect() -> dict[str, object]:
    with tempfile.TemporaryDirectory(prefix="memguardos-action-impact-") as temp_dir:
        root = Path(temp_dir)
        service = MemGuardService(
            root / "memory.segment", root / "meta.sqlite3", namespace="action-impact-evidence"
        )
        useful = service.ingest_text(
            "搜索项目代码时优先使用 rg。", source_kind="manual", tier="long_term"
        )["record"]
        peer = service.ingest_text(
            "项目构建过程使用稳定的终端工具。", source_kind="manual", tier="long_term"
        )["record"]
        service.meta.record_agent_action_outcomes(
            [_outcome(useful, index, "success") for index in range(1, 11)]
            + [_outcome(peer, index, "failure") for index in range(11, 16)]
        )
        report = service.agent_action_impact_report(
            agent_name="evidence-agent", task_type="code_search"
        )
        useful_row = next(
            row for row in report["records"] if row["memory_id"] == useful.memory_id
        )
        useful_metrics = useful_row["metrics"]
        payload = {
            "schema": "memguardos-action-impact-evidence-v1",
            "revision": revision(),
            "platform": platform.platform(),
            "python": sys.version.split()[0],
            "report_schema": report["schema"],
            "summary": report["summary"],
            "preferred_memory": {
                "memory_id": useful.memory_id,
                "executed_count": useful_metrics["executed_count"],
                "success_rate": useful_metrics["success_rate"],
                "wilson_success_lower_bound": useful_metrics["wilson_success_lower_bound"],
                "observed_success_lift": useful_metrics["observed_success_lift"],
                "recommendation": useful_metrics["recommendation"],
            },
            "checks": {
                "report_is_versioned": report["schema"] == "memguardos-agent-action-impact-report-v1",
                "executed_denominator_is_explicit": "success + failure + deviation" in report["method"]["executed_denominator"],
                "small_sample_gate_is_conservative": useful_metrics["wilson_success_lower_bound"] < 1.0,
                "positive_observational_lift_is_visible": useful_metrics["observed_success_lift"] > 0,
                "successful_memory_passes_quality_gate": useful_metrics["quality_gate"] == "pass",
                "no_command_plaintext": report["privacy"].startswith("不包含命令正文"),
                "report_does_not_auto_mutate": "report-only" in report["method"]["decision"],
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

