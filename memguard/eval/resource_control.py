from __future__ import annotations

from pathlib import Path
from time import perf_counter, process_time
from typing import Dict, List
import ctypes
from ctypes import wintypes
import hashlib
import json
import os
import random
import statistics

from memguard.eval.metrics import percentile
from memguard.service import MemGuardService


TOPICS = ("systemd", "journald", "auditd", "mmap", "cgroup", "openKylin", "精准遗忘", "Agent偏好")


def canonical_json(payload: object) -> str:
    return json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def payload_sha256(payload: object) -> str:
    return hashlib.sha256(canonical_json(payload).encode("utf-8")).hexdigest()


def make_workload(memory_count: int = 1000, query_count: int = 120, seed: int = 20260702) -> Dict[str, object]:
    memory_count = max(1, int(memory_count))
    query_count = max(1, int(query_count))
    rng = random.Random(int(seed))
    memories = []
    for index in range(memory_count):
        topic = TOPICS[index % len(TOPICS)]
        memory_id = "MGRES%06d" % index
        memories.append(
            {
                "memory_id": memory_id,
                "topic": topic,
                "text": "%s 是 %s 场景的可复验记忆，编号 %s。" % (memory_id, topic, memory_id),
            }
        )
    queries = []
    for index in range(query_count):
        target = memories[rng.randrange(len(memories))]
        queries.append(
            {
                "query_id": "query-%06d" % index,
                "query": "%s %s" % (target["memory_id"], target["topic"]),
                "expected_id": target["memory_id"],
            }
        )
    return {
        "schema_version": "1.0",
        "seed": int(seed),
        "memory_count": memory_count,
        "query_count": query_count,
        "memories": memories,
        "queries": queries,
    }


