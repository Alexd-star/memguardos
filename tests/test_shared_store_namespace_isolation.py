import pytest

from memguard.service import MemGuardService


def _service(tmp_path, namespace, *, segment="shared.segment", database="shared.sqlite3"):
    return MemGuardService(
        tmp_path / segment, tmp_path / database, namespace=namespace,
    )


def test_two_namespaces_cannot_share_a_metadata_database(tmp_path):
    alpha = _service(tmp_path, "alpha")
    try:
        own = alpha.ingest_text("北辰项目发布日期是周三。")["record"]
        with pytest.raises(ValueError, match="another namespace"):
            _service(tmp_path, "beta", segment="beta.segment")
        assert [row["memory_id"] for row in alpha.search("北辰项目发布日")] == [own.memory_id]
        assert alpha.memory_detail(own.memory_id)["found"] is True
    finally:
        alpha.close()


def test_two_namespaces_cannot_share_a_segment_with_separate_databases(tmp_path):
    alpha = _service(tmp_path, "alpha", database="alpha.sqlite3")
    try:
        alpha.ingest_text("北辰项目发布日期是周三。")
        with pytest.raises(ValueError, match="another namespace"):
            _service(tmp_path, "beta", database="beta.sqlite3")
    finally:
        alpha.close()


def test_same_namespace_can_reopen_both_storage_files(tmp_path):
    first = _service(tmp_path, "alpha")
    try:
        memory_id = first.ingest_text("北辰项目发布日期是周三。")["record"].memory_id
    finally:
        first.close()
    second = _service(tmp_path, "alpha")
    try:
        assert second.memory_detail(memory_id)["found"] is True
    finally:
        second.close()


def test_bound_segment_reopen_does_not_scan_history(tmp_path, monkeypatch):
    from memguard.store.mmap_store import AppendOnlyMemoryStore

    segment = AppendOnlyMemoryStore(tmp_path / "shared.segment")
    segment.bind_namespace("alpha")
    reopened = AppendOnlyMemoryStore(tmp_path / "shared.segment")
    monkeypatch.setattr(
        reopened, "_scan_unlocked",
        lambda: (_ for _ in ()).throw(AssertionError("bound startup scanned segment")),
    )
    reopened.bind_namespace("alpha")


def test_unbound_legacy_database_and_segment_adopt_matching_namespace(tmp_path):
    from memguard.models import MemorySource, make_memory
    from memguard.store.mmap_store import AppendOnlyMemoryStore
    from memguard.store.sqlite_meta import SQLiteMetaStore

    segment = AppendOnlyMemoryStore(tmp_path / "shared.segment")
    meta = SQLiteMetaStore(tmp_path / "shared.sqlite3")
    record = make_memory(
        content="北辰项目发布日期是周三。", memory_type="knowledge",
        source=MemorySource(kind="manual"), scope="alpha",
    )
    segment.append(record)
    meta.upsert_memory(record)
    meta.close()

    adopted = _service(tmp_path, "alpha")
    try:
        assert adopted.memory_detail(record.memory_id)["found"] is True
    finally:
        adopted.close()


def test_default_namespace_adopts_legacy_user_scope(tmp_path):
    from memguard.models import MemorySource, make_memory
    from memguard.store.mmap_store import AppendOnlyMemoryStore
    from memguard.store.sqlite_meta import SQLiteMetaStore

    segment = AppendOnlyMemoryStore(tmp_path / "shared.segment")
    meta = SQLiteMetaStore(tmp_path / "shared.sqlite3")
    record = make_memory(
        content="北辰项目发布日期是周三。", memory_type="knowledge",
        source=MemorySource(kind="manual"),
    )
    assert record.scope == "user"
    segment.append(record)
    meta.upsert_memory(record)
    meta.close()

    adopted = _service(tmp_path, "default")
    try:
        assert adopted.memory_detail(record.memory_id)["found"] is True
    finally:
        adopted.close()


def test_pending_write_from_other_namespace_is_rejected_before_replay(tmp_path):
    from memguard.models import MemorySource, make_memory
    from memguard.retrieval.vector_cache import vector_content_hash
    from memguard.store.sqlite_meta import SQLiteMetaStore

    meta = SQLiteMetaStore(tmp_path / "shared.sqlite3")
    record = make_memory(
        content="北辰项目发布日期是周三。", memory_type="knowledge",
        source=MemorySource(kind="manual"), scope="alpha",
    )
    meta.stage_new_memory_write(
        record, "kylin_sdk", "kylin-sdk-v1",
        vector_content_hash(record.summary, record.content),
    )
    meta.close()

    with pytest.raises(ValueError, match="another namespace"):
        _service(tmp_path, "beta")
    unchanged = SQLiteMetaStore(tmp_path / "shared.sqlite3")
    try:
        assert unchanged.pending_memory_writes()[0]["memory_id"] == record.memory_id
        assert unchanged.get_memory(record.memory_id) is None
    finally:
        unchanged.close()
