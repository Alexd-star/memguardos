from datetime import datetime
import hashlib
import json
from pathlib import Path
import shutil

import pytest

from scripts import build_evidencebench_v2
from scripts import run_evidencebench_v2


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


def write_jsonl(path: Path, rows):
    with path.open("w", encoding="utf-8", newline="\n") as handle:
        for row in rows:
            handle.write(json.dumps(row, ensure_ascii=False, sort_keys=True) + "\n")


def sandbox_bench(tmp_path: Path, monkeypatch):
    sandbox = tmp_path / "benchmarks_v2"
    shutil.copytree(BENCH, sandbox)
    manifest_path = sandbox / "manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    manifest["file_hashes_sha256"] = {
        rel: hashlib.sha256((sandbox / rel).read_bytes()).hexdigest()
        for rel in EXPECTED_FILES
    }
    manifest_path.write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    monkeypatch.setattr(build_evidencebench_v2, "BENCH", sandbox)
    monkeypatch.setattr(run_evidencebench_v2, "BENCH", sandbox)
    return sandbox


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
        raw = path.read_bytes()
        assert b"\r\n" not in raw, f"{rel} must use repository-stable LF line endings"
        digest = hashlib.sha256(raw).hexdigest()
        assert manifest["file_hashes_sha256"][rel] == digest
        total += len(rows)
        if rel.startswith("holdout/"):
            holdout += len(rows)
    assert holdout / total >= 0.20


def test_checked_in_forgetting_cases_have_structured_reachable_candidates():
    for rel in EXPECTED_FILES:
        if not (rel.startswith("forgetting/") or rel == "holdout/holdout_forgetting.jsonl"):
            continue
        for row in read_jsonl(BENCH / rel):
            candidates = row.get("candidates")
            assert isinstance(candidates, list) and candidates, f"{rel}:{row.get('id')}"
            candidate_ids = {item["id"] for item in candidates}
            assert set(row["expected_delete"]) <= candidate_ids
            assert set(row["expected_keep"]) <= candidate_ids
            assert all(isinstance(item.get("text"), str) and item["text"].strip() for item in candidates)


def test_evidencebench_v2_contains_hard_negative_and_openkylin_cases(tmp_path, monkeypatch):
    hard_negative_rows = []
    for rel in EXPECTED_FILES:
        hard_negative_rows.extend(row for row in read_jsonl(BENCH / rel) if row.get("hard_negative") or row.get("hard_negatives"))
    assert len(hard_negative_rows) >= 30
    negative_texts = [
        item["text"]
        for row in hard_negative_rows
        for item in row.get("hard_negatives", [])
    ]
    assert negative_texts
    assert all(
        not text.startswith(("不应召回", "不应遗忘", "不应阻断"))
        for text in negative_texts
    )

    os_rows = []
    for rel in EXPECTED_FILES:
        os_rows.extend(read_jsonl(BENCH / rel))
    joined = "\n".join(json.dumps(row, ensure_ascii=False) for row in os_rows)
    for marker in ["openKylin", "auditd", "BPF LSM", "UKUI", "Peony", "cgroup", "namespace", "mmap"]:
        assert marker in joined

    sandbox = sandbox_bench(tmp_path, monkeypatch)
    before = read_jsonl(sandbox / "retrieval/cross_language_retrieval.jsonl")[0]
    manifest_path = sandbox / "manifest.json"
    manifest_before = json.loads(manifest_path.read_text(encoding="utf-8"))
    manifest_before["self_check"]["topic_acceptable_ids_present"] = True
    with manifest_path.open("w", encoding="utf-8", newline="\n") as fh:
        fh.write(json.dumps(manifest_before, ensure_ascii=False, indent=2, sort_keys=True) + "\n")

    assert build_evidencebench_v2.main([]) == 0

    after = read_jsonl(sandbox / "retrieval/cross_language_retrieval.jsonl")[0]
    assert after["acceptable_ids"] == before["acceptable_ids"]
    assert after["evaluation_mode"] == before["evaluation_mode"]
    assert after["query"] == before["query"]
    manifest_after = json.loads(manifest_path.read_text(encoding="utf-8"))
    assert manifest_after["self_check"]["topic_acceptable_ids_present"] is True


