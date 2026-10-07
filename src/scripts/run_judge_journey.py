"""Execute the five submitted verification routes using the standard library."""

from pathlib import Path
import argparse
import json
import subprocess
import sys


SOURCE_ROOT = Path(__file__).resolve().parents[1]
REPOSITORY_ROOT = SOURCE_ROOT.parent
ROUTES = REPOSITORY_ROOT / "demo/judge_journey"


def main() -> int:
    parser = argparse.ArgumentParser(description="MemGuardOS 功能验证路线")
    parser.add_argument("--list", action="store_true")
    selection = parser.add_mutually_exclusive_group()
    selection.add_argument("--step", default="")
    selection.add_argument("--all", action="store_true")
    parser.add_argument("--evidence-dir", default="output/judge_journey_latest")
    args = parser.parse_args()
    routes = {path.stem: json.loads(path.read_text(encoding="utf-8")) for path in sorted(ROUTES.glob("*.yml"))}
    if args.list or not (args.step or args.all):
        for key, route in routes.items():
            print(f"{key}: {route['title']}")
        return 0
    if args.step and args.step not in routes:
        parser.error("未知步骤：" + args.step)
    selected = routes if args.all else {args.step: routes[args.step]}
    output = Path(args.evidence_dir)
    if not output.is_absolute():
        output = SOURCE_ROOT / output
    output.mkdir(parents=True, exist_ok=True)
    summaries = []
    for key, route in selected.items():
        print(f"\n{route['title']}\n{route['goal']}", flush=True)
        checks = []
        for index, original in enumerate(route["commands"], 1):
            command = list(original)
            if not command or command[0] != "python" or command[1:3] not in (["-m", "pytest"], ["scripts/demo_smoke.py"], ["scripts/verify_final_submission.py"]):
                raise ValueError("Unsupported verification command")
            command[0] = sys.executable
            result = subprocess.run(command, cwd=SOURCE_ROOT, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, encoding="utf-8", errors="replace", check=False)
            (output / f"{key}_{index}.txt").write_text(result.stdout, encoding="utf-8")
            print(result.stdout, flush=True)
            checks.append({"command": original, "returncode": result.returncode, "ok": result.returncode == 0})
        evidence_missing = [name for name in route["evidence_files"] if not (REPOSITORY_ROOT / name).is_file()]
        summary = {"step": key, "checks": checks, "missing_evidence": evidence_missing, "ok": all(item["ok"] for item in checks) and not evidence_missing}
        summaries.append(summary)
        (output / (key + ".json")).write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
    report = {"ok": all(item["ok"] for item in summaries), "steps": summaries}
    (output / "summary.json").write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    return 0 if report["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
