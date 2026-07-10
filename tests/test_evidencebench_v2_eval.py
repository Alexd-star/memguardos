import hashlib
from pathlib import Path

from memguard.service import MemGuardService
from scripts.run_evidencebench_v2 import (
    evaluate_agent_context,
    evaluate_extraction,
    evaluate_forgetting,
    evaluate_retrieval,
    load_jsonl,
    run,
    seed_retrieval_cases,
    to_markdown,
)


ROOT = Path(__file__).resolve().parents[1]
BENCH = ROOT / "data" / "benchmarks_v2"


def test_evidencebench_v2_eval_outputs_required_metrics(tmp_path):
    result = run(latency_queries=8)
    metrics = result["metrics"]
    for key in [
        "strict_recall_at_1",
        "strict_recall_at_5",
        "topic_recall_at_1",
        "topic_recall_at_5",
        "recall_at_1",
        "recall_at_3",
        "recall_at_5",
        "mrr",
        "ndcg_at_5",
        "hard_negative_false_positive_rate",
        "cross_language_recall_at_1",
        "cross_language_recall_at_5",
        "cross_language_cases_evaluated",
        "alias_expansion_hit_rate",
        "extraction_type_accuracy",
        "extraction_subtype_accuracy",
        "extraction_topic_accuracy",
        "extraction_cases_evaluated",
        "conflict_resolution_accuracy",
        "conflict_cases_evaluated",
        "memory_update_accuracy",
        "forget_candidates_evaluated",
        "forget_cases_evaluated",
        "forget_precision",
        "forget_recall",
        "forget_keep_success",
        "forget_residual_leak_rate",
        "sensitive_block_f1",
        "sensitive_redaction_f1",
        "sensitive_false_positive_rate",
        "tool_result_to_preference_extraction_accuracy",
        "agent_context_adherence_rate",
        "agent_context_cases_evaluated",
        "p50_latency_ms",
        "p95_latency_ms",
        "p99_latency_ms",
        "rss_kb",
        "cpu_time_ms",
        "storage_footprint_kb",
        "audit_chain_ok",
    ]:
        assert key in metrics
    assert result["dataset"]["total_samples"] == 260
    assert result["dataset"]["holdout_ratio"] >= 0.20
    assert result["dataset"]["split_overlap_count"] == 0
    assert result["metadata"]["data_revision"]
    assert result["metadata"]["manifest_sha256"] == hashlib.sha256(
        (BENCH / "manifest.json").read_bytes()
    ).hexdigest()
    assert result["metadata"]["evaluation_namespaces"]["public"] != result["metadata"]["evaluation_namespaces"]["holdout"]
    assert set(result["split_metrics"]) == {"public", "holdout"}
    assert result["split_metrics"]["public"]["retrieval_cases_evaluated"] == 40
    assert result["split_metrics"]["holdout"]["retrieval_cases_evaluated"] == 20
    assert result["failure_examples"]
    bounded_metrics = [
        "strict_recall_at_1",
        "strict_recall_at_5",
        "topic_recall_at_1",
        "topic_recall_at_5",
        "mrr",
        "ndcg_at_5",
        "cross_language_recall_at_1",
        "cross_language_recall_at_5",
        "hard_negative_false_positive_rate",
        "sensitive_block_f1",
        "sensitive_redaction_f1",
        "sensitive_false_positive_rate",
        "forget_precision",
        "forget_recall",
    ]
    assert all(0.0 <= metrics[key] <= 1.0 for key in bounded_metrics)
    assert metrics["cross_language_cases_evaluated"] == 10
    assert 0.0 <= metrics["forget_keep_success"] <= 1.0
    if metrics["forget_keep_success"] < 1.0:
        assert any(
            item.get("metric") == "forgetting"
            and set(item.get("expected_keep", [])) & set(item.get("actual", []))
            for item in result["failure_examples"]
        )
    assert metrics["forget_residual_leak_rate"] == 0.0
    assert metrics["forget_cases_evaluated"] == 60
    assert metrics["forget_candidates_evaluated"] == 240
    assert metrics["extraction_cases_evaluated"] == 40
    assert metrics["conflict_cases_evaluated"] == 5
    assert metrics["agent_context_cases_evaluated"] == 60
    assert any(item.get("failure_type") == "strict_rank_miss" for item in result["failure_examples"])
    assert all(item.get("case_id") != "boundary-note" for item in result["failure_examples"])

    report = to_markdown(result)
    assert "EvidenceBench v2 是抗质疑评测" in report
    assert "不是所有指标都必须是 1.0000" in report
    assert result["metadata"]["manifest_sha256"] in report
    assert result["metadata"]["data_revision"] in report
    assert "公开 split" in report
    assert "Holdout split" in report
    assert "strict_rank_miss" in report
    assert "优化前后对比" not in report
    assert "当前样例全部通过" not in report


def test_evidencebench_v2_eval_writes_report_files(tmp_path):
    output = tmp_path / "eval_v2_result.json"
    markdown = tmp_path / "eval_v2_report.md"
    result = run(output=output, markdown=markdown, latency_queries=5)
    assert output.exists()
    assert markdown.exists()
    assert b"\r\n" not in output.read_bytes()
    assert b"\r\n" not in markdown.read_bytes()
    assert "MemGuardOS EvidenceBench v2" in markdown.read_text(encoding="utf-8")
    assert result["metadata"]["schema"] == "memguardos-evidencebench-v2-result"


