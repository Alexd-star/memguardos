"""Focused adversarial regressions for derivation and procedure boundaries."""

from copy import deepcopy

import pytest

from memguard.models import MemorySource, VersionInfo, make_memory
from memguard.retrieval.vector_cache import vector_content_hash
from memguard.service import MemGuardService
from memguard.store.dependencies import DependencyChanged


@pytest.fixture
def service(tmp_path, monkeypatch):
    monkeypatch.setenv("MEMGUARD_DENSE_CANDIDATES", "0")
    instance = MemGuardService(tmp_path / "memory.segment", tmp_path / "meta.sqlite3")
    yield instance
    instance.close()


def source_record(service, **kwargs):
    memory_type = "preference" if "preference_scope" in kwargs else "knowledge"
    return service._persist_new_memory(make_memory(
        "Audited release acceptance criteria.", memory_type, MemorySource("manual"),
        scope=service.namespace, **kwargs,
    ))


def derived_record(service, source, text):
    return make_memory(text, "procedure", MemorySource("verified_procedure"),
                       scope=service.namespace, dependencies=[{
                           "memory_id": source.memory_id,
                           "version_id": source.version.version_id,
                           "relation": "derived_from",
                       }])


def spec():
    return {"goal": "Review release artifact", "steps": ["Inspect the artifact"],
            "postconditions": [{"type": "path_exists", "path": "meta.sqlite3"}]}


def test_existing_dependency_trigger_is_migrated_without_losing_invalidation(service):
    source = source_record(service)
    child = service._persist_new_memory(derived_record(service, source, "Derived acceptance steps."))
    service.meta.conn.execute("DROP TRIGGER invalidate_derived_memory")
    service.meta.conn.execute("""CREATE TRIGGER invalidate_derived_memory AFTER UPDATE ON memory
        WHEN NEW.version_id != OLD.version_id BEGIN SELECT 1; END""")
    service.meta.conn.commit()
    service.meta._init_dependency_schema()
    service.meta.conn.commit()
    source.temporal.superseded_by = "replacement-version"
    service.meta.upsert_memory(source)
    assert service.meta.get_memory(child.memory_id)["governance_state"] == "source_invalidated"


@pytest.mark.parametrize("scope_fields", [
    {"preference_scope": "agent", "agent_name": "restricted-agent"},
    {"preference_scope": "task_type", "task_type": "restricted-task"},
])
def test_learning_cannot_broaden_source_applicability(service, tmp_path, scope_fields):
    source = source_record(service, preference_topic="response_order",
                           preference_value="commands_first", **scope_fields)
    result = service.learn_procedure(spec(), tmp_path, [source.memory_id])
    assert result["stored"] is False, "scoped source became an unrestricted procedure"
    assert service.meta.dependency_descendants([source.memory_id]) == []


def test_superseded_source_cannot_pass_dependency_commit_validation(service):
    source = source_record(service)
    source.temporal.superseded_by = "replacement-version"
    service.meta.upsert_memory(source)
    with pytest.raises(DependencyChanged):
        service._persist_new_memory(derived_record(service, source, "Derived release review."))


def test_feedback_cas_rejects_procedure_trust_revoked_at_write_boundary(
    service, tmp_path, monkeypatch,
):
    source = source_record(service)
    learned = service.learn_procedure(spec(), tmp_path, [source.memory_id])
    assert learned["stored"]
    original = service.meta.record_procedure_feedback

    def revoke_then_record(*args, **kwargs):
        service.meta.set_governance_state(
            learned["memory_id"], "active", "untrusted", 0.0, "trust withdrawn",
        )
        return original(*args, **kwargs)

    monkeypatch.setattr(service.meta, "record_procedure_feedback", revoke_then_record)
    result = service.verify_procedure(
        learned["memory_id"], tmp_path, session_id="review-session", use_id="review-use",
    )
    assert result["verified"] is False, "feedback accepted a now-untrusted procedure"
    assert service.meta.conn.execute("SELECT COUNT(*) FROM procedure_feedback").fetchone()[0] == 0


