from memguard.eval.ablation import VARIANTS
from scripts.evaluate_retrieval_v2 import evaluate_gate


def test_retrieval_v2_ablation_contains_required_variants():
    names = {variant["name"] for variant in VARIANTS}
    assert {
        "hybrid_no_graph",
        "rrf_no_graph",
        "rrf_selective_graph",
        "kylin_persisted",
    } <= names


def test_gate_requires_all_rounds_to_meet_latency_and_recall():
    rounds = [
        {
            "recall_at_5": 1.0,
            "p95_latency_ms": 120.0,
            "p99_latency_ms": 310.0,
            "ok": True,
        },
        {
            "recall_at_5": 1.0,
            "p95_latency_ms": 140.0,
            "p99_latency_ms": 330.0,
            "ok": True,
        },
        {
            "recall_at_5": 1.0,
            "p95_latency_ms": 150.0,
            "p99_latency_ms": 350.0,
            "ok": True,
        },
    ]
    assert evaluate_gate(rounds)["pass"] is True
    rounds[2]["p99_latency_ms"] = 501.0
    result = evaluate_gate(rounds)
    assert result["pass"] is False
    assert "p99_latency_ms" in result["failures"][0]


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
