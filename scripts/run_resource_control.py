from pathlib import Path
import argparse
import json
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from memguard.eval.resource_control import compare_runs, make_workload, run_worker, to_markdown  # noqa: E402


def main() -> int:
    parser = argparse.ArgumentParser(description="运行关闭记忆与启用 MemGuardOS 的同负载独立进程对照")
    parser.add_argument("--output")
    parser.add_argument("--markdown")
    parser.add_argument("--work-dir", required=True)
    parser.add_argument("--memory-count", type=int, default=1000)
    parser.add_argument("--query-count", type=int, default=120)
    parser.add_argument("--warmup-count", type=int, default=20)
    parser.add_argument("--seed", type=int, default=20260702)
    parser.add_argument("--worker-mode", choices=("memory_disabled", "memguardos_enabled"))
    parser.add_argument("--workload")
    parser.add_argument("--worker-output")
    args = parser.parse_args()

    work_dir = Path(args.work_dir).resolve()
    work_dir.mkdir(parents=True, exist_ok=True)
    if args.worker_mode:
        if not args.workload or not args.worker_output:
            parser.error("worker mode requires --workload and --worker-output")
        workload = json.loads(Path(args.workload).read_text(encoding="utf-8"))
        result = run_worker(args.worker_mode, workload, work_dir / args.worker_mode, args.warmup_count)
        Path(args.worker_output).write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        return 0

    if not args.output or not args.markdown:
        parser.error("paired mode requires --output and --markdown")
    workload = make_workload(args.memory_count, args.query_count, args.seed)
    workload_path = work_dir / "workload.json"
    workload_path.write_text(json.dumps(workload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    runs = {}
    for mode in ("memory_disabled", "memguardos_enabled"):
        result_path = work_dir / (mode + ".json")
        command = [
            sys.executable,
            str(Path(__file__).resolve()),
            "--worker-mode",
            mode,
            "--workload",
            str(workload_path),
            "--worker-output",
            str(result_path),
            "--work-dir",
            str(work_dir),
            "--warmup-count",
            str(args.warmup_count),
        ]
        completed = subprocess.run(command, text=True, capture_output=True, check=False)
        if completed.returncode != 0:
            raise RuntimeError("resource worker %s failed: %s" % (mode, completed.stderr.strip()))
        runs[mode] = json.loads(result_path.read_text(encoding="utf-8"))
    payload = {
        "schema_version": "1.0",
        "protocol": {
            "process_isolation": True,
            "same_workload": True,
            "baseline": "memory module disabled; full history scanned for the same retrieval task",
            "enabled": "MemGuardOS initialized, persisted and queried",
        },
        "baseline": runs["memory_disabled"],
        "enabled": runs["memguardos_enabled"],
    }
    payload["comparison"] = compare_runs(payload["baseline"], payload["enabled"])
    Path(args.output).parent.mkdir(parents=True, exist_ok=True)
    Path(args.markdown).parent.mkdir(parents=True, exist_ok=True)
    Path(args.output).write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    Path(args.markdown).write_text(to_markdown(payload), encoding="utf-8")
    print(json.dumps(payload, ensure_ascii=False, indent=2))
    return 0 if payload["comparison"]["protocol_valid"] else 2


if __name__ == "__main__":
    raise SystemExit(main())
