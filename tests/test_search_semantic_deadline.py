from time import monotonic, sleep

from memguard.service import MemGuardService


def test_search_recall_and_rerank_share_one_semantic_deadline(tmp_path, monkeypatch):
    service = MemGuardService(tmp_path / "memory.segment", tmp_path / "memory.sqlite3")
    try:
        service.ingest_text("systemd 服务用 journalctl 查看启动日志。")
        deadlines = []
        original_rank = service.retriever.rank

        def recall(*args, deadline, **kwargs):
            deadlines.append(deadline)
            sleep(0.025)
            return [], None, "no_ready_vectors"

        def rank(*args, deadline, **kwargs):
            deadlines.append(deadline)
            assert deadline - monotonic() < 0.145
            return original_rank(*args, deadline=deadline, **kwargs)

        service.retriever.kylin_rerank_budget_ms = 160
        monkeypatch.setattr(service, "_dense_candidate_ids", recall)
        monkeypatch.setattr(service.retriever, "rank", rank)
        assert service.search("systemd 日志", audit=False)
        assert len(deadlines) == 2 and deadlines[0] == deadlines[1]
    finally:
        service.close()


def test_dense_recall_expired_budget_never_starts_embedding(tmp_path):
    service = MemGuardService(tmp_path / "memory.segment", tmp_path / "memory.sqlite3")
    try:
        service.dense_candidates_enabled = True
        service.retriever.kylin_provider = object()
        ids, vector, status = service._dense_candidate_ids(
            "systemd 日志", 5, deadline=monotonic() - 1,
        )
        assert ids == [] and vector is None and status == "latency_budget_exceeded"
    finally:
        service.close()
