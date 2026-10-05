import sqlite3

import pytest

from memguard.api.server import handle_request
from memguard.extraction.fact_slot import parse_fact_slot
from memguard.models import MemorySource, make_memory
from memguard.retrieval.vector_cache import vector_content_hash
from memguard.service import MemGuardService


def _service(tmp_path, namespace=None):
    return MemGuardService(
        tmp_path / "memory.segment", tmp_path / "meta.sqlite3", namespace=namespace,
    )


def test_fact_slot_only_accepts_unambiguous_supported_attributes():
    first = parse_fact_slot("白鹭项目发布日期是周三。")
    corrected = parse_fact_slot("白鹭项目发布日改为周四。")
    assert (first.subject, first.predicate, first.value) == ("白鹭项目", "release_date", "周三")
    assert first.explicit_update is False
    assert (corrected.subject, corrected.predicate, corrected.value) == ("白鹭项目", "release_date", "周四")
    assert corrected.explicit_update is True
    assert corrected.canonical_content == "白鹭项目发布日是周四"
    assert parse_fact_slot("项目发布日是周三") is None
    assert parse_fact_slot("白鹭项目发布日是周三，负责人是李明") is None
    assert parse_fact_slot("白鹭项目发布日不是周三") is None
    assert parse_fact_slot("请记住：白鹭项目的发布日是周三。").subject == "白鹭项目"


def test_explicit_fact_correction_reuses_version_chain_and_rollback(tmp_path):
    service = _service(tmp_path)
    try:
        first = service.ingest_natural_memory("白鹭项目发布日是周三。")
        memory_id = first["record"].memory_id
        original_version = first["record"].version.version_id

        unresolved = service.ingest_natural_memory("白鹭项目发布日期是周四。")
        assert unresolved["stored"] is False
        assert unresolved["reason"] == "fact_conflict_requires_review"
        assert unresolved["candidate_memory_ids"] == [memory_id]
        assert len(service.list_memories()) == 1

        corrected = service.ingest_natural_memory("白鹭项目发布日期改为周四。")
        assert corrected["stored"] is True
        assert corrected["updated"] is True
        assert corrected["record"].memory_id == memory_id
        assert corrected["feedback"]["updated_memories"]
        assert corrected["feedback"]["new_memories"] == []
        assert service.meta.get_memory(memory_id)["content"] == "白鹭项目发布日是周四"
        assert len(service.list_memories()) == 1
        assert len(service.versions(memory_id)) == 2
        context = service.build_agent_context(
            task="白鹭项目发布日", stage="planning", agent_name="test-agent",
            session_id="fact-temporal",
        )
        assert "周四" in context["context"]
        assert "周三" not in context["context"]

        rolled_back = service.rollback(memory_id, original_version)
        assert rolled_back["rolled_back"] is True
        assert service.meta.get_memory(memory_id)["content"] == "白鹭项目发布日是周三。"
        assert len(service.meta.find_active_fact_candidates("default", "白鹭项目", "release_date")) == 1
    finally:
        service.close()


def test_fact_conflict_is_a_queryable_review_case_and_closes_after_correction(tmp_path):
    service = _service(tmp_path)
    try:
        original = service.ingest_natural_memory("白鹭项目发布日是周三。")
        memory_id = original["record"].memory_id
        conflict = service.ingest_natural_memory("白鹭项目发布日是周四。")

        assert conflict["reason"] == "fact_conflict_requires_review"
        assert conflict["conflict_id"].startswith("conflict_")
        pending = service.fact_conflicts()
        assert pending["count"] == 1
        case = pending["cases"][0]
        assert case["conflict_id"] == conflict["conflict_id"]
        assert case["incoming_value"] == "周四"
        assert case["candidate_memory_ids"] == [memory_id]
        assert case["candidates"][0]["state"] == "active"

        corrected = service.ingest_natural_memory("白鹭项目发布日期改为周四。")
        assert corrected["updated"] is True
        assert service.fact_conflicts()["count"] == 0
        resolved = service.fact_conflicts(status="resolved")
        assert resolved["count"] == 1
        assert resolved["cases"][0]["resolved_memory_id"] == memory_id
    finally:
        service.close()


