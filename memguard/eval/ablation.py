from pathlib import Path
from tempfile import TemporaryDirectory
from typing import Callable, Dict, List, Optional, Tuple
import argparse
import hashlib
import json
import os
import platform
import time

from memguard.eval.metrics import mean, percentile, recall_at_k
from memguard.retrieval.hybrid import HybridRetriever
from memguard.service import MemGuardService


ROOT = Path(__file__).resolve().parents[2]
BENCHMARK_DIR = ROOT / "data" / "benchmarks"
SEED_PATH = BENCHMARK_DIR / "memory_seed.jsonl"
QUERY_PATH = BENCHMARK_DIR / "retrieval.jsonl"

VARIANTS = [
    {"name": "hybrid_no_graph", "fusion": "legacy", "graph": "off", "kylin": "off"},
    {"name": "rrf_no_graph", "fusion": "rrf", "graph": "off", "kylin": "off"},
    {
        "name": "rrf_selective_graph",
        "fusion": "rrf",
        "graph": "selective",
        "kylin": "off",
    },
    {
        "name": "kylin_persisted",
        "fusion": "rrf",
        "graph": "selective",
        "kylin": "persisted",
    },
]


def _load_jsonl(path: Path) -> List[dict]:
    rows = []
    with path.open("r", encoding="utf-8") as handle:
        for raw_line in handle:
            line = raw_line.strip()
            if line:
                rows.append(json.loads(line))
    return rows


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _seed_service(work_dir: Path) -> Tuple[Path, Path, Dict[str, str]]:
    store_path = work_dir / "ablation.segment"
    db_path = work_dir / "ablation.sqlite3"
    service = MemGuardService(store_path=store_path, db_path=db_path)
    id_to_label: Dict[str, str] = {}
    try:
        for row in _load_jsonl(SEED_PATH):
            result = service.ingest_text(
                row["text"],
                source_kind=row.get("source_kind", "manual"),
                source_uri=row.get("source_uri", row["label"]),
            )
            if result.get("stored"):
                id_to_label[result["record"].memory_id] = row["label"]
    finally:
        service.close()
    return store_path, db_path, id_to_label


def _recent_fill(service: MemGuardService, query: str, candidate_limit: int) -> List[dict]:
    candidates = service.meta.search_text(query, limit=candidate_limit)
    seen = {str(item["memory_id"]) for item in candidates}
    if len(candidates) < candidate_limit:
        candidates.extend(
            service.meta.list_recent_memories(
                candidate_limit - len(candidates),
                exclude_ids=list(seen),
            )
        )
    return candidates[:candidate_limit]


def _metrics(
    name: str,
    queries: List[dict],
    repetitions: int,
    id_to_label: Dict[str, str],
    run_query: Callable[[dict], Tuple[List[dict], int]],
    warm_query: Optional[Callable[[dict], None]] = None,
) -> dict:
    recalls: List[float] = []
    latencies: List[float] = []
    candidate_counts: List[float] = []
    started = time.perf_counter()
    for _ in range(repetitions):
        for row in queries:
            if warm_query is not None:
                warm_query(row)
            query_started = time.perf_counter()
            hits, candidate_count = run_query(row)
            latencies.append((time.perf_counter() - query_started) * 1000)
            actual_labels = [id_to_label.get(str(hit["memory_id"]), str(hit["memory_id"])) for hit in hits]
            recalls.append(recall_at_k(row["expected"], actual_labels, row.get("k", 5)))
            candidate_counts.append(float(candidate_count))
    elapsed = max(time.perf_counter() - started, 1e-9)
    return {
        "name": name,
        "status": "completed",
        "query_count": len(latencies),
        "recall_at_5": round(mean(recalls), 4),
        "p50_latency_ms": round(percentile(latencies, 0.50), 3),
        "p95_latency_ms": round(percentile(latencies, 0.95), 3),
        "p99_latency_ms": round(percentile(latencies, 0.99), 3),
        "qps": round(len(latencies) / elapsed, 3),
        "candidate_count": {
            "mean": round(mean(candidate_counts), 3),
            "max": int(max(candidate_counts) if candidate_counts else 0),
        },
    }


