from pathlib import Path

from memguard.service import MemGuardService


def _managed_files(root: Path):
    for path in root.iterdir():
        if path.is_file() and path.suffix in {".segment", ".sqlite3", ".sqlite3-wal", ".sqlite3-shm"}:
            yield path


def test_compact_forget_removes_plaintext_from_managed_storage(tmp_path):
    sentinel = "DELTA-ORCHID-9417-PRIVATE-WORKFLOW"
    service = MemGuardService(tmp_path / "memory.segment", tmp_path / "meta.sqlite3")
    try:
        record = service.ingest_text(
            "知识：%s 仅用于精准遗忘持久化清理验证。" % sentinel,
            source_kind="manual",
        )["record"]
        assert service.store.raw_contains(sentinel)

        result = service.forget(sentinel, confirm=True, compact=True)
        row = service.meta.get_memory(record.memory_id)

        assert result["forgotten"] == [record.memory_id]
        assert result["storage_cleanup"]["vacuum"] is True
        assert result["audit_payloads_redacted"] >= 1
        assert result["audit_chain_ok"] is True
        assert row["deleted"] == 1
        assert row["content"] == ""
        assert row["source_uri"] == ""
        assert row["preference_value"] == ""
        assert row["governance_reason"] == ""
        assert row["governance_state"] == "forgotten"
        assert service.meta.memory_graph(record.memory_id)["entities"] == []
        assert not service.store.raw_contains(sentinel)
    finally:
        service.close()

    needle = sentinel.encode("utf-8")
    for path in _managed_files(tmp_path):
        assert needle not in path.read_bytes(), "%s 仍包含已遗忘明文" % path.name
