import json
import sqlite3

import pytest

from memguard.integrations.events import AgentInteractionEvent
from memguard.models import MemorySource, make_memory
from memguard.retrieval.vector_cache import vector_content_hash
from memguard.service import MemGuardService


def _service(tmp_path):
    return MemGuardService(tmp_path / "memory.segment", tmp_path / "meta.sqlite3")


def test_new_memory_append_failure_replays_after_restart(tmp_path, monkeypatch):
    service = _service(tmp_path)

    def fail_append(_record):
        raise OSError("simulated append failure")

    monkeypatch.setattr(service.store, "append", fail_append)
    with pytest.raises(OSError, match="simulated append failure"):
        service.ingest_text("白鹭项目的例会安排在周四。")
    pending = service.meta.pending_memory_writes()
    assert len(pending) == 1
    assert service.meta.get_memory(pending[0]["memory_id"]) is None
    assert service.search("白鹭项目", audit=False) == []
    service.close()

    recovered = _service(tmp_path)
    try:
        assert recovered.meta.pending_memory_writes() == []
        row = recovered.meta.get_memory(pending[0]["memory_id"])
        assert row is not None and row["deleted"] == 0
        assert "周四" in row["content"]
        assert len(recovered.store.get_versions(row["memory_id"])) == 1
    finally:
        recovered.close()


def test_new_memory_sqlite_failure_does_not_duplicate_segment_on_replay(tmp_path, monkeypatch):
    service = _service(tmp_path)

    def fail_upsert(*_args):
        raise OSError("simulated metadata failure")

    monkeypatch.setattr(service.meta, "_upsert_memory_with_embedding_job_no_commit", fail_upsert)
    with pytest.raises(OSError, match="simulated metadata failure"):
        service.ingest_text("青岚项目采用离线构建流程。")
    pending = service.meta.pending_memory_writes()
    assert len(pending) == 1
    memory_id = pending[0]["memory_id"]
    assert service.meta.get_memory(memory_id) is None
    assert service.versions(memory_id) == []
    assert service.store.get_latest(memory_id) is not None
    service.close()

    recovered = _service(tmp_path)
    try:
        assert recovered.meta.pending_memory_writes() == []
        assert recovered.meta.get_memory(memory_id)["deleted"] == 0
        versions = recovered.store.get_versions(memory_id)
        assert len(versions) == 1
        assert versions[0].version.version_id == pending[0]["version_id"]
    finally:
        recovered.close()


def test_staged_write_identity_mismatch_fails_closed(tmp_path):
    service = _service(tmp_path)
    try:
        with pytest.raises(ValueError, match="not found"):
            service.meta.commit_staged_new_memory_write("missing", service.store.append)
        assert service.meta.pending_memory_writes() == []
    finally:
        service.close()


def test_recovered_agent_preference_keeps_source_links_and_promotion(tmp_path, monkeypatch):
    service = _service(tmp_path)
    event = AgentInteractionEvent(
        agent_name="test-agent", agent_version="1", session_id="preference-recovery",
        turn_id="turn-1", sequence=1, event_type="user_message", role="user",
        content="以后搜索代码优先使用 rg。", event_id="evt-preference-recovery",
    )

    def fail_upsert(*_args):
        raise OSError("simulated preference write failure")

    monkeypatch.setattr(service.meta, "_upsert_memory_with_embedding_job_no_commit", fail_upsert)
    with pytest.raises(OSError, match="simulated preference write failure"):
        service.ingest_interaction(event)
    pending = service.meta.pending_memory_writes()
    assert len(pending) == 1
    service.close()

    recovered = _service(tmp_path)
    try:
        memory_id = pending[0]["memory_id"]
        assert recovered.meta.pending_memory_writes() == []
        assert recovered.meta.get_memory(memory_id)["preference_topic"] == "code_search_tool"
        evidence = recovered.meta.memory_event_evidence(memory_id)
        assert [item["event_id"] for item in evidence] == [event.event_id]
        observation = recovered.meta.conn.execute(
            "SELECT promoted_memory_id FROM preference_observations WHERE event_id = ?",
            (event.event_id,),
        ).fetchone()
        assert observation["promoted_memory_id"] == memory_id
    finally:
        recovered.close()


