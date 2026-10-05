import subprocess
import sys
import time
from concurrent.futures import ThreadPoolExecutor
from threading import Barrier, Event

import pytest

from memguard.retrieval import hybrid, kylin_sdk
from memguard.retrieval.hybrid import HybridRetriever, KylinEmbeddingProvider
from memguard.retrieval.kylin_sdk import KylinEmbeddingBackend


_EMBED_IN_SUBPROCESS = KylinEmbeddingBackend.embed_in_subprocess


@pytest.fixture(autouse=True)
def no_real_sdk(monkeypatch):
    def unexpected(*args, **kwargs):
        pytest.fail("Tests must never invoke the real embedding SDK")

    monkeypatch.setattr(KylinEmbeddingBackend, "embed_in_subprocess", staticmethod(unexpected))


def sleeping_child(monkeypatch):
    real_run = subprocess.run
    timeouts = []

    def run(_args, **kwargs):
        timeouts.append(kwargs["timeout"])
        return real_run(
            [sys.executable, "-c", 'import time; time.sleep(0.65); print(\'{"ok": true, "vector": [1, 0]}\')'],
            **kwargs,
        )

    monkeypatch.setattr(kylin_sdk.subprocess, "run", run)
    monkeypatch.setattr(KylinEmbeddingBackend, "embed_in_subprocess", staticmethod(_EMBED_IN_SUBPROCESS))
    return timeouts


def records():
    return [
        {"memory_id": f"m{index}", "type": "knowledge", "summary": "systemd MemoryMax", "content": "memory limit"}
        for index in range(2)
    ]


def test_sdk_subsecond_timeout_uses_real_sleeping_child(monkeypatch):
    timeouts = sleeping_child(monkeypatch)
    started = time.monotonic()
    result = KylinEmbeddingBackend.embed_in_subprocess("query", 0.160)
    elapsed = time.monotonic() - started

    assert result["ok"] is False
    assert "timeout" in result["error"]
    assert 0 < timeouts[0] <= 0.160
    assert 0.10 <= elapsed < 0.60, elapsed


@pytest.mark.parametrize("timeout", [0.0, -1.0])
def test_sdk_exhausted_timeout_does_not_spawn(monkeypatch, timeout):
    monkeypatch.setattr(kylin_sdk.subprocess, "run", lambda *a, **kw: pytest.fail("No budget to spawn"))
    assert _EMBED_IN_SUBPROCESS("query", timeout)["ok"] is False


def test_sdk_explicit_long_probe_timeout_is_preserved(monkeypatch):
    observed = {}

    def run(args, **kwargs):
        observed.update(kwargs)
        assert "result.pop" in args[-1]
        return subprocess.CompletedProcess(args, 0, '{"ok": true, "dim": 2}', "")

    monkeypatch.setattr(kylin_sdk.subprocess, "run", run)
    result = _EMBED_IN_SUBPROCESS("probe", 45.0, include_vector=False)
    assert result["ok"] is True
    assert 44.0 < observed["timeout"] <= 45.0
    assert "vector" not in result


@pytest.mark.parametrize("mode", ["spawn_error", "non_json", "failed_exit"])
def test_sdk_process_failures_are_reports(monkeypatch, mode):
    def run(args, **kwargs):
        if mode == "spawn_error":
            raise OSError("spawn failed")
        output = "not json" if mode == "non_json" else '{"ok": true, "vector": [1, 0]}'
        return subprocess.CompletedProcess(args, 1, output, "failure")

    monkeypatch.setattr(kylin_sdk.subprocess, "run", run)
    assert _EMBED_IN_SUBPROCESS("query", 0.16)["ok"] is False


@pytest.mark.parametrize("timeout", [0.0, -1.0])
def test_provider_exhausted_budget_never_calls_sdk(timeout):
    provider = KylinEmbeddingProvider()
    assert provider.vector("query", timeout_seconds=timeout) is None
    assert provider.failures == 0
    assert provider._inflight == {}


def test_provider_does_not_raise_tiny_timeout_to_ten_ms(monkeypatch):
    observed = []

    def embed(text, timeout_seconds, include_vector=True):
        observed.append(timeout_seconds)
        return {"ok": True, "vector": [3.0, 4.0]}

    monkeypatch.setattr(KylinEmbeddingBackend, "embed_in_subprocess", staticmethod(embed))
    provider = KylinEmbeddingProvider(timeout_seconds=0.005)
    assert provider.vector("query", timeout_seconds=0.004) == [0.6, 0.8]
    assert 0 < observed[0] <= 0.004


def test_provider_expired_absolute_deadline_also_blocks_cache(monkeypatch):
    monkeypatch.setattr(
        KylinEmbeddingBackend, "embed_in_subprocess",
        staticmethod(lambda *a, **kw: {"ok": True, "vector": [1.0, 0.0]}),
    )
    provider = KylinEmbeddingProvider()
    assert provider.vector("query") == [1.0, 0.0]
    assert provider.vector("query", deadline=time.monotonic() - 1.0) is None
    assert provider.failures == 0