def test_forgetting_evaluator_uses_every_candidate_and_all_gold_ids():
    row = load_jsonl(BENCH / "forgetting/natural_language_erasure.jsonl")[0]
    failures = []

    metrics = evaluate_forgetting(
        {"forgetting/natural_language_erasure.jsonl": [row]},
        failures,
    )

    assert metrics["forget_cases_evaluated"] == 1
    assert metrics["forget_candidates_evaluated"] == len(row["candidates"]) == 4
    assert metrics["forget_expected_delete_count"] == len(row["expected_delete"]) == 2
    assert metrics["forget_expected_keep_count"] == len(row["expected_keep"]) == 2
    assert metrics["forget_precision"] == 1.0
    assert metrics["forget_recall"] == 1.0
    assert metrics["forget_keep_success"] == 1.0
    assert failures == []


def test_extraction_evaluator_scores_all_rows_and_real_conflict_update_logic(tmp_path):
    rows_by_file = {
        path.relative_to(BENCH).as_posix(): load_jsonl(path)
        for path in sorted((BENCH / "extraction").glob("*.jsonl"))
    }
    service = MemGuardService(
        store_path=tmp_path / "extract.segment",
        db_path=tmp_path / "extract.sqlite3",
        namespace="evidencebench-v2-extraction-test",
    )
    failures = []
    try:
        metrics = evaluate_extraction(service, rows_by_file, failures)
    finally:
        service.close()

    assert metrics["extraction_cases_evaluated"] == 40
    assert metrics["extraction_type_accuracy"] == 1.0
    assert metrics["extraction_subtype_accuracy"] == 1.0
    assert metrics["extraction_topic_accuracy"] == 1.0
    assert metrics["conflict_cases_evaluated"] == 5
    assert metrics["conflict_resolution_accuracy"] == 1.0
    assert metrics["memory_update_accuracy"] == 1.0
    assert failures == []


def test_agent_evaluator_checks_every_row_hint_scope_and_selection(tmp_path):
    rows_by_file = {
        path.relative_to(BENCH).as_posix(): load_jsonl(path)
        for directory in ("os_agent", "holdout")
        for path in sorted((BENCH / directory).glob("*.jsonl"))
        if directory == "os_agent" or path.name == "holdout_agent_tasks.jsonl"
    }
    service = MemGuardService(
        store_path=tmp_path / "agent.segment",
        db_path=tmp_path / "agent.sqlite3",
        namespace="evidencebench-v2-agent-test",
    )
    failures = []
    try:
        metrics = evaluate_agent_context(service, rows_by_file, failures)
    finally:
        service.close()

    assert metrics["agent_context_cases_evaluated"] == 60
    assert metrics["agent_context_hint_accuracy"] == 1.0
    assert metrics["agent_context_scope_accuracy"] == 1.0
    assert metrics["agent_context_behavior_accuracy"] == 1.0
    assert metrics["agent_context_adherence_rate"] == 1.0
    assert failures == []


def test_strict_at_1_miss_is_recorded_even_when_topic_acceptable_hits():
    class SearchService:
        @staticmethod
        def search(query, limit=5):
            return [
                {"memory_id": "topic-memory", "score": 0.9, "score_breakdown": {}},
                {"memory_id": "strict-memory", "score": 0.8, "score_breakdown": {}},
            ]

    row = {
        "id": "strict-miss",
        "query": "query",
        "expected_ids": ["strict"],
        "acceptable_ids": ["topic", "strict"],
        "hard_negatives": [],
        "metric_group": "retrieval",
    }
    failures = []

    metrics = evaluate_retrieval(
        SearchService(),
        [row],
        {"topic-memory": "topic", "strict-memory": "strict"},
        failures,
    )

    assert metrics["strict_recall_at_1"] == 0.0
    assert metrics["topic_recall_at_1"] == 1.0
    assert failures[0]["failure_type"] == "strict_rank_miss"
    assert failures[0]["failure_types"] == ["strict_rank_miss"]
    assert failures[0]["strict_at_1"] == 0.0
    assert failures[0]["topic_acceptable_at_1"] == 1.0


def test_hard_negative_ingest_uses_neutral_source_and_text_without_answer_leakage():
    class CaptureService:
        def __init__(self):
            self.calls = []

        def ingest_text(self, text, source_kind, source_uri):
            self.calls.append(
                {
                    "text": text,
                    "source_kind": source_kind,
                    "source_uri": source_uri,
                }
            )
            record = type("Record", (), {"memory_id": "mem-%s" % len(self.calls)})()
            return {"stored": True, "record": record}

    row = load_jsonl(BENCH / "retrieval/hard_negative_retrieval.jsonl")[0]
    service = CaptureService()

    seed_retrieval_cases(
        service,
        {"retrieval/hard_negative_retrieval.jsonl": [row]},
        {},
    )

    assert len(service.calls) == 2
    assert {call["source_kind"] for call in service.calls} == {"evidencebench_v2"}
    assert all("neg_" not in call["source_uri"] for call in service.calls)
    assert all(
        not call["text"].startswith(("不应召回", "不应遗忘", "不应阻断"))
        for call in service.calls
    )
