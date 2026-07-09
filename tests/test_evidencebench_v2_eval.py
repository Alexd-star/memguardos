from scripts.run_evidencebench_v2 import run, to_markdown


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
        "alias_expansion_hit_rate",
        "conflict_resolution_accuracy",
        "forget_precision",
        "forget_recall",
        "forget_keep_success",
        "forget_residual_leak_rate",
        "sensitive_block_f1",
        "sensitive_redaction_f1",
        "sensitive_false_positive_rate",
        "tool_result_to_preference_extraction_accuracy",
        "agent_context_adherence_rate",
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
    assert result["failure_examples"]
    assert metrics["strict_recall_at_1"] >= 0.60
    assert metrics["strict_recall_at_5"] >= 0.90
    assert metrics["topic_recall_at_1"] >= 0.80
    assert metrics["topic_recall_at_5"] >= 0.95
    assert metrics["mrr"] >= 0.70
    assert metrics["ndcg_at_5"] >= 0.75
    assert metrics["cross_language_recall_at_5"] >= 0.90
    assert metrics["hard_negative_false_positive_rate"] <= 0.05
    assert metrics["sensitive_block_f1"] >= 0.90
    assert metrics["sensitive_redaction_f1"] >= 0.90
    assert metrics["forget_precision"] >= 0.95
    assert metrics["forget_recall"] >= 0.95
    assert metrics["forget_keep_success"] == 1.0
    assert metrics["forget_residual_leak_rate"] == 0.0

    report = to_markdown(result)
    assert "EvidenceBench v2 是抗质疑评测" in report
    assert "不是所有指标都必须是 1.0000" in report
    assert "优化前后对比" in report


def test_evidencebench_v2_eval_writes_report_files(tmp_path):
    output = tmp_path / "eval_v2_result.json"
    markdown = tmp_path / "eval_v2_report.md"
    result = run(output=output, markdown=markdown, latency_queries=5)
    assert output.exists()
    assert markdown.exists()
    assert "MemGuardOS EvidenceBench v2" in markdown.read_text(encoding="utf-8")
    assert result["metadata"]["schema"] == "memguardos-evidencebench-v2-result"
