from pathlib import Path
from tempfile import TemporaryDirectory
import argparse
import ctypes
import json
import os
import time
import tracemalloc

from memguard.eval.metrics import f1, mean, percentile, precision, recall_at_k
from memguard.service import MemGuardService


ROOT = Path(__file__).resolve().parents[2]
BENCHMARK_DIR = ROOT / "data" / "benchmarks"


def load_jsonl(path: Path) -> list:
    rows = []
    with path.open("r", encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if line:
                rows.append(json.loads(line))
    return rows


def run(latency_size: int = 200, latency_queries: int = 100) -> dict:
    with TemporaryDirectory() as tmp:
        root = Path(tmp)
        wall_start = time.perf_counter()
        cpu_start = time.process_time()
        tracemalloc.start()
        service = MemGuardService(store_path=root / "memory.segment", db_path=root / "memguard.sqlite3")
        try:
            label_to_id = {}
            id_to_label = {}
            type_correct = 0
            subtype_correct = 0
            seeds = load_jsonl(BENCHMARK_DIR / "memory_seed.jsonl")
            for row in seeds:
                result = service.ingest_text(row["text"], source_kind="benchmark", source_uri=row["label"])
                if not result["stored"]:
                    continue
                record = result["record"]
                label_to_id[row["label"]] = record.memory_id
                id_to_label[record.memory_id] = row["label"]
                if record.type == row["expected_type"]:
                    type_correct += 1
                if row.get("expected_subtype") == record.subtype:
                    subtype_correct += 1

            retrieval_scores = []
            latencies = []
            cross_language_scores = []
            baseline_scores = {
                "text_only_recall_at_5": [],
                "hybrid_no_graph_recall_at_5": [],
                "full_memguardos_recall_at_5": [],
            }
            for row in load_jsonl(BENCHMARK_DIR / "retrieval.jsonl"):
                start = time.perf_counter()
                hits = service.search(row["query"], limit=row.get("k", 5))
                latencies.append((time.perf_counter() - start) * 1000)
                actual_labels = [id_to_label.get(hit["memory_id"], hit["memory_id"]) for hit in hits]
                score = recall_at_k(row["expected"], actual_labels, row.get("k", 5))
                retrieval_scores.append(score)
                baseline_scores["full_memguardos_recall_at_5"].append(score)
                baseline_scores["text_only_recall_at_5"].append(
                    recall_at_k(row["expected"], _text_only_labels(service, row["query"], id_to_label, row.get("k", 5)), row.get("k", 5))
                )
                baseline_scores["hybrid_no_graph_recall_at_5"].append(
                    recall_at_k(row["expected"], _hybrid_no_graph_labels(service, row["query"], id_to_label, row.get("k", 5)), row.get("k", 5))
                )
                if row.get("case") == "cross_language":
                    cross_language_scores.append(score)

            sensitive_expected = []
            sensitive_actual = []
            redaction_expected = []
            redaction_actual = []
            label_expected = []
            label_actual = []
            for row in load_jsonl(BENCHMARK_DIR / "sensitive_detection.jsonl"):
                result = service.ingest_text(row["text"], source_kind="benchmark_sensitive", source_uri=row["source_uri"])
                sensitive_expected.append(not row["expect_stored"])
                sensitive_actual.append(not result["stored"])
                redaction_expected.append(bool(row["expect_redacted"]))
                if result["stored"]:
                    redaction_actual.append(bool(result["record"].sensitivity.redacted))
                    actual_labels = result["record"].sensitivity.labels
                else:
                    redaction_actual.append(False)
                    actual_labels = result["sensitivity"].labels
                row_index = len(sensitive_expected) - 1
                label_expected.extend("%s:%s" % (row_index, label) for label in row.get("labels", []))
                label_actual.extend("%s:%s" % (row_index, label) for label in actual_labels)

            forget_precisions = []
            forget_recalls = []
            keep_success = []
            for row in load_jsonl(BENCHMARK_DIR / "forgetting.jsonl"):
                plan = service.forget(row["request"])
                actual_labels = [id_to_label.get(item["memory_id"], item["memory_id"]) for item in plan["candidates"]]
                forget_precisions.append(precision(row["expected_delete"], actual_labels))
                forget_recalls.append(recall_at_k(row["expected_delete"], actual_labels, len(actual_labels)))
                keep_success.append(1.0 if not set(row["expected_keep"]) & set(actual_labels) else 0.0)

            conflict_correct = []
            for row in load_jsonl(BENCHMARK_DIR / "conflict_resolution.jsonl"):
                local_service = MemGuardService(store_path=root / ("conflict_%s.segment" % row["case"]), db_path=root / ("conflict_%s.sqlite3" % row["case"]))
                try:
                    for item in row["records"]:
                        local_service.ingest_text(item["text"], source_kind=item["source_kind"], source_uri=row["case"])
                    result = local_service.resolve_conflicts()
                    chosen_text = ""
                    if result["resolutions"]:
                        chosen_id = result["resolutions"][0]["chosen_memory_id"]
                        chosen = local_service.meta.get_memory(chosen_id)
                        chosen_text = chosen.get("content", "") if chosen else ""
                    conflict_correct.append(1.0 if row["expected_contains"] in chosen_text else 0.0)
                finally:
                    local_service.close()

            graph_scores = []
            for row in load_jsonl(BENCHMARK_DIR / "graph_retrieval.jsonl"):
                hits = service.graph_search(row["entity"], limit=5)
                actual_labels = [id_to_label.get(hit["memory_id"], hit["memory_id"]) for hit in hits]
                graph_scores.append(recall_at_k(row["expected"], actual_labels, 5))

            sensitive_f1 = f1(
                [str(idx) for idx, value in enumerate(sensitive_expected) if value],
                [str(idx) for idx, value in enumerate(sensitive_actual) if value],
            )
            redaction_f1 = f1(
                [str(idx) for idx, value in enumerate(redaction_expected) if value],
                [str(idx) for idx, value in enumerate(redaction_actual) if value],
            )
            label_f1 = f1(label_expected, label_actual)

            tier_service = MemGuardService(store_path=root / "tier.segment", db_path=root / "tier.sqlite3")
            try:
                working = tier_service.ingest_text("临时调试上下文，只在当前任务中使用。", tier="working")["record"]
                promoted = tier_service.promote_memory(working.memory_id, "long_term")
                session = tier_service.ingest_text("本轮会话记忆，结束后可清理。", tier="session")["record"]
                evicted = tier_service.evict_tier("session", confirm=True)
                tier_flow_ok = (
                    promoted["promoted"]
                    and promoted["record"].tier == "long_term"
                    and session.memory_id in evicted["memory_ids"]
                    and len(tier_service.list_memories(tier="session")) == 0
                )
            finally:
                tier_service.close()

            load_result = run_latency_load(root, latency_size, latency_queries)
            current_bytes, peak_bytes = tracemalloc.get_traced_memory()
            footprint = storage_footprint(
                [
                    service.store.path,
                    service.meta.path,
                    root / "tier.segment",
                    root / "tier.sqlite3",
                ]
            )
            memory_count = len(service.list_memories())

            return {
                "dataset": {
                    "seed_memories": len(seeds),
                    "retrieval_cases": len(load_jsonl(BENCHMARK_DIR / "retrieval.jsonl")),
                    "sensitive_cases": len(load_jsonl(BENCHMARK_DIR / "sensitive_detection.jsonl")),
                    "forgetting_cases": len(load_jsonl(BENCHMARK_DIR / "forgetting.jsonl")),
                    "conflict_cases": len(load_jsonl(BENCHMARK_DIR / "conflict_resolution.jsonl")),
                    "graph_cases": len(load_jsonl(BENCHMARK_DIR / "graph_retrieval.jsonl")),
                },
                "preference_and_knowledge_extraction": {
                    "type_accuracy": round(type_correct / float(len(seeds) or 1), 4),
                    "subtype_accuracy": round(subtype_correct / float(len(seeds) or 1), 4),
                },
                "retrieval": {
                    "mean_recall_at_5": round(mean(retrieval_scores), 4),
                    "cross_language_recall_at_5": round(mean(cross_language_scores), 4),
                    "p50_latency_ms": round(percentile(latencies, 0.50), 3),
                    "p95_latency_ms": round(percentile(latencies, 0.95), 3),
                },
                "baseline_comparison": {
                    key: round(mean(values), 4) for key, values in baseline_scores.items()
                },
                "sensitive_detection": {
                    "block_f1": round(sensitive_f1, 4),
                    "redaction_f1": round(redaction_f1, 4),
                    "label_f1": round(label_f1, 4),
                },
                "forgetting": {
                    "candidate_precision": round(mean(forget_precisions), 4),
                    "candidate_recall": round(mean(forget_recalls), 4),
                    "keep_success": round(mean(keep_success), 4),
                },
                "conflict_resolution": {
                    "correctness": round(mean(conflict_correct), 4),
                },
                "graph_memory": {
                    "entity_recall_at_5": round(mean(graph_scores), 4),
                },
                "memory_tiers": {
                    "flow_ok": bool(tier_flow_ok),
                    "working_session_long_term_supported": True,
                },
                "latency_load": load_result,
                "resource_usage": {
                    "eval_wall_time_ms": round((time.perf_counter() - wall_start) * 1000, 3),
                    "eval_cpu_time_ms": round((time.process_time() - cpu_start) * 1000, 3),
                    "tracemalloc_current_kb": round(current_bytes / 1024.0, 3),
                    "tracemalloc_peak_kb": round(peak_bytes / 1024.0, 3),
                    "process_rss_kb": current_rss_kb(),
                },
                "storage_footprint": {
                    "main_segment_kb": round(file_size(service.store.path) / 1024.0, 3),
                    "main_sqlite_kb": round(file_size(service.meta.path) / 1024.0, 3),
                    "total_eval_store_kb": round(footprint / 1024.0, 3),
                    "bytes_per_live_memory": round(footprint / float(memory_count or 1), 3),
                },
                "memory_count": memory_count,
            }
        finally:
            service.close()
            tracemalloc.stop()


def run_latency_load(root: Path, size: int, query_count: int = 100) -> dict:
    size = max(0, int(size))
    query_count = max(100, int(query_count))
    service = MemGuardService(store_path=root / "latency.segment", db_path=root / "latency.sqlite3")
    try:
        topics = ["systemd", "journald", "auditd", "mmap", "precise_forgetting", "openKylin", "cgroup"]
        for idx in range(size):
            topic = topics[idx % len(topics)]
            service.ingest_text(
                "load memory %04d topic=%s openKylin Linux agent memory benchmark MemoryMax audit precise forgetting" % (idx, topic),
                source_kind="latency_load",
                source_uri="load_%04d" % idx,
            )
        queries = [
            "openKylin systemd MemoryMax",
            "journald audit memguardd",
            "mmap precise forgetting compact",
            "cgroup CPUQuota service",
            "auditd sensitive path",
        ]
        latencies = []
        for idx in range(query_count):
            query = queries[idx % len(queries)]
            start = time.perf_counter()
            service.search(query, limit=5)
            latencies.append((time.perf_counter() - start) * 1000)
        return {
            "memory_count": size,
            "query_count": len(latencies),
            "p50_latency_ms": round(percentile(latencies, 0.50), 3),
            "p95_latency_ms": round(percentile(latencies, 0.95), 3),
            "p99_latency_ms": round(percentile(latencies, 0.99), 3),
            "segment_kb": round(file_size(service.store.path) / 1024.0, 3),
            "sqlite_kb": round(file_size(service.meta.path) / 1024.0, 3),
            "total_store_kb": round(storage_footprint([service.store.path, service.meta.path]) / 1024.0, 3),
        }
    finally:
        service.close()


def file_size(path: Path) -> int:
    path = Path(path)
    return path.stat().st_size if path.exists() else 0


def storage_footprint(paths: list) -> int:
    total = 0
    seen = set()
    for raw in paths:
        path = Path(raw)
        candidates = [path, Path(str(path) + "-wal"), Path(str(path) + "-shm")]
        for candidate in candidates:
            key = str(candidate.resolve()) if candidate.exists() else str(candidate)
            if key in seen:
                continue
            seen.add(key)
            total += file_size(candidate)
    return total


def current_rss_kb():
    try:
        import resource

        value = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
        if value > 1024 * 1024 * 16 and os.name != "posix":
            return round(value / 1024.0, 3)
        return round(float(value), 3)
    except Exception:
        pass
    if os.name != "nt":
        return None
    try:
        class ProcessMemoryCounters(ctypes.Structure):
            _fields_ = [
                ("cb", ctypes.c_ulong),
                ("PageFaultCount", ctypes.c_ulong),
                ("PeakWorkingSetSize", ctypes.c_size_t),
                ("WorkingSetSize", ctypes.c_size_t),
                ("QuotaPeakPagedPoolUsage", ctypes.c_size_t),
                ("QuotaPagedPoolUsage", ctypes.c_size_t),
                ("QuotaPeakNonPagedPoolUsage", ctypes.c_size_t),
                ("QuotaNonPagedPoolUsage", ctypes.c_size_t),
                ("PagefileUsage", ctypes.c_size_t),
                ("PeakPagefileUsage", ctypes.c_size_t),
            ]

        counters = ProcessMemoryCounters()
        counters.cb = ctypes.sizeof(ProcessMemoryCounters)
        handle = ctypes.windll.kernel32.GetCurrentProcess()
        ok = ctypes.windll.psapi.GetProcessMemoryInfo(handle, ctypes.byref(counters), counters.cb)
        if ok:
            return round(counters.WorkingSetSize / 1024.0, 3)
    except Exception:
        return None
    return None


def _text_only_labels(service: MemGuardService, query: str, id_to_label: dict, limit: int) -> list:
    hits = service.meta.search_text(query, limit=limit)
    return [id_to_label.get(hit["memory_id"], hit["memory_id"]) for hit in hits]


def _hybrid_no_graph_labels(service: MemGuardService, query: str, id_to_label: dict, limit: int) -> list:
    ranked = service.retriever.rank(query, service.meta.list_memories(include_deleted=False), limit=limit)
    return [id_to_label.get(record["memory_id"], record["memory_id"]) for _, record in ranked]


def main() -> int:
    parser = argparse.ArgumentParser(description="Run MemGuardOS benchmark evaluation")
    parser.add_argument("--output", help="Optional JSON output path")
    parser.add_argument("--markdown", help="Optional Markdown report output path")
    parser.add_argument("--latency-size", type=int, default=200, help="Synthetic memory count for latency-load benchmark")
    parser.add_argument("--latency-queries", type=int, default=100, help="Retrieval request count for latency-load benchmark; coerced to at least 100")
    args = parser.parse_args()

    result = run(latency_size=args.latency_size, latency_queries=args.latency_queries)
    text = json.dumps(result, ensure_ascii=False, indent=2)
    print(text)
    if args.output:
        Path(args.output).write_text(text + "\n", encoding="utf-8")
    if args.markdown:
        Path(args.markdown).write_text(to_markdown(result), encoding="utf-8")
    return 0


def to_markdown(result: dict) -> str:
    retrieval = result["retrieval"]
    sensitive = result["sensitive_detection"]
    forgetting = result["forgetting"]
    conflict = result["conflict_resolution"]
    graph = result["graph_memory"]
    baseline = result["baseline_comparison"]
    tiers = result["memory_tiers"]
    latency_load = result["latency_load"]
    resource = result["resource_usage"]
    footprint = result["storage_footprint"]
    extraction = result["preference_and_knowledge_extraction"]
    lines = [
        "# MemGuardOS 评测报告",
        "",
        "由 `python -m memguard.eval.run_eval` 自动生成。",
        "",
        "## 数据集",
        "",
        "| 数据分组 | 数量 |",
        "|---|---:|",
    ]
    dataset_labels = {
        "seed_memories": "种子记忆",
        "retrieval_cases": "检索用例",
        "sensitive_cases": "敏感检测用例",
        "forgetting_cases": "精准遗忘用例",
        "conflict_cases": "冲突解决用例",
        "graph_cases": "图谱检索用例",
    }
    for key, value in result["dataset"].items():
        lines.append("| %s | %s |" % (dataset_labels.get(key, key), value))
    lines.extend(
        [
            "",
            "## 指标",
            "",
            "| 指标 | 数值 |",
            "|---|---:|",
            "| 类型识别准确率 | %.4f |" % extraction["type_accuracy"],
            "| 子类型识别准确率 | %.4f |" % extraction["subtype_accuracy"],
            "| 平均 Recall@5 | %.4f |" % retrieval["mean_recall_at_5"],
            "| 跨语言 Recall@5 | %.4f |" % retrieval["cross_language_recall_at_5"],
            "| 纯文本基线 Recall@5 | %.4f |" % baseline["text_only_recall_at_5"],
            "| 无图谱混合基线 Recall@5 | %.4f |" % baseline["hybrid_no_graph_recall_at_5"],
            "| 完整 MemGuardOS Recall@5 | %.4f |" % baseline["full_memguardos_recall_at_5"],
            "| 检索 P50 延迟 ms | %.3f |" % retrieval["p50_latency_ms"],
            "| 检索 P95 延迟 ms | %.3f |" % retrieval["p95_latency_ms"],
            "| 敏感阻断 F1 | %.4f |" % sensitive["block_f1"],
            "| 脱敏 F1 | %.4f |" % sensitive["redaction_f1"],
            "| 敏感标签 F1 | %.4f |" % sensitive["label_f1"],
            "| 遗忘候选精确率 | %.4f |" % forgetting["candidate_precision"],
            "| 遗忘候选召回率 | %.4f |" % forgetting["candidate_recall"],
            "| 应保留记忆成功率 | %.4f |" % forgetting["keep_success"],
            "| 冲突解决正确率 | %.4f |" % conflict["correctness"],
            "| 图谱实体 Recall@5 | %.4f |" % graph["entity_recall_at_5"],
            "| 记忆层级流转是否通过 | %s |" % tiers["flow_ok"],
            "| 负载记忆数量 | %s |" % latency_load["memory_count"],
            "| 负载查询数量 | %s |" % latency_load["query_count"],
            "| 负载 P95 延迟 ms | %.3f |" % latency_load["p95_latency_ms"],
            "| 负载段文件 KB | %.3f |" % latency_load["segment_kb"],
            "| 负载 SQLite KB | %.3f |" % latency_load["sqlite_kb"],
            "| 评测墙钟耗时 ms | %.3f |" % resource["eval_wall_time_ms"],
            "| 评测 CPU 耗时 ms | %.3f |" % resource["eval_cpu_time_ms"],
            "| tracemalloc 峰值 KB | %.3f |" % resource["tracemalloc_peak_kb"],
            "| 进程 RSS KB | %s |" % (resource["process_rss_kb"] if resource["process_rss_kb"] is not None else "N/A"),
            "| 主记忆段文件 KB | %.3f |" % footprint["main_segment_kb"],
            "| 主 SQLite KB | %.3f |" % footprint["main_sqlite_kb"],
            "| 评测存储总占用 KB | %.3f |" % footprint["total_eval_store_kb"],
            "| 单条存活记忆平均字节数 | %.3f |" % footprint["bytes_per_live_memory"],
            "",
            "## 说明",
            "",
            "该报告用于验证评测管线、指标趋势和回归风险。提交前应继续扩充困难样例，并以 openKylin 实机/虚拟机报告作为最终证据。",
            "",
        ]
    )
    return "\n".join(lines)


if __name__ == "__main__":
    raise SystemExit(main())
