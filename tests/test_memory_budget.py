from memguard.api.server import handle_request
from memguard.service import MemGuardService


OLD = "2025-01-01T00:00:00+00:00"
AS_OF = "2026-01-31T00:00:00+00:00"


def _long_term(service, text):
    record = service.ingest_text(text, source_kind="manual", tier="long_term")["record"]
    service.meta.conn.execute(
        "UPDATE memory SET updated_at = ?, last_hit_at = '', hit_count = 0 WHERE memory_id = ?",
        (OLD, record.memory_id),
    )
    service.meta.conn.commit()
    return record


def test_budget_plan_protects_explicit_memory_and_selects_low_value_bytes(tmp_path):
    service = MemGuardService(tmp_path / "memory.segment", tmp_path / "meta.sqlite3")
    protected = _long_term(service, "以后回答系统问题时先给出可复制命令。")
    low = _long_term(service, "项目蓝河的临时构建目录位于 /srv/blue/build。")
    high = service.ingest_text(
        "openKylin 的 MemGuardOS 服务使用 systemd 管理，启动后由 Agent 通过统一接口检索上下文。",
        source_kind="manual",
        tier="long_term",
    )["record"]
    service.meta.record_memory_hits([high.memory_id, high.memory_id, high.memory_id])
    first = service.build_memory_budget_plan(10**9, as_of=AS_OF)
    low_bytes = next(item["storage_bytes"] for item in first["candidates"] if item["memory_id"] == low.memory_id)
    target = first["summary"]["current_bytes"] - low_bytes
    plan = service.build_memory_budget_plan(target, as_of=AS_OF)
    by_id = {item["memory_id"]: item for item in plan["candidates"]}

    assert by_id[protected.memory_id]["recommended_action"] == "retain_protected"
    assert plan["summary"]["protected_count"] >= 1
    assert low.memory_id in plan["demotion_ids"]
    assert plan["summary"]["target_reached"] is True
    assert plan["summary"]["projected_bytes"] <= target


def test_budget_apply_is_confirmed_reversible_and_rejects_stale_plan(tmp_path):
    service = MemGuardService(tmp_path / "memory.segment", tmp_path / "meta.sqlite3")
    low = _long_term(service, "一次性演示产生的临时目录是 /srv/demo/cache。")
    _long_term(service, "openKylin 服务由 systemd 管理，记忆服务随系统启动。")
    first = service.build_memory_budget_plan(10**9, as_of=AS_OF)
    low_bytes = next(item["storage_bytes"] for item in first["candidates"] if item["memory_id"] == low.memory_id)
    plan = service.build_memory_budget_plan(first["summary"]["current_bytes"] - low_bytes, as_of=AS_OF)
    selected_id = plan["demotion_ids"][0]

    preview = service.apply_memory_budget_plan(
        plan, target_session_id="budget-review", selected_memory_ids=[selected_id]
    )
    assert preview["reason"] == "confirmation_required"
    applied = service.apply_memory_budget_plan(
        plan, target_session_id="budget-review", selected_memory_ids=[selected_id], confirm=True
    )
    assert applied["applied"] is True
    current = service.store.get_latest(selected_id)
    assert current.tier == "session"
    assert current.retention_session_id == "budget-review"
    assert applied["bytes_reclaimed"] > 0

    second = service.build_memory_budget_plan(128, as_of=AS_OF)
    stale_id = second["demotion_ids"][0]
    service.update_memory(stale_id, service.store.get_latest(stale_id).content + " 已更新。", reason="new evidence")
    stale = service.apply_memory_budget_plan(
        second, target_session_id="budget-review", selected_memory_ids=[stale_id], confirm=True
    )
    assert stale["applied"] is False
    assert stale["reason"] == "stale_memory_budget_plan"


def test_budget_api_exposes_plan_and_apply_contract(tmp_path):
    service = MemGuardService(tmp_path / "memory.segment", tmp_path / "meta.sqlite3")
    _long_term(service, "项目 Alpha 的构建目录位于 /srv/alpha/build。")
    response = handle_request(
        service,
        {"command": "budget_plan", "target_bytes": 1024, "as_of": AS_OF, "limit": 10},
    )
    assert response["ok"] is True
    assert response["result"]["schema"] == "memguardos-memory-budget-plan-v1"
    assert response["result"]["summary"]["current_bytes"] > 0
