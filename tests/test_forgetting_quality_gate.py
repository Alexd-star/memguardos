import json
from pathlib import Path

from memguard.eval.metrics import mean, precision, recall_at_k
from memguard.service import MemGuardService


ROOT = Path(__file__).resolve().parents[1]
BENCHMARK_DIR = ROOT / "data" / "benchmarks"


def _load_jsonl(path: Path) -> list[dict[str, object]]:
    rows = []
    with path.open("r", encoding="utf-8") as handle:
        for line in handle:
            if line.strip():
                rows.append(json.loads(line))
    return rows


def test_natural_language_forgetting_meets_release_quality_gate(tmp_path):
    service = MemGuardService(
        store_path=tmp_path / "memory.segment",
        db_path=tmp_path / "memguard.sqlite3",
    )
    try:
        id_to_label = {}
        for row in _load_jsonl(BENCHMARK_DIR / "memory_seed.jsonl"):
            result = service.ingest_text(
                str(row["text"]),
                source_kind=str(row.get("source_kind", "benchmark")),
                source_uri=str(row.get("source_uri", row["label"])),
                retention_session_id="quality-gate",
            )
            id_to_label[result["record"].memory_id] = str(row["label"])

        precisions = []
        recalls = []
        keep_success = []
        for row in _load_jsonl(BENCHMARK_DIR / "forgetting.jsonl"):
            plan = service.forget(str(row["request"]))
            actual = [
                id_to_label.get(item["memory_id"], item["memory_id"])
                for item in plan["candidates"]
            ]
            expected_delete = [str(item) for item in row["expected_delete"]]
            expected_keep = {str(item) for item in row["expected_keep"]}
            precisions.append(precision(expected_delete, actual))
            recalls.append(recall_at_k(expected_delete, actual, len(actual)))
            keep_success.append(1.0 if expected_keep.isdisjoint(actual) else 0.0)

        assert mean(precisions) >= 0.9
        assert mean(recalls) >= 0.9
        assert mean(keep_success) >= 0.9
    finally:
        service.close()
