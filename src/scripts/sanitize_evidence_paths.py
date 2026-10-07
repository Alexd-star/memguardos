from __future__ import annotations

import argparse
from pathlib import Path
import re


TEXT_SUFFIXES = {".json", ".md", ".txt", ".log", ".stdout"}
SKIP_NAMES = {"manifest.json", "SHA256SUMS.txt"}
SECRET_PATTERNS = (
    (re.compile(r"\bsk-[A-Za-z0-9_-]{12,}\b"), "<API_TOKEN_REDACTED>"),
)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="将证据中的本机绝对路径替换为可公开占位符")
    parser.add_argument("paths", nargs="+", help="证据文件或目录")
    parser.add_argument("--project-root", default="", help="采集时项目绝对路径")
    parser.add_argument("--home", default="", help="采集用户主目录")
    parser.add_argument("--replace", action="append", default=[], metavar="OLD=NEW")
    args = parser.parse_args(argv)

    replacements: list[tuple[str, str]] = []
    if args.project_root:
        replacements.extend(_path_replacements(args.project_root, "<PROJECT_ROOT>"))
    if args.home:
        replacements.extend(_path_replacements(args.home, "<HOME>"))
    for raw in args.replace:
        if "=" not in raw:
            parser.error("--replace must use OLD=NEW")
        old, new = raw.split("=", 1)
        replacements.extend(_path_replacements(old, new))
    replacements = sorted(set(replacements), key=lambda item: len(item[0]), reverse=True)

    changed: list[str] = []
    for raw_path in args.paths:
        for path in _iter_text_files(Path(raw_path)):
            original = path.read_text(encoding="utf-8", errors="replace")
            updated = original
            for old, new in replacements:
                updated = updated.replace(old, new)
            for pattern, placeholder in SECRET_PATTERNS:
                updated = pattern.sub(placeholder, updated)
            if updated != original:
                with path.open("w", encoding="utf-8", newline="\n") as handle:
                    handle.write(updated)
                changed.append(path.as_posix())
    print(f"sanitized_files={len(changed)}")
    for path in changed:
        print(path)
    return 0


def _path_replacements(value: str, placeholder: str) -> list[tuple[str, str]]:
    normalized = value.rstrip("/\\")
    variants = {
        normalized,
        normalized.replace("\\", "/"),
        normalized.replace("\\", "\\\\"),
    }
    return [(variant, placeholder) for variant in variants if variant]


def _iter_text_files(path: Path):
    candidates = path.rglob("*") if path.is_dir() else [path]
    for candidate in candidates:
        if not candidate.is_file() or candidate.name in SKIP_NAMES:
            continue
        if candidate.suffix.lower() in TEXT_SUFFIXES or candidate.name.endswith(".stdout.txt"):
            yield candidate


if __name__ == "__main__":
    raise SystemExit(main())
