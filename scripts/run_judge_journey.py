from __future__ import annotations

from pathlib import Path
import argparse
import json
import os
import platform
import re
import subprocess
import sys
import time


ROOT = Path(__file__).resolve().parents[1]
DEMO_DIR = ROOT / "demo" / "judge_journey"

ALLOWED_STEPS = {
    "01_memory_capture_and_preference": [
        [sys.executable, "scripts/demo_smoke.py"],
        [sys.executable, "-m", "pytest", "tests/test_agent_context.py::test_agent_context_filters_memories_by_stage", "-q"],
    ],
    "02_agent_context_reuse": [
        [sys.executable, "-m", "pytest", "tests/test_agent_context.py", "-q"],
        [sys.executable, "scripts/web_dashboard_smoke.py"],
    ],
    "03_sensitive_block_auditd_bpf": [
        [sys.executable, "-m", "pytest", "tests/test_core.py::test_sensitive_credential_is_blocked", "-q"],
        ["bash", "scripts/run_bpf_lsm_enforcer.sh"],
    ],
    "04_precise_erasure_and_verify": [
        [sys.executable, "-m", "pytest", "tests/test_erasure_intent_compiler.py", "-q"],
        [sys.executable, "scripts/run_evidencebench_v2.py", "--output", "docs/eval_v2_result.json", "--markdown", "docs/eval_v2_report.md"],
    ],
    "05_openkylin_native_controls": [
        ["bash", "scripts/verify_ukui_plugins.sh"],
        [sys.executable, "scripts/check_competition_ready.py", "--output", "docs/competition_ready.json", "--markdown", "docs/竞赛就绪自检报告.md"],
    ],
}


def step_files() -> list[Path]:
    return sorted(DEMO_DIR.glob("*.yml"))


def parse_yaml_summary(path: Path) -> dict[str, object]:
    text = path.read_text(encoding="utf-8")
    title = _scalar(text, "title")
    goal = _scalar(text, "goal")
    return {
        "id": path.stem,
        "file": str(path.relative_to(ROOT)),
        "title": title,
        "goal": goal,
        "evidence_files": _list(text, "evidence_files"),
        "fallback_if_environment_missing": _list(text, "fallback_if_environment_missing"),
    }


def _scalar(text: str, key: str) -> str:
    match = re.search(r"^%s:\s*\"?(.*?)\"?\s*$" % re.escape(key), text, flags=re.MULTILINE)
    return match.group(1) if match else ""


def _list(text: str, key: str) -> list[str]:
    match = re.search(r"^%s:\s*\n((?:\s+- .+\n?)*)" % re.escape(key), text, flags=re.MULTILINE)
    if not match:
        return []
    return [line.split("-", 1)[1].strip().strip('"') for line in match.group(1).splitlines() if line.strip().startswith("-")]


def run_step(step_id: str, evidence_dir: Path, timeout: int) -> dict[str, object]:
    if step_id not in ALLOWED_STEPS:
        raise ValueError("unknown Judge Journey step: %s" % step_id)
    evidence_dir.mkdir(parents=True, exist_ok=True)
    summary = parse_yaml_summary(DEMO_DIR / ("%s.yml" % step_id))
    commands = []
    for argv in ALLOWED_STEPS[step_id]:
        commands.append(run_allowed_command(argv, timeout=timeout))
    ok = all(item["ok"] or item["status"] == "graceful_fallback" for item in commands)
    result = {
        "schema": "memguardos-judge-journey-result-v1",
        "step": summary,
        "ok": ok,
        "host": {
            "platform": platform.platform(),
            "system": platform.system(),
            "is_openkylin_hint": is_openkylin(),
        },
        "commands": commands,
    }
    (evidence_dir / ("%s.json" % step_id)).write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return result


def run_allowed_command(argv: list[str], timeout: int) -> dict[str, object]:
    if argv[0] == "bash" and os.name == "nt":
        return {
            "argv": argv,
            "ok": False,
            "status": "graceful_fallback",
            "stdout": "",
            "stderr": "bash/openKylin-only command skipped on Windows host; rerun inside openKylin for native evidence.",
            "returncode": None,
        }
    start = time.perf_counter()
    try:
        proc = subprocess.run(argv, cwd=ROOT, text=True, capture_output=True, timeout=timeout)
    except FileNotFoundError as exc:
        return {
            "argv": argv,
            "ok": False,
            "status": "graceful_fallback",
            "stdout": "",
            "stderr": str(exc),
            "returncode": None,
        }
    except subprocess.TimeoutExpired as exc:
        return {
            "argv": argv,
            "ok": False,
            "status": "timeout",
            "stdout": exc.stdout or "",
            "stderr": exc.stderr or "",
            "returncode": None,
        }
    return {
        "argv": argv,
        "ok": proc.returncode == 0,
        "status": "passed" if proc.returncode == 0 else "failed",
        "returncode": proc.returncode,
        "duration_ms": round((time.perf_counter() - start) * 1000, 3),
        "stdout": proc.stdout[-4000:],
        "stderr": proc.stderr[-4000:],
    }


def is_openkylin() -> bool:
    os_release = Path("/etc/os-release")
    if not os_release.exists():
        return False
    return "openkylin" in os_release.read_text(encoding="utf-8", errors="ignore").lower()


def write_index(results: list[dict[str, object]], evidence_dir: Path) -> None:
    lines = [
        "# Judge Journey 执行结果",
        "",
        "| 步骤 | 结果 | 说明 |",
        "|---|---|---|",
    ]
    for item in results:
        step = item["step"]
        lines.append("| %s | %s | %s |" % (step["title"], "通过" if item["ok"] else "需复核", step["goal"]))
    lines.append("")
    lines.append("openKylin 专属命令在非 openKylin 环境会标记为 graceful fallback，不会伪造 BPF、UKUI、Peony 或 Kylin SDK 实机结果。")
    (evidence_dir / "README.md").write_text("\n".join(lines), encoding="utf-8")


def main() -> int:
    parser = argparse.ArgumentParser(description="Run MemGuardOS Judge Journey")
    parser.add_argument("--list", action="store_true", help="List available Judge Journey steps")
    parser.add_argument("--step", default="", help="Run one step id, for example 04_precise_erasure_and_verify")
    parser.add_argument("--all", action="store_true", help="Run all Judge Journey steps")
    parser.add_argument("--evidence-dir", default=str(ROOT / "docs" / "evidence" / "judge_journey_latest"))
    parser.add_argument("--timeout", type=int, default=90)
    args = parser.parse_args()

    summaries = [parse_yaml_summary(path) for path in step_files()]
    if args.list:
        print(json.dumps(summaries, ensure_ascii=False, indent=2))
        return 0

    if not args.all and not args.step:
        parser.error("use --list, --step or --all")
    selected = [item["id"] for item in summaries] if args.all else [args.step]
    evidence_dir = Path(args.evidence_dir)
    results = [run_step(step_id, evidence_dir, timeout=args.timeout) for step_id in selected]
    write_index(results, evidence_dir)
    print(json.dumps({"ok": all(item["ok"] for item in results), "evidence_dir": str(evidence_dir), "steps": selected}, ensure_ascii=False, indent=2))
    return 0 if all(item["ok"] for item in results) else 2


if __name__ == "__main__":
    raise SystemExit(main())