def test_refresh_recomputes_self_check_and_keeps_timestamp_when_data_is_unchanged(tmp_path, monkeypatch):
    sandbox = sandbox_bench(tmp_path, monkeypatch)
    manifest_path = sandbox / "manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    manifest["generated_at"] = "2001-02-03T04:05:06+00:00"
    manifest["self_check"] = {
        "all_gold_ids_exist": False,
        "hard_negative_cases_present": False,
        "holdout_split_present": False,
        "openkylin_offline_runnable": False,
        "topic_acceptable_ids_present": False,
        "legacy_claim": True,
        "evidencebench_v2_dataset_audit": "scripts/audit_evidencebench_v2_dataset.py",
    }
    manifest["safety_policy"] = {
        "no_real_api_keys": False,
        "no_real_pii": False,
        "synthetic_sensitive_data_only": False,
        "legacy_claim": True,
    }
    manifest_path.write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )

    assert build_evidencebench_v2.main([]) == 0

    refreshed = json.loads(manifest_path.read_text(encoding="utf-8"))
    assert refreshed["generated_at"] == "2001-02-03T04:05:06+00:00"
    assert "legacy_claim" not in refreshed["self_check"]
    assert "evidencebench_v2_dataset_audit" not in refreshed["self_check"]
    assert refreshed["self_check"]
    assert all(value is True for value in refreshed["self_check"].values())
    assert refreshed["safety_policy"] == {
        "no_real_api_keys": True,
        "no_real_pii": True,
        "synthetic_sensitive_data_only": True,
    }


def test_refresh_uses_source_date_epoch_for_generated_at(tmp_path, monkeypatch):
    sandbox = sandbox_bench(tmp_path, monkeypatch)
    monkeypatch.setenv("SOURCE_DATE_EPOCH", "946684800")

    assert build_evidencebench_v2.main([]) == 0

    manifest = json.loads((sandbox / "manifest.json").read_text(encoding="utf-8"))
    assert manifest["generated_at"] == "2000-01-01T00:00:00+00:00"


def test_refresh_rebuilds_invalid_existing_generated_at(tmp_path, monkeypatch):
    sandbox = sandbox_bench(tmp_path, monkeypatch)
    manifest_path = sandbox / "manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    manifest["generated_at"] = "not-an-iso-timestamp"
    manifest_path.write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )

    assert build_evidencebench_v2.main([]) == 0

    refreshed = json.loads(manifest_path.read_text(encoding="utf-8"))
    assert refreshed["generated_at"] != "not-an-iso-timestamp"
    assert datetime.fromisoformat(refreshed["generated_at"]).tzinfo is not None


@pytest.mark.parametrize("key", ["seed_id", "seed_text", "query", "expected_ids", "acceptable_ids"])
def test_refresh_rejects_incomplete_retrieval_schema(tmp_path, monkeypatch, key):
    sandbox = sandbox_bench(tmp_path, monkeypatch)
    path = sandbox / "retrieval/cross_language_retrieval.jsonl"
    rows = read_jsonl(path)
    rows[0][key] = [] if key in {"expected_ids", "acceptable_ids"} else ""
    write_jsonl(path, rows)

    assert build_evidencebench_v2.main([]) != 0


@pytest.mark.parametrize("key", ["request", "candidates", "expected_delete", "expected_keep"])
def test_refresh_rejects_incomplete_forgetting_schema(tmp_path, monkeypatch, key):
    sandbox = sandbox_bench(tmp_path, monkeypatch)
    path = sandbox / "forgetting/natural_language_erasure.jsonl"
    rows = read_jsonl(path)
    rows[0][key] = [] if key != "request" else ""
    write_jsonl(path, rows)

    assert build_evidencebench_v2.main([]) != 0


def test_refresh_allows_forgetting_without_acceptable_ids(tmp_path, monkeypatch):
    sandbox = sandbox_bench(tmp_path, monkeypatch)
    for rel in EXPECTED_FILES:
        if not (rel.startswith("forgetting/") or rel == "holdout/holdout_forgetting.jsonl"):
            continue
        path = sandbox / rel
        rows = read_jsonl(path)
        for row in rows:
            row.pop("acceptable_ids", None)
        write_jsonl(path, rows)

    assert build_evidencebench_v2.main([]) == 0


def test_refresh_rejects_unstructured_hard_negatives(tmp_path, monkeypatch):
    sandbox = sandbox_bench(tmp_path, monkeypatch)
    path = sandbox / "retrieval/hard_negative_retrieval.jsonl"
    rows = read_jsonl(path)
    rows[0]["hard_negatives"] = ["not-a-structured-negative"]
    write_jsonl(path, rows)

    assert build_evidencebench_v2.main([]) != 0


