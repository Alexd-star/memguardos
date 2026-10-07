#!/usr/bin/env python3
from __future__ import annotations

import hashlib
import json
from pathlib import Path
import subprocess


ROOT = Path(__file__).resolve().parents[1]
OUTPUT = ROOT / ".source_manifest.json"


def _git(*args: str) -> str:
    completed = subprocess.run(
        ["git", *args],
        cwd=ROOT,
        check=True,
        capture_output=True,
        text=True,
    )
    return completed.stdout.strip()


def _git_blob(relative: str) -> bytes:
    completed = subprocess.run(
        ["git", "show", f"HEAD:{relative}"],
        cwd=ROOT,
        check=True,
        capture_output=True,
    )
    return completed.stdout


def main() -> int:
    if _git("status", "--porcelain"):
        raise RuntimeError("refusing to certify a dirty source tree; commit changes first")
    revision = _git("rev-parse", "HEAD")
    inventory = json.loads(
        (ROOT / "packaging" / "release_manifest.json").read_text(encoding="utf-8")
    )
    paths = sorted(
        {
            str(item)
            for item in inventory.get("required_paths", [])
            if not str(item).startswith("docs/evidence/")
        }
    )
    files = []
    for relative in paths:
        path = ROOT / relative
        if not path.is_file():
            raise FileNotFoundError(relative)
        content = _git_blob(relative)
        files.append(
            {
                "path": relative,
                "bytes": len(content),
                "sha256": hashlib.sha256(content).hexdigest(),
            }
        )
    payload = {
        "schema": "memguardos-source-manifest-v1",
        "source_revision": revision,
        "file_count": len(files),
        "files": files,
    }
    OUTPUT.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"wrote {OUTPUT} with {len(files)} required files at {revision}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
