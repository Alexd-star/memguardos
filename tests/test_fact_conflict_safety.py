import json

import pytest

from memguard.cli import memguard
from memguard.service import MemGuardService


@pytest.fixture
def service(tmp_path):
    instance = MemGuardService(tmp_path / "memory.segment", tmp_path / "meta.sqlite3")
    try:
        yield instance
    finally:
        instance.close()


def _peer(service):
    return MemGuardService(service.store.path, service.meta.path, namespace=service.namespace)


def test_late_conflict_creation_cannot_restore_forgotten_payload(service, monkeypatch):
    record = service.ingest_text("白鹭项目发布日是周三。")["record"]
    create = service.meta.create_fact_conflict

    def race(**kwargs):
        peer = _peer(service)
        try:
            assert peer.forget_memory_ids([record.memory_id], reason="user request", compact=True)["ok"]
        finally:
            peer.close()
        return create(**kwargs)

    monkeypatch.setattr(service.meta, "create_fact_conflict", race)
    outcome = service.ingest_text("白鹭项目发布日是周四。")
    assert not outcome["stored"] and outcome["reason"] == "candidate_version_changed"
    assert service.meta.list_fact_conflicts(status=None) == []
    assert service.meta.get_memory(record.memory_id)["deleted"]
    assert service.verify_audit()["ok"]


def test_repeated_claim_uses_fresh_candidate_snapshot(service):
    record = service.ingest_text("白鹭项目发布日是周三。")["record"]
    old = service.ingest_text("白鹭项目发布日是周四。")
    updated = service.update_memory(record.memory_id, "白鹭项目发布日改为周五。")
    fresh = service.ingest_text("白鹭项目发布日是周四。")
    assert fresh["conflict_id"] != old["conflict_id"]
    assert service.meta.get_fact_conflict(fresh["conflict_id"])["candidate_version_ids"] == {
        record.memory_id: updated["record"].version.version_id,
    }
    assert service.meta.get_fact_conflict(old["conflict_id"])["status"] == "dismissed"
    assert service.resolve_fact_conflict(fresh["conflict_id"], decision="keep_existing")["resolved"]


def test_global_correction_preserves_unrelated_project_conflict(service, tmp_path):
    project = str((tmp_path / "project").resolve())
    local = service.ingest_text("白鹭项目发布日是周三。", project_root=project)["record"]
    global_record = service.ingest_text("白鹭项目发布日是周三。")["record"]
    pending = service.ingest_text("白鹭项目发布日是周四。", project_root=project)
    assert service.ingest_text("白鹭项目发布日改为周四。")["updated"]
    assert service.meta.get_fact_conflict(pending["conflict_id"])["status"] == "pending"
    assert "周三" in service.meta.get_memory(local.memory_id)["content"]
    assert "周四" in service.meta.get_memory(global_record.memory_id)["content"]


def test_project_fact_correction_cannot_rewrite_global_fact(service, tmp_path):
    project = str((tmp_path / "project").resolve())
    global_record = service.ingest_text("白鹭项目发布日是周三。")["record"]
    local = service.ingest_text("白鹭项目发布日改为周四。", project_root=project)
    assert local["stored"] and not local.get("updated")
    local_id = local["record"].memory_id
    assert local_id != global_record.memory_id
    conflict = service.ingest_text("白鹭项目发布日是周五。", project_root=project)
    assert conflict["candidate_memory_ids"] == [local_id]
    assert service.resolve_fact_conflict(conflict["conflict_id"], decision="accept_incoming")["resolved"]
    assert service.meta.get_memory(global_record.memory_id)["content"] == "白鹭项目发布日是周三。"
    assert "周五" in service.meta.get_memory(local_id)["content"]
    assert len(service.versions(global_record.memory_id)) == 1


def test_acceptance_cannot_overwrite_intervening_update(service, monkeypatch):
    record = service.ingest_text("白鹭项目发布日是周三。")["record"]
    pending = service.ingest_text("白鹭项目发布日是周四。")
    update = service.update_memory

    def race(*args, **kwargs):
        peer = _peer(service)
        try:
            assert peer.update_memory(record.memory_id, "白鹭项目发布日改为周五。")["updated"]
        finally:
            peer.close()
        return update(*args, **kwargs)

    monkeypatch.setattr(service, "update_memory", race)
    outcome = service.resolve_fact_conflict(pending["conflict_id"], decision="accept_incoming")
    assert not outcome["resolved"] and outcome["reason"] == "candidate_version_changed"
    assert "周五" in service.meta.get_memory(record.memory_id)["content"]
    assert service.meta.pending_memory_writes() == []


