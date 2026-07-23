from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
import subprocess
from tempfile import TemporaryDirectory
import sys
import time
from typing import Dict, Iterable, List, Optional

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from memguard.eval.ablation import QUERY_PATH, SEED_PATH, run_ablation  # noqa: E402
from memguard.eval.run_eval import run_concurrent_search_load  # noqa: E402


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def git_head() -> str:
    return subprocess.check_output(
        ["git", "rev-parse", "HEAD"], text=True, encoding="utf-8"
    ).strip()


def evaluate_gate(
    rounds: Iterable[Dict[str, object]], required_rounds: Optional[int] = None
) -> Dict[str, object]:
    rows = list(rounds)
    failures: List[str] = []
    if required_rounds is not None and len(rows) != max(1, int(required_rounds)):
        failures.append(
            "required %d rounds, received %d"
            % (max(1, int(required_rounds)), len(rows))
        )
    for index, row in enumerate(rows, start=1):
        if not bool(row.get("ok", True)):
            failures.append("round %d: execution failed" % index)
        if float(row.get("recall_at_5", 0.0)) < 1.0:
            failures.append("round %d: recall_at_5 < 1.0" % index)
        if float(row.get("p95_latency_ms", float("inf"))) >= 500.0:
            failures.append("round %d: p95_latency_ms >= 500" % index)
        if float(row.get("p99_latency_ms", float("inf"))) >= 500.0:
            failures.append("round %d: p99_latency_ms >= 500" % index)
    return {
        "pass": not failures,
        "failures": failures,
        "required_rounds": required_rounds,
        "evaluated_rounds": len(rows),
        "thresholds": {
            "recall_at_5": 1.0,
            "p95_latency_ms_exclusive_upper": 500.0,
            "p99_latency_ms_exclusive_upper": 500.0,
        },
    }


def _gate_metrics(ablation: dict, concurrent: dict) -> dict:
    variants = ablation["variants"]
    selected_name = "kylin_persisted"
    selected = variants.get(selected_name, {})
    if selected.get("status") != "completed":
        selected_name = "rrf_selective_graph"
        selected = variants[selected_name]
    return {
        "variant": selected_name,
        "recall_at_5": float(selected["recall_at_5"]),
        "p95_latency_ms": float(concurrent["p95_latency_ms"]),
        "p99_latency_ms": float(concurrent["p99_latency_ms"]),
        "ok": bool(concurrent.get("ok")),
    }


def run(
    rounds: int = 3,
    *,
    size: int = 1000,
    query_count: int = 120,
    workers: int = 6,
    include_kylin: bool = True,
) -> dict:
    round_count = max(1, int(rounds))
    raw_rounds = []
    gate_rows = []
    with TemporaryDirectory() as temporary_dir:
        root = Path(temporary_dir)
        for index in range(round_count):
            round_root = root / ("round_%02d" % (index + 1))
            started = time.time()
            ablation = run_ablation(
                work_dir=round_root / "ablation",
                repetitions=1,
                include_kylin=include_kylin,
            )
            concurrent = run_concurrent_search_load(
                round_root / "concurrent",
                size=max(1, int(size)),
                query_count=max(1, int(query_count)),
                workers=max(1, int(workers)),
            )
            gate_metrics = _gate_metrics(ablation, concurrent)
            gate_rows.append(gate_metrics)
            raw_rounds.append(
                {
                    "round": index + 1,
                    "started_at_epoch": started,
                    "gate_metrics": gate_metrics,
                    "ablation": ablation,
                    "concurrent": concurrent,
                }
            )
    return {
        "schema_version": "retrieval-evidence-v2",
        "git_head": git_head(),
        "datasets": {
            "seed": {"path": str(SEED_PATH), "sha256": sha256_file(SEED_PATH)},
            "query": {"path": str(QUERY_PATH), "sha256": sha256_file(QUERY_PATH)},
        },
        "protocol": {
            "rounds": round_count,
            "memory_count": max(1, int(size)),
            "queries_per_round": max(1, int(query_count)),
            "workers": max(1, int(workers)),
            "reporting": "all raw rounds plus worst-case gate",
        },
        "rounds": raw_rounds,
        "gate": evaluate_gate(gate_rows, required_rounds=round_count),
    }


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Evaluate retrieval v2 quality and repeated tail latency"
    )
    parser.add_argument("--rounds", type=int, default=3)
    parser.add_argument("--size", type=int, default=1000)
    parser.add_argument("--queries", type=int, default=120)
    parser.add_argument("--workers", type=int, default=6)
    parser.add_argument("--no-kylin", action="store_true")
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    payload = run(
        rounds=args.rounds,
        size=args.size,
        query_count=args.queries,
        workers=args.workers,
        include_kylin=not args.no_kylin,
    )
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    print(json.dumps(payload["gate"], ensure_ascii=False, indent=2))
    return 0 if payload["gate"]["pass"] else 2


if __name__ == "__main__":
    raise SystemExit(main())