def test_provider_lock_wait_is_bounded_by_real_clock():
    provider = KylinEmbeddingProvider(clock=lambda: 100.0)

    def waiting_call():
        started = time.monotonic()
        return provider.vector("query", timeout_seconds=0.060), time.monotonic() - started

    with ThreadPoolExecutor(max_workers=1) as executor:
        provider._lock.acquire()
        try:
            result, elapsed = executor.submit(waiting_call).result(timeout=1.0)
        finally:
            provider._lock.release()
    assert result is None
    assert 0.04 <= elapsed < 0.25, elapsed
    assert provider.failures == 0


def test_provider_lock_wait_reduces_sdk_timeout(monkeypatch):
    entered = Event()
    observed = []

    def embed(text, timeout_seconds, include_vector=True):
        observed.append(timeout_seconds)
        return {"ok": True, "vector": [1.0, 0.0]}

    monkeypatch.setattr(KylinEmbeddingBackend, "embed_in_subprocess", staticmethod(embed))
    provider = KylinEmbeddingProvider()

    def waiting_call():
        entered.set()
        return provider.vector("query", timeout_seconds=0.200)

    with ThreadPoolExecutor(max_workers=1) as executor:
        provider._lock.acquire()
        try:
            future = executor.submit(waiting_call)
            assert entered.wait(1.0)
            time.sleep(0.060)
        finally:
            provider._lock.release()
        assert future.result(timeout=1.0) == [1.0, 0.0]
    assert 0 < observed[0] < 0.160


def test_singleflight_waiter_deadline_does_not_cancel_owner(monkeypatch):
    entered = Event()
    release = Event()
    calls = []

    def embed(text, timeout_seconds, include_vector=True):
        calls.append(text)
        entered.set()
        assert release.wait(1.0)
        return {"ok": True, "vector": [1.0, 0.0]}

    monkeypatch.setattr(KylinEmbeddingBackend, "embed_in_subprocess", staticmethod(embed))
    provider = KylinEmbeddingProvider()
    with ThreadPoolExecutor(max_workers=1) as executor:
        owner = executor.submit(provider.vector, "same query", 1.0)
        try:
            assert entered.wait(1.0)
            started = time.monotonic()
            assert provider.vector("same query", deadline=started + 0.060) is None
            elapsed = time.monotonic() - started
            assert 0.04 <= elapsed < 0.25, elapsed
            assert provider.failures == 0
            assert provider.disabled_reason == ""
        finally:
            release.set()
        assert owner.result(timeout=1.0) == [1.0, 0.0]
    assert provider.vector("same query") == [1.0, 0.0]
    assert calls == ["same query"]
    assert provider._inflight == {}


@pytest.mark.parametrize("fail", [False, True])
def test_singleflight_concurrent_calls_share_success_or_exception(monkeypatch, fail):
    barrier = Barrier(4)
    waiting = Event()
    release = Event()
    calls = []

    class ObservedFuture(hybrid.Future):
        waiters = 0

        def result(self, timeout=None):
            with provider._lock:
                self.waiters += 1
                if self.waiters == 3:
                    waiting.set()
            return super().result(timeout=timeout)

    def embed(text, timeout_seconds, include_vector=True):
        calls.append(text)
        assert release.wait(1.0)
        if fail:
            raise OSError("temporary SDK error")
        return {"ok": True, "vector": [1.0, 0.0]}

    monkeypatch.setattr(hybrid, "Future", ObservedFuture)
    monkeypatch.setattr(KylinEmbeddingBackend, "embed_in_subprocess", staticmethod(embed))
    provider = KylinEmbeddingProvider(max_failures=3)

    def call():
        barrier.wait(timeout=1.0)
        return provider.vector("same query", timeout_seconds=1.0)

    with ThreadPoolExecutor(max_workers=4) as executor:
        futures = [executor.submit(call) for _ in range(4)]
        try:
            assert waiting.wait(1.0)
        finally:
            release.set()
        results = [future.result(timeout=1.0) for future in futures]
    assert results == ([None] * 4 if fail else [[1.0, 0.0]] * 4)
    assert calls == ["same query"]
    assert provider.failures == (1 if fail else 0)
    assert provider._inflight == {}


def test_provider_late_success_is_not_cached(monkeypatch):
    def embed(*args, **kwargs):
        time.sleep(0.030)
        return {"ok": True, "vector": [1.0, 0.0]}

    monkeypatch.setattr(KylinEmbeddingBackend, "embed_in_subprocess", staticmethod(embed))
    provider = KylinEmbeddingProvider()
    assert provider.vector("query", timeout_seconds=0.010) is None
    assert provider.status()["failures"] == 1
    assert not provider.cache
    assert provider.failures == 1
    assert provider._inflight == {}


