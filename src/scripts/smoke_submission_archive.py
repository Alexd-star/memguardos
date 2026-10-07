from __future__ import annotations

from pathlib import Path
from tempfile import TemporaryDirectory
from zipfile import ZipFile
import argparse
import json
import os
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scripts.inspect_submission_package import inspect_package, inferred_manifest, latest_competition_package  # noqa: E402


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    package = Path(args.package) if args.package else latest_competition_package()
    manifest = Path(args.manifest) if args.manifest else inferred_manifest(package)
    package_report = inspect_package(package, manifest if manifest.exists() else None)
    if not package_report["ok"]:
        print(json.dumps({"ok": False, "stage": "package_audit", "package_report": package_report}, ensure_ascii=False, indent=2))
        return 2

    with TemporaryDirectory() as tmp:
        root = Path(tmp) / "submission"
        root.mkdir()
        _extract_archive_safe(package, root)
        if (root / "src/pyproject.toml").is_file():
            root = root / "src"
        checks = [
            _run(root, [sys.executable, "-m", "compileall", "-q", "memguard", "scripts"]),
            _run(root, [sys.executable, "scripts/demo_smoke.py"]),
            _run(root, [sys.executable, "scripts/web_dashboard_smoke.py"]),
        ]
    report = {
        "ok": all(item["ok"] for item in checks),
        "package": str(package),
        "package_audit_ok": package_report["ok"],
        "checks": checks,
    }
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0 if report["ok"] else 2


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Extract and smoke-test a MemGuardOS competition submission archive")
    parser.add_argument("--package", default="", help="Submission zip path. Defaults to latest dist/memguardos_competition_submission_*.zip")
    parser.add_argument("--manifest", default="", help="Optional package manifest JSON path")
    return parser


def _extract_archive_safe(package: Path, root: Path) -> None:
    root_resolved = root.resolve()
    with ZipFile(package) as zf:
        for member in zf.infolist():
            target = (root / member.filename).resolve()
            if target != root_resolved and root_resolved not in target.parents:
                raise RuntimeError("unsafe archive member: %s" % member.filename)
        zf.extractall(root)


def _run(cwd: Path, command: list[str]) -> dict[str, object]:
    env = dict(os.environ)
    env["PYTHONPATH"] = str(cwd)
    completed = subprocess.run(command, cwd=cwd, env=env, text=True, capture_output=True, check=False)
    return {
        "name": command_name(command),
        "command": command,
        "ok": completed.returncode == 0,
        "returncode": completed.returncode,
        "stdout_tail": completed.stdout[-2000:],
        "stderr_tail": completed.stderr[-2000:],
    }


def command_name(command: list[str]) -> str:
    if len(command) >= 3 and command[1:3] == ["-m", "compileall"]:
        return "compileall"
    if command:
        return Path(command[-1]).name
    return "unknown"


if __name__ == "__main__":
    raise SystemExit(main())
