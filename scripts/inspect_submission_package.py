from __future__ import annotations

from pathlib import Path
from zipfile import ZipFile
import argparse
import json
import sys

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scripts.package_submission import COMPETITION_ROLES, DIST, classify_file  # noqa: E402


REQUIRED_PATHS = {
    "README.md",
    "LICENSE",
    "pyproject.toml",
    "memguard/service.py",
    "memguard/api/server.py",
    "memguard/web/dashboard.py",
    "memguard/store/mmap_store.py",
    "memguard/store/sqlite_meta.py",
    "memguard/retrieval/hybrid.py",
    "memguard/retrieval/kylin_sdk.py",
    "scripts/install_openkylin.sh",
    "scripts/build_deb.sh",
    "scripts/collect_openkylin_evidence.sh",
    "scripts/web_dashboard_smoke.py",
    "native/bpf_lsm/memguard_lsm.bpf.c",
    "native/ukui/peony-memguard/memguard_peony_plugin.cpp",
    "native/ukui/ukcc-memguard/memguard_ukcc_plugin.cpp",
    "docs/项目介绍总览.md",
    "docs/最终提交说明.md",
    "docs/赛题符合性说明.md",
    "docs/测试报告.md",
    "docs/评委证据包索引.md",
    "docs/evidence/latest/pytest.txt",
    "docs/evidence/latest/web_dashboard.txt",
    "docs/evidence/latest/sdk_status.txt",
    "docs/evidence/latest/verify_audit.txt",
    "docs/evidence/latest/manifest.json",
    "docs/evidence/latest/SHA256SUMS.txt",
}

FORBIDDEN_EXACT = {
    "scripts/package_submission.py",
    "scripts/release_preflight.py",
    "scripts/smoke_submission_archive.py",
    "scripts/check_competition_ready.py",
    "scripts/check_docs_links.py",
    "docs/赛题要求逐句符合性核查.md",
    "docs/竞赛就绪自检报告.md",
    "tests/test_packaging.py",
    "CHANGELOG.md",
    "CONTRIBUTING.md",
    "CODE_OF_CONDUCT.md",
    "SECURITY.md",
    "Dockerfile",
    "Makefile",
}

FORBIDDEN_PREFIXES = {
    ".git/",
    ".github/",
    ".devcontainer/",
    ".pytest_cache/",
    ".ruff_cache/",
    ".venv/",
    "__pycache__/",
    "dist/",
    "local_notes/",
}

FORBIDDEN_SUFFIXES = {
    ".pyc",
    ".pyo",
    ".sqlite3",
    ".segment",
    ".log",
    ".tmp",
}

LOCAL_PATH_MARKERS = {
    "D:\\",
    "C:\\Users\\",
    "/home/luo",
    "Luo_docs",
    "26312",
    "192.168.73.128",
}


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    package = Path(args.package) if args.package else latest_competition_package()
    manifest = Path(args.manifest) if args.manifest else inferred_manifest(package)
    report = inspect_package(package, manifest if manifest.exists() else None)
    if args.output:
        Path(args.output).write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0 if report["ok"] else 2


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Inspect a MemGuardOS competition submission package")
    parser.add_argument("--package", default="", help="Submission zip path. Defaults to the latest dist/memguardos_competition_submission_*.zip")
    parser.add_argument("--manifest", default="", help="Optional package manifest JSON path")
    parser.add_argument("--output", default="", help="Optional JSON report path")
    return parser


def latest_competition_package() -> Path:
    candidates = [
        path
        for path in DIST.glob("memguardos_competition_submission_*.zip")
        if not path.name.endswith("_manifest.zip")
    ]
    if not candidates:
        raise SystemExit("no competition submission package found in dist/")
    return max(candidates, key=lambda item: item.stat().st_mtime)


def inferred_manifest(package: Path) -> Path:
    return package.with_name(package.stem + "_manifest.json")


def inspect_package(package: Path, manifest: Path | None = None) -> dict[str, object]:
    with ZipFile(package) as zf:
        names = sorted(name for name in zf.namelist() if not name.endswith("/"))

    name_set = set(names)
    missing_required = sorted(REQUIRED_PATHS - name_set)
    forbidden_hits = sorted(name for name in names if is_forbidden(name))
    bad_roles = sorted(
        ({"path": name, "role": classify_file(Path(name))}
        for name in names
        if classify_file(Path(name)) not in COMPETITION_ROLES),
        key=lambda item: item["path"],
    )
    local_path_hits = scan_formal_docs_for_local_paths(package, names)
    manifest_report = inspect_manifest(manifest, name_set) if manifest else {"available": False}
    ok = not missing_required and not forbidden_hits and not bad_roles and not local_path_hits and bool(manifest_report.get("ok", True))
    return {
        "ok": ok,
        "package": str(package),
        "file_count": len(names),
        "missing_required": missing_required,
        "forbidden_hits": forbidden_hits,
        "bad_roles": bad_roles,
        "local_path_hits": local_path_hits,
        "manifest": manifest_report,
    }


def inspect_manifest(manifest: Path, expected_names: set[str]) -> dict[str, object]:
    if not manifest.exists():
        return {"available": False, "ok": False, "missing": str(manifest)}
    data = json.loads(manifest.read_text(encoding="utf-8"))
    entries = data.get("files", [])
    paths = {str(item.get("path", "")) for item in entries if isinstance(item, dict)}
    roles = {str(item.get("role", "")) for item in entries if isinstance(item, dict)}
    missing_in_manifest = sorted(expected_names - paths)
    extra_in_manifest = sorted(paths - expected_names)
    bad_manifest_roles = sorted(role for role in roles if role not in COMPETITION_ROLES)
    return {
        "available": True,
        "ok": not missing_in_manifest and not extra_in_manifest and not bad_manifest_roles,
        "path": str(manifest),
        "file_count": len(paths),
        "missing_in_manifest": missing_in_manifest,
        "extra_in_manifest": extra_in_manifest,
        "bad_roles": bad_manifest_roles,
    }


def is_forbidden(name: str) -> bool:
    parts = name.split("/")
    if name in FORBIDDEN_EXACT:
        return True
    if any(name.startswith(prefix) for prefix in FORBIDDEN_PREFIXES):
        return True
    if "__pycache__" in parts:
        return True
    return any(name.endswith(suffix) for suffix in FORBIDDEN_SUFFIXES)


def scan_formal_docs_for_local_paths(package: Path, names: list[str]) -> list[dict[str, object]]:
    hits: list[dict[str, object]] = []
    formal_docs = [name for name in names if classify_file(Path(name)) == "正式中文文档"]
    with ZipFile(package) as zf:
        for name in formal_docs:
            try:
                text = zf.read(name).decode("utf-8")
            except UnicodeDecodeError:
                continue
            for line_no, line in enumerate(text.splitlines(), start=1):
                matched = sorted(marker for marker in LOCAL_PATH_MARKERS if marker in line)
                if matched:
                    hits.append({"path": name, "line": line_no, "markers": matched})
    return hits


if __name__ == "__main__":
    raise SystemExit(main())
