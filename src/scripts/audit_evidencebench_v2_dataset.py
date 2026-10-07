from __future__ import annotations

from collections import defaultdict
from difflib import SequenceMatcher
from pathlib import Path
import argparse
import json


ROOT = Path(__file__).resolve().parents[1]
BENCH = ROOT / "data" / "benchmarks_v2"


def load_jsonl(path: Path) -> list[dict]:
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


def _manifest_files() -> list[str]:
    manifest = json.loads((BENCH / "manifest.json").read_text(encoding="utf-8"))
    return list(manifest["sample_counts"])


def audit_dataset() -> dict:
    rows: list[dict] = []
    for rel in _manifest_files():
        for row in load_jsonl(BENCH / rel):
            item = dict(row)
            item["_file"] = rel
            rows.append(item)

    query_map: dict[str, list[dict]] = defaultdict(list)
    seed_map: dict[str, list[dict]] = defaultdict(list)
    id_set: set[str] = set()
    for row in rows:
        if row.get("query"):
            query_map[str(row["query"]).strip()].append(row)
        if row.get("seed_text"):
            seed_map[str(row["seed_text"]).strip()].append(row)
        for key in ("seed_id",):
            if row.get(key):
                id_set.add(str(row[key]))
        for neg in row.get("hard_negatives") or []:
            if isinstance(neg, dict) and neg.get("id"):
                id_set.add(str(neg["id"]))
        for key in ("expected_ids", "acceptable_ids", "expected_delete", "expected_keep"):
            for item in row.get(key) or []:
                id_set.add(str(item))

    duplicate_queries = [
        {
            "query": query,
            "case_ids": [str(row["id"]) for row in group],
            "expected_ids": sorted({str(item) for row in group for item in row.get("expected_ids", [])}),
        }
        for query, group in query_map.items()
        if len(group) > 1
    ]
    multi_expected_query = [
        item
        for item in duplicate_queries
        if len(item["expected_ids"]) > 1
    ]
    duplicate_seed_text = [
        {"seed_text": seed, "case_ids": [str(row["id"]) for row in group]}
        for seed, group in seed_map.items()
        if len(group) > 1
    ]
    similar_queries = []
    query_items = list(query_map)
    for idx, left in enumerate(query_items):
        for right in query_items[idx + 1 :]:
            ratio = SequenceMatcher(a=left, b=right).ratio()
            if ratio >= 0.92:
                similar_queries.append({"left": left, "right": right, "ratio": round(ratio, 4)})

    hard_negative_too_similar = []
    expected_ids_missing = []
    missing_acceptable_ids = []
    missing_evaluation_mode = []
    for row in rows:
        if row.get("expected_ids"):
            if not row.get("acceptable_ids"):
                missing_acceptable_ids.append(str(row["id"]))
            if not row.get("evaluation_mode"):
                missing_evaluation_mode.append(str(row["id"]))
        for expected_id in row.get("expected_ids") or []:
            if expected_id not in id_set:
                expected_ids_missing.append({"case_id": row["id"], "expected_id": expected_id})
        seed_text = str(row.get("seed_text") or "")
        for neg in row.get("hard_negatives") or []:
            if not isinstance(neg, dict):
                continue
            ratio = SequenceMatcher(a=seed_text, b=str(neg.get("text") or "")).ratio()
            if ratio >= 0.86:
                hard_negative_too_similar.append({"case_id": row["id"], "negative_id": neg.get("id"), "ratio": round(ratio, 4)})

    return {
        "summary": {
            "total_rows": len(rows),
            "duplicate_query_count": len(duplicate_queries),
            "similar_query_count": len(similar_queries),
            "duplicate_seed_text_count": len(duplicate_seed_text),
            "multi_expected_query_count": len(multi_expected_query),
            "hard_negative_too_similar_count": len(hard_negative_too_similar),
            "missing_expected_ids": len(expected_ids_missing),
            "missing_acceptable_ids": len(missing_acceptable_ids),
            "missing_evaluation_mode": len(missing_evaluation_mode),
        },
        "duplicate_queries": duplicate_queries[:50],
        "similar_queries": similar_queries[:50],
        "duplicate_seed_text": duplicate_seed_text[:50],
        "multi_expected_query": multi_expected_query[:50],
        "hard_negative_too_similar": hard_negative_too_similar[:50],
        "expected_ids_missing": expected_ids_missing[:50],
        "missing_acceptable_ids": missing_acceptable_ids[:50],
        "missing_evaluation_mode": missing_evaluation_mode[:50],
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="Audit EvidenceBench v2 dataset fairness and metadata")
    parser.add_argument("--output", default="")
    args = parser.parse_args()
    report = audit_dataset()
    text = json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True)
    if args.output:
        Path(args.output).write_text(text + "\n", encoding="utf-8")
    print(text)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
