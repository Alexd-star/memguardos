from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
import argparse
import hashlib
import json


EXCLUDED_NAMES = {"SHA256SUMS.txt", "manifest.json", "evidence_manifest.txt"}


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as fh:
        for chunk in iter(lambda: fh.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def build_manifest(root: Path) -> dict:
    root = root.resolve()
    entries = []
    for path in sorted(root.rglob("*")):
        if not path.is_file() or path.name in EXCLUDED_NAMES:
            continue
        rel = path.relative_to(root).as_posix()
        entries.append(
            {
                "path": rel,
                "bytes": path.stat().st_size,
                "sha256": sha256_file(path),
            }
        )
    return {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "root": display_root(root),
        "file_count": len(entries),
        "total_bytes": sum(item["bytes"] for item in entries),
        "files": entries,
    }


def write_outputs(root: Path, manifest: dict) -> None:
    root.mkdir(parents=True, exist_ok=True)
    (root / "manifest.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    lines = ["%s  %s" % (item["sha256"], item["path"]) for item in manifest["files"]]
    (root / "SHA256SUMS.txt").write_text("\n".join(lines) + ("\n" if lines else ""), encoding="utf-8")


def display_root(root: Path) -> str:
    try:
        return root.relative_to(Path.cwd().resolve()).as_posix()
    except ValueError:
        return root.name


def main() -> int:
    parser = argparse.ArgumentParser(description="Write SHA256 manifest for a MemGuardOS evidence directory")
    parser.add_argument("directory", help="Evidence directory")
    args = parser.parse_args()
    root = Path(args.directory)
    manifest = build_manifest(root)
    write_outputs(root, manifest)
    print(json.dumps({"directory": str(root), "file_count": manifest["file_count"], "total_bytes": manifest["total_bytes"]}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
