import subprocess

import pytest

from memguard.eval.ablation import VARIANTS
from memguard.eval.run_eval import run_concurrent_search_load
from memguard.retrieval import hybrid
from scripts.evaluate_retrieval_v2 import evaluate_gate, git_head


def test_retrieval_v2_ablation_contains_required_variants():
    names = {variant["name"] for variant in VARIANTS}
    assert {
        "hybrid_no_graph",
        "rrf_no_graph",
        "rrf_selective_graph",
        "kylin_persisted",
    } <= names


def test_concurrent_gate_can_pin_stable_hash_backend(monkeypatch, tmp_path):
    monkeypatch.setattr(hybrid, "_platform_default_backend", lambda: "auto")
    monkeypatch.setattr(
        hybrid.KylinEmbeddingProvider,
        "vector",
        lambda *args, **kwargs: pytest.fail(
            "hash load gate must not invoke the platform SDK"
        ),
    )

    result = run_concurrent_search_load(
        tmp_path,
        size=12,
        query_count=6,
        workers=2,
        embedding_backend="hash",
    )

    assert result["ok"] is True
    assert result["embedding_backend"] == "hash"


def test_gate_requires_all_rounds_to_meet_latency_and_recall():
    rounds = [
        {
            "recall_at_5": 1.0,
            "p95_latency_ms": 120.0,
            "p99_latency_ms": 310.0,
            "quality_p99_latency_ms": 220.0,
            "ok": True,
        },
        {
            "recall_at_5": 1.0,
            "p95_latency_ms": 140.0,
            "p99_latency_ms": 330.0,
            "quality_p99_latency_ms": 230.0,
            "ok": True,
        },
        {
            "recall_at_5": 1.0,
            "p95_latency_ms": 150.0,
            "p99_latency_ms": 350.0,
            "quality_p99_latency_ms": 240.0,
            "ok": True,
        },
    ]
    assert evaluate_gate(rounds)["pass"] is True
    rounds[2]["p99_latency_ms"] = 501.0
    result = evaluate_gate(rounds)
    assert result["pass"] is False
    assert "p99_latency_ms" in result["failures"][0]
    rounds[2]["p99_latency_ms"] = 350.0
    rounds[2]["quality_p99_latency_ms"] = 501.0
    result = evaluate_gate(rounds)
    assert result["pass"] is False
    assert any("quality_p99_latency_ms" in item for item in result["failures"])


def test_gate_rejects_failed_or_incomplete_rounds():
    result = evaluate_gate(
        [
            {
                "recall_at_5": 1.0,
                "p95_latency_ms": 1.0,
                "p99_latency_ms": 2.0,
                "ok": False,
            }
        ],
        required_rounds=3,
    )
    assert result["pass"] is False
    assert any("required 3 rounds" in item for item in result["failures"])
    assert any("execution failed" in item for item in result["failures"])


def test_source_revision_falls_back_to_verified_environment(monkeypatch, tmp_path):
    revision = "a" * 40
    monkeypatch.setattr(
        subprocess,
        "check_output",
        lambda *args, **kwargs: (_ for _ in ()).throw(
            subprocess.CalledProcessError(128, args[0])
        ),
    )

    assert git_head(tmp_path, {"MEMGUARD_SOURCE_REVISION": revision}) == revision


def test_source_revision_falls_back_to_archive_marker(monkeypatch, tmp_path):
    revision = "b" * 40
    (tmp_path / ".source_revision").write_text(revision + "\n", encoding="utf-8")
    monkeypatch.setattr(
        subprocess,
        "check_output",
        lambda *args, **kwargs: (_ for _ in ()).throw(OSError("git unavailable")),
    )

    assert git_head(tmp_path, {}) == revision


def test_source_revision_rejects_unverified_packaged_source(monkeypatch, tmp_path):
    (tmp_path / ".source_revision").write_text("not-a-revision\n", encoding="utf-8")
    monkeypatch.setattr(
        subprocess,
        "check_output",
        lambda *args, **kwargs: (_ for _ in ()).throw(OSError("git unavailable")),
    )

    with pytest.raises(RuntimeError, match="verified source revision"):
        git_head(tmp_path, {"MEMGUARD_SOURCE_REVISION": "also-invalid"})
