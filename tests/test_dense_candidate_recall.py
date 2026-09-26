from pathlib import Path
from time import sleep

from memguard.retrieval.vector_cache import vector_content_hash
from memguard.service import MemGuardService


class FixedProvider:
    def __init__(self, vector):
        self.result = vector
        self.calls = 0

    def vector(self, text, timeout_seconds=None):
        self.calls += 1
        return self.result


def make_service(tmp_path: Path, monkeypatch, *, dense=True) -> MemGuardService:
    monkeypatch.setenv("MEMGUARD_EMBEDDING_BACKEND", "kylin")
    if dense:
        monkeypatch.setenv("MEMGUARD_DENSE_CANDIDATES", "1")
    else:
        monkeypatch.delenv("MEMGUARD_DENSE_CANDIDATES", raising=False)
    return MemGuardService(
        store_path=tmp_path / "memories.jsonl",
        db_path=tmp_path / "meta.sqlite3",
        namespace="default",
    )


def ready_vector(service, memory_id, vector, *, revision=None):
    record = service.meta.get_memory(memory_id)
    service.meta.upsert_memory_embedding(
        memory_id,
        vector_content_hash(record["summary"], record["content"]),
        service.EMBEDDING_BACKEND,
        revision or service.EMBEDDING_MODEL_REVISION,
        vector,
    )


def test_dense_recalls_memory_missing_from_lexical_candidates(tmp_path, monkeypatch):
    service = make_service(tmp_path, monkeypatch)
    try:
        memory_id = service.ingest_text(
            "会议纪要请使用清晰的小标题", source_kind="manual"
        )["record"].memory_id
        ready_vector(service, memory_id, [1.0, 0.0])
        provider = FixedProvider([1.0, 0.0])
        service.retriever.kylin_provider = provider
        monkeypatch.setattr(service, "_search_candidates", lambda *args, **kwargs: [])

        hits = service.search("meeting minutes format", limit=3)

        assert [hit["memory_id"] for hit in hits] == [memory_id]
        assert hits[0]["retrieval_explanation"]["fusion_sources"] == ["dense"]
        assert hits[0]["retrieval_explanation"]["dense_candidate_status"] == "ready"
        assert hits[0]["vector_backend"] == "kylin_sdk_dense"
        assert hits[0]["match_reason"] == "已保存的语义向量与查询匹配"
        assert provider.calls == 1
    finally:
        service.close()


def test_dense_requires_current_authorized_vector_and_complete_scan(tmp_path, monkeypatch):
    service = make_service(tmp_path, monkeypatch)
    try:
        approved = service.ingest_text("批准保留的会议记录", source_kind="manual")["record"]
        withheld = service.ingest_text("待审查的会议记录", source_kind="manual")["record"]
        old_model = service.ingest_text("旧模型的会议记录", source_kind="manual")["record"]
        ready_vector(service, approved.memory_id, [1.0, 0.0])
        ready_vector(service, withheld.memory_id, [1.0, 0.0])
        ready_vector(service, old_model.memory_id, [1.0, 0.0], revision="old-model")
        service.meta.set_governance_state(withheld.memory_id, "quarantined", "untrusted", 0.0, "review")
        vectors, complete = service.meta.list_dense_candidate_vectors(
            service.EMBEDDING_BACKEND, service.EMBEDDING_MODEL_REVISION, "default", 1
        )
        assert complete is True
        assert [memory_id for memory_id, _ in vectors] == [approved.memory_id]

        extra = service.ingest_text("另一条批准保留的会议记录", source_kind="manual")["record"]
        ready_vector(service, extra.memory_id, [0.0, 1.0])
        vectors, complete = service.meta.list_dense_candidate_vectors(
            service.EMBEDDING_BACKEND, service.EMBEDDING_MODEL_REVISION, "default", 1
        )
        assert vectors == []
        assert complete is False
        monkeypatch.setenv("MEMGUARD_DENSE_SCAN_LIMIT", "1")
        service.retriever.kylin_provider = FixedProvider([1.0, 0.0])
        ids, query_vector, status = service._dense_candidate_ids("meeting notes", 10)
        assert (ids, query_vector, status) == ([], None, "scan_limit_exceeded")
        assert service.retriever.kylin_provider.calls == 0
    finally:
        service.close()