def test_preference_supersession_commits_before_feedback_failure(tmp_path, monkeypatch):
    service = _service(tmp_path)
    old = AgentInteractionEvent(
        agent_name="test-agent", agent_version="1", session_id="old",
        turn_id="turn-1", sequence=1, event_type="user_message", role="user",
        content="以后搜索代码优先使用 grep。", event_id="evt-pref-old",
    )
    old_id = service.ingest_interaction(old)["memory_ids"][0]

    def fail_feedback(*_args):
        raise OSError("simulated feedback failure")

    monkeypatch.setattr(service, "_adjudicate_preference_update", fail_feedback)
    new = AgentInteractionEvent(
        agent_name="test-agent", agent_version="1", session_id="new",
        turn_id="turn-1", sequence=1, event_type="user_message", role="user",
        content="以后搜索代码优先使用 rg。", event_id="evt-pref-new",
    )
    with pytest.raises(OSError, match="simulated feedback failure"):
        service.ingest_interaction(new)
    service.close()

    recovered = _service(tmp_path)
    try:
        assert recovered.meta.get_memory(old_id)["governance_state"] == "superseded"
        active = [row for row in recovered.meta.list_memories()
                  if row["preference_topic"] == "code_search_tool"]
        assert len(active) == 1
        assert active[0]["preference_value"] == "rg"
    finally:
        recovered.close()


def _version_operation(service, operation):
    if operation == "approve":
        created = service.ingest_text(
            "External note: openKylin uses systemctl status for service checks.",
            source_kind="web_content", source_uri="https://docs.example.invalid/service",
        )["record"]
        assert created.governance_state == "quarantined"
        return created.memory_id, lambda: service.review_quarantined_memory(
            created.memory_id, "approve", "reviewer", "checked against manual",
        )
    created = service.ingest_text("The Lark project meets on Thursday.")["record"]
    if operation == "update":
        return created.memory_id, lambda: service.update_memory(
            created.memory_id, "The Lark project meets on Friday."
        )
    if operation == "promote":
        return created.memory_id, lambda: service.promote_memory(
            created.memory_id, "working"
        )
    service.update_memory(created.memory_id, "The Lark project meets on Friday.")
    return created.memory_id, lambda: service.rollback(
        created.memory_id, created.version.version_id
    )


@pytest.mark.parametrize("operation", ["update", "promote", "rollback", "approve"])
def test_existing_version_recovers_after_sqlite_failure(tmp_path, monkeypatch, operation):
    service = _service(tmp_path)
    memory_id, run = _version_operation(service, operation)
    old_version = service.meta.get_memory(memory_id)["version_id"]
    old_count = len(service.store.get_versions(memory_id))

    def fail_upsert(*_args):
        raise OSError("simulated metadata failure")

    monkeypatch.setattr(service.meta, "_upsert_memory_with_embedding_job_no_commit", fail_upsert)
    with pytest.raises(OSError, match="simulated metadata failure"):
        run()
    pending = service.meta.pending_memory_writes()
    assert len(pending) == 1
    assert pending[0]["expected_version_id"] == old_version
    assert service.meta.get_memory(memory_id)["version_id"] == old_version
    assert len(service.store.get_versions(memory_id)) == old_count + 2
    visible = service.versions(memory_id)
    assert visible[-1]["version_id"] == old_version
    assert visible[-1]["active"] is True
    service.close()

    recovered = _service(tmp_path)
    try:
        assert recovered.meta.pending_memory_writes() == []
        current = recovered.meta.get_memory(memory_id)
        assert current["version_id"] == pending[0]["version_id"]
        assert current["deleted"] == 0
        assert recovered.store.get_latest(memory_id).version.version_id == current["version_id"]
        assert len(recovered.store.get_versions(memory_id)) == old_count + 2
        assert recovered.versions(memory_id)[-1]["active"] is True
        committed = [
            item for item in recovered.meta.list_audit(
                50, event_type="memory_version_committed"
            )
            if json.loads(item["detail"])["version_id"] == current["version_id"]
        ]
        assert len(committed) == 1
        assert recovered.meta.verify_audit_chain()["ok"] is True
    finally:
        recovered.close()


