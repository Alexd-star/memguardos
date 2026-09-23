import pytest

from memguard.integrations.events import AgentInteractionEvent
from memguard.service import MemGuardService


def _service(tmp_path):
    return MemGuardService(tmp_path / "memory.segment", tmp_path / "meta.sqlite3")


def _memory_with_event(service):
    record = service.ingest_text("白鹭项目的交付日是星期四。", source_kind="manual")["record"]
    event = AgentInteractionEvent(
        agent_name="test-agent", agent_version="1", session_id="recovery",
        turn_id="turn-1", sequence=1, event_type="user_message", role="user",
        content=record.content, event_id="evt-recovery",
    )
    service.meta.insert_agent_event(event)
    service.meta.link_memory_event(record.memory_id, event.event_id, "source")
    return record


def test_interrupted_physical_erasure_recovers_on_reopen(tmp_path, monkeypatch):
    service = _service(tmp_path)
    record = _memory_with_event(service)

    def fail_purge(_memory_ids):
        raise OSError("simulated segment failure")

    monkeypatch.setattr(service.store, "purge_ids", fail_purge)
    with pytest.raises(OSError, match="simulated segment failure"):
        service.forget_memory_ids([record.memory_id], "user request", compact=True)

    assert service.meta.get_memory(record.memory_id)["deleted"] == 1
    assert service.meta.pending_erasures()[0]["memory_id"] == record.memory_id
    assert service.search("白鹭项目", audit=False) == []
    assert service.meta.memory_event_evidence(record.memory_id)[0]["content"] == ""
    assert service.store.raw_contains(record.content)
    service.close()

    recovered = _service(tmp_path)
    try:
        assert recovered.meta.pending_erasures() == []
        assert not recovered.store.raw_contains(record.content)
        assert recovered.store.get_latest(record.memory_id) is None
        assert recovered.meta.verify_audit_chain()["ok"] is True
        assert recovered.verify_forget_probe(record.memory_id, record.content)["irreversible_ok"] is True
    finally:
        recovered.close()


def test_failed_sqlite_erasure_rolls_back_all_managed_surfaces(tmp_path, monkeypatch):
    service = _service(tmp_path)
    record = _memory_with_event(service)

    def fail_audit(*_args):
        raise OSError("simulated audit failure")

    monkeypatch.setattr(service.meta, "_redact_audit_payloads_no_commit", fail_audit)
    try:
        with pytest.raises(OSError, match="simulated audit failure"):
            service.forget_memory_ids([record.memory_id], "user request", compact=True)
        assert service.meta.pending_erasures() == []
        assert service.meta.get_memory(record.memory_id)["deleted"] == 0
        assert service.meta.memory_event_evidence(record.memory_id)[0]["content"] == record.content
        assert service.store.get_latest(record.memory_id) is not None
    finally:
        service.close()


def test_interrupted_logical_erasure_recovers_without_claiming_physical_purge(tmp_path, monkeypatch):
    service = _service(tmp_path)
    record = service.ingest_text("白鹭项目的审批人是林工。", source_kind="manual")["record"]

    def fail_tombstone(*_args, **_kwargs):
        raise OSError("simulated append failure")

    monkeypatch.setattr(service.store, "tombstone", fail_tombstone)
    with pytest.raises(OSError, match="simulated append failure"):
        service.forget_memory_ids([record.memory_id], "user request", compact=False)
    assert service.meta.get_memory(record.memory_id)["deleted"] == 1
    assert service.update_memory(record.memory_id, "审批人改为王工")["updated"] is False
    assert service.rollback(record.memory_id, record.version.version_id)["rolled_back"] is False
    service.close()

    recovered = _service(tmp_path)
    try:
        assert recovered.meta.pending_erasures() == []
        assert recovered.store.get_latest(record.memory_id) is None
        assert recovered.store.raw_contains(record.content)
        assert recovered.verify_forget_probe(record.memory_id, record.content)["irreversible_ok"] is False
    finally:
        recovered.close()


