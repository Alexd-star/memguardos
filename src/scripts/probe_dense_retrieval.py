"""Isolated development probe for Kylin dense-candidate retrieval."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import sys
from pathlib import Path
from statistics import mean
from tempfile import TemporaryDirectory
from time import perf_counter

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from memguard.service import MemGuardService  # noqa: E402


def _percentile(values: list[float], fraction: float) -> float:
    ordered = sorted(values)
    if not ordered:
        return 0.0
    position = (len(ordered) - 1) * fraction
    lower = int(position)
    upper = min(lower + 1, len(ordered) - 1)
    return ordered[lower] + (ordered[upper] - ordered[lower]) * (position - lower)


def _measure(service: MemGuardService, cases: list[dict], labels: dict[str, str]) -> dict:
    rank_1 = []
    recall_5 = []
    hard_negative = []
    hard_negative_cases = []
    latencies = []
    for case in cases:
        started = perf_counter()
        hits = service.search(str(case["query"]), limit=5, audit=False)
        latencies.append((perf_counter() - started) * 1000)
        actual = [labels.get(str(hit["memory_id"]), "") for hit in hits]
        expected = set(case["expected_ids"])
        negative_ids = {item["id"] for item in case.get("hard_negatives", [])}
        rank_1.append(bool(actual and actual[0] in expected))
        recall_5.append(bool(expected.intersection(actual[:5])))
        negative_hit = bool(negative_ids.intersection(actual[:5]))
        hard_negative.append(negative_hit)
        if negative_hit:
            hard_negative_cases.append({
                "case_id": case["id"],
                "hits": [
                    {
                        "label": labels.get(str(hit["memory_id"]), ""),
                        "channels": (hit.get("retrieval_explanation") or {}).get("fusion_sources", []),
                        "score": round(float(hit.get("score") or 0), 4),
                    }
                    for hit in hits
                ],
            })
    return {
        "cases": len(cases),
        "top_1": round(mean(rank_1), 4),
        "recall_at_5": round(mean(recall_5), 4),
        "hard_negative_hit_rate_at_5": round(mean(hard_negative), 4),
        "hard_negative_cases": hard_negative_cases,
        "p50_ms": round(_percentile(latencies, 0.50), 2),
        "p95_ms": round(_percentile(latencies, 0.95), 2),
    }


def run(split: str) -> dict:
    data_file = ROOT / "data" / "benchmarks_v2" / (
        "retrieval/cross_language_retrieval.jsonl" if split == "public"
        else "holdout/holdout_retrieval.jsonl"
    )
    raw = data_file.read_bytes()
    cases = [json.loads(line) for line in raw.decode("utf-8").splitlines() if line.strip()]
    if not cases:
        raise ValueError("retrieval dataset is empty")
    previous_backend = os.environ.get("MEMGUARD_EMBEDDING_BACKEND")
    previous_dense = os.environ.get("MEMGUARD_DENSE_CANDIDATES")
    try:
        with TemporaryDirectory(prefix="memguard-dense-probe-") as directory:
            root = Path(directory)
            store_path, db_path = root / "memory.segment", root / "meta.sqlite3"
            os.environ["MEMGUARD_EMBEDDING_BACKEND"] = "hash"
            os.environ["MEMGUARD_DENSE_CANDIDATES"] = "0"
            baseline = MemGuardService(store_path, db_path, namespace="dense-probe")
            labels: dict[str, str] = {}
            try:
                for case in cases:
                    entries = [(case["seed_id"], case["seed_text"])] + [
                        (item["id"], item["text"]) for item in case.get("hard_negatives", [])
                    ]
                    for label, content in entries:
                        result = baseline.ingest_text(content, source_kind="manual")
                        if not result.get("stored"):
                            raise RuntimeError("candidate rejected: %s (%s)" % (label, result.get("reason")))
                        memory_id = result["record"].memory_id
                        if memory_id in labels and labels[memory_id] != label:
                            raise RuntimeError("distinct candidate labels collapsed into one memory")
                        labels[memory_id] = label
                hash_metrics = _measure(baseline, cases, labels)
            finally:
                baseline.close()

            os.environ["MEMGUARD_EMBEDDING_BACKEND"] = "auto"
            os.environ["MEMGUARD_DENSE_CANDIDATES"] = "0"
            rerank = MemGuardService(store_path, db_path, namespace="dense-probe")
            try:
                backfill = rerank.backfill_embeddings(limit=len(labels), timeout_seconds=60)
                if int(backfill["ready"]) != len(labels):
                    raise RuntimeError("Kylin vector backfill incomplete: %s" % backfill)
                rerank_metrics = _measure(rerank, cases, labels)
            finally:
                rerank.close()

            os.environ["MEMGUARD_DENSE_CANDIDATES"] = "1"
            dense = MemGuardService(store_path, db_path, namespace="dense-probe")
            try:
                dense_metrics = _measure(dense, cases, labels)
            finally:
                dense.close()
    finally:
        for key, value in (
            ("MEMGUARD_EMBEDDING_BACKEND", previous_backend),
            ("MEMGUARD_DENSE_CANDIDATES", previous_dense),
        ):
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value
    return {
        "evaluation_scope": "project-authored development data; not an external benchmark",
        "split": split,
        "data_sha256": hashlib.sha256(raw).hexdigest(),
        "candidate_count": len(labels),
        "hash_baseline": hash_metrics,
        "kylin_rerank_only": rerank_metrics,
        "kylin_dense": dense_metrics,
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--split", choices=("public", "holdout"), default="public")
    args = parser.parse_args()
    print(json.dumps(run(args.split), ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