@pytest.mark.parametrize("failure_point", ["before_previous", "before_new"])
def test_existing_version_recovers_after_segment_failure(tmp_path, monkeypatch, failure_point):
    service = _service(tmp_path)
    memory_id, run = _version_operation(service, "update")
    old_version = service.meta.get_memory(memory_id)["version_id"]
    if failure_point == "before_previous":
        def fail_version(_previous, _record):
            raise OSError("simulated first append failure")
        monkeypatch.setattr(service.store, "append_version_once", fail_version)
    else:
        original_append = service.store._append_unlocked

        def fail_second(record):
            if record.version.version_id != old_version:
                raise OSError("simulated second append failure")
            return original_append(record)

        monkeypatch.setattr(service.store, "_append_unlocked", fail_second)
    with pytest.raises(OSError, match="append failure"):
        run()
    assert service.meta.get_memory(memory_id)["version_id"] == old_version
    assert len(service.meta.pending_memory_writes()) == 1
    visible = service.versions(memory_id)
    assert visible[-1]["version_id"] == old_version
    assert visible[-1]["active"] is True
    service.close()

    recovered = _service(tmp_path)
    try:
        assert recovered.meta.pending_memory_writes() == []
        versions = recovered.store.get_versions(memory_id)
        assert len(versions) == 3
        assert versions[1].temporal.superseded_by == versions[2].version.version_id
        assert recovered.meta.get_memory(memory_id)["version_id"] == versions[-1].version.version_id
    finally:
        recovered.close()


def test_erasure_waits_for_staged_version_recovery(tmp_path, monkeypatch):
    service = _service(tmp_path)
    memory_id, run = _version_operation(service, "update")

    def fail_version(_previous, _record):
        raise OSError("simulated append failure")

    monkeypatch.setattr(service.store, "append_version_once", fail_version)
    with pytest.raises(OSError):
        run()
    with pytest.raises(OSError, match="append failure"):
        run()
    current = service.store.get_latest(memory_id)
    with pytest.raises(ValueError, match="pending memory write"):
        service._begin_memory_erasure(current, compact=True)
    assert service.meta.get_memory(memory_id)["deleted"] == 0
    service.close()

    recovered = _service(tmp_path)
    try:
        current = recovered.store.get_latest(memory_id)
        recovered._begin_memory_erasure(current, compact=True)
        recovered._recover_pending_erasures()
        assert recovered.meta.get_memory(memory_id)["deleted"] == 1
        assert recovered.store.get_latest(memory_id) is None
    finally:
        recovered.close()


def test_same_process_retry_replays_once_without_duplicate_update(tmp_path, monkeypatch):
    service = _service(tmp_path)
    memory_id, run = _version_operation(service, "update")
    original_upsert = service.meta._upsert_memory_with_embedding_job_no_commit
    failed = False

    def fail_once(*args):
        nonlocal failed
        if not failed:
            failed = True
            raise OSError("simulated one-time metadata failure")
        return original_upsert(*args)

    monkeypatch.setattr(service.meta, "_upsert_memory_with_embedding_job_no_commit", fail_once)
    try:
        with pytest.raises(OSError, match="one-time metadata failure"):
            run()
        retried = run()
        assert retried["updated"] is False
        assert retried["reason"] == "unchanged"
        assert service.meta.pending_memory_writes() == []
        assert len(service.versions(memory_id)) == 2
        assert len(service.store.get_versions(memory_id)) == 3
        assert service.meta.verify_audit_chain()["ok"] is True
    finally:
        service.close()


@pytest.mark.parametrize(
    ("operation", "flag", "reason"),
    [
        ("promote", "promoted", "already_in_tier"),
        ("rollback", "rolled_back", "already_rolled_back"),
        ("approve", "reviewed", "memory_not_quarantined"),
    ],
)
def test_same_process_retry_skips_completed_version_operation(
    tmp_path, monkeypatch, operation, flag, reason,
):
    service = _service(tmp_path)
    memory_id, run = _version_operation(service, operation)
    old_count = len(service.store.get_versions(memory_id))
    original_upsert = service.meta._upsert_memory_with_embedding_job_no_commit
    failed = False

    def fail_once(*args):
        nonlocal failed
        if not failed:
            failed = True
            raise OSError("simulated one-time metadata failure")
        return original_upsert(*args)

    monkeypatch.setattr(service.meta, "_upsert_memory_with_embedding_job_no_commit", fail_once)
    try:
        with pytest.raises(OSError, match="one-time metadata failure"):
            run()
        retried = run()
        assert retried[flag] is False
        assert retried["reason"] == reason
        assert service.meta.pending_memory_writes() == []
        assert len(service.store.get_versions(memory_id)) == old_count + 2
    finally:
        service.close()


