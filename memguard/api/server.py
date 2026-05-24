from __future__ import annotations

from dataclasses import asdict, is_dataclass
import hmac
import json
import os
import socket
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional

from memguard.service import MemGuardService


_MAX_SOCKET_REQUEST_BYTES = 1024 * 1024


class RequestTooLarge(ValueError):
    pass


def handle_request(service: MemGuardService, request: Dict[str, object], auth_token: Optional[str] = None) -> Dict[str, object]:
    if auth_token and not hmac.compare_digest(str(request.get("token", "")), auth_token):
        return {"ok": False, "error": "unauthorized"}
    try:
        return _handle_authorized_request(service, request)
    except (TypeError, ValueError) as exc:
        return {"ok": False, "error": "invalid_request", "detail": str(exc)}
    except Exception:
        return {"ok": False, "error": "internal_error", "detail": "unexpected failure while processing command"}


def _handle_authorized_request(service: MemGuardService, request: Dict[str, object]) -> Dict[str, object]:
    command = str(request.get("command", ""))
    if command == "search":
        query = str(request.get("query", ""))
        limit = _int_param(request, "limit", 5, minimum=1, maximum=50)
        return {"ok": True, "result": service.search(query, limit=limit)}
    if command == "batch_search":
        queries = _string_list_param(request, "queries", max_items=100, max_chars=512)
        limit = _int_param(request, "limit", 5, minimum=1, maximum=50)
        workers = _int_param(request, "workers", 4, minimum=1, maximum=16)
        return {"ok": True, "result": service.batch_search(queries, limit=limit, workers=workers)}
    if command == "build_context":
        query = str(request.get("query", ""))
        limit = _int_param(request, "limit", 5, minimum=1, maximum=50)
        max_chars = _int_param(request, "max_chars", 2000, minimum=128, maximum=20000)
        token_budget = request.get("token_budget")
        token_budget = _int_param(request, "token_budget", 0, minimum=64, maximum=32000) if token_budget else None
        return {"ok": True, "result": service.build_context(query, limit=limit, max_chars=max_chars, token_budget=token_budget)}
    if command == "explain_extraction":
        text = str(request.get("text", ""))
        source_kind = str(request.get("source_kind", "manual"))
        return {"ok": True, "result": _jsonable(service.explain_extraction(text, source_kind=source_kind))}
    if command == "namespace_status":
        return {"ok": True, "result": service.namespace_status()}
    if command == "memory_policy":
        eviction_limit = _int_param(request, "eviction_limit", 10, minimum=1, maximum=100)
        return {"ok": True, "result": service.memory_policy_status(eviction_limit=eviction_limit)}
    if command == "list":
        return {
            "ok": True,
            "result": _jsonable(service.list_memories(
                include_deleted=bool(request.get("include_deleted", False)),
                tier=str(request["tier"]) if request.get("tier") else None,
            )),
        }
    if command == "update":
        memory_id = str(request.get("memory_id", ""))
        text = str(request.get("text", ""))
        reason = str(request.get("reason", "api update"))
        return {"ok": True, "result": _jsonable(service.update_memory(memory_id, text, reason=reason))}
    if command == "versions":
        return {"ok": True, "result": _jsonable(service.versions(str(request.get("memory_id", ""))))}
    if command == "promote":
        memory_id = str(request.get("memory_id", ""))
        tier = str(request.get("tier", ""))
        return {"ok": True, "result": _jsonable(service.promote_memory(memory_id, tier))}
    if command == "evict_tier":
        tier = str(request.get("tier", ""))
        confirm = bool(request.get("confirm", False))
        return {"ok": True, "result": _jsonable(service.evict_tier(tier, confirm=confirm))}
    if command == "forget":
        forget_request = str(request.get("request", request.get("text", "")))
        confirm = bool(request.get("confirm", False))
        compact = bool(request.get("compact", False))
        return {"ok": True, "result": _jsonable(service.forget(forget_request, confirm=confirm, compact=compact))}
    if command == "verify_forget":
        memory_id = str(request.get("memory_id", ""))
        probe = str(request.get("probe", ""))
        return {"ok": True, "result": _jsonable(service.verify_forget_probe(memory_id, probe))}
    if command == "audit":
        return {
            "ok": True,
            "result": _jsonable(service.audit(
                limit=_int_param(request, "limit", 20, minimum=1, maximum=500),
                event_type=str(request["event_type"]) if request.get("event_type") else None,
            )),
        }
    if command == "verify_audit":
        result = _jsonable(service.verify_audit())
        return {"ok": bool(result.get("ok")) if isinstance(result, dict) else False, "result": result}
    if command == "retrieval_status":
        return {"ok": True, "result": _jsonable(service.retrieval_status())}
    if command == "ingest":
        text = str(request.get("text", ""))
        source_kind = str(request.get("source_kind", "api"))
        source_uri = str(request.get("source_uri", "local_socket"))
        tier = request.get("tier")
        result = service.ingest_text(text, source_kind=source_kind, source_uri=source_uri, tier=str(tier) if tier else None)
        if result.get("stored"):
            record = result["record"]
            return {
                "ok": True,
                "result": {
                    "stored": True,
                    "memory_id": record.memory_id,
                    "type": record.type,
                    "subtype": record.subtype,
                    "scope": record.scope,
                    "namespace": service.namespace,
                    "tier": record.tier,
                    "extraction": result.get("extraction"),
                },
            }
        sensitivity = result.get("sensitivity")
        return {
            "ok": True,
            "result": {
                "stored": False,
                "reason": result.get("reason"),
                "sensitivity": sensitivity.__dict__ if sensitivity else None,
            },
        }
    if command == "health":
        return {
            "ok": True,
            "result": {
                "status": "ready",
                "namespace": service.namespace,
                "store_path": str(service.store.path),
                "db_path": str(service.meta.path),
            },
        }
    return {"ok": False, "error": "unknown_command", "command": command}