@pytest.mark.parametrize("drain_by", ["vector", "status"])
def test_real_timeouts_defer_bookkeeping_without_losing_cooldown(monkeypatch, drain_by):
    timeouts = sleeping_child(monkeypatch)
    run_child = kylin_sdk.subprocess.run
    entered = Event()
    lock_held = Event()
    waiter_started = Event()
    now = [100.0]
    provider = KylinEmbeddingProvider(max_failures=2, cooldown_seconds=10.0, clock=lambda: now[0])

    class ObservedFuture(hybrid.Future):
        def result(self, timeout=None):
            waiter_started.set()
            return super().result(timeout=timeout)

    def run(args, **kwargs):
        entered.set()
        assert lock_held.wait(1.0)
        return run_child(args, **kwargs)

    monkeypatch.setattr(kylin_sdk.subprocess, "run", run)
    monkeypatch.setattr(hybrid, "Future", ObservedFuture)
    with ThreadPoolExecutor(max_workers=2) as executor:
        for attempt in range(2):
            entered.clear()
            lock_held.clear()
            waiter_started.clear()
            started = time.monotonic()
            owner = executor.submit(provider.vector, "same query", 0.160)
            assert entered.wait(1.0)
            waiter = executor.submit(provider.vector, "same query", 1.0)
            assert waiter_started.wait(1.0)
            provider._lock.acquire()
            try:
                lock_held.set()
                assert owner.result(timeout=0.60) is None
                assert waiter.result(timeout=0.60) is None
                assert time.monotonic() - started < 0.60
                # The owner has returned while the state lock is still held.
                assert provider.failures == attempt
                assert len(provider._inflight) == 1
                assert not provider._completions.empty()
            finally:
                provider._lock.release()
            if drain_by == "status":
                if attempt == 1:
                    now[0] = 106.0
                assert provider.status()["failures"] == attempt + 1
                assert provider.status()["failures"] == attempt + 1

    now[0] = 106.0
    # vector must drain failures before checking the circuit or reusing a done flight.
    assert provider.vector("same query", deadline=time.monotonic() + 0.1) is None
    assert len(timeouts) == 2
    status = provider.status()
    assert status["failures"] == 2
    assert status["degraded"] is True
    assert "timeout" in status["disabled_reason"]
    assert status["last_error_at"] == 100.0
    assert status["disabled_until"] == 110.0
    assert provider._inflight == {}
    assert provider._completions.empty()
    assert provider.vector("different query") is None
    assert provider.status()["failures"] == 2
    assert len(timeouts) == 2

    now[0] = 111.0
    monkeypatch.setattr(
        KylinEmbeddingBackend, "embed_in_subprocess",
        staticmethod(lambda *a, **kw: {"ok": True, "vector": [1.0, 0.0]}),
    )
    assert provider.vector("same query") == [1.0, 0.0]
    assert provider.status()["failures"] == 0
    assert provider.disabled_reason == ""


def test_deferred_success_is_cached_without_waiting_for_metadata_lock(monkeypatch):
    entered = Event()
    release = Event()
    calls = []
    now = [100.0]
    provider = KylinEmbeddingProvider(clock=lambda: now[0])

    def embed(text, timeout_seconds, include_vector=True):
        calls.append(text)
        entered.set()
        assert release.wait(1.0)
        return {"ok": True, "vector": [3.0, 4.0]}

    monkeypatch.setattr(KylinEmbeddingBackend, "embed_in_subprocess", staticmethod(embed))
    with ThreadPoolExecutor(max_workers=1) as executor:
        owner = executor.submit(provider.vector, "query", 1.0)
        assert entered.wait(1.0)
        provider._lock.acquire()
        try:
            started = time.monotonic()
            release.set()
            assert owner.result(timeout=0.30) == [0.6, 0.8]
            assert time.monotonic() - started < 0.30
            assert not provider.cache
            assert not provider._completions.empty()
        finally:
            provider._lock.release()
    now[0] = 106.0
    assert provider.status()["cache_entries"] == 1
    assert provider.vector("query") == [0.6, 0.8]
    assert calls == ["query"]
    assert provider.status()["last_success_at"] == 100.0
    assert provider._inflight == {}
    assert provider._completions.empty()


def test_provider_failed_normalization_recovers_after_cooldown(monkeypatch):
    now = [100.0]
    calls = []

    def embed(*args, **kwargs):
        calls.append(True)
        return {"ok": True, "vector": ["invalid"] if len(calls) == 1 else [1.0, 0.0]}

    monkeypatch.setattr(KylinEmbeddingBackend, "embed_in_subprocess", staticmethod(embed))
    provider = KylinEmbeddingProvider(clock=lambda: now[0], cooldown_seconds=10.0, max_cache_entries=1)
    assert provider.vector("query") is None
    assert provider.vector("query") is None
    assert len(calls) == 1
    now[0] = 111.0
    assert provider.vector("query") == [1.0, 0.0]
    assert provider.vector("other") == [1.0, 0.0]
    assert len(provider.cache) == 1
    assert provider.last_success_at == 111.0
    assert provider.disabled_reason == ""