def test_cleanup_failure_keeps_recovery_marker(tmp_path, monkeypatch):
    service = _service(tmp_path)
    record = service.ingest_text("白鹭项目的发布窗口是周末。", source_kind="manual")["record"]

    def fail_cleanup(*_args, **_kwargs):
        raise RuntimeError("simulated checkpoint busy")

    monkeypatch.setattr(service.meta, "secure_cleanup", fail_cleanup)
    with pytest.raises(RuntimeError, match="checkpoint busy"):
        service.forget_memory_ids([record.memory_id], "user request", compact=True)
    assert service.meta.pending_erasures()
    assert not service.store.raw_contains(record.content)
    service.close()

    recovered = _service(tmp_path)
    try:
        assert recovered.meta.pending_erasures() == []
        assert recovered.meta.get_memory(record.memory_id)["deleted"] == 1
        assert recovered.meta.verify_audit_chain()["ok"] is True
    finally:
        recovered.close()


def test_deleted_id_cannot_be_reactivated_by_stale_writer(tmp_path):
    first = _service(tmp_path)
    second = _service(tmp_path)
    try:
        record = first.ingest_text("白鹭项目使用离线构建。", source_kind="manual")["record"]
        assert first.forget_memory_ids([record.memory_id], "user request", compact=True)["ok"]
        with pytest.raises(ValueError, match="cannot be reactivated"):
            second.meta.upsert_memory(record)
        assert first.meta.get_memory(record.memory_id)["deleted"] == 1
        assert second.meta.get_memory(record.memory_id)["deleted"] == 1
        assert second.promote_memory(record.memory_id, "long_term")["promoted"] is False
        assert second.versions(record.memory_id)[-1]["op"] == "forget"
        assert record.content not in str(second.versions(record.memory_id))
    finally:
        first.close()
        second.close()


def test_batch_forget_rolls_back_when_one_preview_becomes_stale(tmp_path, monkeypatch):
    service = _service(tmp_path)
    try:
        first = service.ingest_text("白鹭项目的交付日是周四。", source_kind="manual")["record"]
        second = service.ingest_text("青岚项目的负责人是林工。", source_kind="manual")["record"]
        versions = {
            record.memory_id: record.version.version_id for record in (first, second)
        }
        begin = service._begin_memory_erasures

        def concurrent_update(records, *, compact):
            updated = service.update_memory(second.memory_id, "青岚项目的负责人改为王工。")
            assert updated["updated"]
            return begin(records, compact=compact)

        monkeypatch.setattr(service, "_begin_memory_erasures", concurrent_update)
        with pytest.raises(ValueError, match="stale memory version"):
            service.forget_memory_ids(
                [first.memory_id, second.memory_id], "user request",
                compact=True, expected_versions=versions,
            )
        assert service.meta.pending_erasures() == []
        assert service.meta.get_memory(first.memory_id)["deleted"] == 0
        assert service.meta.get_memory(second.memory_id)["deleted"] == 0
        assert "王工" in service.meta.get_memory(second.memory_id)["content"]
    finally:
        service.close()


def test_batch_erasure_scrubs_shared_event_once_and_keeps_audit_valid(tmp_path):
    service = _service(tmp_path)
    try:
        first = service.ingest_text("白鹭项目的维护窗口为周四。", source_kind="manual")["record"]
        second = service.ingest_text("青岚项目的维护窗口为周五。", source_kind="manual")["record"]
        event = AgentInteractionEvent(
            agent_name="test-agent", agent_version="1", session_id="batch",
            turn_id="turn-1", sequence=1, event_type="user_message", role="user",
            content="%s %s" % (first.content, second.content), event_id="evt-batch",
        )
        service.meta.insert_agent_event(event)
        for record in (first, second):
            service.meta.link_memory_event(record.memory_id, event.event_id, "source")

        result = service.forget_memory_ids(
            [first.memory_id, second.memory_id], "user request", compact=True,
        )
        assert result["ok"] is True
        assert result["scrubbed_event_count"] == 1
        assert service.meta.pending_erasures() == []
        assert service.meta.verify_audit_chain()["ok"] is True
        assert not service.store.raw_contains(first.content)
        assert not service.store.raw_contains(second.content)
    finally:
        service.close()
