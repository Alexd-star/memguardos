from __future__ import annotations

from argparse import ArgumentParser
from pathlib import Path
import hashlib
import json
import zipfile


def restore_memguard(archive_path: Path, output_dir: Path) -> dict[str, object]:
    archive_path = Path(archive_path)
    output_dir = Path(output_dir)
    if not archive_path.exists():
        raise FileNotFoundError("backup archive not found: %s" % archive_path)
    output_dir.mkdir(parents=True, exist_ok=True)

    with zipfile.ZipFile(archive_path, "r") as zf:
        names = zf.namelist()
        duplicates = sorted({name for name in names if names.count(name) > 1})
        if duplicates:
            raise ValueError("sha256 manifest validation failed: duplicate path %s" % ", ".join(duplicates))
        if "manifest.json" not in names:
            raise ValueError("backup manifest.json is missing")
        manifest = json.loads(zf.read("manifest.json").decode("utf-8"))
        if manifest.get("schema") != "memguardos.backup.v1":
            raise ValueError("unsupported backup schema")
        files = manifest.get("files")
        if not isinstance(files, list):
            raise ValueError("backup manifest files must be an array")
        for item in files:
            rel = str(item.get("path", ""))
            if not rel or rel.startswith("/") or ".." in Path(rel).parts:
                raise ValueError("unsafe backup path: %s" % rel)
            if rel not in names:
                raise ValueError("backup payload missing: %s" % rel)
            data = zf.read(rel)
            expected = str(item.get("sha256", ""))
            actual = hashlib.sha256(data).hexdigest()
            if actual != expected:
                raise ValueError("sha256 mismatch for %s" % rel)
            expected_bytes = int(item.get("bytes", -1))
            if len(data) != expected_bytes:
                raise ValueError("byte length mismatch for %s" % rel)
        restored_files = {}
        for item in files:
            rel = str(item["path"])
            destination = output_dir / rel
            destination.parent.mkdir(parents=True, exist_ok=True)
            destination.write_bytes(zf.read(rel))
            restored_files[rel] = str(destination)
    return {
        "ok": True,
        "archive": str(archive_path),
        "output_dir": str(output_dir),
        "namespace": manifest.get("namespace"),
        "files": restored_files,
        "manifest_sha256": manifest.get("sha256"),
    }


def main() -> int:
    parser = ArgumentParser(description="Restore a MemGuardOS backup after strict manifest validation.")
    parser.add_argument("--archive", required=True, type=Path)
    parser.add_argument("--output-dir", required=True, type=Path)
    args = parser.parse_args()
    result = restore_memguard(args.archive, args.output_dir)
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