def test_old_erasure_preview_is_stale_after_automatic_recovery(tmp_path, monkeypatch):
    service = _service(tmp_path)
    memory_id, run = _version_operation(service, "update")
    old_version = service.meta.get_memory(memory_id)["version_id"]
    original_upsert = service.meta._upsert_memory_with_embedding_job_no_commit
    failed = False

    def fail_once(*args):
        nonlocal failed
        if not failed:
            failed = True
            raise OSError("simulated one-time metadata failure")
        return original_upsert(*args)

    monkeypatch.setattr(service.meta, "_upsert_memory_with_embedding_job_no_commit", fail_once)
    try:
        with pytest.raises(OSError):
            run()
        result = service.forget_memory_ids(
            [memory_id], "test stale preview", expected_versions={memory_id: old_version},
        )
        assert result["ok"] is False
        assert result["reason"] == "stale_preview"
        assert result["forgotten_memory_ids"] == []
        assert service.meta.get_memory(memory_id)["deleted"] == 0
        assert service.meta.pending_memory_writes() == []
    finally:
        service.close()


def test_version_audit_failure_replays_with_memory(tmp_path, monkeypatch):
    service = _service(tmp_path)
    memory_id, run = _version_operation(service, "update")
    original = service.meta._add_audit_no_commit

    def fail_version_audit(event_type, memory_id, detail):
        if event_type == "memory_version_committed":
            raise OSError("simulated version audit failure")
        return original(event_type, memory_id, detail)

    monkeypatch.setattr(service.meta, "_add_audit_no_commit", fail_version_audit)
    with pytest.raises(OSError, match="version audit failure"):
        run()
    pending = service.meta.pending_memory_writes()
    assert len(pending) == 1
    assert service.meta.get_memory(memory_id)["version_id"] != pending[0]["version_id"]
    service.close()

    recovered = _service(tmp_path)
    try:
        assert recovered.meta.pending_memory_writes() == []
        assert recovered.meta.get_memory(memory_id)["version_id"] == pending[0]["version_id"]
        assert recovered.meta.verify_audit_chain()["ok"] is True
        committed = recovered.meta.list_audit(50, event_type="memory_version_committed")
        assert sum(
            json.loads(item["detail"])["version_id"] == pending[0]["version_id"]
            for item in committed
        ) == 1
    finally:
        recovered.close()


def test_old_pending_write_schema_migrates_and_replays(tmp_path):
    record = make_memory(
        "The Lark project meets on Thursday.", "knowledge",
        MemorySource(kind="manual"),
    )
    db_path = tmp_path / "meta.sqlite3"
    with sqlite3.connect(db_path) as conn:
        conn.execute(
            """CREATE TABLE pending_memory_write (
                version_id TEXT PRIMARY KEY, memory_id TEXT NOT NULL,
                record_json TEXT NOT NULL, backend TEXT NOT NULL,
                model_revision TEXT NOT NULL, content_hash TEXT NOT NULL,
                created_at TEXT NOT NULL
            )"""
        )
        conn.execute(
            "INSERT INTO pending_memory_write VALUES (?, ?, ?, ?, ?, ?, ?)",
            (
                record.version.version_id, record.memory_id, record.to_json(),
                "kylin_sdk", "kylin-sdk-v1",
                vector_content_hash(record.summary, record.content),
                "2026-01-01T00:00:00+00:00",
            ),
        )

    recovered = _service(tmp_path)
    try:
        assert recovered.meta.pending_memory_writes() == []
        assert recovered.meta.get_memory(record.memory_id)["version_id"] == record.version.version_id
        assert len(recovered.store.get_versions(record.memory_id)) == 1
        columns = {
            row["name"] for row in recovered.meta.conn.execute(
                "PRAGMA table_info(pending_memory_write)"
            )
        }
        assert {"expected_version_id", "previous_record_json", "source_link_json"} <= columns
    finally:
        recovered.close()
