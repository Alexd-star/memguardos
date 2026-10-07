"""Benchmark the provider-neutral memory-to-action contract."""

from __future__ import annotations

import argparse
import json
from collections import Counter
from pathlib import Path
from typing import Any, Iterable, Mapping

from memguard.integrations.action_contract import evaluate_action


def evaluate_cases(rows: Iterable[Mapping[str, Any]]) -> dict[str, Any]:
    total = 0
    correct = 0
    confusion: Counter[tuple[str, str]] = Counter()
    by_expected: Counter[str] = Counter()
    by_predicted: Counter[str] = Counter()
    results = []
    for row in rows:
        expected = str(row.get("expected_decision") or "").strip()
        result = evaluate_action(
            tool_name=str(row.get("tool_name") or ""),
            tool_input=dict(row.get("tool_input") or {}),
            memories=[dict(item) for item in row.get("memories") or []],
        )
        predicted = str(result.get("decision") or "")
        total += 1
        correct += int(predicted == expected)
        by_expected[expected] += 1
        by_predicted[predicted] += 1
        confusion[(expected, predicted)] += 1
        results.append(
            {
                "id": row.get("id", ""),
                "expected": expected,
                "predicted": predicted,
                "correct": predicted == expected,
                "policy_category": result.get("policy_category", ""),
                "reason": result.get("reason", ""),
            }
        )
    return {
        "benchmark": "MemGuardOS memory-to-action contract v1",
        "total": total,
        "correct": correct,
        "accuracy": round(correct / total, 6) if total else 0.0,
        "expected_counts": dict(sorted(by_expected.items())),
        "predicted_counts": dict(sorted(by_predicted.items())),
        "confusion": {
            "%s->%s" % (expected, predicted): count
            for (expected, predicted), count in sorted(confusion.items())
        },
        "results": results,
    }


def load_jsonl(path: Path) -> list[dict[str, Any]]:
    rows = []
    for line_number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), start=1):
        if not line.strip():
            continue
        value = json.loads(line)
        if not isinstance(value, dict):
            raise ValueError("line %d must contain an object" % line_number)
        rows.append(value)
    return rows


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Evaluate MemGuardOS action preflight decisions")
    parser.add_argument("--input", type=Path, required=True)
    parser.add_argument("--output", type=Path, default=None)
    args = parser.parse_args(argv)
    report = evaluate_cases(load_jsonl(args.input))
    text = json.dumps(report, ensure_ascii=False, indent=2)
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(text + "\n", encoding="utf-8")
    print(text)
    return 0 if report["accuracy"] == 1.0 else 2


if __name__ == "__main__":
    raise SystemExit(main())

