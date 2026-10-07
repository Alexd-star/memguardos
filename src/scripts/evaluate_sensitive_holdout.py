"""Audit the fixed synthetic sensitivity holdout without printing sample values."""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from memguard.sensitivity.detector import SensitiveDetector  # noqa: E402


DEFAULT_DATASET = ROOT / "data/benchmarks/sensitivity_blind_holdout.jsonl"


def _scores(tp: int, fp: int, fn: int, tn: int = 0) -> dict[str, float | int]:
    precision = tp / (tp + fp) if tp + fp else (1.0 if fn == 0 else 0.0)
    recall = tp / (tp + fn) if tp + fn else 1.0
    return {
        "tp": tp, "fp": fp, "fn": fn, "tn": tn,
        "precision": round(precision, 4),
        "recall": round(recall, 4),
        "f1": round(2 * precision * recall / (precision + recall), 4) if precision + recall else 0.0,
    }


def load_cases(path: Path) -> tuple[list[dict], str]:
    raw = path.read_bytes()
    rows = [json.loads(line) for line in raw.decode("utf-8").splitlines() if line.strip()]
    seen: set[str] = set()
    if not rows:
        raise ValueError("sensitivity holdout is empty")
    for row in rows:
        case_id = str(row.get("id") or "")
        if not case_id or case_id in seen:
            raise ValueError("sensitivity holdout has a missing or duplicate case id")
        seen.add(case_id)
        if row.get("holdout") is not True or not isinstance(row.get("input"), str):
            raise ValueError(f"invalid holdout case: {case_id}")
        if not isinstance(row.get("expected_labels"), list) or not all(
            isinstance(label, str) and label for label in row["expected_labels"]
        ):
            raise ValueError(f"invalid expected labels: {case_id}")
        if not isinstance(row.get("synthetic_values"), list) or not all(
            isinstance(value, str) and value for value in row["synthetic_values"]
        ):
            raise ValueError(f"invalid synthetic values: {case_id}")
        if type(row.get("expected_block")) is not bool or type(row.get("expected_redaction")) is not bool:
            raise ValueError(f"invalid expected action: {case_id}")
    return rows, hashlib.sha256(raw).hexdigest()


def evaluate(rows: list[dict], detector: SensitiveDetector | None = None) -> dict:
    detector = detector or SensitiveDetector()
    block_tp = block_fp = block_fn = block_tn = 0
    expected_pairs: set[tuple[str, str]] = set()
    actual_pairs: set[tuple[str, str]] = set()
    negative_count = negative_fp = redaction_pass = 0
    mismatches: list[dict] = []
    for row in rows:
        case_id = row["id"]
        expected_labels = set(row["expected_labels"])
        sensitivity = detector.classify(row["input"])
        actual_labels = set(sensitivity.labels)
        expected_pairs.update((case_id, label) for label in expected_labels)
        actual_pairs.update((case_id, label) for label in actual_labels)
        expected_block = row["expected_block"]
        actual_block = sensitivity.level == "high"
        if expected_block and actual_block:
            block_tp += 1
        elif expected_block:
            block_fn += 1
        elif actual_block:
            block_fp += 1
        else:
            block_tn += 1
        if expected_block != actual_block or expected_labels != actual_labels:
            mismatches.append({
                "case_id": case_id, "kind": "detection",
                "expected_block": expected_block, "actual_block": actual_block,
                "missing_labels": sorted(expected_labels - actual_labels),
                "extra_labels": sorted(actual_labels - expected_labels),
            })
        if row.get("hard_negative") and not expected_labels and not expected_block:
            negative_count += 1
            negative_fp += bool(actual_labels)
        redacted = detector.redact(row["input"])
        changed = redacted != row["input"]
        leaked = any(value in redacted for value in row["synthetic_values"])
        if changed == row["expected_redaction"] and not (row["expected_redaction"] and leaked):
            redaction_pass += 1
        else:
            mismatches.append({
                "case_id": case_id, "kind": "redaction",
                "expected_redaction": row["expected_redaction"],
                "actual_redaction": changed, "synthetic_value_leaked": leaked,
            })

    labels = {}
    for label in sorted({label for _, label in expected_pairs | actual_pairs}):
        expected = {pair for pair in expected_pairs if pair[1] == label}
        actual = {pair for pair in actual_pairs if pair[1] == label}
        labels[label] = _scores(len(expected & actual), len(actual - expected), len(expected - actual))
    return {
        "case_count": len(rows),
        "block": _scores(block_tp, block_fp, block_fn, block_tn),
        "label_micro": _scores(
            len(expected_pairs & actual_pairs),
            len(actual_pairs - expected_pairs),
            len(expected_pairs - actual_pairs),
        ),
        "labels": labels,
        "redaction_pass_count": redaction_pass,
        "redaction_pass_rate": round(redaction_pass / len(rows), 4) if rows else 0.0,
        "hard_negative_count": negative_count,
        "hard_negative_false_positive_rate": round(negative_fp / negative_count, 4) if negative_count else None,
        "mismatches": mismatches,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dataset", type=Path, default=DEFAULT_DATASET)
    args = parser.parse_args()
    rows, digest = load_cases(args.dataset)
    try:
        dataset_name = args.dataset.resolve().relative_to(ROOT).as_posix()
    except ValueError:
        dataset_name = args.dataset.name
    result = {
        "dataset": dataset_name, "dataset_sha256": digest,
        "annotation_provenance": "project-authored synthetic holdout; no independent human annotation claimed",
        **evaluate(rows),
    }
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
