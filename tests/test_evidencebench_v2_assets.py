import hashlib
import json
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
BENCH = ROOT / "data" / "benchmarks_v2"


EXPECTED_FILES = [
    "extraction/preference_explicit.jsonl",
    "extraction/preference_implicit.jsonl",
    "extraction/tool_result_to_preference.jsonl",
    "extraction/knowledge_structured_cases.jsonl",
    "retrieval/cross_language_retrieval.jsonl",
    "retrieval/hard_negative_retrieval.jsonl",
    "retrieval/temporal_retrieval.jsonl",
    "retrieval/graph_association_retrieval.jsonl",
    "forgetting/natural_language_erasure.jsonl",
    "forgetting/scoped_agent_forgetting.jsonl",
    "forgetting/time_range_forgetting.jsonl",
    "forgetting/forget_keep_hard_negatives.jsonl",
    "sensitivity/pii_synthetic_zh.jsonl",
    "sensitivity/credential_synthetic.jsonl",
    "sensitivity/custom_sensitive_patterns.jsonl",
    "sensitivity/sensitive_hard_negatives.jsonl",
    "os_agent/openkylin_service_tasks.jsonl",
    "os_agent/auditd_bpf_tasks.jsonl",
    "os_agent/ukui_peony_tasks.jsonl",
    "os_agent/tool_failure_traces.jsonl",
    "holdout/holdout_retrieval.jsonl",
    "holdout/holdout_forgetting.jsonl",
    "holdout/holdout_agent_tasks.jsonl",
]


def read_jsonl(path: Path):
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


def test_evidencebench_v2_manifest_and_files_are_complete():
    manifest = json.loads((BENCH / "manifest.json").read_text(encoding="utf-8"))
    assert manifest["benchmark_name"] == "MemGuardOS EvidenceBench"
    assert manifest["version"] == "v2"
    assert manifest["seed"] == 20260709
    assert manifest["safety_policy"]["synthetic_sensitive_data_only"] is True
    assert manifest["self_check"]["holdout_split_present"] is True
    assert set(manifest["sample_counts"]) == set(EXPECTED_FILES)

    total = 0
    holdout = 0
    for rel in EXPECTED_FILES:
        path = BENCH / rel
        rows = read_jsonl(path)
        assert 10 <= len(rows) <= 20, rel
        assert manifest["sample_counts"][rel] == len(rows)
        digest = hashlib.sha256(path.read_bytes()).hexdigest()
        assert manifest["file_hashes_sha256"][rel] == digest
        total += len(rows)
        if rel.startswith("holdout/"):
            holdout += len(rows)
    assert holdout / total >= 0.20


def test_evidencebench_v2_contains_hard_negative_and_openkylin_cases():
    hard_negative_rows = []
    for rel in EXPECTED_FILES:
        hard_negative_rows.extend(row for row in read_jsonl(BENCH / rel) if row.get("hard_negative") or row.get("hard_negatives"))
    assert len(hard_negative_rows) >= 30
    assert any("不应召回" in json.dumps(row, ensure_ascii=False) for row in hard_negative_rows)

    os_rows = []
    for rel in EXPECTED_FILES:
        os_rows.extend(read_jsonl(BENCH / rel))
    joined = "\n".join(json.dumps(row, ensure_ascii=False) for row in os_rows)
    for marker in ["openKylin", "auditd", "BPF LSM", "UKUI", "Peony", "cgroup", "namespace", "mmap"]:
        assert marker in joined
