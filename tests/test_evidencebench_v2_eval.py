from scripts.run_evidencebench_v2 import run, to_markdown


def test_evidencebench_v2_eval_outputs_required_metrics(tmp_path):
    result = run(latency_queries=8)
    metrics = result["metrics"]
    for key in [
        "recall_at_1",
        "recall_at_3",
        "recall_at_5",
        "mrr",
        "ndcg_at_5",
        "hard_negative_false_positive_rate",
        "cross_language_recall_at_1",
        "cross_language_recall_at_5",
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

    report = to_markdown(result)
    assert "EvidenceBench v2 是抗质疑评测" in report
    assert "不是所有指标都必须是 1.0000" in report


def test_evidencebench_v2_eval_writes_report_files(tmp_path):
    output = tmp_path / "eval_v2_result.json"
    markdown = tmp_path / "eval_v2_report.md"
    result = run(output=output, markdown=markdown, latency_queries=5)
    assert output.exists()
    assert markdown.exists()
    assert "MemGuardOS EvidenceBench v2" in markdown.read_text(encoding="utf-8")
    assert result["metadata"]["schema"] == "memguardos-evidencebench-v2-result"
