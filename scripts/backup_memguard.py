from __future__ import annotations

from argparse import ArgumentParser
from datetime import datetime, timezone
from pathlib import Path
import hashlib
import json
import shutil
import sqlite3
import subprocess
import tempfile
import zipfile


def backup_memguard(store_path: Path, db_path: Path, output_path: Path, namespace: str = "default") -> dict[str, object]:
    store_path = Path(store_path)
    db_path = Path(db_path)
    output_path = Path(output_path)
    if not store_path.exists():
        raise FileNotFoundError("store file not found: %s" % store_path)
    if not db_path.exists():
        raise FileNotFoundError("sqlite db not found: %s" % db_path)
    output_path.parent.mkdir(parents=True, exist_ok=True)

    with tempfile.TemporaryDirectory(prefix="memguardos-backup-") as tmp:
        staging = Path(tmp)
        staged_store = staging / "memory.segment"
        staged_db = staging / "memory.sqlite3"
        shutil.copy2(store_path, staged_store)
        _sqlite_backup(db_path, staged_db)

        files = [
            _manifest_file(staged_store, "memory.segment"),
            _manifest_file(staged_db, "memory.sqlite3"),
        ]
        manifest = {
            "ok": True,
            "schema": "memguardos.backup.v1",
            "created_at": datetime.now(timezone.utc).isoformat(),
            "namespace": namespace,
            "source": {
                "store_path": str(store_path),
                "db_path": str(db_path),
                "git_head": _git_head(),
            },
            "file_count": len(files),
            "files": files,
        }
        manifest["sha256"] = _stable_manifest_hash(manifest)
        (staging / "manifest.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")

        with zipfile.ZipFile(output_path, "w", compression=zipfile.ZIP_DEFLATED) as zf:
            for item in ["manifest.json", "memory.segment", "memory.sqlite3"]:
                zf.write(staging / item, item)
    return manifest


def _sqlite_backup(source: Path, destination: Path) -> None:
    source_conn = sqlite3.connect(str(source))
    try:
        dest_conn = sqlite3.connect(str(destination))
        try:
            source_conn.backup(dest_conn)
        finally:
            dest_conn.close()
    finally:
        source_conn.close()


def _manifest_file(path: Path, arcname: str) -> dict[str, object]:
    data = path.read_bytes()
    return {
        "path": arcname,
        "bytes": len(data),
        "sha256": hashlib.sha256(data).hexdigest(),
    }


def _stable_manifest_hash(manifest: dict[str, object]) -> str:
    without_hash = {key: value for key, value in manifest.items() if key != "sha256"}
    raw = json.dumps(without_hash, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return hashlib.sha256(raw).hexdigest()


def _git_head() -> str:
    try:
        return subprocess.check_output(["git", "rev-parse", "HEAD"], text=True, stderr=subprocess.DEVNULL).strip()
    except Exception:
        return "unknown"


def main() -> int:
    parser = ArgumentParser(description="Create a verifiable MemGuardOS backup archive.")
    parser.add_argument("--store-path", required=True, type=Path)
    parser.add_argument("--db-path", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument("--namespace", default="default")
    args = parser.parse_args()
    manifest = backup_memguard(args.store_path, args.db_path, args.output, namespace=args.namespace)
    print(json.dumps({"ok": True, "archive": str(args.output), "manifest": manifest}, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