def peak_rss_kb() -> int:
    if os.name == "nt":
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
        counters.cb = ctypes.sizeof(counters)
        kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
        psapi = ctypes.WinDLL("psapi", use_last_error=True)
        kernel32.GetCurrentProcess.restype = wintypes.HANDLE
        psapi.GetProcessMemoryInfo.argtypes = [
            wintypes.HANDLE,
            ctypes.POINTER(ProcessMemoryCounters),
            wintypes.DWORD,
        ]
        psapi.GetProcessMemoryInfo.restype = wintypes.BOOL
        handle = kernel32.GetCurrentProcess()
        if psapi.GetProcessMemoryInfo(handle, ctypes.byref(counters), counters.cb):
            return int(counters.PeakWorkingSetSize // 1024)
        return 0
    try:
        import resource

        value = int(resource.getrusage(resource.RUSAGE_SELF).ru_maxrss)
        return value if os.uname().sysname != "Darwin" else value // 1024
    except (ImportError, AttributeError, OSError):
        return 0


def run_worker(mode: str, workload: Dict[str, object], work_dir: Path, warmup_count: int = 10) -> Dict[str, object]:
    if mode not in {"memory_disabled", "memguardos_enabled"}:
        raise ValueError("unsupported resource-control mode: %s" % mode)
    work_dir = Path(work_dir)
    work_dir.mkdir(parents=True, exist_ok=True)
    wall_started = perf_counter()
    cpu_started = process_time()
    memories = list(workload["memories"])
    queries = list(workload["queries"])
    latencies: List[float] = []
    successes = 0

    if mode == "memory_disabled":
        full_history = [dict(row) for row in memories]

        def search(query: str) -> List[Dict[str, object]]:
            query_tokens = set(query.casefold().split())
            scored = []
            for row in full_history:
                text_tokens = set(str(row["text"]).casefold().replace("。", "").split())
                score = len(query_tokens & text_tokens)
                if str(row["memory_id"]).casefold() in query.casefold():
                    score += 10
                scored.append((score, row))
            return [row for score, row in sorted(scored, key=lambda item: (-item[0], item[1]["memory_id"]))[:5] if score > 0]

        closer = None
    else:
        service = MemGuardService(store_path=work_dir / "memory.segment", db_path=work_dir / "memory.sqlite3")
        for row in memories:
            service.ingest_text(str(row["text"]), source_kind="resource_control", source_uri=str(row["memory_id"]))

        def search(query: str) -> List[Dict[str, object]]:
            return service.search(query, limit=5)

        closer = service.close

    try:
        for query in queries[: max(0, int(warmup_count))]:
            search(str(query["query"]))
        for query in queries:
            started = perf_counter()
            hits = search(str(query["query"]))
            latencies.append((perf_counter() - started) * 1000.0)
            expected = str(query["expected_id"])
            if mode == "memory_disabled":
                actual = {str(hit.get("memory_id") or "") for hit in hits}
            else:
                actual = {
                    str(hit.get("source", {}).get("uri") or hit.get("source_uri") or "")
                    for hit in hits
                }
            successes += int(expected in actual)
    finally:
        if closer is not None:
            closer()

    wall_seconds = max(perf_counter() - wall_started, 1e-9)
    cpu_seconds = max(process_time() - cpu_started, 0.0)
    return {
        "mode": mode,
        "workload_sha256": payload_sha256(workload),
        "memory_count": len(memories),
        "query_count": len(queries),
        "warmup_count": max(0, int(warmup_count)),
        "task_success_count": successes,
        "task_success_rate": round(successes / float(len(queries) or 1), 6),
        "peak_rss_kb": peak_rss_kb(),
        "cpu_time_ms": round(cpu_seconds * 1000.0, 3),
        "wall_time_ms": round(wall_seconds * 1000.0, 3),
        "mean_cpu_percent": round(cpu_seconds / wall_seconds * 100.0, 3),
        "p50_latency_ms": round(percentile(latencies, 0.50), 3),
        "p95_latency_ms": round(percentile(latencies, 0.95), 3),
        "p99_latency_ms": round(percentile(latencies, 0.99), 3),
        "mean_latency_ms": round(statistics.mean(latencies), 3) if latencies else 0.0,
    }


def compare_runs(baseline: Dict[str, object], enabled: Dict[str, object]) -> Dict[str, object]:
    same_workload = baseline.get("workload_sha256") == enabled.get("workload_sha256")
    expected_modes = baseline.get("mode") == "memory_disabled" and enabled.get("mode") == "memguardos_enabled"
    return {
        "protocol_valid": bool(same_workload and expected_modes),
        "same_workload_sha256": bool(same_workload),
        "isolated_process_modes": bool(expected_modes),
        "task_success_rate_delta": round(float(enabled["task_success_rate"]) - float(baseline["task_success_rate"]), 6),
        "peak_rss_delta_kb": int(enabled["peak_rss_kb"]) - int(baseline["peak_rss_kb"]),
        "peak_rss_ratio": round(float(enabled["peak_rss_kb"]) / max(float(baseline["peak_rss_kb"]), 1.0), 6),
        "mean_cpu_percent_delta": round(float(enabled["mean_cpu_percent"]) - float(baseline["mean_cpu_percent"]), 3),
        "cpu_time_delta_ms": round(float(enabled["cpu_time_ms"]) - float(baseline["cpu_time_ms"]), 3),
        "p95_latency_delta_ms": round(float(enabled["p95_latency_ms"]) - float(baseline["p95_latency_ms"]), 3),
    }


def to_markdown(payload: Dict[str, object]) -> str:
    baseline = payload["baseline"]
    enabled = payload["enabled"]
    comparison = payload["comparison"]
    return "\n".join(
        [
            "# MemGuardOS 同负载独立进程资源对照",
            "",
            "两组使用同一 workload SHA-256，在独立 Python 进程中执行。禁用组扫描完整历史，启用组使用 MemGuardOS 写入与检索。",
            "",
            "| 指标 | 关闭记忆模块 | 启用 MemGuardOS | 差值 |",
            "|---|---:|---:|---:|",
            "| 任务成功率 | %.4f | %.4f | %+.4f |" % (baseline["task_success_rate"], enabled["task_success_rate"], comparison["task_success_rate_delta"]),
            "| 峰值 RSS (KB) | %d | %d | %+d |" % (baseline["peak_rss_kb"], enabled["peak_rss_kb"], comparison["peak_rss_delta_kb"]),
            "| 平均 CPU（单核等效 %%） | %.3f | %.3f | %+.3f |" % (baseline["mean_cpu_percent"], enabled["mean_cpu_percent"], comparison["mean_cpu_percent_delta"]),
            "| CPU 时间 (ms) | %.3f | %.3f | %+.3f |" % (baseline["cpu_time_ms"], enabled["cpu_time_ms"], comparison["cpu_time_delta_ms"]),
            "| P50 查询延迟 (ms) | %.3f | %.3f |  |" % (baseline["p50_latency_ms"], enabled["p50_latency_ms"]),
            "| P95 查询延迟 (ms) | %.3f | %.3f | %+.3f |" % (baseline["p95_latency_ms"], enabled["p95_latency_ms"], comparison["p95_latency_delta_ms"]),
            "| P99 查询延迟 (ms) | %.3f | %.3f |  |" % (baseline["p99_latency_ms"], enabled["p99_latency_ms"]),
            "",
            "- 协议有效：`%s`" % str(comparison["protocol_valid"]).lower(),
            "- workload SHA-256：`%s`" % baseline["workload_sha256"],
            "- 该对照衡量运行时工程代价，不把基线扫描算法视为长期记忆系统。",
        ]
    ) + "\n"