def test_mixed_fact_correction_links_event_and_replays_after_failure(tmp_path, monkeypatch):
    service = _service(tmp_path)
    original = service.ingest_text("白鹭项目发布日是周三。")
    memory_id = original["record"].memory_id
    original_link = service.meta._insert_memory_event_link_no_commit

    def fail_link(memory_id_arg, event_id, relation, source=None):
        if relation == "source_clause":
            raise OSError("simulated correction source failure")
        return original_link(memory_id_arg, event_id, relation, source)

    monkeypatch.setattr(service.meta, "_insert_memory_event_link_no_commit", fail_link)
    with pytest.raises(OSError, match="correction source failure"):
        service.ingest_natural_memory(
            "以后搜索项目代码优先使用 rg，白鹭项目发布日期改为周四。",
            session_id="fact-correction",
        )
    assert service.meta.get_memory(memory_id)["content"] == "白鹭项目发布日是周三。"
    assert len(service.meta.pending_memory_writes()) == 1
    service.close()

    recovered = _service(tmp_path)
    try:
        assert recovered.meta.pending_memory_writes() == []
        assert recovered.meta.get_memory(memory_id)["content"] == "白鹭项目发布日是周四"
        evidence = recovered.meta.memory_event_evidence(memory_id)
        assert len(evidence) == 1
        assert evidence[0]["source_excerpt"] == "白鹭项目发布日期改为周四"
        assert len(recovered.versions(memory_id)) == 2
        assert recovered.meta.verify_audit_chain()["ok"] is True
    finally:
        recovered.close()


def test_fact_slot_index_backfills_old_database_and_clears_on_forget(tmp_path):
    service = _service(tmp_path)
    memory_id = service.ingest_text("白鹭项目发布日是周三。")["record"].memory_id
    service.close()
    with sqlite3.connect(tmp_path / "meta.sqlite3") as conn:
        conn.execute("DROP TABLE fact_slot")
        conn.execute("DELETE FROM schema_migrations WHERE name = 'fact_slot_backfill_v1'")

    recovered = _service(tmp_path)
    try:
        indexed = recovered.meta.find_active_fact_candidates("default", "白鹭项目", "release_date")
        assert [row["memory_id"] for row in indexed] == [memory_id]
        assert recovered.forget_memory_ids([memory_id], reason="user forget", compact=True)["ok"] is True
        assert recovered.meta.conn.execute(
            "SELECT COUNT(*) FROM fact_slot WHERE memory_id = ?", (memory_id,),
        ).fetchone()[0] == 0
    finally:
        recovered.close()


def test_interrupted_fact_index_backfill_is_resumed(tmp_path):
    service = _service(tmp_path)
    memory_id = service.ingest_text("白鹭项目发布日是周三。")["record"].memory_id
    service.close()
    with sqlite3.connect(tmp_path / "meta.sqlite3") as conn:
        conn.execute("DELETE FROM fact_slot")
        conn.execute("DELETE FROM schema_migrations WHERE name = 'fact_slot_backfill_v1'")

    recovered = _service(tmp_path)
    try:
        indexed = recovered.meta.find_active_fact_candidates("default", "白鹭项目", "release_date")
        assert [row["memory_id"] for row in indexed] == [memory_id]
        assert recovered.meta.conn.execute(
            "SELECT 1 FROM schema_migrations WHERE name = 'fact_slot_backfill_v1'"
        ).fetchone() is not None
    finally:
        recovered.close()


def test_fact_correction_does_not_cross_namespace(tmp_path):
    first = _service(tmp_path, namespace="project-a")
    second_root = tmp_path / "project-b"
    second_root.mkdir()
    second = _service(second_root, namespace="project-b")
    try:
        first_id = first.ingest_text("白鹭项目发布日是周三。")["record"].memory_id
        second_result = second.ingest_text("白鹭项目发布日改为周四。")
        assert second_result["stored"] is True
        assert second_result.get("updated") is not True
        assert second_result["record"].memory_id != first_id
        assert first.meta.get_memory(first_id)["content"] == "白鹭项目发布日是周三。"
    finally:
        second.close()
        first.close()


