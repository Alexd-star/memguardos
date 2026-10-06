from memguard.api.server import handle_request
from memguard.service import MemGuardService


AS_OF = "2026-01-31T00:00:00+00:00"
OLD = "2025-01-01T00:00:00+00:00"


def _old_long_term(service, text):
    record = service.ingest_text(text, source_kind="manual", tier="long_term")["record"]
    service.meta.conn.execute(
        "UPDATE memory SET updated_at = ?, last_hit_at = '', hit_count = 0 WHERE memory_id = ?",
        (OLD, record.memory_id),
    )
    service.meta.conn.commit()
    return record


def test_decay_plan_uses_importance_and_reinforcement_signals(tmp_path):
    service = MemGuardService(tmp_path / "memory.segment", tmp_path / "meta.sqlite3")
    cold = _old_long_term(service, "项目 Alpha 的构建目录位于 /srv/alpha/build。")
    explicit = _old_long_term(service, "以后回答系统问题时先给出可复制命令。")
    reinforced = _old_long_term(service, "openKylin 服务使用 systemd 管理。")
    service.meta.record_memory_hits([reinforced.memory_id])

    plan = service.build_decay_plan(as_of=AS_OF, max_age_days=30, score_threshold=0.35)
    by_id = {item["memory_id"]: item for item in plan["candidates"]}
    assert by_id[cold.memory_id]["recommended_action"] == "demote_to_session"
    assert by_id[explicit.memory_id]["recommended_action"] == "retain_explicit_preference"
    assert by_id[reinforced.memory_id]["recommended_action"] == "retain_reinforced"
    assert plan["summary"]["demote_count"] == 1


def test_decay_requires_confirmation_and_demotes_reversibly(tmp_path):
    service = MemGuardService(tmp_path / "memory.segment", tmp_path / "meta.sqlite3")
    cold = _old_long_term(service, "项目 Alpha 的构建目录位于 /srv/alpha/build。")
    plan = service.build_decay_plan(as_of=AS_OF)

    preview = service.apply_decay_plan(
        plan,
        selected_memory_ids=[cold.memory_id],
        target_session_id="maintenance-2026-01",
    )
    assert preview["reason"] == "confirmation_required"
    applied = service.apply_decay_plan(
        plan,
        selected_memory_ids=[cold.memory_id],
        target_session_id="maintenance-2026-01",
        confirm=True,
    )
    assert applied["applied"] is True
    current = service.store.get_latest(cold.memory_id)
    assert current.tier == "session"
    assert current.retention_session_id == "maintenance-2026-01"


def test_decay_rejects_stale_plan_and_api_exposes_policy(tmp_path):
    service = MemGuardService(tmp_path / "memory.segment", tmp_path / "meta.sqlite3")
    cold = _old_long_term(service, "项目 Alpha 的构建目录位于 /srv/alpha/build。")
    plan = service.build_decay_plan(as_of=AS_OF)
    service.update_memory(cold.memory_id, "项目 Alpha 的构建目录需要保留。", reason="new evidence")
    stale = service.apply_decay_plan(plan, target_session_id="maintenance-2026-01", confirm=True)
    assert stale["applied"] is False
    assert stale["reason"] == "stale_decay_plan"

    response = handle_request(
        service,
        {"command": "decay_plan", "as_of": AS_OF, "max_age_days": 30, "limit": 10},
    )
    assert response["ok"] is True
    assert response["result"]["schema"] == "memguardos-decay-plan-v1"