def test_dense_falls_back_on_provider_failure_and_cache_refreshes_on_backfill(tmp_path, monkeypatch):
    service = make_service(tmp_path, monkeypatch)
    try:
        record = service.ingest_text("本地备份保留七天", source_kind="manual")["record"]
        monkeypatch.setattr(service, "_search_candidates", lambda *args, **kwargs: [])
        assert service.search("backup retention policy", limit=3) == []
        ready_vector(service, record.memory_id, [1.0, 0.0])
        service.retriever.kylin_provider = FixedProvider(None)
        assert service.search("backup retention policy", limit=3) == []
        service.retriever.kylin_provider = FixedProvider([1.0, 0.0])
        hits = service.search("backup retention policy", limit=3)
        assert hits[0]["memory_id"] == record.memory_id
    finally:
        service.close()


def test_dense_is_disabled_by_default(tmp_path, monkeypatch):
    service = make_service(tmp_path, monkeypatch, dense=False)
    try:
        record = service.ingest_text("会议纪要请使用清晰的小标题", source_kind="manual")["record"]
        ready_vector(service, record.memory_id, [1.0, 0.0])
        service.retriever.kylin_provider = FixedProvider([1.0, 0.0])
        monkeypatch.setattr(service, "_search_candidates", lambda *args, **kwargs: [])
        assert service.search("meeting minutes format", limit=3) == []
        assert service.retriever.kylin_provider.calls == 0
    finally:
        service.close()


def test_dense_rejects_stale_and_mismatched_dimensions(tmp_path, monkeypatch):
    service = make_service(tmp_path, monkeypatch)
    try:
        stale = service.ingest_text("上周的备份策略保留七天", source_kind="manual")["record"]
        wrong_dim = service.ingest_text("另一个备份策略保留两周", source_kind="manual")["record"]
        ready_vector(service, stale.memory_id, [1.0, 0.0])
        ready_vector(service, wrong_dim.memory_id, [1.0, 0.0, 0.0])
        service.meta.conn.execute(
            "UPDATE memory SET content = ? WHERE memory_id = ?",
            ("备份策略已改为三十天", stale.memory_id),
        )
        service.meta.conn.commit()
        vectors, complete = service.meta.list_dense_candidate_vectors(
            service.EMBEDDING_BACKEND, service.EMBEDDING_MODEL_REVISION, "default", 10
        )
        assert complete is True
        assert [memory_id for memory_id, _ in vectors] == [wrong_dim.memory_id]

        service.retriever.kylin_provider = FixedProvider([1.0, 0.0])
        ids, _, status = service._dense_candidate_ids("backup policy", 10)
        assert ids == []
        assert status == "ready"
    finally:
        service.close()


def test_dense_timeout_does_not_retry_provider_during_fallback(tmp_path, monkeypatch):
    service = make_service(tmp_path, monkeypatch)
    try:
        record = service.ingest_text("本地备份保留七天", source_kind="manual")["record"]
        ready_vector(service, record.memory_id, [1.0, 0.0])
        service.retriever.kylin_rerank_budget_ms = 1

        class SlowProvider(FixedProvider):
            def vector(self, text, timeout_seconds=None):
                sleep(0.01)
                return super().vector(text, timeout_seconds)

        provider = SlowProvider([1.0, 0.0])
        service.retriever.kylin_provider = provider
        monkeypatch.setattr(service, "_search_candidates", lambda *args, **kwargs: [])
        assert service.search("backup retention policy", limit=3) == []
        assert provider.calls == 1
    finally:
        service.close()


def test_dense_uses_cosine_not_raw_dot_product(tmp_path, monkeypatch):
    service = make_service(tmp_path, monkeypatch)
    try:
        record = service.ingest_text("会议纪要采用标准模板", source_kind="manual")["record"]
        ready_vector(service, record.memory_id, [100.0, 0.0])
        service.retriever.kylin_provider = FixedProvider([0.1, 0.995])
        ids, _, status = service._dense_candidate_ids("meeting notes", 10)
        assert status == "ready"
        assert ids == []
    finally:
        service.close()