def test_fact_index_residue_fails_forget_verification(tmp_path):
    service = _service(tmp_path)
    try:
        original = "白鹭项目发布日是周三。"
        memory_id = service.ingest_text(original)["record"].memory_id
        assert service.forget_memory_ids([memory_id], reason="user forget", compact=True)["ok"] is True
        service.meta.conn.execute(
            "INSERT INTO fact_slot(memory_id, scope, subject, predicate) VALUES (?, ?, ?, ?)",
            (memory_id, "default", "白鹭项目", "release_date"),
        )
        service.meta.conn.commit()
        checked = service.verify_forget_probe(memory_id, original)
        assert checked["retrieval_surfaces"]["fact_slot_recallable"] is True
        assert checked["irreversible_ok"] is False
    finally:
        service.close()


def test_api_reports_fact_update_and_review_candidate(tmp_path):
    service = _service(tmp_path)
    try:
        first = handle_request(service, {"command": "ingest", "text": "白鹭项目发布日是周三。"})
        memory_id = first["result"]["memory_id"]
        conflict = handle_request(service, {"command": "ingest", "text": "白鹭项目发布日是周四。"})
        assert conflict["result"]["stored"] is False
        assert conflict["result"]["candidate_memory_ids"] == [memory_id]
        corrected = handle_request(service, {"command": "ingest", "text": "白鹭项目发布日改为周四。"})
        assert corrected["result"]["updated"] is True
        assert corrected["result"]["memory_id"] == memory_id
    finally:
        service.close()


def test_mixed_preference_and_fact_conflict_is_reported_as_partial(tmp_path):
    service = _service(tmp_path)
    try:
        previous = service.ingest_text("白鹭项目发布日是周三。")["record"]
        result = service.ingest_natural_memory(
            "以后搜索项目代码优先使用 rg，白鹭项目发布日是周四。",
            session_id="partial-fact-conflict",
        )
        assert result["stored"] is True
        assert result["partial_conflict"] is True
        assert result["candidate_memory_ids"] == [previous.memory_id]
        assert result["feedback"]["new_memories"]
        assert any(
            item["reason"] == "fact_conflict_requires_review"
            for item in result["feedback"]["skipped_items"]
        )
        assert service.meta.get_memory(previous.memory_id)["content"] == "白鹭项目发布日是周三。"
    finally:
        service.close()


def test_tool_result_cannot_rewrite_user_fact(tmp_path):
    service = _service(tmp_path)
    try:
        original = service.ingest_text("白鹭项目发布日是周三。")["record"]
        rejected = service.ingest_text(
            "白鹭项目发布日改为周四。", source_kind="tool_result",
        )
        assert rejected["reason"] == "fact_conflict_requires_review"
        assert rejected["candidate_memory_ids"] == [original.memory_id]
        assert service.meta.get_memory(original.memory_id)["content"] == "白鹭项目发布日是周三。"
    finally:
        service.close()


def test_competing_fact_commit_is_blocked_before_segment_append(tmp_path, monkeypatch):
    service = _service(tmp_path)
    peer = _service(tmp_path)
    original_stage = service.meta.stage_new_memory_write

    def stage_then_commit_peer(*args, **kwargs):
        original_stage(*args, **kwargs)
        competing = make_memory(
            content="白鹭项目发布日是周四。", memory_type="knowledge",
            source=MemorySource(kind="manual"), scope="default",
        )
        peer.meta.stage_new_memory_write(
            competing, peer.EMBEDDING_BACKEND, peer.EMBEDDING_MODEL_REVISION,
            vector_content_hash(competing.summary, competing.content),
        )
        peer.meta.commit_staged_new_memory_write(
            competing.version.version_id, peer._append_staged_record_once,
        )

    monkeypatch.setattr(service.meta, "stage_new_memory_write", stage_then_commit_peer)
    try:
        blocked = service.ingest_text("白鹭项目发布日是周三。")
        assert blocked["reason"] == "fact_conflict_requires_review"
        assert len(blocked["candidate_memory_ids"]) == 1
        assert service.meta.pending_memory_writes() == []
        assert len(service.store.scan()) == 1
        assert len(service.list_memories()) == 1
        assert service.meta.verify_audit_chain()["ok"] is True
    finally:
        peer.close()
        service.close()


