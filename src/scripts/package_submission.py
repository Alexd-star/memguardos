from __future__ import annotations

from collections import Counter
from datetime import datetime
from pathlib import Path
from zipfile import ZIP_DEFLATED, ZipFile, ZipInfo
import argparse
import fnmatch
import hashlib
import json
import os
import stat
import subprocess
import sys
import time

ROOT = Path(__file__).resolve().parents[1]
DIST = ROOT / "dist"
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scripts.release_inventory import inventory_set, is_competition_path  # noqa: E402

EXCLUDED_DIRS = {
    ".git",
    ".mypy_cache",
    ".playwright-cli",
    ".pytest_cache",
    ".ruff_cache",
    ".venv",
    ".venv-agent",
    ".worktrees",
    ".tmp-commitment-review",
    ".tmp-memory-value-audit-2",
    "__pycache__",
    "artifacts",
    "build",
    "dist",
    "local_notes",
    "node_modules",
    "output",
    "outputs",
}

EXCLUDED_PATTERNS = [
    "*.pyc",
    "*.pyo",
    "*.sqlite3",
    "*.segment",
    "*.log",
    "*.tmp",
    ".DS_Store",
]

ROLE_DESCRIPTIONS = {
    "核心代码": "memguard/",
    "原生集成": "native/",
    "安装与交付": "packaging/ 与运行、安装、构建脚本",
    "测试": "tests/",
    "数据与样例": "data/ 与 demo/",
    "正式文档": "README.md 与精选 docs/（含安全说明）",
    "验证证据": "docs/evidence/、SBOM 与安全扫描",
    "项目配置": "pyproject.toml、LICENSE、Makefile 与容器配置",
    "社区与持续集成": ".github/、贡献规范与变更记录",
}

FORMAL_DOCS = inventory_set("formal_docs")
COMPETITION_ROLES = set(ROLE_DESCRIPTIONS) - {"社区与持续集成"}


def classify_file(path: Path) -> str:
    path = Path(path)
    rel = path.as_posix() if not path.is_absolute() else path.relative_to(ROOT).as_posix()
    if rel.startswith("memguard/"):
        return "核心代码"
    if rel.startswith("native/"):
        return "原生集成"
    if rel.startswith(("packaging/", "scripts/")):
        return "安装与交付"
    if rel.startswith("tests/"):
        return "测试"
    if rel.startswith(("data/", "demo/")):
        return "数据与样例"
    if rel.startswith("docs/evidence/") or rel in {"docs/sbom.json", "docs/security_scan.json"}:
        return "验证证据"
    if rel.startswith("docs/") or rel in FORMAL_DOCS:
        return "正式文档"
    if rel.startswith((".github/", ".devcontainer/")) or rel in {
        "CONTRIBUTING.md", "CHANGELOG.md", "CODE_OF_CONDUCT.md"
    }:
        return "社区与持续集成"
    return "项目配置"


def should_skip(path: Path) -> bool:
    rel = path.relative_to(ROOT).as_posix()
    if rel.startswith("docs/research/"):
        return True
    rel_parts = path.relative_to(ROOT).parts
    if any(part in EXCLUDED_DIRS for part in rel_parts):
        return True
    if any(part.endswith(".egg-info") for part in rel_parts):
        return True
    return any(fnmatch.fnmatch(path.name, pattern) for pattern in EXCLUDED_PATTERNS)


def iter_files() -> list[Path]:
    """Return versioned files only, never arbitrary local workspace content."""

    completed = subprocess.run(
        ["git", "ls-files", "-z"],
        cwd=ROOT,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        check=False,
    )
    if completed.returncode != 0:
        raise RuntimeError("submission packaging requires a Git source checkout")
    files: list[Path] = []
    for raw in completed.stdout.split(b"\0"):
        if not raw:
            continue
        relative = raw.decode("utf-8", errors="surrogateescape")
        path = ROOT / relative
        if path.is_file() and not should_skip(path):
            files.append(path)
    return sorted(files, key=lambda item: item.relative_to(ROOT).as_posix())


