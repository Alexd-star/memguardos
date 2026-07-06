from __future__ import annotations

from pathlib import Path
from typing import Iterable
import hashlib
import hmac
import json
import os
import secrets
import time
import uuid

from memguard.config import namespace_home
from memguard.desktop.peony_scan import scan_peony_file
from memguard.service import MemGuardService


SCHEMA = "memguardos-desktop-scan-manifest-v1"


def create_scan_manifest(
    paths: Iterable[str | Path],
    service: MemGuardService,
    *,
    store_dir: Path | None = None,
    now: float | None = None,
    ttl_seconds: int = 300,
    max_files: int = 100,
    max_file_bytes: int = 1024 * 1024,
    max_total_bytes: int = 10 * 1024 * 1024,
) -> dict[str, object]:
    created_at = time.time() if now is None else float(now)
    normalized = [_canonical_path(path) for path in paths]
    if not normalized:
        return _error("no_files", "没有可扫描的文件。")
    if len(normalized) > max_files:
        return _error("too_many_files", f"一次最多扫描 {max_files} 个文件。")
    total_bytes = sum(_safe_size(path) for path in normalized)
    if total_bytes > max_total_bytes:
        return _error("total_size_limit_exceeded", f"选中文件总大小超过 {max_total_bytes} 字节。")

    items = []
    for path in normalized:
        report = scan_peony_file(path, service, ingest=False, max_bytes=max_file_bytes)
        report["path"] = str(path)
        report["selectable"] = report.get("recommended_action") == "import_text"
        report["fingerprint"] = _fingerprint(path)
        items.append(report)

    root = store_dir or (namespace_home(service.namespace) / "desktop-scans")
    root.mkdir(parents=True, exist_ok=True)
    _set_private_mode(root, directory=True)
    document: dict[str, object] = {
        "schema": SCHEMA,
        "ok": True,
        "manifest_id": str(uuid.uuid4()),
        "nonce": secrets.token_urlsafe(24),
        "namespace": service.namespace,
        "created_at": created_at,
        "expires_at": created_at + max(1, int(ttl_seconds)),
        "used": False,
        "limits": {
            "max_files": max_files,
            "max_file_bytes": max_file_bytes,
            "max_total_bytes": max_total_bytes,
        },
        "total_bytes": total_bytes,
        "items": items,
    }
    document["signature"] = _sign(document, _load_or_create_key(root))
    manifest_path = root / f"{document['manifest_id']}.json"
    _atomic_private_json(manifest_path, document)
    result = dict(document)
    result["manifest_path"] = str(manifest_path)
    return result


def apply_scan_manifest(
    manifest_path: str | Path,
    selected_paths: Iterable[str | Path],
    service: MemGuardService,
    *,
    now: float | None = None,
    notify: bool = False,
) -> dict[str, object]:
    path = Path(manifest_path).expanduser().resolve()
    try:
        document = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError, TypeError):
        return _error("manifest_unreadable", "扫描清单无法读取或格式无效。")
    if document.get("schema") != SCHEMA:
        return _error("manifest_schema_invalid", "扫描清单版本不受支持。")
    key = _load_or_create_key(path.parent)
    if not hmac.compare_digest(str(document.get("signature", "")), _sign(document, key)):
        return _error("manifest_signature_invalid", "扫描清单签名无效，操作已取消。")
    if bool(document.get("used")):
        return _error("manifest_already_used", "扫描清单已经执行，不能重复使用。")
    current_time = time.time() if now is None else float(now)
    if current_time > float(document.get("expires_at", 0)):
        return _error("manifest_expired", "扫描清单已过期，请重新扫描。")
    if document.get("namespace") != service.namespace:
        return _error("namespace_mismatch", "扫描清单不属于当前记忆空间。")

    requested = [_canonical_path(item) for item in selected_paths]
    by_path = {str(item.get("path")): item for item in document.get("items", []) if isinstance(item, dict)}
    invalid = [str(item) for item in requested if str(item) not in by_path or not bool(by_path[str(item)].get("selectable"))]
    if invalid:
        return _error("selection_not_allowed", "包含未扫描或不可导入的文件。", paths=invalid)

    changed = [str(item) for item in requested if _fingerprint(item) != by_path[str(item)].get("fingerprint")]
    if changed:
        return _error("file_changed_after_scan", "文件在确认前发生变化，请重新扫描。", paths=changed)

    results = []
    stored = blocked = failed = 0
    for selected in requested:
        try:
            report = scan_peony_file(selected, service, ingest=True, notify=False)
            results.append(report)
            if bool(report.get("ingest", {}).get("stored")):
                stored += 1
            elif report.get("recommended_action") == "block_sensitive":
                blocked += 1
            else:
                failed += 1
        except Exception as exc:  # Per-file isolation is intentional for desktop batches.
            failed += 1
            results.append({"path": str(selected), "ok": False, "error_code": "scan_apply_failed", "message": str(exc)})

    document["used"] = True
    document["used_at"] = current_time
    document["signature"] = _sign(document, key)
    _atomic_private_json(path, document)
    result = {
        "schema": "memguardos-desktop-scan-apply-v1",
        "ok": failed == 0,
        "manifest_id": document["manifest_id"],
        "summary": {
            "selected": len(requested),
            "stored": stored,
            "blocked": blocked,
            "failed": failed,
            "skipped": len(document.get("items", [])) - len(requested),
        },
        "results": results,
        "audit_chain": service.verify_audit(),
    }
    if notify:
        _notify_batch(result)
    return result


