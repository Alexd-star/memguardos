"""Collect isolated evidence for admission gating and action plans."""

from __future__ import annotations

import argparse
import json
import platform
import sys
import tempfile
from pathlib import Path

from memguard.service import MemGuardService


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", required=True)
    args = parser.parse_args()
    output = Path(args.output)
    output.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="memguardos-admission-") as root:
        base = Path(root)
        service = MemGuardService(
            store_path=base / "memory.mmap",
            db_path=base / "meta.sqlite3",
            namespace="admission-evidence",
        )
        try:
            explicit = service.preview_memory_admission(
                "以后搜索项目代码优先使用 rg。",
                source_kind="manual",
            )
            automatic = service.preview_memory_admission(
                "工具结果：本次搜索使用 rg 成功，后续可能复用。",
                source_kind="tool_result",
                session_id="evidence-session",
                project_root=str(base / "project"),
            )
            sensitive = service.preview_memory_admission(
                "请记住 password=AdmissionDemo-12345。",
                source_kind="manual",
            )
            before = len(service.meta.list_memories(include_deleted=True, governance_state=None))
            service.ingest_natural_memory("以后搜索项目代码优先使用 rg。", source_kind="user_cli")
            enforced = service.ingest_text(
                "工具结果：本次搜索使用 rg 成功，后续可能复用。",
                source_kind="tool_result",
                retention_session_id="enforced-evidence-session",
                enforce_admission=True,
            )
            plan = service.build_agent_action_plan(
                "查找 build_agent_context 的定义",
                tool_name="terminal",
                tool_input={"command": "grep -n build_agent_context ."},
                agent_name="evidence-agent",
                session_id="evidence-action",
            )
            after = len(service.meta.list_memories(include_deleted=True, governance_state=None))
            payload = {
                "schema": "memguardos-memory-admission-evidence-v1",
                "platform": platform.platform(),
                "python": sys.version.split()[0],
                "cases": {
                    "explicit_user_preference": explicit,
                    "automatic_tool_observation": automatic,
                    "high_risk_sensitive": sensitive,
                    "action_plan": plan,
                    "enforced_automatic_source": {
                        "recorded": bool(enforced.get("stored")),
                        "tier": getattr(enforced.get("record"), "tier", ""),
                        "admission": enforced.get("admission"),
                    },
                },
                "checks": {
                    "explicit_admit_long_term": explicit["admission"]["decision"] == "admit_long_term",
                    "automatic_retain_session": automatic["admission"]["decision"] == "retain_session",
                    "sensitive_denied": sensitive["admission"]["decision"] == "deny",
                    "preview_does_not_persist": explicit["persisted"] is False and before == 0,
                    "action_plan_redirects_grep": plan["execution_gate"] == "replace_then_execute",
                    "action_plan_has_versioned_evidence": bool(plan.get("evidence")),
                    "action_plan_requires_feedback": bool((plan.get("feedback") or {}).get("required")),
                    "enforced_automatic_source_session": bool(enforced.get("stored")) and getattr(enforced.get("record"), "tier", "") == "session",
                    "stored_count_after_enforced_ingest": after == 2,
                },
            }
        finally:
            service.close()
    payload["passed"] = all(payload["checks"].values())
    output.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({"output": str(output), "passed": payload["passed"], "checks": payload["checks"]}, ensure_ascii=False, indent=2))
    return 0 if payload["passed"] else 2


if __name__ == "__main__":
    raise SystemExit(main())