def _run_variants(
    store_path: Path,
    db_path: Path,
    id_to_label: Dict[str, str],
    repetitions: int,
    include_kylin: bool,
) -> Dict[str, dict]:
    queries = _load_jsonl(QUERY_PATH)
    service = MemGuardService(store_path=store_path, db_path=db_path)
    candidate_limit = 64
    try:
        variants: Dict[str, dict] = {}

        def text_query(row: dict) -> Tuple[List[dict], int]:
            hits = service.meta.search_text(row["query"], limit=row.get("k", 5))
            return hits, len(hits)

        variants["text_only"] = _metrics(
            "text_only",
            queries,
            repetitions,
            id_to_label,
            text_query,
        )

        def hybrid_query(row: dict) -> Tuple[List[dict], int]:
            candidates = _recent_fill(service, row["query"], candidate_limit)
            ranked = service.retriever.rank(row["query"], candidates, limit=row.get("k", 5))
            return [record for _, record in ranked], len(candidates)

        variants["hybrid_no_graph"] = _metrics(
            "hybrid_no_graph",
            queries,
            repetitions,
            id_to_label,
            hybrid_query,
        )

        def rrf_no_graph_query(row: dict) -> Tuple[List[dict], int]:
            service._search_cache.clear()
            original_graph_search = service.hierarchical_graph_search
            service.hierarchical_graph_search = lambda entity, limit=10: {
                "layers": {"summary": [], "entity_relation": [], "chunk": []}
            }
            try:
                hits = service.search(row["query"], limit=row.get("k", 5), audit=False)
            finally:
                service.hierarchical_graph_search = original_graph_search
            return hits, candidate_count_by_query[row["query"]]

        candidate_count_by_query = {
            row["query"]: len(
                service._search_candidates(
                    row["query"], limit=candidate_limit, include_graph=False
                )
            )
            for row in queries
        }

        variants["rrf_no_graph"] = _metrics(
            "rrf_no_graph",
            queries,
            repetitions,
            id_to_label,
            rrf_no_graph_query,
        )

        def full_query(row: dict) -> Tuple[List[dict], int]:
            service._search_cache.clear()
            return (
                service.search(row["query"], limit=row.get("k", 5), audit=False),
                candidate_count_by_query[row["query"]],
            )

        variants["full_no_cache"] = _metrics(
            "full_no_cache",
            queries,
            repetitions,
            id_to_label,
            full_query,
        )
        variants["rrf_selective_graph"] = dict(variants["full_no_cache"])
        variants["rrf_selective_graph"]["name"] = "rrf_selective_graph"

        def warm(row: dict) -> None:
            service.search(row["query"], limit=row.get("k", 5), audit=False)

        def warm_query(row: dict) -> Tuple[List[dict], int]:
            return (
                service.search(row["query"], limit=row.get("k", 5), audit=False),
                candidate_count_by_query[row["query"]],
            )

        variants["full_warm_cache"] = _metrics(
            "full_warm_cache",
            queries,
            repetitions,
            id_to_label,
            warm_query,
            warm_query=warm,
        )

        if not include_kylin:
            variants["kylin_persisted"] = {
                "name": "kylin_persisted",
                "status": "skipped",
                "reason": "disabled_by_caller",
            }
        elif os.name != "posix":
            variants["kylin_persisted"] = {
                "name": "kylin_persisted",
                "status": "skipped",
                "reason": "requires_openkylin_or_compatible_linux",
            }
        else:
            original_retriever = service.retriever
            service.retriever = HybridRetriever(embedding_backend="kylin_sdk")
            try:
                backfill = service.backfill_embeddings(limit=1000, timeout_seconds=120.0)
                probe_row = queries[0]
                service._search_cache.clear()
                service.search(probe_row["query"], limit=probe_row.get("k", 5), audit=False)
                if service.retriever.last_backend_used != "kylin_sdk_persisted":
                    variants["kylin_persisted"] = {
                        "name": "kylin_persisted",
                        "status": "skipped",
                        "reason": service.retriever.status()["kylin"].get("disabled_reason")
                        or "kylin_sdk_did_not_complete_inference",
                        "embedding_backfill": backfill,
                    }
                else:
                    variants["kylin_persisted"] = _metrics(
                        "kylin_persisted",
                        queries,
                        repetitions,
                        id_to_label,
                        full_query,
                    )
                    variants["kylin_persisted"]["embedding_backfill"] = backfill
            finally:
                service.retriever = original_retriever
        variants["full_kylin_sdk"] = dict(variants["kylin_persisted"])
        variants["full_kylin_sdk"]["name"] = "full_kylin_sdk"
        return variants
    finally:
        service.close()


def _startup_and_cache_measurement(
    store_path: Path,
    db_path: Path,
    query: str,
) -> dict:
    startup_started = time.perf_counter()
    service = MemGuardService(store_path=store_path, db_path=db_path)
    startup_ms = (time.perf_counter() - startup_started) * 1000
    try:
        first_started = time.perf_counter()
        service.search(query, limit=5, audit=False)
        first_query_ms = (time.perf_counter() - first_started) * 1000

        warm_started = time.perf_counter()
        service.search(query, limit=5, audit=False)
        warm_query_ms = (time.perf_counter() - warm_started) * 1000
    finally:
        service.close()
    return {
        "measurement_scope": "process_cold_vs_process_warm",
        "service_startup_ms": round(startup_ms, 3),
        "first_query_ms": round(first_query_ms, 3),
        "warm_query_ms": round(warm_query_ms, 3),
        "warm_speedup": round(first_query_ms / max(warm_query_ms, 1e-9), 3),
        "os_page_cache_claimed": False,
        "note": "该测量比较新服务实例首次查询与同进程重复查询，不等同于清空 Linux 内核页缓存。",
    }