def test_startup_replay_resolves_two_staged_facts_without_duplicate(tmp_path):
    service = _service(tmp_path)
    for value in ("周三", "周四"):
        record = make_memory(
            content=f"白鹭项目发布日是{value}。", memory_type="knowledge",
            source=MemorySource(kind="manual"), scope="default",
        )
        service.meta.stage_new_memory_write(
            record, service.EMBEDDING_BACKEND, service.EMBEDDING_MODEL_REVISION,
            vector_content_hash(record.summary, record.content),
        )
    service.close()

    recovered = _service(tmp_path)
    try:
        assert recovered.meta.pending_memory_writes() == []
        assert len(recovered.list_memories()) == 1
        assert len(recovered.store.scan()) == 1
        assert recovered.meta.verify_audit_chain()["ok"] is True
    finally:
        recovered.close()


def test_fact_conflict_snapshot_is_project_scoped_and_scrubbed_by_forgetting(tmp_path):
    service = _service(tmp_path)
    project_a = str((tmp_path / "project-a").resolve())
    project_b = str((tmp_path / "project-b").resolve())
    try:
        original = service.ingest_text(
            "白鹭项目发布日是周三。", project_root=project_a,
        )["record"]
        isolated = service.ingest_text(
            "白鹭项目发布日是周四。", project_root=project_b,
        )
        assert isolated["stored"] is True
        conflict = service.ingest_text(
            "白鹭项目发布日是周四。", project_root=project_a,
        )
        assert conflict["reason"] == "fact_conflict_requires_review"
        case = service.meta.get_fact_conflict(conflict["conflict_id"])
        assert case["project_root"] == project_a
        assert case["candidate_version_ids"][original.memory_id] == original.version.version_id
        assert service.forget_memory_ids([original.memory_id], reason="scrub conflict", compact=True)["ok"] is True
        closed = service.fact_conflicts(status="dismissed")["cases"]
        scrubbed = [item for item in closed if item["conflict_id"] == conflict["conflict_id"]][0]
        assert scrubbed["incoming_value"] == ""
        assert scrubbed["incoming_content"] == ""
        assert scrubbed["candidate_memory_ids"] == []
        assert scrubbed["resolution"] == "redacted_for_forgetting"
        checked = service.verify_forget_probe(original.memory_id, "白鹭项目发布日是周三")
        assert checked["retrieval_surfaces"]["fact_conflict_recallable"] is False
    finally:
        service.close()


def test_fact_conflict_can_keep_old_value_or_accept_new_value_with_cas(tmp_path):
    service = _service(tmp_path)
    try:
        original = service.ingest_text("白鹭项目发布日是周三。")["record"]
        pending = service.ingest_text("白鹭项目发布日是周四。")
        kept = service.resolve_fact_conflict(
            pending["conflict_id"], decision="keep_existing",
        )
        assert kept["resolved"] is True
        assert service.meta.get_memory(original.memory_id)["content"] == "白鹭项目发布日是周三。"

        pending_again = service.ingest_text("白鹭项目发布日是周五。")
        accepted = service.resolve_fact_conflict(
            pending_again["conflict_id"], decision="accept_incoming",
        )
        assert accepted["resolved"] is True
        assert accepted["updated"] is True
        assert service.meta.get_memory(original.memory_id)["content"] == "白鹭项目发布日是周五"
    finally:
        service.close()