def write_package(package_path: Path, manifest_path: Path, files: list[Path], package_type: str) -> None:
    source_revision = _git_output(["rev-parse", "HEAD"], "unknown")
    source_epoch = _source_date_epoch()
    zip_time = time.gmtime(max(source_epoch, 315532800))[:6]
    with ZipFile(package_path, "w", ZIP_DEFLATED, compresslevel=9) as zf:
        for path in files:
            relative = path.relative_to(ROOT).as_posix()
            info = ZipInfo(relative, date_time=zip_time)
            info.compress_type = ZIP_DEFLATED
            mode = path.stat().st_mode
            permissions = 0o755 if mode & (stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH) else 0o644
            info.external_attr = permissions << 16
            zf.writestr(info, path.read_bytes())

    file_entries = [
        {
            "path": path.relative_to(ROOT).as_posix(),
            "role": classify_file(path),
            "bytes": path.stat().st_size,
            "sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
        }
        for path in files
    ]
    role_summary = dict(sorted(Counter(entry["role"] for entry in file_entries).items()))
    manifest = {
        "package": package_path.name,
        "package_type": package_type,
        "source_revision": source_revision,
        "source_date_epoch": source_epoch,
        "archive_sha256": hashlib.sha256(package_path.read_bytes()).hexdigest(),
        "file_count": len(files),
        "excluded_dirs": sorted(EXCLUDED_DIRS),
        "excluded_patterns": EXCLUDED_PATTERNS,
        "roles": ROLE_DESCRIPTIONS,
        "role_summary": role_summary,
        "files": file_entries,
    }
    manifest_path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")

    print(f"生成 {package_type}: {package_path}")
    print(f"清单 {manifest_path}")
    print(f"文件数 {len(files)}")
    print("角色汇总")
    for role, count in role_summary.items():
        print(f"- {role}: {count}")


def _source_date_epoch() -> int:
    configured = os.environ.get("SOURCE_DATE_EPOCH", "").strip()
    if configured:
        try:
            return int(configured)
        except ValueError as exc:
            raise ValueError("SOURCE_DATE_EPOCH must be an integer") from exc
    value = _git_output(["show", "-s", "--format=%ct", "HEAD"], "315532800")
    try:
        return int(value)
    except ValueError:
        return 315532800


def _git_output(arguments: list[str], fallback: str) -> str:
    completed = subprocess.run(
        ["git", *arguments],
        cwd=ROOT,
        stdout=subprocess.PIPE,
        stderr=subprocess.DEVNULL,
        text=True,
        check=False,
    )
    return completed.stdout.strip() or fallback


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Build reproducible MemGuardOS delivery archives")
    parser.add_argument(
        "--kind",
        choices=("all", "competition", "full"),
        default="all",
        help="Archive type to build. Defaults to both competition and full repository archives.",
    )
    parser.add_argument(
        "--output-dir",
        default=str(DIST),
        help="Output directory. Defaults to the repository dist directory.",
    )
    parser.add_argument(
        "--timestamp",
        default="",
        help="Optional filename timestamp for deterministic automation.",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    output_dir = Path(args.output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    timestamp = args.timestamp or datetime.now().strftime("%Y%m%d_%H%M%S")
    all_files = iter_files()
    competition_files = [
        path for path in all_files if is_competition_path(path.relative_to(ROOT).as_posix())
    ]

    if args.kind in {"all", "competition"}:
        write_package(
            output_dir / f"memguardos_competition_submission_{timestamp}.zip",
            output_dir / f"memguardos_competition_submission_{timestamp}_manifest.json",
            competition_files,
            "竞赛提交包",
        )
    if args.kind == "all":
        print()
    if args.kind in {"all", "full"}:
        write_package(
            output_dir / f"memguardos_full_repository_{timestamp}.zip",
            output_dir / f"memguardos_full_repository_{timestamp}_manifest.json",
            all_files,
            "完整开源仓库包",
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
