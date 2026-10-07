from pathlib import Path
from tempfile import TemporaryDirectory
from typing import List
import argparse
import json
import statistics

from memguard.eval.ablation import run_ablation, to_markdown as ablation_to_markdown
from memguard.eval.run_eval import run_concurrent_search_load


def summarize_concurrent_trials(trials: List[dict]) -> dict:
    if not trials:
        return {
            "trial_count": 0,
            "all_ok": False,
            "median_p50_latency_ms": 0.0,
            "median_p95_latency_ms": 0.0,
            "worst_p95_latency_ms": 0.0,
            "worst_p99_latency_ms": 0.0,
        }
    return {
        "trial_count": len(trials),
        "all_ok": all(bool(trial.get("ok")) for trial in trials),
        "median_p50_latency_ms": round(
            statistics.median(float(trial["p50_latency_ms"]) for trial in trials),
            3,
        ),
        "median_p95_latency_ms": round(
            statistics.median(float(trial["p95_latency_ms"]) for trial in trials),
            3,
        ),
        "worst_p95_latency_ms": round(
            max(float(trial["p95_latency_ms"]) for trial in trials),
            3,
        ),
        "worst_p99_latency_ms": round(
            max(float(trial["p99_latency_ms"]) for trial in trials),
            3,
        ),
    }


def run_performance_suite(
    work_dir: Path,
    size: int = 1000,
    query_count: int = 120,
    workers: int = 6,
    trials: int = 3,
    ablation_repetitions: int = 3,
    include_kylin: bool = True,
) -> dict:
    work_dir = Path(work_dir)
    work_dir.mkdir(parents=True, exist_ok=True)
    trial_results = []
    for trial_index in range(max(1, int(trials))):
        trial_root = work_dir / ("concurrent_trial_%02d" % (trial_index + 1))
        trial_root.mkdir(parents=True, exist_ok=True)
        trial_results.append(
            run_concurrent_search_load(
                trial_root,
                size=size,
                query_count=query_count,
                workers=workers,
            )
        )
    ablation_root = work_dir / "ablation"
    return {
        "concurrent_protocol": {
            "memory_count": max(1, int(size)),
            "query_count_per_trial": max(int(workers), int(query_count)),
            "workers": max(1, int(workers)),
            "trial_count": max(1, int(trials)),
            "reporting_policy": "median_and_worst_tail_latency",
        },
        "concurrent_trials": trial_results,
        "concurrent_summary": summarize_concurrent_trials(trial_results),
        "ablation": run_ablation(
            work_dir=ablation_root,
            repetitions=ablation_repetitions,
            include_kylin=include_kylin,
        ),
    }


def to_markdown(result: dict) -> str:
    summary = result["concurrent_summary"]
    protocol = result["concurrent_protocol"]
    lines = [
        "# MemGuardOS 并发性能与检索消融报告",
        "",
        "本报告由 `python -m memguard.eval.performance_suite` 自动生成。",
        "",
        "## 多轮并发检索",
        "",
        "| 项目 | 数值 |",
        "|---|---:|",
        "| 记忆数量 | %s |" % protocol["memory_count"],
        "| 每轮查询数 | %s |" % protocol["query_count_per_trial"],
        "| 工作线程 | %s |" % protocol["workers"],
        "| 独立轮次 | %s |" % protocol["trial_count"],
        "| 全部轮次通过 | %s |" % summary["all_ok"],
        "| P50 中位数 | %.3f ms |" % summary["median_p50_latency_ms"],
        "| P95 中位数 | %.3f ms |" % summary["median_p95_latency_ms"],
        "| 最差 P95 | %.3f ms |" % summary["worst_p95_latency_ms"],
        "| 最差 P99 | %.3f ms |" % summary["worst_p99_latency_ms"],
        "",
        "报告同时给出中位数和最差尾延迟，不选择性报告表现最好的一轮。",
        "",
        "### 各轮原始结果",
        "",
        "| 轮次 | P50 ms | P95 ms | P99 ms | 通过 |",
        "|---:|---:|---:|---:|---|",
    ]
    for index, trial in enumerate(result["concurrent_trials"], start=1):
        lines.append(
            "| %s | %.3f | %.3f | %.3f | %s |"
            % (
                index,
                trial["p50_latency_ms"],
                trial["p95_latency_ms"],
                trial["p99_latency_ms"],
                trial["ok"],
            )
        )
    lines.extend(["", ablation_to_markdown(result["ablation"])])
    return "\n".join(lines)


def main() -> int:
    parser = argparse.ArgumentParser(description="Run repeated concurrency and retrieval ablation benchmarks")
    parser.add_argument("--output", required=True, help="JSON output path")
    parser.add_argument("--markdown", required=True, help="Chinese Markdown report path")
    parser.add_argument("--work-dir", help="Persistent benchmark work directory")
    parser.add_argument("--size", type=int, default=1000)
    parser.add_argument("--queries", type=int, default=120)
    parser.add_argument("--workers", type=int, default=6)
    parser.add_argument("--trials", type=int, default=3)
    parser.add_argument("--ablation-repetitions", type=int, default=3)
    parser.add_argument("--no-kylin", action="store_true")
    args = parser.parse_args()

    if args.work_dir:
        result = run_performance_suite(
            Path(args.work_dir),
            size=args.size,
            query_count=args.queries,
            workers=args.workers,
            trials=args.trials,
            ablation_repetitions=args.ablation_repetitions,
            include_kylin=not args.no_kylin,
        )
    else:
        with TemporaryDirectory() as temporary_dir:
            result = run_performance_suite(
                Path(temporary_dir),
                size=args.size,
                query_count=args.queries,
                workers=args.workers,
                trials=args.trials,
                ablation_repetitions=args.ablation_repetitions,
                include_kylin=not args.no_kylin,
            )

    output_path = Path(args.output)
    markdown_path = Path(args.markdown)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    markdown_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    markdown_path.write_text(to_markdown(result), encoding="utf-8")
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
