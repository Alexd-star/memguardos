from pathlib import Path

import pytest

from memguard.retrieval.vector_cache import vector_content_hash
from memguard.service import MemGuardService


def make_service(tmp_path: Path, namespace: str) -> MemGuardService:
    return MemGuardService(
        store_path=tmp_path / (namespace + ".jsonl"),
        db_path=tmp_path / (namespace + ".sqlite3"),
        namespace=namespace,
    )


class FakeKylinProvider:
    def __init__(self, vectors):
        self.vectors = list(vectors)
        self.calls = []

    def vector(self, text, timeout_seconds=None):
        self.calls.append({"text": text, "timeout_seconds": timeout_seconds})
        return self.vectors.pop(0)


class CountingProvider:
    def __init__(self, vector):
        self.result = vector
        self.calls = []

    def vector(self, text, timeout_seconds=None):
        self.calls.append({"text": text, "timeout_seconds": timeout_seconds})
        return self.result

    def status(self):
        return {"enabled": True, "test_double": True}


def test_backfill_persists_ready_vector_without_query_time_candidate_calls(tmp_path: Path):
    service = make_service(tmp_path, "backfill")
    try:
        result = service.ingest_text(
            "用户偏好使用 rg 搜索代码", source_kind="agent_interaction"
        )
        provider = FakeKylinProvider([[0.6, 0.8]])
        service.retriever.kylin_provider = provider

        report = service.backfill_embeddings(limit=10, timeout_seconds=1.0)

        record = result["record"]
        content_hash = vector_content_hash(record.summary, record.content)
        cached = service.meta.get_memory_embedding(
            record.memory_id,
            content_hash,
            service.EMBEDDING_BACKEND,
            service.EMBEDDING_MODEL_REVISION,
        )
        assert report["processed"] == 1
        assert report["ready"] == 1
        assert report["failed"] == 0
        assert report["remaining"] == 0
        assert record.memory_id in report["memory_ids"]
        assert cached is not None
        assert cached.vector == pytest.approx([0.6, 0.8])
        assert len(provider.calls) == 1
        assert 0 < provider.calls[0]["timeout_seconds"] <= 1.0
    finally:
        service.close()


def test_backfill_records_failure_and_returns_within_budget(tmp_path: Path):
    service = make_service(tmp_path, "backfill-failure")
    try:
        service.ingest_text("工具失败后改用 ripgrep", source_kind="agent_interaction")
        service.retriever.kylin_provider = FakeKylinProvider([None])

        report = service.backfill_embeddings(limit=10, timeout_seconds=0.2)

        assert report["failed"] == 1
        assert report["elapsed_ms"] < 500
        status = service.embedding_cache_status()
        assert status["failed"] == 1
        assert status["retry_deferred"] == 1
        assert status["next_retry_at"]
    finally:
        service.close()


def test_backfill_reports_deferred_retry_without_busy_loop(tmp_path: Path):
    service = make_service(tmp_path, "backfill-deferred")
    try:
        service.ingest_text("工具失败后改用 ripgrep", source_kind="agent_interaction")
        service.retriever.kylin_provider = FakeKylinProvider([None])
        first = service.backfill_embeddings(limit=10, timeout_seconds=0.2)
        assert first["failed"] == 1

        service.retriever.kylin_provider = FakeKylinProvider([])
        second = service.backfill_embeddings(limit=10, timeout_seconds=0.2)

        assert second["processed"] == 0
        assert second["due_remaining"] == 0
        assert second["retry_deferred"] == 1
        assert second["next_retry_at"]
        assert second["has_more"] is False
    finally:
        service.close()


def test_backfill_distinguishes_empty_queue_from_completed_scan(tmp_path: Path):
    service = make_service(tmp_path, "backfill-scan-completion")
    try:
        first_record = service.ingest_text(
            "偏好使用 rg 搜索代码", source_kind="agent_interaction"
        )["record"]
        second_record = service.ingest_text(
            "偏好先给出可复制命令", source_kind="agent_interaction"
        )["record"]
        # Simulate historical records that predate automatic vector-job creation.
        service.meta.delete_memory_embeddings(first_record.memory_id)
        service.meta.delete_memory_embeddings(second_record.memory_id)
        service.retriever.kylin_provider = FakeKylinProvider(
            [[1.0, 0.0], [0.0, 1.0]]
        )

        first = service.backfill_embeddings(limit=1, timeout_seconds=1.0)
        assert first["queue_remaining"] == 0
        assert first["scan_complete"] is False
        assert first["remaining"] is None
        assert first["has_more"] is True

        reports = [first]
        for _ in range(3):
            if not reports[-1]["has_more"]:
                break
            reports.append(
                service.backfill_embeddings(limit=1, timeout_seconds=1.0)
            )
        assert reports[-1]["scan_complete"] is True
        assert reports[-1]["remaining"] == 0
        assert reports[-1]["has_more"] is False
    finally:
        service.close()


def test_embedding_cache_cli_status(tmp_path, monkeypatch, capsys):
    from memguard.cli.memctl import main

    monkeypatch.setenv("MEMGUARD_HOME", str(tmp_path))
    assert main(["--namespace", "cli-cache", "embedding-cache", "status"]) == 0
    output = capsys.readouterr().out
    assert '"backend": "kylin_sdk"' in output
    assert '"pending"' in output
    assert '"retry_deferred"' in output


def test_rank_uses_one_query_embedding_and_no_candidate_sdk_calls(tmp_path: Path):
    service = make_service(tmp_path, "query-cache")
    try:
        result = service.ingest_text(
            "用户明确偏好使用 rg 搜索代码", source_kind="agent_interaction"
        )
        record = result["record"]
        content_hash = vector_content_hash(record.summary, record.content)
        service.meta.upsert_memory_embedding(
            record.memory_id,
            content_hash,
            service.EMBEDDING_BACKEND,
            service.EMBEDDING_MODEL_REVISION,
            [1.0, 0.0],
        )
        provider = CountingProvider([1.0, 0.0])
        service.retriever.kylin_provider = provider

        results = service.search("代码搜索工具偏好", limit=5)

        assert results[0]["memory_id"] == record.memory_id
        assert len(provider.calls) == 1
        status = service.retriever.status()
        assert status["persisted_vector_hits"] >= 1
        assert status["persisted_vector_misses"] == 0
        assert results[0]["vector_backend"] == "kylin_sdk_persisted"
    finally:
        service.close()


def test_missing_persisted_vector_falls_back_without_candidate_sdk_call(tmp_path: Path):
    service = make_service(tmp_path, "query-fallback")
    try:
        record = service.ingest_text(
            "用户偏好使用 rg 搜索代码", source_kind="agent_interaction"
        )["record"]
        service.meta.delete_memory_embeddings(record.memory_id)
        provider = CountingProvider([1.0, 0.0])
        service.retriever.kylin_provider = provider

        results = service.search("rg 搜索", limit=5)

        assert results
        assert len(provider.calls) == 1
        assert service.retriever.status()["persisted_vector_misses"] >= 1
        assert results[0]["vector_backend"] == "hash"
    finally:
        service.close()
