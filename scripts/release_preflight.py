from __future__ import annotations

from dataclasses import asdict, dataclass
from pathlib import Path
import argparse
import json
import subprocess
import sys
import time


ROOT = Path(__file__).resolve().parents[1]


@dataclass
class StepResult:
    name: str
    command: list[str]
    ok: bool
    returncode: int
    elapsed_s: float


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    steps = _steps(include_package=not args.skip_package)
    results = []
    started = time.perf_counter()
    for name, command in steps:
        print("==> %s" % name, flush=True)
        result = _run_step(name, command)
        results.append(result)
        print("    %s in %.2fs" % ("ok" if result.ok else "failed", result.elapsed_s), flush=True)
        if not result.ok and args.fail_fast:
            break
    payload = {
        "ok": all(item.ok for item in results),
        "elapsed_s": round(time.perf_counter() - started, 3),
        "results": [asdict(item) for item in results],
    }
    if args.output:
        Path(args.output).write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(payload, ensure_ascii=False, indent=2))
    return 0 if payload["ok"] else 2


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Run MemGuardOS release preflight checks")
    parser.add_argument("--skip-package", action="store_true", help="Skip submission package generation")
    parser.add_argument("--fail-fast", action="store_true", help="Stop after the first failed step")
    parser.add_argument("--output", default="", help="Optional JSON report path")
    return parser


def _steps(*, include_package: bool) -> list[tuple[str, list[str]]]:
    py = sys.executable
    steps = [
        ("compile", [py, "-m", "compileall", "-q", "memguard", "scripts"]),
        ("ruff", [py, "-m", "ruff", "check", "."]),
        ("pytest", [py, "-m", "pytest", "-q"]),
        ("docs links", [py, "scripts/check_docs_links.py"]),
        ("web dashboard smoke", [py, "scripts/web_dashboard_smoke.py"]),
        (
            "competition readiness",
            [
                py,
                "scripts/check_competition_ready.py",
                "--output",
                "docs/competition_ready.json",
                "--markdown",
                "docs/竞赛就绪自检报告.md",
            ],
        ),
    ]
    if include_package:
        steps.append(("submission package", [py, "scripts/package_submission.py"]))
    return steps


def _run_step(name: str, command: list[str]) -> StepResult:
    started = time.perf_counter()
    completed = subprocess.run(command, cwd=ROOT, check=False)
    return StepResult(
        name=name,
        command=command,
        ok=completed.returncode == 0,
        returncode=completed.returncode,
        elapsed_s=round(time.perf_counter() - started, 3),
    )


if __name__ == "__main__":
    raise SystemExit(main())
