import pytest

from memguard.integrations.events import AgentInteractionEvent
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