def forget_imported_memories(
    memory_ids: Iterable[str],
    service: MemGuardService,
    *,
    reason: str = "撤销 Peony 文件导入",
    compact: bool = True,
    notify: bool = False,
) -> dict[str, object]:
    result = service.forget_memory_ids(list(memory_ids), reason=reason, compact=compact)
    result["schema"] = "memguardos-desktop-forget-import-v1"
    result["audit_chain"] = service.verify_audit()
    if notify:
        _notify_forget(result)
    return result


def _canonical_path(path: str | Path) -> Path:
    return Path(path).expanduser().resolve()


def _safe_size(path: Path) -> int:
    try:
        return path.stat().st_size if path.is_file() else 0
    except OSError:
        return 0


def _fingerprint(path: Path) -> dict[str, object]:
    try:
        stat = path.stat()
    except OSError:
        return {"exists": False}
    return {
        "exists": True,
        "device": stat.st_dev,
        "inode": stat.st_ino,
        "size": stat.st_size,
        "mtime_ns": stat.st_mtime_ns,
        "mode": stat.st_mode,
    }


def _load_or_create_key(root: Path) -> bytes:
    root.mkdir(parents=True, exist_ok=True)
    key_path = root / ".manifest-key"
    try:
        key = key_path.read_bytes()
    except FileNotFoundError:
        key = secrets.token_bytes(32)
        flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL
        try:
            descriptor = os.open(key_path, flags, 0o600)
        except FileExistsError:
            return key_path.read_bytes()
        with os.fdopen(descriptor, "wb") as handle:
            handle.write(key)
    if len(key) < 32:
        raise ValueError("desktop scan manifest key is invalid")
    _set_private_mode(key_path)
    return key


def _sign(document: dict[str, object], key: bytes) -> str:
    unsigned = {name: value for name, value in document.items() if name not in {"signature", "manifest_path"}}
    payload = json.dumps(unsigned, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return hmac.new(key, payload, hashlib.sha256).hexdigest()


def _atomic_private_json(path: Path, document: dict[str, object]) -> None:
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(json.dumps(document, ensure_ascii=False, indent=2), encoding="utf-8")
    _set_private_mode(temporary)
    temporary.replace(path)
    _set_private_mode(path)


def _set_private_mode(path: Path, *, directory: bool = False) -> None:
    try:
        path.chmod(0o700 if directory else 0o600)
    except OSError:
        pass


def _error(code: str, message: str, **extra: object) -> dict[str, object]:
    return {"schema": SCHEMA, "ok": False, "error_code": code, "message": message, **extra}


def _notify_batch(result: dict[str, object]) -> None:
    from memguard.desktop.peony_scan import notify_desktop

    summary = result["summary"]
    notify_desktop(
        "MemGuardOS 文件检查完成",
        f"已导入 {summary['stored']}，阻断 {summary['blocked']}，失败 {summary['failed']}。",
    )


def _notify_forget(result: dict[str, object]) -> None:
    from memguard.desktop.peony_scan import notify_desktop

    summary = result["summary"]
    if result.get("ok"):
        notify_desktop("MemGuardOS 已撤销导入", f"已遗忘本次导入的 {summary['forgotten']} 条记忆。")
    else:
        notify_desktop("MemGuardOS 撤销导入需要确认", f"已处理 {summary['forgotten']} 条，仍有 {summary['missing']} 条需要检查。")
