from __future__ import annotations

import json
import os
import socket
from pathlib import Path
from typing import Callable, Dict, Optional

from memguard.service import MemGuardService


def handle_request(service: MemGuardService, request: Dict[str, object], auth_token: Optional[str] = None) -> Dict[str, object]:
    if auth_token and request.get("token") != auth_token:
        return {"ok": False, "error": "unauthorized"}
    command = str(request.get("command", ""))
    if command == "search":
        query = str(request.get("query", ""))
        limit = int(request.get("limit", 5))
        return {"ok": True, "result": service.search(query, limit=limit)}
    if command == "build_context":
        query = str(request.get("query", ""))
        limit = int(request.get("limit", 5))
        max_chars = int(request.get("max_chars", 2000))
        token_budget = request.get("token_budget")
        token_budget = int(token_budget) if token_budget else None
        return {"ok": True, "result": service.build_context(query, limit=limit, max_chars=max_chars, token_budget=token_budget)}
    if command == "namespace_status":
        return {"ok": True, "result": service.namespace_status()}
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
                payload = _read_request(conn)
                response = _dispatch_payload(service, payload, auth_token=auth_token)
                conn.sendall((json.dumps(response, ensure_ascii=False) + "\n").encode("utf-8"))
    if path.exists():
        path.unlink()


def _read_request(conn: socket.socket) -> bytes:
    chunks = []
    while True:
        chunk = conn.recv(65536)
        if not chunk:
            break
        chunks.append(chunk)
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