def test_rank_160_ms_timeout_returns_complete_hash_fallback(monkeypatch):
    timeouts = sleeping_child(monkeypatch)
    retriever = HybridRetriever("kylin")
    retriever.kylin_rerank_budget_ms = 160.0
    fallback = retriever.rank("systemd MemoryMax", records(), allow_kylin_rerank=False)
    started = time.monotonic()
    result = retriever.rank("systemd MemoryMax", records(), persisted_vectors={"m0": [1.0, 0.0]})
    elapsed = time.monotonic() - started
    assert result == fallback
    assert retriever.last_backend_used == "hash"
    assert 0.10 <= elapsed < 0.60, elapsed
    assert 0 < timeouts[0] <= 0.160


def test_rank_candidate_preparation_does_not_reset_budget():
    retriever = HybridRetriever("kylin")
    retriever.kylin_rerank_budget_ms = 20.0

    def slow_records():
        time.sleep(0.030)
        yield from records()

    fallback = retriever.rank("systemd MemoryMax", records(), allow_kylin_rerank=False)
    assert retriever.rank("systemd MemoryMax", slow_records()) == fallback


def test_rank_passes_only_budget_left_after_preparation(monkeypatch):
    observed = []

    def embed(text, timeout_seconds, include_vector=True):
        observed.append(timeout_seconds)
        return {"ok": True, "vector": [1.0, 0.0]}

    monkeypatch.setattr(KylinEmbeddingBackend, "embed_in_subprocess", staticmethod(embed))
    retriever = HybridRetriever("kylin")
    retriever.kylin_rerank_budget_ms = 160.0

    def slow_records():
        time.sleep(0.050)
        yield from records()

    assert retriever.rank("systemd MemoryMax", slow_records())
    assert 0 < observed[0] < 0.120


@pytest.mark.parametrize("expired", [False, True])
def test_rank_zero_or_expired_budget_never_calls_provider(expired):
    retriever = HybridRetriever("kylin")
    retriever.kylin_rerank_budget_ms = 160.0 if expired else 0.0
    fallback = retriever.rank("systemd MemoryMax", records(), allow_kylin_rerank=False)
    deadline = time.monotonic() - 1.0 if expired else None
    assert retriever.rank("systemd MemoryMax", records(), deadline=deadline) == fallback


def test_rank_rerank_deadline_discards_partial_results(monkeypatch):
    retriever = HybridRetriever("kylin")
    retriever.kylin_rerank_budget_ms = 500.0
    fallback = retriever.rank("systemd MemoryMax", records(), allow_kylin_rerank=False)
    original_score = retriever._score_with_vectors
    dense_calls = []

    def score(query, record, query_vector, candidate_vector):
        if len(query_vector) == 2:
            dense_calls.append(record["memory_id"])
            time.sleep(0.040)
        return original_score(query, record, query_vector, candidate_vector)

    monkeypatch.setattr(retriever, "_score_with_vectors", score)
    started = time.monotonic()
    result = retriever.rank(
        "systemd MemoryMax", records(), kylin_query_vector=[1.0, 0.0],
        persisted_vectors={"m0": [1.0, 0.0], "m1": [1.0, 0.0]}, deadline=started + 0.025,
    )
    assert result == fallback
    assert dense_calls == ["m0"]
    assert time.monotonic() - started < 0.20
    assert retriever.persisted_vector_hits == 0
    assert retriever.last_backend_used == "hash"


def test_rank_reuses_supplied_query_vector_and_persisted_vectors():
    retriever = HybridRetriever("kylin")
    result = retriever.rank(
        "systemd MemoryMax", records(), kylin_query_vector=[1.0, 0.0],
        persisted_vectors={"m0": [1.0, 0.0], "m1": [1.0, 0.0]}, deadline=time.monotonic() + 0.1,
    )
    assert len(result) == 2
    assert all(item["_vector_backend"] == "kylin_sdk_persisted" for _, item in result)
    assert retriever.persisted_vector_hits == 2


def test_rank_bad_persisted_vector_transparently_falls_back():
    retriever = HybridRetriever("kylin")
    fallback = retriever.rank("systemd MemoryMax", records(), allow_kylin_rerank=False)
    assert retriever.rank(
        "systemd MemoryMax", records(), kylin_query_vector=[1.0, 0.0],
        persisted_vectors={"m0": ["bad", "vector"]},
    ) == fallback