def test_refresh_rejects_unstructured_forgetting_candidates(tmp_path, monkeypatch):
    sandbox = sandbox_bench(tmp_path, monkeypatch)
    path = sandbox / "forgetting/natural_language_erasure.jsonl"
    rows = read_jsonl(path)
    rows[0]["candidates"] = [rows[0]["expected_delete"][0]]
    write_jsonl(path, rows)

    assert build_evidencebench_v2.main([]) != 0


def test_refresh_rejects_missing_acceptable_ids(tmp_path, monkeypatch):
    sandbox = sandbox_bench(tmp_path, monkeypatch)
    path = sandbox / "retrieval/cross_language_retrieval.jsonl"
    rows = read_jsonl(path)
    rows[0].pop("acceptable_ids")
    write_jsonl(path, rows)

    assert build_evidencebench_v2.main([]) != 0


def test_refresh_rejects_dangling_gold_id(tmp_path, monkeypatch):
    sandbox = sandbox_bench(tmp_path, monkeypatch)
    path = sandbox / "retrieval/cross_language_retrieval.jsonl"
    rows = read_jsonl(path)
    rows[0]["expected_ids"] = ["seed_missing_case"]
    rows[0]["acceptable_ids"] = ["seed_missing_case"]
    write_jsonl(path, rows)

    assert build_evidencebench_v2.main([]) != 0


def test_refresh_rejects_retrieval_gold_borrowed_from_forgetting_task(tmp_path, monkeypatch):
    sandbox = sandbox_bench(tmp_path, monkeypatch)
    forgetting_row = read_jsonl(sandbox / "forgetting/natural_language_erasure.jsonl")[0]
    borrowed_id = forgetting_row["candidates"][0]["id"]
    path = sandbox / "retrieval/cross_language_retrieval.jsonl"
    rows = read_jsonl(path)
    rows[0]["expected_ids"] = [borrowed_id]
    rows[0]["acceptable_ids"] = [borrowed_id]
    write_jsonl(path, rows)

    assert build_evidencebench_v2.main([]) != 0


def test_refresh_rejects_strict_expected_id_borrowed_from_another_retrieval_row(tmp_path, monkeypatch):
    sandbox = sandbox_bench(tmp_path, monkeypatch)
    path = sandbox / "retrieval/cross_language_retrieval.jsonl"
    rows = read_jsonl(path)
    rows[0]["expected_ids"] = [rows[1]["seed_id"]]
    write_jsonl(path, rows)

    assert build_evidencebench_v2.main([]) != 0


def test_refresh_rejects_forgetting_gold_outside_row_candidates(tmp_path, monkeypatch):
    sandbox = sandbox_bench(tmp_path, monkeypatch)
    path = sandbox / "forgetting/natural_language_erasure.jsonl"
    rows = read_jsonl(path)
    rows[0]["expected_delete"] = [rows[1]["candidates"][0]["id"]]
    write_jsonl(path, rows)

    assert build_evidencebench_v2.main([]) != 0


def test_refresh_rejects_dataset_without_hard_negatives(tmp_path, monkeypatch):
    sandbox = sandbox_bench(tmp_path, monkeypatch)
    for rel in EXPECTED_FILES:
        path = sandbox / rel
        rows = read_jsonl(path)
        for row in rows:
            row["hard_negative"] = False
            row["hard_negative_note"] = ""
            if "hard_negatives" in row:
                row["hard_negatives"] = []
        write_jsonl(path, rows)

    assert build_evidencebench_v2.main([]) != 0


def test_refresh_rejects_holdout_below_twenty_percent(tmp_path, monkeypatch):
    sandbox = sandbox_bench(tmp_path, monkeypatch)
    for rel in EXPECTED_FILES:
        path = sandbox / rel
        rows = read_jsonl(path)
        for row in rows:
            row["holdout"] = False
        write_jsonl(path, rows)

    assert build_evidencebench_v2.main([]) != 0


def test_refresh_rejects_false_holdout_flag_inside_holdout_directory(tmp_path, monkeypatch):
    sandbox = sandbox_bench(tmp_path, monkeypatch)
    path = sandbox / "holdout/holdout_retrieval.jsonl"
    rows = read_jsonl(path)
    rows[0]["holdout"] = False
    write_jsonl(path, rows)

    assert build_evidencebench_v2.main([]) != 0


def test_refresh_rejects_true_holdout_flag_outside_holdout_directory(tmp_path, monkeypatch):
    sandbox = sandbox_bench(tmp_path, monkeypatch)
    path = sandbox / "retrieval/cross_language_retrieval.jsonl"
    rows = read_jsonl(path)
    rows[0]["holdout"] = True
    write_jsonl(path, rows)

    assert build_evidencebench_v2.main([]) != 0


