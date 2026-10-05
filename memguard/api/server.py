from __future__ import annotations

import hmac
import hashlib
import json
import os
import socket
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional

from memguard.json_support import jsonable
from memguard.service import MemGuardService


_MAX_SOCKET_REQUEST_BYTES = 1024 * 1024


class RequestTooLarge(ValueError):
    pass


def handle_request(service: MemGuardService, request: Dict[str, object], auth_token: Optional[str] = None) -> Dict[str, object]:
    if auth_token and not hmac.compare_digest(str(request.get("token", "")), auth_token):
        return {"ok": False, "error": "unauthorized"}
    command = str(request.get("command", ""))
    try:
        return _handle_authorized_request(service, request)
    except ValueError as exc:
        return {"ok": False, "error": "invalid_request", "detail": str(exc)}
    except Exception as exc:
        error_id = _internal_error_id(command, exc)
        _audit_internal_error(service, command, exc, error_id)
        return {
            "ok": False,
            "error": "internal_error",
            "command": command,
            "error_id": error_id,
            "detail": "unexpected failure while processing command",
        }


def _handle_authorized_request(service: MemGuardService, request: Dict[str, object]) -> Dict[str, object]:
    command = str(request.get("command", ""))
    if command == "search":
        query = _string_param(request, "query", "", max_chars=2048)
        limit = _int_param(request, "limit", 5, minimum=1, maximum=50)
        session_id = _string_param(request, "session_id", "", max_chars=256)
        return {"ok": True, "result": service.search(query, limit=limit, session_id=session_id, **_project_scope_param(request))}
    if command == "batch_search":
        queries = _string_list_param(request, "queries", max_items=100, max_chars=512)
        limit = _int_param(request, "limit", 5, minimum=1, maximum=50)
        workers = _int_param(request, "workers", 4, minimum=1, maximum=16)
        session_id = _string_param(request, "session_id", "", max_chars=256)
        scope = _project_scope_param(request)
        if session_id:
            scope["session_id"] = session_id
        return {"ok": True, "result": service.batch_search(queries, limit=limit, workers=workers, **scope)}
    if command == "build_context":
        query = _string_param(request, "query", "", max_chars=2048)
        limit = _int_param(request, "limit", 5, minimum=1, maximum=50)
        max_chars = _int_param(request, "max_chars", 2000, minimum=128, maximum=20000)
        token_budget = request.get("token_budget")
        token_budget = _int_param(request, "token_budget", 0, minimum=64, maximum=32000) if token_budget else None
        session_id = _string_param(request, "session_id", "", max_chars=256)
        return {"ok": True, "result": service.build_context(query, limit=limit, max_chars=max_chars, token_budget=token_budget, session_id=session_id, **_project_scope_param(request))}
    if command == "hierarchical_graph_search":
        entity = _string_param(request, "entity", request.get("query", ""), max_chars=1024)
        limit = _int_param(request, "limit", 10, minimum=1, maximum=50)
        session_id = _string_param(request, "session_id", "", max_chars=256)
        return {"ok": True, "result": service.hierarchical_graph_search(entity, limit=limit, session_id=session_id, **_project_scope_param(request))}
    if command == "graph_search":
        entity = _string_param(request, "entity", request.get("query", ""), max_chars=1024)
        limit = _int_param(request, "limit", 10, minimum=1, maximum=50)
        session_id = _string_param(request, "session_id", "", max_chars=256)
        return {"ok": True, "result": service.graph_search(entity, limit=limit, session_id=session_id, **_project_scope_param(request))}
    if command == "related":
        memory_id = _string_param(request, "memory_id", "", max_chars=128)
        limit = _int_param(request, "limit", 10, minimum=1, maximum=50)
        session_id = _string_param(request, "session_id", "", max_chars=256)
        return {"ok": True, "result": service.related(memory_id, limit=limit, session_id=session_id, **_project_scope_param(request))}
    if command == "explain_extraction":
        text = _string_param(request, "text", "", max_chars=200000)
        source_kind = _string_param(request, "source_kind", "manual", max_chars=64)
        return {"ok": True, "result": _jsonable(service.explain_extraction(text, source_kind=source_kind))}
    if command == "namespace_status":
        return {"ok": True, "result": service.namespace_status()}
    if command == "feedback_home":
        limit = _int_param(request, "limit", 8, minimum=1, maximum=50)
        return {"ok": True, "result": _jsonable(service.feedback_home_snapshot(limit=limit))}
    if command == "notifications":
        limit = _int_param(request, "limit", 30, minimum=1, maximum=200)
        return {"ok": True, "result": _jsonable(service.recent_notifications(limit=limit))}
    if command == "memory_value_comparison":
        task = _string_param(request, "task", "", max_chars=4096)
        agent_name = _string_param(request, "agent_name", "openhands", max_chars=128)
        session_id = _string_param(request, "session_id", "", max_chars=256)
        if not task:
            return {"ok": False, "error": "task_required"}
        return {"ok": True, "result": _jsonable(service.memory_value_comparison(task, agent_name=agent_name, session_id=session_id))}
    if command == "memory_detail":
        memory_id = _string_param(request, "memory_id", "", max_chars=128)
        task = _string_param(request, "task", "验证这条记忆的适用范围", max_chars=4096)
        agent_name = _string_param(request, "agent_name", "openhands", max_chars=128)
        session_id = _string_param(request, "session_id", "detail-preview", max_chars=256)
        result = service.memory_detail(
            memory_id,
            task=task,
            agent_name=agent_name,
            session_id=session_id,
            light=bool(request.get("light", False)),
        )
        found = bool(result.get("found"))
        response = {"ok": found, "result": _jsonable(result)}
        if not found:
            response.update(
                {
                    "error": "memory_not_found",
                    "message": "这条记忆已不存在、已完成遗忘，或不属于当前记忆空间。",
                }
            )
        return response
    if command == "lifecycle_snapshot":
        memory_id = _string_param(request, "memory_id", "", max_chars=128)
        include_events = bool(request.get("include_events", True))
        result = service.lifecycle_snapshot(memory_id, include_events=include_events)
        return {
            "ok": bool(result.get("found")),
            "error": "memory_not_found" if not result.get("found") else "",
            "result": _jsonable(result),
        }
    if command == "lifecycle_overview":
        limit = _int_param(request, "limit", 500, minimum=1, maximum=2000)
        return {"ok": True, "result": _jsonable(service.lifecycle_overview(limit=limit))}
    if command == "fact_conflicts":
        status = _string_param(request, "status", "pending", max_chars=16)
        limit = _int_param(request, "limit", 100, minimum=1, maximum=1000)
        return {"ok": True, "result": _jsonable(service.fact_conflicts(status=status, limit=limit))}
    if command == "resolve_fact_conflict":
        conflict_id = _string_param(request, "conflict_id", "", max_chars=128)
        decision = _string_param(request, "decision", "", max_chars=32)
        memory_id = _string_param(request, "memory_id", "", max_chars=128)
        expected_version_id = _string_param(request, "expected_version_id", "", max_chars=128)
        if not conflict_id or decision not in {"accept_incoming", "keep_existing"}:
            return {"ok": False, "error": "invalid_fact_conflict_decision"}
        result = service.resolve_fact_conflict(
            conflict_id,
            decision=decision,
            memory_id=memory_id,
            expected_version_id=expected_version_id,
        )
        return {"ok": bool(result.get("resolved")), "result": _jsonable(result)}
    if command == "preference_effect_proof":
        memory_id = _string_param(request, "memory_id", "", max_chars=128)
        task = _string_param(request, "task", "验证当前偏好是否生效", max_chars=4096)
        stage = _string_param(request, "stage", "tool_selection", max_chars=64)
        agent_name = _string_param(request, "agent_name", "openhands", max_chars=128)
        session_id = _string_param(request, "session_id", "proof-preview", max_chars=256)
        result = service.preference_effect_proof(
            memory_id,
            task=task,
            stage=stage,
            agent_name=agent_name,
            session_id=session_id,
        )
        return {"ok": bool(result.get("found")), "result": _jsonable(result)}
    if command == "agent_replay":
        task = _string_param(request, "task", "查找记忆服务实现位置", max_chars=4096)
        mode = _string_param(request, "mode", "replay", max_chars=32)
        session_id = _string_param(request, "session_id", "", max_chars=256)
        return {
            "ok": True,
            "result": _jsonable(service.run_feedback_replay(task, mode=mode, session_id=session_id)),
        }
    if command == "memory_policy":
        eviction_limit = _int_param(request, "eviction_limit", 10, minimum=1, maximum=100)
        return {"ok": True, "result": service.memory_policy_status(eviction_limit=eviction_limit)}
    if command == "governance_list":
        limit = _int_param(request, "limit", 100, minimum=1, maximum=1000)
        return {"ok": True, "result": _jsonable(service.list_quarantined_memories(limit=limit))}
    if command == "governance_review":
        memory_id = _string_param(request, "memory_id", "", max_chars=128)
        decision = _string_param(request, "decision", "", max_chars=16)
        reviewer = _string_param(request, "reviewer", "", max_chars=256)
        reason = _string_param(request, "reason", "", max_chars=2048)
        result = service.review_quarantined_memory(memory_id, decision, reviewer, reason)
        return {"ok": bool(result.get("reviewed")), "result": _jsonable(result)}
    if command == "temporal_status":
        limit = _int_param(request, "limit", 20, minimum=1, maximum=100)
        return {"ok": True, "result": service.temporal_status(limit=limit)}
    if command == "list":
        return {
            "ok": True,
            "result": _jsonable(service.list_memories(
                include_deleted=bool(request.get("include_deleted", False)),
                tier=str(request["tier"]) if request.get("tier") else None,
            )),
        }
    if command == "update":
        memory_id = _string_param(request, "memory_id", "", max_chars=128)
        text = _string_param(request, "text", "", max_chars=200000)
        reason = _string_param(request, "reason", "api update", max_chars=512)
        return {"ok": True, "result": _jsonable(service.update_memory(memory_id, text, reason=reason))}
    if command == "versions":
        return {"ok": True, "result": _jsonable(service.versions(_string_param(request, "memory_id", "", max_chars=128)))}
    if command == "promote":
        memory_id = _string_param(request, "memory_id", "", max_chars=128)
        tier = _string_param(request, "tier", "", max_chars=32)
        return {"ok": True, "result": _jsonable(service.promote_memory(
            memory_id, tier, session_id=_string_param(request, "session_id", "", max_chars=256),
        ))}
    if command == "evict_tier":
        tier = _string_param(request, "tier", "", max_chars=32)
        confirm = bool(request.get("confirm", False))
        return {"ok": True, "result": _jsonable(service.evict_tier(tier, confirm=confirm))}
    if command == "evict_session":
        session_id = _string_param(request, "session_id", "", max_chars=256)
        confirm = bool(request.get("confirm", False))
        expected_versions = request.get("expected_versions")
        if expected_versions is not None and (
            not isinstance(expected_versions, dict)
            or len(expected_versions) > 500
            or any(not isinstance(key, str) or not isinstance(value, str) for key, value in expected_versions.items())
        ):
            raise ValueError("expected_versions must be a bounded version map")
        result = service.evict_session(session_id, confirm=confirm, expected_versions=expected_versions)
        ok = bool(result.get("evicted")) if confirm else True
        return {
            "ok": ok, "error": str(result.get("reason") or "session_evict_failed") if not ok else "",
            "result": _jsonable(result),
        }
    if command == "forget":
        forget_request = _string_param(request, "request", request.get("text", ""), max_chars=2048)
        confirm = bool(request.get("confirm", False))
        compact = bool(request.get("compact", confirm))
        result = service.forget(forget_request, confirm=confirm, compact=compact)
        return {"ok": bool(result.get("ok")) if confirm else True, "result": _jsonable(result)}
    if command == "forget_memory_ids":
        raw_ids = request.get("memory_ids")
        if not isinstance(raw_ids, list):
            raise ValueError("memory_ids must be an array")
        memory_ids = [str(item).strip() for item in raw_ids if str(item).strip()]
        if len(memory_ids) > 100:
            raise ValueError("memory_ids must contain at most 100 items")
        reason = _string_param(request, "reason", "user forget", max_chars=2048)
        raw_versions = request.get("expected_versions")
        if raw_versions is not None and not isinstance(raw_versions, dict):
            raise ValueError("expected_versions must be an object")
        result = service.forget_memory_ids(
            memory_ids, reason, compact=bool(request.get("compact", True)),
            expected_versions={str(key): str(value) for key, value in raw_versions.items()}
            if raw_versions is not None else None,
        )
        result["verification_report"] = service.forget_verification_report(result, request=reason, dry_run=False)
        return {"ok": bool(result.get("ok")), "result": _jsonable(result)}
    if command == "verify_forget":
        memory_id = _string_param(request, "memory_id", "", max_chars=128)
        probe = _string_param(request, "probe", "", max_chars=4096)
        return {"ok": True, "result": _jsonable(service.verify_forget_probe(memory_id, probe))}
    if command == "audit":
        return {
            "ok": True,
            "result": _jsonable(service.audit(
                limit=_int_param(request, "limit", 20, minimum=1, maximum=500),
                event_type=_string_param(request, "event_type", "", max_chars=64) if request.get("event_type") else None,
            )),
        }
    if command == "verify_audit":
        result = _jsonable(service.verify_audit())
        return {"ok": bool(result.get("ok")) if isinstance(result, dict) else False, "result": result}
    if command == "retrieval_status":
        return {"ok": True, "result": _jsonable(service.retrieval_status())}
    if command == "ingest_interaction":
        event = request.get("event")
        if not isinstance(event, dict):
            raise ValueError("event must be an object")
        return {"ok": True, "result": _jsonable(service.ingest_interaction(event))}
    if command == "ingest_interactions":
        events = request.get("events")
        if not isinstance(events, list):
            raise ValueError("events must be an array")
        if len(events) > 500:
            raise ValueError("events must contain at most 500 items")
        if any(not isinstance(event, dict) for event in events):
            raise ValueError("every event must be an object")
        return {"ok": True, "result": _jsonable(service.ingest_interactions(events))}
    if command == "interaction_status":
        return {"ok": True, "result": _jsonable(service.interaction_status())}
    if command == "forget_agent_interactions":
        raw_filters = request.get("filters")
        if not isinstance(raw_filters, dict):
            raise ValueError("filters must be an object")
        filters = {}
        for key, max_chars in {
            "agent_name": 128,
            "session_id": 256,
            "turn_id": 256,
            "preference_topic": 128,
            "start_time": 64,
            "end_time": 64,
        }.items():
            if raw_filters.get(key):
                value = str(raw_filters[key]).strip()
                if len(value) > max_chars:
                    raise ValueError("%s exceeds maximum length" % key)
                filters[key] = value
        return {
            "ok": True,
            "result": _jsonable(
                service.forget_agent_interactions(
                    filters,
                    confirm=bool(request.get("confirm", False)),
                    compact=bool(request.get("compact", True)),
                    external_persistence=bool(request.get("external_persistence", True)),
                )
            ),
        }
    if command == "build_agent_context":
        task = _string_param(request, "task", "", max_chars=4096)
        stage = _string_param(request, "stage", "", max_chars=64)
        agent_name = _string_param(request, "agent_name", "", max_chars=128)
        session_id = _string_param(request, "session_id", "", max_chars=256)
        project_root = _string_param(request, "project_root", "", max_chars=2048)
        task_type = _string_param(request, "task_type", "", max_chars=128)
        limit = _int_param(request, "limit", 8, minimum=1, maximum=50)
        max_chars = _int_param(request, "max_chars", 4000, minimum=128, maximum=20000)
        token_budget = _int_param(request, "token_budget", 800, minimum=64, maximum=32000)
        return {
            "ok": True,
            "result": _jsonable(
                service.build_agent_context(
                    task,
                    stage=stage,
                    agent_name=agent_name,
                    session_id=session_id,
                    project_root=project_root,
                    task_type=task_type,
                    limit=limit,
                    max_chars=max_chars,
                    token_budget=token_budget,
                )
            ),
        }
    if command == "preference_evidence":
        memory_id = _string_param(request, "memory_id", "", max_chars=128)
        result = _jsonable(service.preference_evidence(memory_id))
        return {"ok": bool(result.get("found")) if isinstance(result, dict) else False, "result": result}
    if command == "ingest":
        text = _string_param(request, "text", "", max_chars=200000)
        source_kind = _string_param(request, "source_kind", "api", max_chars=64)
        source_uri = _string_param(request, "source_uri", "local_socket", max_chars=1024)
        tier = request.get("tier")
        selected_tier = _string_param(request, "tier", "", max_chars=32) if tier else None
        result = (
            service.ingest_natural_memory(
                text,
                source_kind=source_kind,
                source_uri=source_uri,
                session_id=_string_param(request, "session_id", "", max_chars=256),
                tier=selected_tier,
                **_project_scope_param(request),
            )
            if bool(request.get("natural", False))
            else service.ingest_text(
                text,
                source_kind=source_kind,
                source_uri=source_uri,
                tier=selected_tier,
                retention_session_id=_string_param(request, "session_id", "", max_chars=256),
                **_project_scope_param(request),
            )
        )
        if result.get("stored"):
            record = result["record"]
            record_value = record if isinstance(record, dict) else record.to_dict()
            return {
                "ok": True,
                "result": {
                    "stored": True,
                    "updated": bool(result.get("updated")),
                    "partial_conflict": bool(result.get("partial_conflict")),
                    "candidate_memory_ids": result.get("candidate_memory_ids") or [],
                    "conflict_id": result.get("conflict_id", ""),
                    "conflict": result.get("conflict"),
                    "memory_id": record_value.get("memory_id", ""),
                    "memory_ids": result.get("memory_ids") or [record_value.get("memory_id", "")],
                    "type": record_value.get("type", ""),
                    "subtype": record_value.get("subtype", ""),
                    "scope": record_value.get("scope", ""),
                    "namespace": service.namespace,
                    "tier": record_value.get("tier", ""),
                    "extraction": result.get("extraction"),
                    "feedback": result.get("feedback"),
                    "audit_event_id": result.get("audit_event_id"),
                },
            }
        sensitivity = result.get("sensitivity")
        sensitivity_value = (
            dict(sensitivity)
            if isinstance(sensitivity, dict)
            else (sensitivity.__dict__ if sensitivity else None)
        )
        return {
            "ok": True,
            "result": {
                "stored": False,
                "reason": result.get("reason"),
                "duplicate": bool(result.get("duplicate")),
                "memory_id": getattr(result.get("record"), "memory_id", ""),
                "partial_conflict": bool(result.get("partial_conflict")),
                "candidate_memory_ids": result.get("candidate_memory_ids") or [],
                "conflict_id": result.get("conflict_id", ""),
                "conflict": result.get("conflict"),
                "sensitivity": sensitivity_value,
                "feedback": result.get("feedback"),
                "sensitive_notice": result.get("sensitive_notice"),
                "audit_event_id": result.get("audit_event_id"),
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


def _project_scope_param(request: Dict[str, object]) -> Dict[str, str]:
    # An omitted scope preserves administrative cross-project queries;
    # an explicit empty scope selects only global memories.
    if "project_root" not in request:
        return {}
    value = request["project_root"]
    if not isinstance(value, str) or len(value) > 2048:
        raise ValueError("project_root must be a string of at most 2048 characters")
    return {"project_root": value}


def _int_param(request: Dict[str, object], name: str, default: int, *, minimum: int, maximum: int) -> int:
    raw = request.get(name, default)
    try:
        value = int(raw)
    except (TypeError, ValueError) as exc:
        raise ValueError("%s must be an integer" % name) from exc
    if value < minimum or value > maximum:
        raise ValueError("%s must be between %s and %s" % (name, minimum, maximum))
    return value


def _string_param(request: Dict[str, object], name: str, default: object, *, max_chars: int) -> str:
    value = str(request.get(name, default))
    if len(value) > max_chars:
        raise ValueError("%s must contain at most %s characters" % (name, max_chars))
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


def _internal_error_id(command: str, exc: Exception) -> str:
    material = "%s|%s" % (command, exc.__class__.__name__)
    return hashlib.sha256(material.encode("utf-8")).hexdigest()[:16]


def _audit_internal_error(service: MemGuardService, command: str, exc: Exception, error_id: str) -> None:
    audit = getattr(service, "_audit", None)
    if not callable(audit):
        return
    try:
        audit(
            "api_internal_error",
            None,
            {
                "command": command,
                "error_id": error_id,
                "error_type": exc.__class__.__name__,
            },
        )
    except Exception:
        return


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
    socket_service = MemGuardService(
        store_path=service.store.path,
        db_path=service.meta.path,
        namespace=service.namespace,
    )
    try:
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
                        response = _dispatch_payload(socket_service, payload, auth_token=auth_token)
                    except RequestTooLarge:
                        response = {"ok": False, "error": "payload_too_large", "detail": "request body exceeds 1 MiB"}
                    conn.sendall((json.dumps(response, ensure_ascii=False) + "\n").encode("utf-8"))
    finally:
        socket_service.close()
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
    return jsonable(value)