def run_ablation(
    work_dir: Optional[Path] = None,
    repetitions: int = 3,
    include_kylin: bool = True,
) -> dict:
    repetitions = max(1, int(repetitions))
    if work_dir is None:
        with TemporaryDirectory() as temporary_dir:
            return run_ablation(Path(temporary_dir), repetitions=repetitions, include_kylin=include_kylin)

    work_dir = Path(work_dir)
    work_dir.mkdir(parents=True, exist_ok=True)
    store_path, db_path, id_to_label = _seed_service(work_dir)
    queries = _load_jsonl(QUERY_PATH)
    variants = _run_variants(
        store_path,
        db_path,
        id_to_label,
        repetitions,
        include_kylin,
    )
    return {
        "protocol": {
            "same_dataset_for_all_variants": True,
            "same_query_order_for_all_variants": True,
            "top_k": 5,
            "repetitions": repetitions,
            "latency_clock": "time.perf_counter",
            "cache_policy": {
                "text_only": "no_application_result_cache",
                "hybrid_no_graph": "no_application_result_cache",
                "full_no_cache": "clear_before_each_query",
                "full_warm_cache": "one_prime_then_timed_hit",
                "full_kylin_sdk": "clear_before_each_query",
            },
        },
        "environment": {
            "python": platform.python_version(),
            "platform": platform.platform(),
            "machine": platform.machine(),
        },
        "dataset": {
            "seed_count": len(_load_jsonl(SEED_PATH)),
            "stored_seed_count": len(id_to_label),
            "query_count": len(queries),
            "seed_sha256": _sha256(SEED_PATH),
            "query_sha256": _sha256(QUERY_PATH),
        },
        "variants": variants,
        "startup_and_cache": _startup_and_cache_measurement(
            store_path,
            db_path,
            queries[0]["query"],
        ),
    }


def to_markdown(result: dict) -> str:
    lines = [
        "# MemGuardOS 检索消融实验报告",
        "",
        "本报告由 `python -m memguard.eval.ablation` 自动生成。",
        "",
        "## 统一实验协议",
        "",
        "| 项目 | 数值 |",
        "|---|---|",
        "| 种子记忆 | %s |" % result["dataset"]["seed_count"],
        "| 实际写入种子 | %s |" % result["dataset"]["stored_seed_count"],
        "| 查询数量 | %s |" % result["dataset"]["query_count"],
        "| 重复次数 | %s |" % result["protocol"]["repetitions"],
        "| Top-K | %s |" % result["protocol"]["top_k"],
        "| 种子数据 SHA256 | `%s` |" % result["dataset"]["seed_sha256"],
        "| 查询数据 SHA256 | `%s` |" % result["dataset"]["query_sha256"],
        "",
        "所有变体使用相同数据集、相同查询顺序和相同 Top-K。延迟使用 `time.perf_counter` 测量。",
        "",
        "## 检索变体结果",
        "",
        "| 变体 | 状态 | Recall@5 | P50 ms | P95 ms | P99 ms | QPS | 平均候选数 |",
        "|---|---|---:|---:|---:|---:|---:|---:|",
    ]
    for name, metrics in result["variants"].items():
        if metrics["status"] != "completed":
            lines.append(
                "| %s | 跳过：%s | - | - | - | - | - | - |"
                % (name, metrics.get("reason", "unknown"))
            )
            continue
        lines.append(
            "| %s | 完成 | %.4f | %.3f | %.3f | %.3f | %.3f | %.3f |"
            % (
                name,
                metrics["recall_at_5"],
                metrics["p50_latency_ms"],
                metrics["p95_latency_ms"],
                metrics["p99_latency_ms"],
                metrics["qps"],
                metrics["candidate_count"]["mean"],
            )
        )

    startup = result["startup_and_cache"]
    lines.extend(
        [
            "",
            "## 进程冷启动与应用缓存",
            "",
            "| 指标 | 数值 |",
            "|---|---:|",
            "| 服务初始化 | %.3f ms |" % startup["service_startup_ms"],
            "| 新服务实例首次查询 | %.3f ms |" % startup["first_query_ms"],
            "| 同进程重复查询 | %.3f ms |" % startup["warm_query_ms"],
            "| 进程内热查询加速比 | %.3f x |" % startup["warm_speedup"],
            "",
            startup["note"],
            "该结果不等同于清空 Linux 内核页缓存后的物理冷启动；若需要内核页缓存实验，应在隔离的 openKylin 环境中单独执行并记录权限与操作。",
            "",
        ]
    )
    return "\n".join(lines)


def main() -> int:
    parser = argparse.ArgumentParser(description="Run MemGuardOS retrieval ablation experiments")
    parser.add_argument("--output", help="JSON output path")
    parser.add_argument("--markdown", help="Chinese Markdown report path")
    parser.add_argument("--work-dir", help="Persistent experiment working directory")
    parser.add_argument("--repetitions", type=int, default=3)
    parser.add_argument("--no-kylin", action="store_true", help="Skip the optional Kylin embedding variant")
    args = parser.parse_args()

    result = run_ablation(
        work_dir=Path(args.work_dir) if args.work_dir else None,
        repetitions=args.repetitions,
        include_kylin=not args.no_kylin,
    )
    text = json.dumps(result, ensure_ascii=False, indent=2)
    print(text)
    if args.output:
        Path(args.output).write_text(text + "\n", encoding="utf-8")
    if args.markdown:
        Path(args.markdown).write_text(to_markdown(result), encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
