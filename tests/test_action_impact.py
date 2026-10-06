from memguard.api.server import handle_request
from memguard.service import MemGuardService


def _service(tmp_path):
    return MemGuardService(tmp_path / "memory.segment", tmp_path / "meta.sqlite3")


def _record(service, text):
    return service.ingest_text(text, source_kind="manual", tier="long_term")["record"]


def _outcome(memory, suffix, outcome, *, task_type="code_search", project_root="/tmp/project"):
    return {
        "idempotency_key": "impact-%s-%s" % (memory.memory_id, suffix),
        "memory_id": memory.memory_id,
        "version_id": memory.version.version_id,
        "decision": "redirect",
        "outcome": outcome,
        "adherence": "followed" if outcome == "success" else "violated" if outcome == "deviation" else "not_applicable",
        "tool_name": "terminal",
        "command_hash": "hash-%s" % suffix,
        "agent_name": "openhands",
        "session_id": "impact-session",
        "project_root": project_root,
        "task_type": task_type,
        "context_fingerprint": "context-%s" % suffix,
        "observed_at": "2026-10-07T10:00:%02d+00:00" % int(suffix),
    }


def test_action_impact_report_is_conservative_and_cohort_aware(tmp_path):
    service = _service(tmp_path)
    preferred = _record(service, "搜索代码时优先使用 rg。")
    peer = _record(service, "项目构建流程使用现有工具。")
    service.meta.record_agent_action_outcomes([
        _outcome(preferred, "1", "success"),
        _outcome(preferred, "2", "success"),
        _outcome(preferred, "3", "success"),
        _outcome(peer, "4", "failure"),
        _outcome(peer, "5", "failure"),
        _outcome(peer, "6", "deviation"),
    ])

    report = service.agent_action_impact_report(agent_name="openhands", task_type="code_search")
    assert report["schema"] == "memguardos-agent-action-impact-report-v1"
    by_id = {item["memory_id"]: item for item in report["records"]}
    preferred_metrics = by_id[preferred.memory_id]["metrics"]
    assert preferred_metrics["executed_count"] == 3
    assert preferred_metrics["recommendation"] in {"retain_observe", "retain_reinforced"}
    assert preferred_metrics["cohort_baseline_success_rate"] == 0.0
    assert preferred_metrics["observed_success_lift"] == 1.0
    assert report["summary"]["executed_count"] == 6
    assert "observational" in report["method"]["baseline"]


def test_action_impact_report_handles_empty_memory_and_api_contract(tmp_path):
    service = _service(tmp_path)
    memory = _record(service, "仅有一条尚未执行的临时偏好。")
    report = service.agent_action_impact_report(memory_id=memory.memory_id)
    assert report["records"][0]["metrics"]["recommendation"] == "observe"
    assert report["records"][0]["metrics"]["executed_count"] == 0
    response = handle_request(service, {"command": "action_impact_report", "memory_id": memory.memory_id})
    assert response["ok"] is True
    assert response["result"]["schema"] == "memguardos-agent-action-impact-report-v1"

