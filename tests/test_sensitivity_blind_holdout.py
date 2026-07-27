"""Blind-holdout gate for sensitive detection.

These formats were deliberately kept out of the development set: they are the
credential and PII shapes that a reviewer is most likely to try by hand
(+86 mobile numbers, bare JWTs, AWS secret assignments, URL-embedded
credentials, Chinese password hints, 16-digit cards). A regression here means a
real secret would reach long-term memory, so the gate is strict.
"""

import json
from pathlib import Path

from memguard.sensitivity.detector import SensitiveDetector

DATASET = Path(__file__).resolve().parents[1] / "data" / "benchmarks" / "sensitivity_blind_holdout.jsonl"


def _rows():
    return [
        json.loads(line)
        for line in DATASET.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]


def test_blind_holdout_dataset_is_declared_as_holdout_and_synthetic():
    rows = _rows()

    assert len(rows) >= 12
    assert all(row["holdout"] is True for row in rows)
    assert len({row["id"] for row in rows}) == len(rows)
    assert len({row["blind_format"] for row in rows}) == len(rows)
    # 必须同时包含正例和 hard negative，避免"自选题"式评测。
    assert any(row["hard_negative"] for row in rows)
    assert any(row["expected_labels"] for row in rows)


def test_blind_holdout_formats_are_all_detected_without_false_positives():
    detector = SensitiveDetector()
    missed = []
    false_positives = []

    for row in _rows():
        sensitivity = detector.classify(row["input"])
        expected = set(row["expected_labels"])
        got = set(sensitivity.labels)
        if expected - got:
            missed.append((row["id"], row["blind_format"], sorted(expected - got)))
        if not expected and got:
            false_positives.append((row["id"], row["blind_format"], sorted(got)))

    assert not missed, "blind holdout formats missed: %s" % missed
    assert not false_positives, "hard negatives raised findings: %s" % false_positives


def test_blind_holdout_block_decisions_match_expected_actions():
    detector = SensitiveDetector()
    mismatched = []

    for row in _rows():
        sensitivity = detector.classify(row["input"])
        blocked = sensitivity.level == "high"
        if bool(row["expected_block"]) != blocked:
            mismatched.append((row["id"], row["expected_block"], sensitivity.level))

    assert not mismatched, "block decision mismatch: %s" % mismatched


def test_blind_holdout_redaction_removes_every_synthetic_value():
    detector = SensitiveDetector()
    leaked = []

    for row in _rows():
        if not row["expected_redaction"]:
            continue
        redacted = detector.redact(row["input"])
        for value in row["synthetic_values"]:
            if value and value in redacted:
                leaked.append((row["id"], value))

    assert not leaked, "synthetic secrets survived redaction: %s" % leaked
