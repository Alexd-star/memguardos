from __future__ import annotations

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


def should_skip(path: Path) -> bool:
    rel_parts = path.relative_to(ROOT).parts
    if any(part in EXCLUDED_DIRS for part in rel_parts):
        return True
    return any(fnmatch.fnmatch(path.name, pattern) for pattern in EXCLUDED_PATTERNS)


def iter_files() -> list[Path]:
    files: list[Path] = []
    for path in ROOT.rglob("*"):
        if path.is_file() and not should_skip(path):
            files.append(path)
    return sorted(files)


def main() -> int:
    DIST.mkdir(exist_ok=True)
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    package_path = DIST / f"memguardos_submission_{timestamp}.zip"
    manifest_path = DIST / f"memguardos_submission_{timestamp}_manifest.json"
    files = iter_files()

    with ZipFile(package_path, "w", ZIP_DEFLATED) as zf:
        for path in files:
            zf.write(path, path.relative_to(ROOT).as_posix())

    manifest = {
        "package": package_path.name,
        "file_count": len(files),
        "excluded_dirs": sorted(EXCLUDED_DIRS),
        "excluded_patterns": EXCLUDED_PATTERNS,
        "files": [path.relative_to(ROOT).as_posix() for path in files],
    }
    manifest_path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")

    print(f"created {package_path}")
    print(f"manifest {manifest_path}")
    print(f"files {len(files)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