def test_winning_keep_decision_cancels_staged_acceptance(service, monkeypatch):
    record = service.ingest_text("白鹭项目发布日是周三。")["record"]
    pending = service.ingest_text("白鹭项目发布日是周四。")
    commit = service.meta.commit_staged_existing_memory_write
    peer = _peer(service)

    def race(*args, **kwargs):
        assert peer.resolve_fact_conflict(pending["conflict_id"], decision="keep_existing")["resolved"]
        return commit(*args, **kwargs)

    monkeypatch.setattr(service.meta, "commit_staged_existing_memory_write", race)
    try:
        outcome = service.resolve_fact_conflict(pending["conflict_id"], decision="accept_incoming")
    finally:
        peer.close()
    assert not outcome["resolved"] and outcome["reason"] == "conflict_already_closed"
    assert "周三" in service.meta.get_memory(record.memory_id)["content"]
    assert service.meta.get_fact_conflict(pending["conflict_id"])["resolution"] == "keep_existing"
    assert service.meta.pending_memory_writes() == []
    operations = service.lifecycle_snapshot(record.memory_id)["operations"]
    assert any(
        operation["operation"] == "fact_conflict_write_cancelled"
        and operation["status"] == "cancelled"
        and operation["label"] == "事实更新取消"
        for operation in operations
    )


def test_mixed_conflict_acceptance_preserves_source_and_can_be_erased(service):
    record = service.ingest_text("白鹭项目发布日是周三。")["record"]
    incoming = service.ingest_natural_memory(
        "以后搜索项目代码优先使用 rg，白鹭项目发布日是周四。", session_id="mixed-source",
    )
    conflict_id = incoming["conflict_ids"][0]
    assert service.meta.get_fact_conflict(conflict_id)["source_link"]["event_id"]
    assert service.resolve_fact_conflict(conflict_id, decision="accept_incoming")["resolved"]
    snapshot = service.lifecycle_snapshot(record.memory_id)
    assert any("周四" in support["excerpt"] for support in snapshot["supports"])
    assert service.forget_memory_ids([record.memory_id], reason="user request", compact=True)["ok"]
    assert "周四" not in json.dumps(service.meta.list_agent_events(), ensure_ascii=False)
    scrubbed = service.meta.get_fact_conflict(conflict_id)
    assert scrubbed["subject"] == "" and scrubbed["project_root"] == ""
    assert not scrubbed.get("source_link")
    assert any(row.get("preference_value") == "rg" for row in service.list_memories())
    assert service.verify_audit()["ok"]


def test_acceptance_recovers_version_and_resolution_as_one_commit(service, monkeypatch):
    record = service.ingest_text("白鹭项目发布日是周三。")["record"]
    pending = service.ingest_text("白鹭项目发布日是周四。")
    append = service.store.append_version_once

    def crash(*args, **kwargs):
        append(*args, **kwargs)
        raise RuntimeError("simulated crash after segment append")

    with monkeypatch.context() as scoped:
        scoped.setattr(service.store, "append_version_once", crash)
        with pytest.raises(RuntimeError, match="simulated crash"):
            service.resolve_fact_conflict(pending["conflict_id"], decision="accept_incoming")
    assert service.meta.get_memory(record.memory_id)["version_id"] == record.version.version_id
    assert service.meta.get_fact_conflict(pending["conflict_id"])["status"] == "pending"
    assert service._recover_pending_memory_writes() == 1
    assert "周四" in service.meta.get_memory(record.memory_id)["content"]
    assert service.meta.get_fact_conflict(pending["conflict_id"])["resolution"] == "accept_incoming"
    assert service.meta.pending_memory_writes() == []
    assert service.verify_audit()["ok"]


def test_forgotten_source_cannot_be_accepted_later(service):
    record = service.ingest_text("白鹭项目发布日是周三。")["record"]
    incoming = service.ingest_natural_memory(
        "以后搜索项目代码优先使用 rg，白鹭项目发布日是周四。", session_id="erase-source",
    )
    conflict_id = incoming["conflict_ids"][0]
    event_id = service.meta.get_fact_conflict(conflict_id)["source_link"]["event_id"]
    result = service.forget_agent_interactions(
        {"session_id": "erase-source"}, confirm=True, compact=True,
    )
    assert result["confirmed"]
    case = service.meta.get_fact_conflict(conflict_id)
    assert case["status"] == "dismissed" and not case["incoming_content"]
    assert not service.resolve_fact_conflict(conflict_id, decision="accept_incoming")["resolved"]
    assert service.meta.get_memory(record.memory_id)["content"] == "白鹭项目发布日是周三。"
    assert "周四" not in json.dumps(service.meta.list_agent_events(), ensure_ascii=False)
    assert event_id not in json.dumps(case, ensure_ascii=False)


def test_cli_fact_resolution_is_readable_and_closed_case_is_not_success(service, capsys):
    service.ingest_text("白鹭项目发布日是周三。")
    pending = service.ingest_text("白鹭项目发布日是周四。")
    args = [
        "--store", str(service.store.path), "--db", str(service.meta.path),
        "resolve-conflict", pending["conflict_id"], "--decision", "keep_existing",
    ]
    assert memguard.main(args) == 0
    output = capsys.readouterr().out
    assert "已保留原值" in output and "核对编号" in output
    assert memguard.main(args) == 2
    output = capsys.readouterr().out
    assert "已处理" in output and "[已完成]" not in output