def _int_param(request: Dict[str, object], name: str, default: int, *, minimum: int, maximum: int) -> int:
    raw = request.get(name, default)
    try:
        value = int(raw)
    except (TypeError, ValueError) as exc:
        raise ValueError("%s must be an integer" % name) from exc
    if value < minimum or value > maximum:
        raise ValueError("%s must be between %s and %s" % (name, minimum, maximum))
    return value


def _string_list_param(request: Dict[str, object], name: str, *, max_items: int, max_chars: int) -> List[str]:
    raw = request.get(name, [])
    if not isinstance(raw, list):
        raise ValueError("%s must be an array" % name)
    if len(raw) > max_items:
        raise ValueError("%s must contain at most %s items" % (name, max_items))
    values = []
    for index, item in enumerate(raw):
        value = str(item).strip()
        if len(value) > max_chars:
            raise ValueError("%s[%s] must contain at most %s characters" % (name, index, max_chars))
        if value:
            values.append(value)
    return values


def serve_unix_socket(
    path: Path,
    service: MemGuardService,
    should_stop: Callable[[], bool],
    on_ready: Callable[[str], None] | None = None,
    mode: int = 0o660,
    auth_token: Optional[str] = None,
) -> None:
    if not hasattr(socket, "AF_UNIX"):
        raise RuntimeError("Unix domain sockets are not supported on this Python platform")
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.exists():
        path.unlink()
    with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as server:
        server.bind(str(path))
        os.chmod(path, mode)
        server.listen(8)
        server.settimeout(1.0)
        if on_ready:
            on_ready(str(path))
        while not should_stop():
            try:
                conn, _addr = server.accept()
            except socket.timeout:
                continue
            with conn:
                try:
                    payload = _read_request(conn)
                    response = _dispatch_payload(service, payload, auth_token=auth_token)
                except RequestTooLarge:
                    response = {"ok": False, "error": "payload_too_large", "detail": "request body exceeds 1 MiB"}
                conn.sendall((json.dumps(response, ensure_ascii=False) + "\n").encode("utf-8"))
    if path.exists():
        path.unlink()


def _read_request(conn: socket.socket) -> bytes:
    chunks = []
    total = 0
    while True:
        chunk = conn.recv(65536)
        if not chunk:
            break
        chunks.append(chunk)
        total += len(chunk)
        if total > _MAX_SOCKET_REQUEST_BYTES:
            raise RequestTooLarge("request body exceeds 1 MiB")
        if b"\n" in chunk:
            break
    return b"".join(chunks).strip()


def _dispatch_payload(service: MemGuardService, payload: bytes, auth_token: Optional[str] = None) -> Dict[str, object]:
    try:
        request = json.loads(payload.decode("utf-8"))
        if not isinstance(request, dict):
            return {"ok": False, "error": "request_must_be_object"}
        return handle_request(service, request, auth_token=auth_token)
    except Exception as exc:
        return {"ok": False, "error": "bad_request", "detail": str(exc)}


def _jsonable(value: Any) -> Any:
    if hasattr(value, "to_dict") and callable(value.to_dict):
        return _jsonable(value.to_dict())
    if is_dataclass(value):
        return _jsonable(asdict(value))
    if isinstance(value, dict):
        return {str(key): _jsonable(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_jsonable(item) for item in value]
    if isinstance(value, Path):
        return str(value)
    return value