@pytest.mark.parametrize(
    "payload",
    [
        "production API key: sk-live-1234567890abcdef1234567890abcd",
        "真实联系人手机号为 13912345678",
    ],
)
def test_refresh_rejects_obvious_real_credentials_or_pii(tmp_path, monkeypatch, payload):
    sandbox = sandbox_bench(tmp_path, monkeypatch)
    path = sandbox / "extraction/knowledge_structured_cases.jsonl"
    rows = read_jsonl(path)
    rows[0]["input"] = payload
    write_jsonl(path, rows)

    assert build_evidencebench_v2.main([]) != 0


@pytest.mark.parametrize(
    ("rel", "suffix"),
    [
        ("sensitivity/credential_synthetic.jsonl", " production key sk-live-1234567890abcdef1234567890abcd"),
        ("sensitivity/pii_synthetic_zh.jsonl", "；真实联系人手机号为 13912345678"),
    ],
)
def test_refresh_rejects_real_secret_appended_to_synthetic_fixture(tmp_path, monkeypatch, rel, suffix):
    sandbox = sandbox_bench(tmp_path, monkeypatch)
    path = sandbox / rel
    rows = read_jsonl(path)
    rows[0]["input"] += suffix
    write_jsonl(path, rows)

    assert build_evidencebench_v2.main([]) != 0


@pytest.mark.parametrize(
    ("rel", "suffix"),
    [
        ("sensitivity/credential_synthetic.jsonl", "；备用值 sk-live-1234567890abcdef1234567890abcd"),
        ("sensitivity/pii_synthetic_zh.jsonl", "；备用联系电话 13912345678"),
    ],
)
def test_refresh_rejects_unlisted_sensitive_match_appended_to_synthetic_fixture(
    tmp_path,
    monkeypatch,
    rel,
    suffix,
):
    sandbox = sandbox_bench(tmp_path, monkeypatch)
    path = sandbox / rel
    rows = read_jsonl(path)
    rows[0]["input"] += suffix
    write_jsonl(path, rows)

    assert build_evidencebench_v2.main([]) != 0


def test_refresh_allows_only_explicitly_listed_synthetic_matches(tmp_path, monkeypatch):
    sandbox = sandbox_bench(tmp_path, monkeypatch)
    path = sandbox / "sensitivity/pii_synthetic_zh.jsonl"
    rows = read_jsonl(path)
    rows[0]["input"] = "该号码不是真实信息，仅为合成样例：13800138000。"
    rows[0]["synthetic_values"] = ["13800138000"]
    write_jsonl(path, rows)

    assert build_evidencebench_v2.main([]) == 0


