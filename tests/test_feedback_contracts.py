from memguard.feedback import (
    ForgetVerificationReport,
    MemoryChangeSummary,
    PreferenceEffectProof,
    SensitiveBlockNotice,
    build_forget_verification_report,
    build_memory_change_summary,
    build_preference_effect_proof,
    build_sensitive_block_notice,
    is_urgent_notification,
    notifications_from_audit,
)
from memguard.api.server import handle_request
from memguard.web.dashboard import _dashboard_html
from memguard.service import MemGuardService


def test_memory_change_summary_explains_stored_record_and_next_actions():
    service = MemGuardService(store_path=None, db_path=None)
    try:
        result = service.ingest_text(
            "以后查代码优先使用 rg。",
            source_kind="agent",
            source_uri="session-1",
        )
        summary = build_memory_change_summary(
            result,
            namespace=service.namespace,
            source="agent",
            session_id="session-1",
        )
    finally:
        service.close()

    payload = summary.to_dict()
    assert isinstance(summary, MemoryChangeSummary)
    assert payload["schema"] == "memguardos-memory-change-summary-v1"
    assert payload["new_memories"][0]["memory_id"] == result["record"].memory_id
    assert payload["new_memories"][0]["summary_for_user"]
    assert payload["new_memories"][0]["why_extracted"]
    assert "test_effect" in payload["new_memories"][0]["actions"]


def test_sensitive_block_notice_proves_not_stored_or_entered_rag():
    notice = build_sensitive_block_notice(
        {
            "stored": False,
            "reason": "sensitive_block",
            "sensitivity": {"level": "high", "labels": ["api_key"]},
        },
        source="peony",
        audit_event_id="audit-3",
    )

    assert isinstance(notice, SensitiveBlockNotice)
    payload = notice.to_dict()
    assert payload["action"] == "blocked"
    assert payload["stored"] is False
    assert payload["entered_rag"] is False
    assert "api_key" in payload["detected_labels"]
    assert "view_audit" in payload["next_actions"]


def test_preference_effect_proof_only_passes_for_observed_behavior():
    proof = build_preference_effect_proof(
        task="查找遗忘实现",
        agent_name="openhands",
        session_id="s1",
        stage="tool_selection",
        preference={
            "memory_id": "mem-1",
            "preference_topic": "code_search_tool",
            "preference_value": "rg",
            "preference_scope": "global",
        },
        actual_behavior={"tool_name": "terminal", "command": 'rg -n "forget" memguard'},
        source_event_ids=["evt-1"],
    )
    assert isinstance(proof, PreferenceEffectProof)
    assert proof.to_dict()["compliance"] == "passed"
    assert proof.to_dict()["injected"] is True

    unknown = build_preference_effect_proof(
        task="查找遗忘实现",
        agent_name="openhands",
        session_id="s1",
        stage="tool_selection",
        preference={"memory_id": "mem-1", "preference_topic": "code_search_tool", "preference_value": "rg"},
    )
    assert unknown.to_dict()["compliance"] == "not_applicable"


def test_forget_report_keeps_managed_steps_and_external_boundary():
    report = build_forget_verification_report(
        {
            "confirmed": True,
            "request": "忘掉 blue-river",
            "forgotten": ["mem-1"],
            "verification": [{"memory_id": "mem-1", "irreversible_ok": True}],
            "external_residue": {"requires_separate_cleanup": True},
        },
        request="忘掉 blue-river",
        dry_run=False,
    )
    assert isinstance(report, ForgetVerificationReport)
    payload = report.to_dict()
    assert payload["confirmed"] is True
    assert payload["forgotten_memory_ids"] == ["mem-1"]
    assert {step["name"] for step in payload["verification_steps"]} >= {
        "main_record",
        "sqlite_fts",
        "agent_context_absence",
        "audit_chain",
    }
    assert payload["external_residue"]["filesystem_backups"] == "not_managed"


def test_notifications_filter_to_user_relevant_events():
    rows = [
        {"audit_id": 3, "event_type": "sensitive_block", "detail": '{"labels":["api_key"]}', "created_at": "now"},
        {"audit_id": 2, "event_type": "search", "detail": "{}", "created_at": "now"},
        {"audit_id": 1, "event_type": "forget", "detail": '{"forgotten":["mem-1"]}', "created_at": "now"},
    ]
    notifications = notifications_from_audit(rows)
    assert [item["kind"] for item in notifications] == ["sensitive_block", "forget"]
    assert notifications[0]["level"] == "critical"
    assert notifications[1]["level"] == "info"


def test_notification_policy_only_prompts_for_high_signal_events():
    assert is_urgent_notification({"level": "critical", "kind": "sensitive_block"}) is True
    assert is_urgent_notification({"level": "info", "kind": "forget"}) is True
    assert is_urgent_notification({"level": "info", "kind": "agent_activity"}) is True
    assert is_urgent_notification({"level": "info", "kind": "memory_added"}) is False
    assert is_urgent_notification({"level": "warning", "kind": "sdk_status"}) is False


def test_denied_agent_policy_event_becomes_high_risk_notification():
    rows = [
        {
            "audit_id": 9,
            "event_type": "interaction_ingest",
            "detail": '{"event_type":"policy_decision","policy_decision":"deny","policy_category":"high_risk"}',
        }
    ]
    notifications = notifications_from_audit(rows)
    assert notifications[0]["kind"] == "tool_block"
    assert notifications[0]["level"] == "critical"


def test_service_feedback_api_and_replay_return_product_contracts(tmp_path):
    service = MemGuardService(store_path=tmp_path / "memory.segment", db_path=tmp_path / "memguard.sqlite3")
    try:
        response = handle_request(service, {"command": "feedback_home", "limit": 5})
        assert response["ok"] is True
        assert response["result"]["schema"] == "memguardos-feedback-home-v1"

        replay = handle_request(
            service,
            {"command": "agent_replay", "mode": "replay", "task": "查找记忆服务"},
        )
        assert replay["ok"] is True
        assert replay["result"]["execution_mode"] == "replay"
        assert replay["result"]["truthful_note"]
        assert "preference_effect_proofs" in replay["result"]
        assert replay["result"]["memory_change_summary"]["audit_event_ids"]
    finally:
        service.close()


def test_dashboard_exposes_feedback_surfaces_and_safe_navigation():
    page = _dashboard_html()
    for marker in (
        "Agent 工作台",
        "偏好生效",
        "记忆详情",
        "遗忘验证",
        "通知中心",
        "preferenceProofs",
        "verification_steps",
        "runAgentTask",
        "loadMemoryDetail",
        "loadNotifications",
        "openMemoryDetail",
        "本地回放（可复现）",
        "window.location.hash",
        "hashchange",
    ):
        assert marker in page
