from __future__ import annotations

import re
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
REPOSITORY_ROOT = ROOT.parent if ROOT.name == "src" else ROOT
DOC_PATHS = [ROOT / "README.md", ROOT / "docs"]
if ROOT != REPOSITORY_ROOT:
    DOC_PATHS += [REPOSITORY_ROOT / "README.md", REPOSITORY_ROOT / "demo", REPOSITORY_ROOT / "tests" / "README.md"]
LINK_RE = re.compile(r"\[[^\]]+\]\(([^)]+)\)")
IGNORE_PREFIXES = ("http://", "https://", "mailto:", "#")


def iter_markdown_files() -> list[Path]:
    files: list[Path] = []
    for path in DOC_PATHS:
        if path.is_file():
            files.append(path)
        elif path.is_dir():
            files.extend(path.rglob("*.md"))
    return sorted(files)


def normalize_target(raw: str) -> str:
    target = raw.strip()
    if " " in target and not target.startswith("<"):
        target = target.split()[0]
    if target.startswith("<") and target.endswith(">"):
        target = target[1:-1]
    if "#" in target:
        target = target.split("#", 1)[0]
    return target


def main() -> int:
    broken: list[str] = []
    for md in iter_markdown_files():
        text = md.read_text(encoding="utf-8", errors="replace")
        for match in LINK_RE.finditer(text):
            target = normalize_target(match.group(1))
            if not target or target.startswith(IGNORE_PREFIXES):
                continue
            if re.match(r"^[a-zA-Z][a-zA-Z0-9+.-]*:", target):
                continue
            resolved = (md.parent / target).resolve()
            try:
                resolved.relative_to(REPOSITORY_ROOT.resolve())
            except ValueError:
                broken.append(f"{md.relative_to(REPOSITORY_ROOT)} -> {target} escapes repository")
                continue
            if not resolved.exists():
                broken.append(f"{md.relative_to(REPOSITORY_ROOT)} -> {target}")
    if broken:
        print("Broken local Markdown links:")
        for item in broken:
            print(f"- {item}")
        return 1
    print(f"Checked {len(iter_markdown_files())} Markdown files; local links ok.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