def test_cancelled_post_append_derivative_is_physically_erased(service, monkeypatch):
    source = source_record(service)
    sentinel = "DERIVED-POST-APPEND-PRIVATE-REVIEW-9371"
    child = derived_record(service, source, sentinel)
    service.meta.stage_new_memory_write(
        child, service.EMBEDDING_BACKEND, service.EMBEDDING_MODEL_REVISION,
        vector_content_hash(child.summary, child.content),
    )

    def crash_after_append(*args, **kwargs):
        raise RuntimeError("simulated interruption after durable segment append")

    with monkeypatch.context() as patch:
        patch.setattr(service.meta, "_upsert_memory_with_embedding_job_no_commit", crash_after_append)
        with pytest.raises(RuntimeError, match="simulated interruption"):
            service.meta.commit_staged_new_memory_write(
                child.version.version_id, service._append_staged_record_once,
            )
    assert service.meta.get_memory(child.memory_id) is None
    assert service.meta.pending_memory_writes()
    assert service.store.raw_contains(sentinel)

    # Another writer can commit a source update before this writer recovers.
    previous = deepcopy(source)
    current = deepcopy(source)
    current.content = "Release acceptance criteria changed concurrently."
    current.version = VersionInfo(parent_version_id=source.version.version_id, op="update")
    previous.invalidate(superseded_by=current.version.version_id)
    current.finalize_hash()
    service._persist_existing_memory(previous, current)
    service._recover_pending_memory_writes()
    assert service.meta.pending_memory_writes() == []
    assert service.meta.get_memory(child.memory_id) is None

    result = service.forget_memory_ids([source.memory_id], "withdraw source", compact=True)
    assert result["ok"]
    assert not service.store.raw_contains(sentinel), "cancelled derived plaintext survived erasure"


def interrupted_write(service, monkeypatch, record, previous=None):
    options = (service.EMBEDDING_BACKEND, service.EMBEDDING_MODEL_REVISION,
               vector_content_hash(record.summary, record.content))
    if previous is None:
        service.meta.stage_new_memory_write(record, *options)
        commit = service.meta.commit_staged_new_memory_write
        append = service._append_staged_record_once
    else:
        service.meta.stage_existing_memory_write(previous, record, *options)
        commit = service.meta.commit_staged_existing_memory_write
        append = service.store.append_version_once

    def interrupt(*args, **kwargs):
        raise RuntimeError("interrupted metadata commit")

    with monkeypatch.context() as patch:
        patch.setattr(service.meta, "_upsert_memory_with_embedding_job_no_commit", interrupt)
        with pytest.raises(RuntimeError, match="interrupted metadata commit"):
            commit(record.version.version_id, append)


def advance_record(service, record, content):
    previous = deepcopy(record)
    current = deepcopy(record)
    current.content = content
    current.version = VersionInfo(parent_version_id=record.version.version_id, op="update")
    previous.invalidate(superseded_by=current.version.version_id)
    current.finalize_hash()
    return previous, current


