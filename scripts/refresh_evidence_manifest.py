#!/usr/bin/env python3
from __future__ import annotations

from pathlib import Path
import argparse
import hashlib
import json


def refresh_evidence_manifest(root: Path) -> dict[str, object]:
    evidence_root = Path(root).expanduser().resolve()
    if not evidence_root.is_dir():
        raise ValueError("evidence root must be an existing directory")

    checksum_path = evidence_root / "SHA256SUMS.txt"
    manifest_path = evidence_root / "manifest.json"
    content_files = [
        path
        for path in sorted(
            evidence_root.rglob("*"),
            key=lambda item: item.relative_to(evidence_root).as_posix(),
        )
        if path.is_file() and path not in {checksum_path, manifest_path}
    ]
    checksum_lines = [
        "%s  %s"
        % (
            hashlib.sha256(path.read_bytes()).hexdigest(),
            path.relative_to(evidence_root).as_posix(),
        )
        for path in content_files
    ]
    checksum_tmp = checksum_path.with_suffix(".txt.tmp")
    checksum_tmp.write_text(
        "\n".join(checksum_lines) + ("\n" if checksum_lines else ""),
        encoding="utf-8",
    )
    checksum_tmp.replace(checksum_path)

    manifest_files = []
    for path in sorted(
        evidence_root.rglob("*"),
        key=lambda item: item.relative_to(evidence_root).as_posix(),
    ):
        if not path.is_file() or path in {checksum_path, manifest_path}:
            continue
        manifest_files.append(
            {
                "path": path.relative_to(evidence_root).as_posix(),
                "bytes": path.stat().st_size,
                "sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
            }
        )
    manifest = {
        "schema_version": "1.0",
        "file_count": len(manifest_files),
        "files": manifest_files,
    }
    manifest_tmp = manifest_path.with_suffix(".json.tmp")
    manifest_tmp.write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    manifest_tmp.replace(manifest_path)
    return manifest


def main() -> int:
    parser = argparse.ArgumentParser(
        description="重建 MemGuardOS 证据目录的 SHA256 清单与 manifest"
    )
    parser.add_argument("root")
    args = parser.parse_args()
    result = refresh_evidence_manifest(Path(args.root))
    print(
        json.dumps(
            {
                "ok": True,
                "root": str(Path(args.root).resolve()),
                "file_count": result["file_count"],
            },
            ensure_ascii=False,
            indent=2,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