@pytest.mark.parametrize(
    "mutation",
    ["tampered_jsonl", "missing_manifest_key", "stale_hash", "extra_jsonl"],
)
def test_evaluator_rejects_incomplete_or_stale_manifest_before_execution(
    tmp_path,
    monkeypatch,
    mutation,
):
    sandbox = sandbox_bench(tmp_path, monkeypatch)
    manifest_path = sandbox / "manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    if mutation == "tampered_jsonl":
        path = sandbox / "retrieval/cross_language_retrieval.jsonl"
        rows = read_jsonl(path)
        rows[0]["query"] += " 篡改"
        write_jsonl(path, rows)
    elif mutation == "missing_manifest_key":
        manifest.pop("safety_policy")
        manifest_path.write_text(
            json.dumps(manifest, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )
    elif mutation == "stale_hash":
        manifest["file_hashes_sha256"]["retrieval/cross_language_retrieval.jsonl"] = "0" * 64
        manifest_path.write_text(
            json.dumps(manifest, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )
    else:
        write_jsonl(sandbox / "retrieval/unexpected.jsonl", [{"id": "unexpected"}])

    with pytest.raises(build_evidencebench_v2.EvidenceBenchValidationError):
        run_evidencebench_v2.run(latency_queries=5)


def test_refresh_rejects_cross_split_acceptable_id_reference(tmp_path, monkeypatch):
    sandbox = sandbox_bench(tmp_path, monkeypatch)
    public_path = sandbox / "retrieval/cross_language_retrieval.jsonl"
    holdout_path = sandbox / "holdout/holdout_retrieval.jsonl"
    public_rows = read_jsonl(public_path)
    holdout_rows = read_jsonl(holdout_path)
    public_rows[0]["acceptable_ids"].append(holdout_rows[0]["seed_id"])
    write_jsonl(public_path, public_rows)

    assert build_evidencebench_v2.main([]) != 0


def test_refresh_rejects_normalized_holdout_text_overlap(tmp_path, monkeypatch):
    sandbox = sandbox_bench(tmp_path, monkeypatch)
    public_row = read_jsonl(sandbox / "retrieval/cross_language_retrieval.jsonl")[0]
    holdout_path = sandbox / "holdout/holdout_retrieval.jsonl"
    holdout_rows = read_jsonl(holdout_path)
    holdout_rows[0]["seed_text"] = "留出集复核 A组：" + public_row["seed_text"]
    write_jsonl(holdout_path, holdout_rows)

    assert build_evidencebench_v2.main([]) != 0


@pytest.mark.parametrize(
    "key",
    ["expected_type", "expected_subtype", "expected_topic", "evaluation_source_kind"],
)
def test_refresh_rejects_incomplete_extraction_gold_schema(tmp_path, monkeypatch, key):
    sandbox = sandbox_bench(tmp_path, monkeypatch)
    path = sandbox / "extraction/preference_explicit.jsonl"
    rows = read_jsonl(path)
    rows[0].pop(key)
    write_jsonl(path, rows)

    assert build_evidencebench_v2.main([]) != 0


@pytest.mark.parametrize(
    "key",
    ["expected_context_hint", "expected_scope", "expected_behavior"],
)
def test_refresh_rejects_incomplete_agent_gold_schema(tmp_path, monkeypatch, key):
    sandbox = sandbox_bench(tmp_path, monkeypatch)
    path = sandbox / "holdout/holdout_agent_tasks.jsonl"
    rows = read_jsonl(path)
    rows[0].pop(key)
    write_jsonl(path, rows)

    assert build_evidencebench_v2.main([]) != 0


def test_checked_in_hard_negative_text_has_no_answer_prefix_or_cross_split_gold():
    public_seed_ids = set()
    holdout_seed_ids = set()
    retrieval_rows = []
    for rel in EXPECTED_FILES:
        if not (rel.startswith("retrieval/") or rel == "holdout/holdout_retrieval.jsonl"):
            continue
        rows = read_jsonl(BENCH / rel)
        retrieval_rows.extend((rel, row) for row in rows)
        target = holdout_seed_ids if rel.startswith("holdout/") else public_seed_ids
        target.update(row["seed_id"] for row in rows)

    forbidden = ("不应召回", "不应遗忘", "不应阻断")
    for rel, row in retrieval_rows:
        own_split = holdout_seed_ids if rel.startswith("holdout/") else public_seed_ids
        other_split = public_seed_ids if rel.startswith("holdout/") else holdout_seed_ids
        assert set(row["expected_ids"]) <= own_split
        assert set(row["acceptable_ids"]) <= own_split
        assert not (set(row["acceptable_ids"]) & other_split)
        assert all(not item["text"].startswith(forbidden) for item in row["hard_negatives"])


def test_sensitivity_files_use_semantically_matching_gold():
    credential_rows = read_jsonl(BENCH / "sensitivity/credential_synthetic.jsonl")
    pii_rows = read_jsonl(BENCH / "sensitivity/pii_synthetic_zh.jsonl")
    hard_negative_rows = read_jsonl(BENCH / "sensitivity/sensitive_hard_negatives.jsonl")

    assert all(row["expected_block"] is True for row in credential_rows)
    assert all(
        set(row["expected_labels"])
        & {"api_key", "aws_access_key_id", "token_assignment", "private_key"}
        for row in credential_rows
    )
    assert all(row["expected_block"] is False for row in pii_rows)
    assert all(row["expected_block"] is False for row in hard_negative_rows)
    assert all(row["expected_redaction"] is False for row in hard_negative_rows)
    assert all(row["synthetic_values"] == [] for row in hard_negative_rows)


def test_rebuild_baseline_is_byte_identical_without_source_date_epoch(tmp_path, monkeypatch):
    sandbox = tmp_path / "benchmarks_v2"
    monkeypatch.setattr(build_evidencebench_v2, "BENCH", sandbox)
    monkeypatch.delenv("SOURCE_DATE_EPOCH", raising=False)

    assert build_evidencebench_v2.main(["--rebuild-baseline"]) == 0
    first = {
        path.relative_to(sandbox).as_posix(): path.read_bytes()
        for path in sorted(sandbox.rglob("*"))
        if path.is_file()
    }
    assert build_evidencebench_v2.main(["--rebuild-baseline"]) == 0
    second = {
        path.relative_to(sandbox).as_posix(): path.read_bytes()
        for path in sorted(sandbox.rglob("*"))
        if path.is_file()
    }

    assert second == first