@pytest.mark.parametrize("failure", ["rewrite", "post_rewrite", "checkpoint"])
def test_cleanup_queue_survives_failure_and_reopens(service, monkeypatch, failure):
    source = source_record(service)
    child = derived_record(service, source, "QUEUED-ORPHAN-RESTART-PRIVATE-9432")
    interrupted_write(service, monkeypatch, child)
    service.meta.set_governance_state(source.memory_id, "quarantined", "untrusted", 0.0, "revoked")
    original = service.store._replace_atomic_unlocked

    def interrupt(*args, **kwargs):
        if failure == "post_rewrite":
            original(*args, **kwargs)
        raise RuntimeError("interrupted physical cleanup")

    with monkeypatch.context() as patch:
        if failure == "checkpoint":
            patch.setattr(service.meta, "secure_cleanup", interrupt)
        else:
            patch.setattr(service.store, "_replace_atomic_unlocked", interrupt)
        with pytest.raises(RuntimeError, match="interrupted physical cleanup"):
            service._recover_pending_memory_writes()
    assert service.meta.pending_memory_writes() == []
    assert [(row["memory_id"], row["version_id"]) for row in service.meta.pending_segment_cleanups()] == [
        (child.memory_id, child.version.version_id),
    ]
    with pytest.raises(ValueError, match="requires cleanup"):
        service.meta.stage_new_memory_write(
            child, service.EMBEDDING_BACKEND, service.EMBEDDING_MODEL_REVISION,
            vector_content_hash(child.summary, child.content),
        )
    service.close()
    reopened = MemGuardService(service.store.path, service.meta.path)
    try:
        assert reopened.meta.pending_segment_cleanups() == []
        assert not reopened.store.raw_contains(child.content)
        assert reopened.store.get_latest(source.memory_id).to_json() == source.to_json()
        assert reopened._recover_pending_segment_cleanups() == 0
    finally:
        reopened.close()


def test_cancelled_update_keeps_all_committed_versions(service, monkeypatch):
    source = source_record(service)
    child = service._persist_new_memory(derived_record(service, source, "First committed release procedure."))
    previous, current = advance_record(service, child, "Second committed release procedure.")
    child = service._persist_existing_memory(previous, current)
    history = [record.to_json() for record in service.store.get_versions(child.memory_id)]
    assert service.meta.pending_segment_cleanups() == []
    previous, cancelled = advance_record(service, child, "CANCELLED-UPDATE-PRIVATE-9581")
    interrupted_write(service, monkeypatch, cancelled, previous)
    service.meta.set_governance_state(source.memory_id, "quarantined", "untrusted", 0.0, "revoked")
    service._recover_pending_memory_writes()
    assert service.meta.pending_segment_cleanups() == []
    assert not service.store.raw_contains(cancelled.content)
    assert [record.to_json() for record in service.store.get_versions(child.memory_id)] == history
    assert service.store.get_latest(child.memory_id).to_json() == child.to_json()
    assert service.meta.get_memory(child.memory_id)["version_id"] == child.version.version_id


@pytest.mark.parametrize("compact", [False, True])
def test_erasure_cancellation_drains_orphan_queue(service, monkeypatch, compact):
    source = source_record(service)
    child = derived_record(service, source, "ERASURE-ORPHAN-PRIVATE-9732")
    interrupted_write(service, monkeypatch, child)
    service.meta.begin_memory_erasure(
        source.memory_id, "review-tombstone", compact=compact,
        plaintext=source.content, expected_version_id=source.version.version_id,
    )
    assert service.meta.pending_memory_writes() == []
    assert service.meta.pending_segment_cleanups()
    service._recover_pending_erasures()
    assert service.meta.pending_segment_cleanups() == []
    assert service.meta.pending_erasures() == []
    assert not service.store.raw_contains(child.content)


def test_cancellation_before_append_does_not_rewrite_committed_history(service, monkeypatch):
    source = source_record(service)
    child = derived_record(service, source, "Never appended derivative.")
    service.meta.stage_new_memory_write(
        child, service.EMBEDDING_BACKEND, service.EMBEDDING_MODEL_REVISION,
        vector_content_hash(child.summary, child.content),
    )
    history = [record.to_json() for record in service.store.scan()]
    service.meta.set_governance_state(source.memory_id, "quarantined", "untrusted", 0.0, "revoked")

    def unexpected_rewrite(*args, **kwargs):
        raise AssertionError("unrelated committed history must not be rewritten")

    monkeypatch.setattr(service.store, "_replace_atomic_unlocked", unexpected_rewrite)
    service._recover_pending_memory_writes()
    assert service.meta.pending_segment_cleanups() == []
    assert [record.to_json() for record in service.store.scan()] == history
