import pytest

from memguard.eval.ablation import run_ablation, to_markdown
from memguard.eval.performance_suite import summarize_concurrent_trials
from memguard.retrieval import hybrid


def test_ablation_uses_shared_dataset_and_reports_required_variants(tmp_path):
    result = run_ablation(
        work_dir=tmp_path,
        repetitions=2,
        include_kylin=False,
    )

    assert result["protocol"]["same_dataset_for_all_variants"] is True
    assert result["protocol"]["same_query_order_for_all_variants"] is True
    assert result["protocol"]["top_k"] == 5
    assert result["protocol"]["backend_isolation"] == {
        "baseline_variants": "hash",
        "kylin_persisted": "kylin_sdk_with_persisted_candidate_vectors",
    }
    assert result["dataset"]["seed_count"] > 0
    assert result["dataset"]["query_count"] > 0
    assert len(result["dataset"]["seed_sha256"]) == 64
    assert len(result["dataset"]["query_sha256"]) == 64

    variants = result["variants"]
    assert set(variants) == {
        "text_only",
        "hybrid_no_graph",
        "rrf_no_graph",
        "rrf_selective_graph",
        "kylin_persisted",
        "full_no_cache",
        "full_warm_cache",
        "full_kylin_sdk",
    }
    for name in (
        "text_only",
        "hybrid_no_graph",
        "rrf_no_graph",
        "rrf_selective_graph",
        "full_no_cache",
        "full_warm_cache",
    ):
        metrics = variants[name]
        assert metrics["status"] == "completed"
        assert metrics["query_count"] == result["dataset"]["query_count"] * 2
        assert 0.0 <= metrics["recall_at_5"] <= 1.0
        assert metrics["p50_latency_ms"] >= 0.0
        assert metrics["p95_latency_ms"] >= metrics["p50_latency_ms"]
        assert metrics["p99_latency_ms"] >= metrics["p95_latency_ms"]
        assert metrics["qps"] > 0.0
        assert metrics["candidate_count"]["mean"] >= 0.0

    assert variants["full_kylin_sdk"]["status"] == "skipped"
    assert variants["full_kylin_sdk"]["reason"] == "disabled_by_caller"
    assert variants["kylin_persisted"]["status"] == "skipped"
    assert variants["kylin_persisted"]["reason"] == "disabled_by_caller"


def test_non_kylin_ablation_never_invokes_platform_default_sdk(
    monkeypatch, tmp_path
):
    monkeypatch.setattr(hybrid, "_platform_default_backend", lambda: "auto")
    monkeypatch.setattr(
        hybrid.KylinEmbeddingProvider,
        "vector",
        lambda *args, **kwargs: pytest.fail(
            "non-Kylin ablation must not invoke the platform SDK"
        ),
    )

    result = run_ablation(
        work_dir=tmp_path,
        repetitions=1,
        include_kylin=False,
    )

    assert result["variants"]["rrf_selective_graph"]["status"] == "completed"


def test_ablation_reports_cold_and_warm_process_measurements(tmp_path):
    result = run_ablation(
        work_dir=tmp_path,
        repetitions=1,
        include_kylin=False,
    )

    startup = result["startup_and_cache"]
    assert startup["measurement_scope"] == "process_cold_vs_process_warm"
    assert startup["service_startup_ms"] >= 0.0
    assert startup["first_query_ms"] >= 0.0
    assert startup["warm_query_ms"] >= 0.0
    assert startup["warm_speedup"] > 0.0
    assert startup["os_page_cache_claimed"] is False

    markdown = to_markdown(result)
    assert "# MemGuardOS 检索消融实验报告" in markdown
    assert "统一实验协议" in markdown
    assert "进程冷启动" in markdown
    assert "不等同于清空 Linux 内核页缓存" in markdown


def test_performance_suite_summarizes_median_and_worst_tail_latency():
    summary = summarize_concurrent_trials(
        [
            {"p50_latency_ms": 1.0, "p95_latency_ms": 120.0, "p99_latency_ms": 180.0, "ok": True},
            {"p50_latency_ms": 1.2, "p95_latency_ms": 150.0, "p99_latency_ms": 210.0, "ok": True},
            {"p50_latency_ms": 0.9, "p95_latency_ms": 90.0, "p99_latency_ms": 160.0, "ok": True},
        ]
    )

    assert summary["trial_count"] == 3
    assert summary["all_ok"] is True
    assert summary["median_p95_latency_ms"] == 120.0
    assert summary["worst_p95_latency_ms"] == 150.0
    assert summary["worst_p99_latency_ms"] == 210.0
