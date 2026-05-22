from __future__ import annotations

from collections import Counter
from datetime import datetime
from pathlib import Path
from zipfile import ZIP_DEFLATED, ZipFile
import fnmatch
import json


ROOT = Path(__file__).resolve().parents[1]
DIST = ROOT / "dist"

EXCLUDED_DIRS = {
    ".git",
    ".mypy_cache",
    ".pytest_cache",
    ".ruff_cache",
    ".venv",
    "__pycache__",
    "dist",
    "local_notes",
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
    "作品主体/核心代码": "memguard/",
    "作品主体/脚本": "scripts/",
    "作品主体/原生增强": "native/",
    "作品主体/测试": "tests/",
    "作品主体/数据与评测样例": "data/",
    "中文文档": "README.md and docs/",
    "证据包": "docs/evidence/",
    "GitHub工程化支撑": ".github/, .devcontainer/, CONTRIBUTING.md, SECURITY.md, CHANGELOG.md, CODE_OF_CONDUCT.md, requirements-dev.txt, .dockerignore, .gitattributes, .gitignore",
    "开发环境支撑": "Dockerfile",
    "项目配置": "pyproject.toml, Makefile, LICENSE",
}

COMPETITION_ROLES = {
    "作品主体/核心代码",
    "作品主体/脚本",
    "作品主体/原生增强",
    "作品主体/测试",
    "作品主体/数据与评测样例",
    "中文文档",
    "证据包",
    "项目配置",
}


def classify_file(path: Path) -> str:
    rel = path.relative_to(ROOT).as_posix()
    if rel.startswith("memguard/"):
        return "作品主体/核心代码"
    if rel.startswith("scripts/"):
        return "作品主体/脚本"
    if rel.startswith("native/"):
        return "作品主体/原生增强"
    if rel.startswith("tests/"):
        return "作品主体/测试"
    if rel.startswith("data/"):
        return "作品主体/数据与评测样例"
    if rel.startswith("docs/evidence/"):
        return "证据包"
    if rel.startswith("docs/") or rel == "README.md":
        return "中文文档"
    if rel.startswith(".github/") or rel.startswith(".devcontainer/") or rel in {
        "CONTRIBUTING.md",
        "SECURITY.md",
        "CHANGELOG.md",
        "CODE_OF_CONDUCT.md",
        "requirements-dev.txt",
        ".dockerignore",
        ".gitattributes",
        ".gitignore",
    }:
        return "GitHub工程化支撑"
    if rel == "Dockerfile":
        return "开发环境支撑"
    if rel in {"pyproject.toml", "Makefile", "LICENSE"}:
        return "项目配置"
    return "其他"


def should_skip(path: Path) -> bool:
    rel = path.relative_to(ROOT).as_posix()
    if rel.startswith("docs/evidence/openkylin_") or rel.startswith("docs/evidence/kylin_embedding_diag_"):
        return True
    rel_parts = path.relative_to(ROOT).parts
    if any(part in EXCLUDED_DIRS for part in rel_parts):
        return True
    if any(part.endswith(".egg-info") for part in rel_parts):
        return True
    return any(fnmatch.fnmatch(path.name, pattern) for pattern in EXCLUDED_PATTERNS)


def iter_files() -> list[Path]:
    files: list[Path] = []
    for path in ROOT.rglob("*"):
        if path.is_file() and not should_skip(path):
            files.append(path)
    return sorted(files)


def write_package(package_path: Path, manifest_path: Path, files: list[Path], package_type: str) -> None:
    with ZipFile(package_path, "w", ZIP_DEFLATED) as zf:
        for path in files:
            zf.write(path, path.relative_to(ROOT).as_posix())

    file_entries = [{"path": path.relative_to(ROOT).as_posix(), "role": classify_file(path)} for path in files]
    role_summary = dict(sorted(Counter(entry["role"] for entry in file_entries).items()))
    manifest = {
        "package": package_path.name,
        "package_type": package_type,
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


def main() -> int:
    DIST.mkdir(exist_ok=True)
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    all_files = iter_files()
    competition_files = [path for path in all_files if classify_file(path) in COMPETITION_ROLES]

    write_package(
        DIST / f"memguardos_competition_submission_{timestamp}.zip",
        DIST / f"memguardos_competition_submission_{timestamp}_manifest.json",
        competition_files,
        "竞赛提交包",
    )
    print()
    write_package(
        DIST / f"memguardos_full_repository_{timestamp}.zip",
        DIST / f"memguardos_full_repository_{timestamp}_manifest.json",
        all_files,
        "完整开源仓库包",
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
