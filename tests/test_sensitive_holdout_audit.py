import json

import pytest

from scripts.evaluate_sensitive_holdout import DEFAULT_DATASET, evaluate, load_cases


def test_fixed_synthetic_holdout_reports_exact_labels_without_values():
    rows, digest = load_cases(DEFAULT_DATASET)
    report = evaluate(rows)

    assert len(rows) == report["case_count"] == 14
    assert len(digest) == 64
    assert report["block"] == {
        "tp": 7, "fp": 0, "fn": 0, "tn": 7,
        "precision": 1.0, "recall": 1.0, "f1": 1.0,
    }
    assert report["label_micro"]["fp"] == 3
    assert report["label_micro"]["f1"] == 0.8889
    assert report["redaction_pass_count"] == 14
    assert report["hard_negative_false_positive_rate"] == 0.0
    assert {item["case_id"] for item in report["mismatches"]} == {
        "sensitivity_blind_holdout_05",
        "sensitivity_blind_holdout_06",
        "sensitivity_blind_holdout_08",
    }
    rendered = json.dumps(report, ensure_ascii=False)
    assert all(value not in rendered for row in rows for value in row["synthetic_values"])


def test_holdout_loader_rejects_duplicate_case_ids(tmp_path):
    row = json.loads(DEFAULT_DATASET.read_text(encoding="utf-8").splitlines()[0])
    path = tmp_path / "duplicate.jsonl"
    path.write_text(
        json.dumps(row, ensure_ascii=False) + "\n" + json.dumps(row, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )
    with pytest.raises(ValueError, match="duplicate case id"):
        load_cases(path)


def test_holdout_loader_rejects_training_split(tmp_path):
    row = json.loads(DEFAULT_DATASET.read_text(encoding="utf-8").splitlines()[0])
    row["holdout"] = False
    path = tmp_path / "not-holdout.jsonl"
    path.write_text(json.dumps(row, ensure_ascii=False) + "\n", encoding="utf-8")
    with pytest.raises(ValueError, match="invalid holdout case"):
        load_cases(path)
