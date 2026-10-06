from memguard.api.server import handle_request
from memguard.service import MemGuardService


def _session_service(tmp_path):
    service = MemGuardService(tmp_path / "memory.segment", tmp_path / "meta.sqlite3")
    preference = service.ingest_text(
        "以后代码搜索优先使用 rg。",
        source_kind="manual",
        tier="session",
        retention_session_id="session-1",
    )["record"]
    knowledge = service.ingest_text(
        "项目服务重启前先查看 journalctl。",
        source_kind="manual",
        tier="session",
        retention_session_id="session-1",
    )["record"]
    return service, preference, knowledge


def test_consolidation_plan_separates_explicit_preference_and_unreinforced_knowledge(tmp_path):
    service, preference, knowledge = _session_service(tmp_path)
    plan = service.build_consolidation_plan("session-1")
    by_id = {item["memory_id"]: item for item in plan["candidates"]}
    assert by_id[preference.memory_id]["recommended_action"] == "promote"
    assert by_id[knowledge.memory_id]["recommended_action"] == "retain_session"
    assert plan["expected_versions"][preference.memory_id] == preference.version.version_id
    assert plan["summary"]["promote_count"] == 1


def test_consolidation_requires_confirmation_and_promotes_only_selected_ids(tmp_path):
    service, preference, _ = _session_service(tmp_path)
    plan = service.build_consolidation_plan("session-1")
    preview = service.apply_consolidation_plan(plan)
    assert preview["reason"] == "confirmation_required"
    applied = service.apply_consolidation_plan(plan, confirm=True)
    assert applied["applied"] is True
    assert [item["memory_id"] for item in applied["promoted"]] == [preference.memory_id]
    assert service.store.get_latest(preference.memory_id).tier == "long_term"


def test_consolidation_rejects_stale_plan(tmp_path):
    service, preference, _ = _session_service(tmp_path)
    plan = service.build_consolidation_plan("session-1")
    service.promote_memory(preference.memory_id, "long_term", session_id="session-1")
    result = service.apply_consolidation_plan(plan, confirm=True)
    assert result["applied"] is False
    assert result["reason"] == "stale_consolidation_plan"


def test_consolidation_plan_api_exposes_versioned_review_contract(tmp_path):
    service, _, _ = _session_service(tmp_path)
    response = handle_request(
        service,
        {"command": "consolidation_plan", "session_id": "session-1", "limit": 10},
    )
    assert response["ok"] is True
    assert response["result"]["schema"] == "memguardos-consolidation-plan-v1"
    assert response["result"]["plan_id"]
    assert response["result"]["expected_versions"]


def test_consolidation_rejects_tampered_plan_and_rechecks_policy(tmp_path):
    service, preference, _ = _session_service(tmp_path)
    plan = service.build_consolidation_plan("session-1")
    next(item for item in plan["candidates"] if item["memory_id"] == preference.memory_id)["recommended_action"] = "retain_session"
    assert service.apply_consolidation_plan(plan, confirm=True)["reason"] == "plan_integrity_failed"

    fresh_plan = service.build_consolidation_plan("session-1")
    service.update_memory(
        preference.memory_id,
        "以后代码搜索优先使用 grep。",
        reason="policy recheck",
    )
    result = service.apply_consolidation_plan(fresh_plan, confirm=True)
    assert result["applied"] is False
    assert result["reason"] == "stale_consolidation_plan"
