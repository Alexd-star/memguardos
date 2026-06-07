import json
from pathlib import Path

from scripts.build_realistic_benchmark_cases import build_cases, extend_benchmarks


def read_jsonl(path: Path) -> list[dict]:
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


def test_realistic_case_builder_extends_multiple_files_and_is_idempotent(tmp_path):
    bench = tmp_path / "benchmarks"
    bench.mkdir()
    for name in [
        "memory_seed.jsonl",
        "retrieval.jsonl",
        "forgetting.jsonl",
        "sensitive_detection.jsonl",
        "procedural_memory.jsonl",
        "graph_retrieval.jsonl",
    ]:
        (bench / name).write_text("", encoding="utf-8")

    first = extend_benchmarks(bench, build_cases())
    second = extend_benchmarks(bench, build_cases())

    assert first["added"] > 0
    assert second["added"] == 0
    assert first["files"]["memory_seed.jsonl"]["added"] >= 6
    assert first["files"]["retrieval.jsonl"]["added"] >= 6
    assert first["files"]["forgetting.jsonl"]["added"] >= 4
    assert first["files"]["sensitive_detection.jsonl"]["added"] >= 5
    assert first["files"]["procedural_memory.jsonl"]["added"] >= 4
    assert first["files"]["graph_retrieval.jsonl"]["added"] >= 4

    seed_labels = {row["label"] for row in read_jsonl(bench / "memory_seed.jsonl")}
    assert "knowledge_real_sdk_recovery_runbook" in seed_labels
    assert "pref_real_answer_evidence_first" in seed_labels

    sensitive_rows = read_jsonl(bench / "sensitive_detection.jsonl")
    assert any(row["source_uri"] == "/home/demo/.config/gcloud/application_default_credentials.json" for row in sensitive_rows)
    assert any(row["expect_stored"] is True and "token 预算" in row["text"] for row in sensitive_rows)


def test_realistic_case_builder_check_mode_does_not_write(tmp_path):
    bench = tmp_path / "benchmarks"
    bench.mkdir()
    seed_file = bench / "memory_seed.jsonl"
    seed_file.write_text("", encoding="utf-8")

    report = extend_benchmarks(bench, {"memory_seed.jsonl": build_cases()["memory_seed.jsonl"]}, dry_run=True)

    assert report["added"] == len(build_cases()["memory_seed.jsonl"])
    assert report["files"]["memory_seed.jsonl"]["dry_run"] is True
    assert seed_file.read_text(encoding="utf-8") == ""
