from __future__ import annotations

from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from threading import Event, Lock, Thread
from typing import Any
from urllib.parse import parse_qs, urlencode, urlparse
import hmac
import json
import os
import secrets
import stat
import time

from memguard.api.server import handle_request
from memguard.json_support import jsonable
from memguard.os_guard.cgroup import cgroup_status
from memguard.service import MemGuardService
from memguard.web.acceptance import acceptance_plan, acceptance_report, report_to_markdown, run_acceptance_item


def run_dashboard(
    service: MemGuardService,
    host: str = "127.0.0.1",
    port: int = 8765,
    auth_token: str | dict[str, str] = "",
    state_file: str | Path = "",
    lock_file: str | Path = "",
    idle_timeout: float = 0.0,
    show_access_url: bool = False,
) -> dict[str, object]:
    """Run a dependency-free local dashboard for local operation and inspection."""

    effective_auth_token: str | dict[str, str] = auth_token or secrets.token_urlsafe(32)
    runtime_state = Path(state_file) if state_file else None
    runtime_lock = _RuntimeFileLock(Path(lock_file)) if lock_file else None
    if runtime_lock is not None and not runtime_lock.acquire():
        existing = _wait_for_existing_runtime_state(runtime_state, service.namespace)
        return {
            "status": "existing",
            "port": int(existing.get("port", 0)),
            "namespace": service.namespace,
        }

    class Handler(_DashboardHandler):
        store_path = service.store.path
        db_path = service.meta.path
        namespace = service.namespace
        dashboard_auth_tokens = _normalize_auth_tokens(effective_auth_token)
        dashboard_preview_receipts: dict[str, dict[str, Any]] = {}
        dashboard_preview_receipts_lock = Lock()

    server: _DashboardHTTPServer | None = None
    actual_port = 0
    runtime_payload: dict[str, object] = {}
    stop_monitor = Event()
    monitor: Thread | None = None
    state_written = False
    try:
        server = _DashboardHTTPServer((host, port), Handler)
        actual_port = int(server.server_address[1])
        runtime_payload = {
            "schema": "memguardos-dashboard-runtime-v1",
            "namespace": service.namespace,
            "port": actual_port,
            "pid": os.getpid(),
        }
        if runtime_state is not None:
            _write_runtime_state(runtime_state, runtime_payload)
            state_written = True

        print("MemGuardOS dashboard listening on http://%s:%s" % (host, actual_port), flush=True)
        if show_access_url:
            token = _admin_token(effective_auth_token)
            if token:
                print(
                    "Open: http://%s:%s/#%s" % (host, actual_port, urlencode({"token": token})),
                    flush=True,
                )

        monitor = _start_idle_monitor(server, float(idle_timeout), stop_monitor)
        server.serve_forever()
    finally:
        stop_monitor.set()
        if server is not None:
            server.server_close()
        if monitor is not None:
            monitor.join(timeout=1.0)
        if runtime_state is not None and state_written:
            _remove_owned_runtime_state(runtime_state, runtime_payload)
        if runtime_lock is not None:
            runtime_lock.release()
    return {
        "status": "stopped",
        "port": actual_port,
        "namespace": service.namespace,
    }


class _DashboardHTTPServer(ThreadingHTTPServer):
    daemon_threads = True

    def __init__(self, server_address: tuple[str, int], handler_type: type[BaseHTTPRequestHandler]):
        super().__init__(server_address, handler_type)
        self._activity_lock = Lock()
        self._last_activity = time.monotonic()

    def touch_activity(self) -> None:
        with self._activity_lock:
            self._last_activity = time.monotonic()

    def idle_for(self) -> float:
        with self._activity_lock:
            return time.monotonic() - self._last_activity


class _RuntimeFileLock:
    def __init__(self, path: Path):
        self.path = path
        self.owned = False

    def acquire(self) -> bool:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        for _attempt in range(2):
            try:
                descriptor = os.open(self.path, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
            except FileExistsError:
                if _runtime_lock_owner_is_alive(self.path):
                    return False
                try:
                    self.path.unlink()
                except FileNotFoundError:
                    pass
                continue
            try:
                os.write(descriptor, ("%d\n" % os.getpid()).encode("ascii"))
                os.fsync(descriptor)
            finally:
                os.close(descriptor)
            _set_owner_only_permissions(self.path)
            self.owned = True
            return True
        return False

    def release(self) -> None:
        if not self.owned:
            return
        try:
            owner = int(self.path.read_text(encoding="ascii").strip())
        except (FileNotFoundError, OSError, ValueError):
            owner = 0
        if owner == os.getpid():
            try:
                self.path.unlink()
            except FileNotFoundError:
                pass
        self.owned = False


def _runtime_lock_owner_is_alive(path: Path) -> bool:
    try:
        pid = int(path.read_text(encoding="ascii").strip())
    except (OSError, ValueError):
        return False
    if pid <= 0:
        return False
    if pid == os.getpid():
        return True
    try:
        os.kill(pid, 0)
    except PermissionError:
        return True
    except OSError:
        return False
    return True


def _wait_for_existing_runtime_state(
    state_file: Path | None,
    namespace: str,
    timeout: float = 1.5,
) -> dict[str, object]:
    if state_file is None:
        return {}
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        payload = _read_runtime_state(state_file)
        if (
            payload.get("schema") == "memguardos-dashboard-runtime-v1"
            and payload.get("namespace") == namespace
            and 0 < _safe_int(payload.get("port"), 0) <= 65535
        ):
            return payload
        time.sleep(0.02)
    return {}


def _read_runtime_state(path: Path) -> dict[str, object]:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (FileNotFoundError, OSError, json.JSONDecodeError):
        return {}
    return payload if isinstance(payload, dict) else {}


def _write_runtime_state(path: Path, payload: dict[str, object]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(".%s.%d.%s.tmp" % (path.name, os.getpid(), secrets.token_hex(6)))
    raw = json.dumps(payload, ensure_ascii=False, allow_nan=False, sort_keys=True).encode("utf-8") + b"\n"
    descriptor = os.open(temporary, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
    try:
        os.write(descriptor, raw)
        os.fsync(descriptor)
    finally:
        os.close(descriptor)
    _set_owner_only_permissions(temporary)
    os.replace(temporary, path)
    _set_owner_only_permissions(path)


def _remove_owned_runtime_state(path: Path, expected: dict[str, object]) -> None:
    current = _read_runtime_state(path)
    if current.get("pid") != expected.get("pid") or current.get("port") != expected.get("port"):
        return
    try:
        path.unlink()
    except FileNotFoundError:
        pass


def _set_owner_only_permissions(path: Path) -> None:
    try:
        path.chmod(stat.S_IRUSR | stat.S_IWUSR)
    except OSError:
        return


def _start_idle_monitor(
    server: _DashboardHTTPServer,
    idle_timeout: float,
    stop_event: Event,
) -> Thread | None:
    if idle_timeout <= 0:
        return None

    def monitor_idle() -> None:
        interval = min(1.0, max(0.05, idle_timeout / 4.0))
        while not stop_event.wait(interval):
            if server.idle_for() >= idle_timeout:
                server.shutdown()
                return

    monitor = Thread(target=monitor_idle, name="memguard-dashboard-idle", daemon=True)
    monitor.start()
    return monitor


def _admin_token(auth_token: str | dict[str, str]) -> str:
    if isinstance(auth_token, dict):
        return str(auth_token.get("admin", ""))
    return str(auth_token)


class _DashboardHandler(BaseHTTPRequestHandler):
    store_path: object
    db_path: object
    namespace: str
    dashboard_auth_tokens: dict[str, str] = {}
    dashboard_preview_receipts: dict[str, dict[str, Any]] = {}
    dashboard_preview_receipts_lock = Lock()
    dashboard_preview_ttl_seconds = 300.0

    server_version = "MemGuardOSDashboard/0.1"

    def handle_one_request(self) -> None:
        touch_activity = getattr(self.server, "touch_activity", None)
        if callable(touch_activity):
            touch_activity()
        super().handle_one_request()

    def do_GET(self) -> None:
        parsed = urlparse(self.path)
        if parsed.path == "/":
            self._send_html(_dashboard_html())
            return
        if parsed.path == "/healthz":
            self._send_json({"ok": True, "status": "alive"})
            return
        if parsed.path == "/readyz":
            if not self._require_role("viewer"):
                return
            self._send_json(
                {
                    "ok": True,
                    "status": "ready",
                    "namespace": self.namespace,
                }
            )
            return
        if parsed.path.startswith("/api/") and not self._require_role("viewer"):
            return
        if parsed.path == "/api/status":
            self._send_api({"command": "namespace_status"})
            return
        if parsed.path == "/api/session":
            role = self._current_role()
            rank = _ROLE_RANK.get(role, 0)
            can_write = rank >= _ROLE_RANK["operator"]
            can_administer = rank >= _ROLE_RANK["admin"]
            self._send_json(
                {
                    "ok": True,
                    "result": {
                        "role": role,
                        "capabilities": {
                            "read": rank >= _ROLE_RANK["viewer"],
                            "write_memory": can_write,
                            "preview_forgetting": can_write,
                            "confirm_forgetting": can_administer,
                            "run_acceptance": can_administer,
                            "administer": can_administer,
                        },
                    },
                }
            )
            return
        if parsed.path == "/api/feedback-home":
            params = parse_qs(parsed.query)
            self._send_api({"command": "feedback_home", "limit": _safe_int(params.get("limit", ["8"])[0], 8)})
            return
        if parsed.path == "/api/notifications":
            params = parse_qs(parsed.query)
            self._send_api({"command": "notifications", "limit": _safe_int(params.get("limit", ["30"])[0], 30)})
            return
        if parsed.path == "/api/memory-policy":
            self._send_api({"command": "memory_policy"})
            return
        if parsed.path == "/api/verify-audit":
            self._send_api({"command": "verify_audit"})
            return
        if parsed.path == "/api/retrieval-status":
            self._send_api({"command": "retrieval_status"})
            return
        if parsed.path == "/api/diagnostics":
            self._send_json({"ok": True, "result": self._diagnostics_payload(probe_sdk=False)})
            return
        if parsed.path == "/api/acceptance-plan":
            self._send_json({"ok": True, "result": acceptance_plan()})
            return
        if parsed.path == "/api/agent-overview":
            params = parse_qs(parsed.query)
            session_id = params.get("session_id", [""])[0]
            task = params.get("task", ["查看当前 Agent 记忆状态"])[0]
            service = self._service()
            try:
                result = service.agent_dashboard_snapshot(
                    session_id=session_id,
                    task=task,
                    limit=_safe_int(params.get("limit", ["100"])[0], 100),
                )
            finally:
                service.close()
            self._send_json({"ok": True, "result": result})
            return
        if parsed.path == "/api/agent-evidence":
            params = parse_qs(parsed.query)
            self._send_api(
                {
                    "command": "preference_evidence",
                    "memory_id": params.get("memory_id", [""])[0],
                }
            )
            return
        if parsed.path == "/api/memory-detail":
            params = parse_qs(parsed.query)
            self._send_api(
                {
                    "command": "memory_detail",
                    "memory_id": params.get("memory_id", [""])[0],
                    "task": params.get("task", ["验证这条记忆的适用范围"])[0],
                    "agent_name": params.get("agent_name", ["openhands"])[0],
                    "session_id": params.get("session_id", ["detail-preview"])[0],
                }
            )
            return
        if parsed.path == "/api/preference-proof":
            params = parse_qs(parsed.query)
            self._send_api(
                {
                    "command": "preference_effect_proof",
                    "memory_id": params.get("memory_id", [""])[0],
                    "task": params.get("task", ["验证当前偏好是否生效"])[0],
                    "stage": params.get("stage", ["tool_selection"])[0],
                    "agent_name": params.get("agent_name", ["openhands"])[0],
                    "session_id": params.get("session_id", ["proof-preview"])[0],
                }
            )
            return
        if parsed.path == "/api/export-diagnostics":
            self._send_json_attachment(
                self._diagnostics_payload(probe_sdk=False),
                filename="memguardos-diagnostics.json",
            )
            return
        if parsed.path == "/api/search":
            params = parse_qs(parsed.query)
            query = params.get("q", [""])[0]
            limit = _safe_int(params.get("top_k", ["5"])[0], 5)
            self._send_api({"command": "search", "query": query, "limit": limit})
            return
        if parsed.path == "/api/context":
            params = parse_qs(parsed.query)
            query = params.get("q", [""])[0]
            budget = _safe_int(params.get("token_budget", ["1200"])[0], 1200)
            self._send_api({"command": "build_context", "query": query, "limit": 5, "token_budget": budget})
            return
        if parsed.path == "/api/hierarchical-graph":
            params = parse_qs(parsed.query)
            entity = params.get("q", [""])[0]
            limit = _safe_int(params.get("limit", ["8"])[0], 8)
            self._send_api({"command": "hierarchical_graph_search", "entity": entity, "limit": limit})
            return
        if parsed.path == "/api/audit":
            params = parse_qs(parsed.query)
            limit = _safe_int(params.get("limit", ["20"])[0], 20)
            self._send_api({"command": "audit", "limit": limit})
            return
        if parsed.path == "/api/governance":
            params = parse_qs(parsed.query)
            limit = _safe_int(params.get("limit", ["100"])[0], 100)
            self._send_api({"command": "governance_list", "limit": limit})
            return
        if parsed.path == "/api/explain-extraction":
            params = parse_qs(parsed.query)
            text = params.get("text", [""])[0]
            source_kind = params.get("source_kind", ["manual"])[0]
            service = self._service()
            try:
                result = service.explain_extraction(text, source_kind=source_kind)
            finally:
                service.close()
            self._send_json({"ok": True, "result": result})
            return
        self.send_error(404, "not found")

    def _service(self) -> MemGuardService:
        return MemGuardService(store_path=self.store_path, db_path=self.db_path, namespace=self.namespace)

    def do_POST(self) -> None:
        parsed = urlparse(self.path)
        required_role = {
            "/api/ingest": "operator",
            "/api/agent-run": "operator",
            "/api/journey": "operator",
            "/api/forget-memory": "admin",
            "/api/forget": "operator",
            "/api/agent-forget": "operator",
            "/api/governance-review": "admin",
            "/api/acceptance-run": "admin",
            "/api/acceptance-report": "admin",
        }.get(parsed.path, "admin")
        if parsed.path.startswith("/api/") and not self._require_role(required_role):
            return
        content_type = self.headers.get("Content-Type", "").split(";", 1)[0].strip().lower()
        if parsed.path.startswith("/api/") and content_type != "application/json":
            self._send_json(
                {
                    "ok": False,
                    "error": "unsupported_media_type",
                    "expected": "application/json",
                },
                status=415,
            )
            return
        payload, payload_error = self._read_json()
        if payload_error:
            self._send_json({"ok": False, "error": payload_error}, status=413 if payload_error == "payload_too_large" else 400)
            return
        if parsed.path == "/api/ingest":
            if not self._require_role("operator"):
                return
            self._send_api(
                {
                    "command": "ingest",
                    "text": str(payload.get("text", "")),
                    "source_kind": str(payload.get("source_kind", "dashboard")),
                    "source_uri": "web_dashboard",
                    "tier": payload.get("tier") or None,
                }
            )
            return
        if parsed.path in {"/api/agent-run", "/api/journey"}:
            if not self._require_role("operator"):
                return
            self._send_api(
                {
                    "command": "agent_replay",
                    "task": str(payload.get("task", "")),
                    "mode": str(payload.get("mode", "replay")),
                    "session_id": str(payload.get("session_id", "")),
                }
            )
            return
        if parsed.path == "/api/forget-memory":
            if not self._require_role("admin"):
                return
            self._send_api(
                {
                    "command": "forget_memory_ids",
                    "memory_ids": payload.get("memory_ids") if isinstance(payload.get("memory_ids"), list) else [],
                    "reason": str(payload.get("reason", "user forget")),
                    "compact": bool(payload.get("compact", True)),
                }
            )
            return
        if parsed.path == "/api/forget":
            if not self._require_role("admin" if bool(payload.get("confirm", False)) else "operator"):
                return
            request_text = str(payload.get("request", ""))
            self._send_previewed_api(
                request={
                    "command": "forget",
                    "request": request_text,
                    "confirm": bool(payload.get("confirm", False)),
                    "compact": bool(payload.get("compact", False)),
                },
                kind="forget",
                identity={"request": request_text},
                preview_token=str(payload.get("preview_token", "")),
            )
            return
        if parsed.path == "/api/agent-forget":
            if not self._require_role("admin" if bool(payload.get("confirm", False)) else "operator"):
                return
            filters = payload.get("filters") if isinstance(payload.get("filters"), dict) else {}
            self._send_previewed_api(
                request={
                    "command": "forget_agent_interactions",
                    "filters": filters,
                    "confirm": bool(payload.get("confirm", False)),
                    "compact": bool(payload.get("compact", True)),
                    "external_persistence": bool(payload.get("external_persistence", True)),
                },
                kind="agent_forget",
                identity={"filters": filters},
                preview_token=str(payload.get("preview_token", "")),
            )
            return
        if parsed.path == "/api/governance-review":
            if not self._require_role("admin"):
                return
            self._send_api(
                {
                    "command": "governance_review",
                    "memory_id": str(payload.get("memory_id", "")),
                    "decision": str(payload.get("decision", "")),
                    "reviewer": str(payload.get("reviewer", "")),
                    "reason": str(payload.get("reason", "")),
                }
            )
            return
        if parsed.path == "/api/acceptance-run":
            if not self._require_role("admin"):
                return
            item_id = str(payload.get("item_id", ""))
            service = self._service()
            try:
                try:
                    result = run_acceptance_item(service, item_id)
                except KeyError:
                    self._send_json({"ok": False, "error": "unknown_acceptance_item"}, status=400)
                    return
            finally:
                service.close()
            self._send_json({"ok": bool(result.get("ok")), "result": result}, status=200 if result.get("ok") else 400)
            return
        if parsed.path == "/api/acceptance-report":
            if not self._require_role("admin"):
                return
            try:
                report = acceptance_report(payload.get("results"))
            except ValueError as exc:
                self._send_json(
                    {
                        "ok": False,
                        "error": "invalid_acceptance_results",
                        "message": str(exc),
                    },
                    status=400,
                )
                return
            if payload.get("format") == "markdown":
                self._send_json({"ok": True, "result": {"markdown": report_to_markdown(report), "summary": report["summary"]}})
            else:
                self._send_json({"ok": True, "result": report})
            return
        self.send_error(404, "not found")

    def log_message(self, format: str, *args: Any) -> None:
        return

    def _require_role(self, required_role: str) -> bool:
        allowed, actual_role, reason = _authorize_role(
            self.headers.get("Authorization", ""),
            self.dashboard_auth_tokens,
            required_role,
        )
        if allowed:
            return True
        self._audit_authorization_denied(reason, required_role, actual_role)
        if reason == "insufficient_role":
            self._send_json(
                {
                    "ok": False,
                    "error": "forbidden",
                    "required_role": required_role,
                    "actual_role": actual_role,
                },
                status=403,
            )
            return False
        self._send_json({"ok": False, "error": "unauthorized"}, status=401)
        return False

    def _current_role(self) -> str:
        allowed, actual_role, _reason = _authorize_role(
            self.headers.get("Authorization", ""),
            self.dashboard_auth_tokens,
            "viewer",
        )
        return actual_role if allowed else "anonymous"

    def _read_json(self) -> tuple[dict[str, Any], str]:
        length = _safe_int(self.headers.get("Content-Length", "0"), 0)
        if length <= 0:
            return {}, ""
        if length > 262144:
            self.rfile.read(min(length, 262144))
            return {}, "payload_too_large"
        raw = self.rfile.read(length).decode("utf-8", errors="replace")
        try:
            data = json.loads(raw)
        except json.JSONDecodeError:
            return {}, "invalid_json"
        if not isinstance(data, dict):
            return {}, "json_body_must_be_object"
        return data, ""

    def _send_api(self, request: dict[str, Any]) -> None:
        response = self._api_response(request)
        self._send_json(response, status=200 if response.get("ok") else 400)

    def _api_response(self, request: dict[str, Any]) -> dict[str, Any]:
        service = self._service()
        try:
            response = handle_request(service, request, auth_token=None)
        finally:
            service.close()
        return response

    def _send_previewed_api(
        self,
        *,
        request: dict[str, Any],
        kind: str,
        identity: dict[str, Any],
        preview_token: str,
    ) -> None:
        confirmed = bool(request.get("confirm", False))
        if not confirmed:
            response = self._api_response(request)
            if response.get("ok"):
                result = response.get("result")
                if isinstance(result, dict):
                    item_ids = _preview_item_ids(result)
                    token = self._store_preview_receipt(kind, identity, item_ids)
                    result["preview_token"] = token
                    result["preview_candidate_count"] = len(item_ids)
                    result["preview_expires_in_seconds"] = int(self.dashboard_preview_ttl_seconds)
            self._send_json(response, status=200 if response.get("ok") else 400)
            return

        receipt = self._preview_receipt(preview_token)
        if receipt is None:
            self._send_json(
                {
                    "ok": False,
                    "error": "forget_preview_required",
                    "message": "请先预览当前影响范围，再确认遗忘。",
                },
                status=409,
            )
            return
        if receipt.get("kind") != kind or receipt.get("identity") != _canonical_preview_identity(identity):
            self._send_json(
                {
                    "ok": False,
                    "error": "forget_preview_stale",
                    "message": "遗忘条件已变化，请重新预览影响范围。",
                },
                status=409,
            )
            return

        current_request = dict(request)
        current_request["confirm"] = False
        current_request["compact"] = False
        current = self._api_response(current_request)
        current_result = current.get("result")
        current_ids = _preview_item_ids(current_result if isinstance(current_result, dict) else {})
        if not current.get("ok") or tuple(current_ids) != tuple(receipt.get("item_ids", ())):
            self._discard_preview_receipt(preview_token)
            self._send_json(
                {
                    "ok": False,
                    "error": "forget_preview_stale",
                    "message": "候选范围已变化，请重新预览后再确认。",
                    "current_candidate_count": len(current_ids),
                },
                status=409,
            )
            return

        self._discard_preview_receipt(preview_token)
        response = self._api_response(request)
        self._send_json(response, status=200 if response.get("ok") else 400)

    def _store_preview_receipt(
        self,
        kind: str,
        identity: dict[str, Any],
        item_ids: tuple[str, ...],
    ) -> str:
        token = secrets.token_urlsafe(32)
        now = time.monotonic()
        with self.dashboard_preview_receipts_lock:
            self._purge_preview_receipts_locked(now)
            self.dashboard_preview_receipts[token] = {
                "kind": kind,
                "identity": _canonical_preview_identity(identity),
                "item_ids": tuple(item_ids),
                "created_at": now,
            }
        return token

    def _preview_receipt(self, token: str) -> dict[str, Any] | None:
        if not token:
            return None
        now = time.monotonic()
        with self.dashboard_preview_receipts_lock:
            self._purge_preview_receipts_locked(now)
            receipt = self.dashboard_preview_receipts.get(token)
            return dict(receipt) if receipt is not None else None

    def _discard_preview_receipt(self, token: str) -> None:
        if not token:
            return
        with self.dashboard_preview_receipts_lock:
            self.dashboard_preview_receipts.pop(token, None)

    def _purge_preview_receipts_locked(self, now: float) -> None:
        expired = [
            token
            for token, receipt in self.dashboard_preview_receipts.items()
            if now - float(receipt.get("created_at", 0.0)) > self.dashboard_preview_ttl_seconds
        ]
        for token in expired:
            self.dashboard_preview_receipts.pop(token, None)

    def _audit_authorization_denied(self, reason: str, required_role: str, actual_role: str) -> None:
        service = self._service()
        try:
            audit = getattr(service, "_audit", None)
            if callable(audit):
                audit(
                    "web_authorization_denied",
                    None,
                    {
                        "path": urlparse(self.path).path,
                        "method": self.command,
                        "reason": reason,
                        "required_role": required_role,
                        "actual_role": actual_role,
                    },
                )
        finally:
            service.close()

    def _send_json(self, payload: dict[str, Any], status: int = 200) -> None:
        raw = json.dumps(jsonable(payload), ensure_ascii=False, allow_nan=False, indent=2).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self._send_security_headers()
        self.send_header("Content-Length", str(len(raw)))
        self.end_headers()
        self.wfile.write(raw)

    def _send_json_attachment(self, payload: dict[str, Any], filename: str) -> None:
        raw = json.dumps(jsonable(payload), ensure_ascii=False, allow_nan=False, indent=2).encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Disposition", 'attachment; filename="%s"' % filename)
        self._send_security_headers()
        self.send_header("Content-Length", str(len(raw)))
        self.end_headers()
        self.wfile.write(raw)

    def _send_html(self, html: str) -> None:
        raw = html.encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self._send_security_headers()
        self.send_header("Content-Security-Policy", "default-src 'self'; connect-src 'self'; img-src 'self' data:; style-src 'self' 'unsafe-inline'; script-src 'self' 'unsafe-inline'; base-uri 'none'")
        self.send_header("Content-Length", str(len(raw)))
        self.end_headers()
        self.wfile.write(raw)

    def _send_security_headers(self) -> None:
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("Referrer-Policy", "no-referrer")
        self.send_header("X-Frame-Options", "DENY")
        self.send_header("Cross-Origin-Opener-Policy", "same-origin")
        self.send_header("Permissions-Policy", "camera=(), microphone=(), geolocation=(), usb=(), payment=()")

    def _diagnostics_payload(self, probe_sdk: bool = False) -> dict[str, Any]:
        service = self._service()
        try:
            audit_chain = service.verify_audit()
            retrieval = service.retrieval_status()
            return {
                "ok": bool(audit_chain.get("ok")),
                "namespace": service.namespace,
                "doctor": {
                    "mode": "quick_dashboard",
                    "namespace": service.namespace,
                    "note": "Web 控制台默认使用快速诊断；完整 doctor 请运行 memctl diagnostics --probe-sdk。",
                },
                "runtime_status": cgroup_status(),
                "namespace_status": service.namespace_status(),
                "retrieval_status": retrieval,
                "memory_policy": service.memory_policy_status(),
                "temporal_status": service.temporal_status(limit=10),
                "agent_interactions": service.agent_dashboard_snapshot(limit=20),
                "kylin_sdk": {
                    "mode": "quick_dashboard",
                    "probe_embedding": bool(probe_sdk),
                    "requested_backend": retrieval.get("requested_backend"),
                    "last_backend_used": retrieval.get("last_backend_used"),
                    "kylin": retrieval.get("kylin"),
                    "note": "完整 Kylin SDK 包、符号、模型和推理探针请运行 memctl sdk-status --probe-embedding。",
                },
                "audit_chain": audit_chain,
                "recent_audit": service.audit(limit=20),
            }
        finally:
            service.close()


def _safe_int(value: object, default: int) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def _request_token(header: str) -> str:
    prefix = "Bearer "
    if header.startswith(prefix):
        return header[len(prefix) :].strip()
    return ""


_ROLE_RANK = {"viewer": 1, "operator": 2, "admin": 3}


def _normalize_auth_tokens(auth_token: str | dict[str, str]) -> dict[str, str]:
    if isinstance(auth_token, dict):
        return {
            role: str(token)
            for role, token in auth_token.items()
            if role in _ROLE_RANK and str(token)
        }
    token = str(auth_token)
    return {"admin": token} if token else {}


def _authorize_role(header: str, tokens: dict[str, str], required_role: str) -> tuple[bool, str, str]:
    if not tokens:
        return True, "admin", ""
    request_token = _request_token(header)
    if not request_token:
        return False, "anonymous", "missing_token"
    actual_role = ""
    actual_rank = 0
    for role, expected in tokens.items():
        matched = hmac.compare_digest(request_token, expected)
        if matched and _ROLE_RANK[role] > actual_rank:
            actual_role = role
            actual_rank = _ROLE_RANK[role]
    if not actual_role:
        return False, "unknown", "invalid_token"
    required_rank = _ROLE_RANK.get(required_role, _ROLE_RANK["admin"])
    if actual_rank < required_rank:
        return False, actual_role, "insufficient_role"
    return True, actual_role, ""


def _canonical_preview_identity(identity: dict[str, Any]) -> str:
    return json.dumps(jsonable(identity), ensure_ascii=False, allow_nan=False, sort_keys=True, separators=(",", ":"))


def _preview_item_ids(result: dict[str, Any]) -> tuple[str, ...]:
    item_ids: set[str] = set()
    candidates = result.get("candidates")
    if isinstance(candidates, list):
        for item in candidates:
            if not isinstance(item, dict):
                continue
            memory_id = str(item.get("memory_id", "")).strip()
            if memory_id:
                item_ids.add("memory:" + memory_id)

    affected = result.get("affected")
    if isinstance(affected, dict):
        for field, prefix in (("memory_ids", "memory:"), ("event_ids", "event:")):
            values = affected.get(field)
            if not isinstance(values, list):
                continue
            for value in values:
                normalized = str(value).strip()
                if normalized:
                    item_ids.add(prefix + normalized)
    return tuple(sorted(item_ids))


def _dashboard_html() -> str:
    return """<!doctype html>
<html lang="zh-CN">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1, viewport-fit=cover">
  <link rel="icon" href="data:,">
  <title>MemGuardOS 记忆安全中心</title>
  <style>
    :root {
      color-scheme: light;
      --ink:#1f2937; --muted:#667085; --line:#d6dee8; --accent:#1677c8; --accent-2:#356f9f;
      --warn:#a15c00; --bad:#b42318; --ok:#087443; --bg:#f4f6f8; --panel:#ffffff; --soft:#f3f8fc;
    }
    * { box-sizing: border-box; }
    body { margin:0; font-family:system-ui,sans-serif; color:var(--ink); background:var(--bg); letter-spacing:0; }
    header { display:flex; align-items:center; justify-content:space-between; gap:16px; padding:15px 24px; background:#fff; color:var(--ink); border-bottom:1px solid #cbd8e5; box-shadow:0 1px 3px rgba(31,41,55,.04); }
    header h1 { margin:0; font-size:19px; font-weight:700; color:#175c96; }
    header .subtitle { margin-top:4px; font-size:12px; color:var(--muted); }
    header .meta { font-size:12px; color:var(--muted); }
    main { display:grid; grid-template-columns:280px 1fr; min-height:calc(100vh - 72px); }
    nav { border-right:1px solid var(--line); background:#f8fafc; padding:18px 14px; }
    .nav-group { margin:4px 0 8px; padding:0 2px; color:var(--muted); font-size:12px; font-weight:700; }
    .nav-group + button { margin-top:0; }
    nav button { width:100%; min-height:40px; margin:0 0 7px; text-align:left; border:1px solid transparent; background:transparent; color:var(--ink); padding:9px 11px; border-radius:6px; cursor:pointer; font-weight:600; }
    nav button.active, nav button:hover { border-color:#b7d5ed; background:#eaf4fc; color:#145b92; }
    section { display:none; padding:20px 24px 28px; }
    section.active { display:block; }
    h2 { font-size:22px; margin:0 0 8px; }
    .lead { color:var(--muted); margin:0 0 16px; max-width:900px; line-height:1.65; }
    .grid { display:grid; grid-template-columns:repeat(3,minmax(0,1fr)); gap:12px; }
    .grid.two { grid-template-columns:repeat(2,minmax(0,1fr)); }
    .grid.three { grid-template-columns:repeat(3,minmax(0,1fr)); }
    .grid.four { grid-template-columns:repeat(4,minmax(0,1fr)); }
    .panel { min-width:0; overflow:hidden; background:var(--panel); border:1px solid var(--line); border-radius:8px; padding:14px; }
    .panel.emphasis { border-color:#b7d5ed; background:#f8fbfe; }
    .toolbar { display:flex; flex-wrap:wrap; gap:8px; align-items:center; margin:12px 0; }
    label { display:block; font-size:13px; color:var(--muted); margin:10px 0 5px; }
    textarea, input, select { width:100%; border:1px solid var(--line); border-radius:6px; padding:9px 10px; font:inherit; line-height:1.45; background:white; color:var(--ink); }
    textarea { min-height:120px; resize:vertical; }
    .actions { display:flex; flex-wrap:wrap; gap:8px; margin-top:12px; }
    button.primary { border:0; background:var(--accent); color:white; border-radius:6px; padding:9px 12px; cursor:pointer; min-height:36px; }
    button.secondary { border:1px solid var(--line); background:white; color:var(--ink); border-radius:6px; padding:9px 12px; cursor:pointer; min-height:36px; }
    button.danger { border:0; background:#b42318; color:white; border-radius:6px; padding:9px 12px; cursor:pointer; min-height:36px; }
    button:disabled { opacity:.55; cursor:progress; }
    pre { white-space:pre-wrap; word-break:break-word; background:#0b1220; color:#d1e9ff; border-radius:8px; padding:12px; min-height:200px; overflow:auto; }
    .metric { font-size:26px; font-weight:750; color:var(--accent); margin-top:4px; }
    .metric.compact { font-size:18px; line-height:1.4; }
    .muted { color:var(--muted); font-size:13px; }
    .warning { color:var(--warn); font-weight:650; }
    .statusline { display:flex; align-items:center; gap:10px; margin:0 0 14px; color:var(--muted); font-size:13px; }
    .badge { display:inline-flex; align-items:center; min-height:24px; padding:2px 8px; border-radius:999px; border:1px solid var(--line); background:#fff; color:var(--muted); }
    .badge.ok { border-color:#99f6e4; background:#ccfbf1; color:#115e59; }
    .badge.warn { border-color:#fde68a; background:#fef3c7; color:#92400e; }
    .badge.bad { border-color:#fecaca; background:#fee2e2; color:#991b1b; }
    .health { display:flex; justify-content:space-between; gap:10px; align-items:center; }
    .health strong { font-size:15px; }
    .list { display:grid; gap:8px; }
    .item { border:1px solid var(--line); border-radius:8px; padding:10px 12px; background:#fff; }
    .item-title { font-weight:700; margin-bottom:4px; }
    .quick-actions { display:flex; flex-wrap:wrap; gap:10px; align-items:center; margin:14px 0; padding:12px; border:1px solid var(--line); border-radius:8px; background:#fff; }
    .quick-actions .label { color:var(--muted); font-weight:700; margin-right:4px; }
    .health-matrix { display:grid; grid-template-columns:repeat(4,minmax(0,1fr)); gap:12px; margin-top:12px; }
    .health-tile { display:flex; align-items:center; justify-content:space-between; gap:12px; min-height:58px; border:1px solid var(--line); border-radius:8px; background:#fff; padding:12px 14px; }
    .health-title { display:flex; align-items:center; gap:8px; font-weight:700; }
    .health-tile .health-title > span:not(.status-dot) { white-space:nowrap; }
    .status-dot { width:10px; height:10px; border-radius:50%; background:#94a3b8; box-shadow:0 0 0 3px #f1f5f9; flex:none; }
    .status-dot.ok { background:var(--ok); box-shadow:0 0 0 3px #dcfce7; }
    .status-dot.warn { background:var(--warn); box-shadow:0 0 0 3px #fef3c7; }
    .status-dot.bad { background:var(--bad); box-shadow:0 0 0 3px #fee2e2; }
    .lifecycle { display:grid; grid-template-columns:repeat(4,minmax(0,1fr)); gap:0; margin-top:12px; border:1px solid var(--line); border-radius:8px; background:#fff; overflow:hidden; }
    .lifecycle-step { position:relative; min-height:96px; padding:14px 14px 14px 18px; border-right:1px solid var(--line); }
    .lifecycle-step:last-child { border-right:0; }
    .lifecycle-step::before { content:""; position:absolute; left:0; top:0; bottom:0; width:3px; background:#d0d5dd; }
    .lifecycle-step.ready::before { background:var(--accent); }
    .lifecycle-step strong { display:flex; align-items:center; gap:8px; margin-bottom:8px; }
    .progress { height:8px; background:#e5e7eb; border-radius:999px; overflow:hidden; margin-top:8px; }
    .progress span { display:block; height:100%; background:var(--accent); width:0%; }
    .trace { display:grid; grid-template-columns:repeat(3,minmax(0,1fr)); gap:10px; margin-top:12px; }
    .trace .step { border:1px solid var(--line); background:#fff; border-radius:8px; padding:10px; }
    .step b { display:block; color:#145b92; margin-bottom:4px; }
    .timeline { position:relative; display:grid; gap:0; }
    .timeline::before { content:""; position:absolute; left:11px; top:8px; bottom:8px; width:1px; background:var(--line); }
    .timeline-row { position:relative; padding:0 0 14px 34px; min-width:0; }
    .timeline-dot { position:absolute; left:4px; top:4px; width:15px; height:15px; border:3px solid #fff; border-radius:50%; background:var(--accent-2); box-shadow:0 0 0 1px var(--line); }
    .timeline-row.tool .timeline-dot { background:var(--warn); }
    .timeline-row.forgotten .timeline-dot { background:var(--muted); }
    .timeline-meta { color:var(--muted); font-size:12px; margin-bottom:4px; }
    .timeline-body { border-bottom:1px solid #eaecf0; padding-bottom:10px; line-height:1.55; word-break:break-word; }
    .stage-columns { display:grid; grid-template-columns:repeat(3,minmax(0,1fr)); gap:10px; }
    .stage-column { border-top:3px solid var(--accent); background:#fff; padding:12px; min-width:0; }
    .stage-column h3 { margin:0 0 8px; font-size:15px; }
    .error { display:none; margin:0 0 12px; padding:10px 12px; border:1px solid #fecaca; background:#fff1f2; color:#991b1b; border-radius:8px; }
    details { margin-top:12px; border:1px solid var(--line); border-radius:8px; background:#fff; }
    details summary { cursor:pointer; padding:10px 12px; font-weight:700; color:var(--ink); }
    details[open] summary { border-bottom:1px solid var(--line); }
    .advanced-output { margin:0; border-radius:0 0 8px 8px; min-height:140px; }
    .overview-more > summary { padding:12px 14px; }
    .overview-more-body { padding:0 12px 12px; }
    .operation-status { display:none; margin:12px 24px 0; border:1px solid #b7d5ed; background:#eef7ff; color:#185a91; border-radius:6px; padding:10px 12px; font-size:13px; }
    .operation-status.ok { border-color:#bbf7d0; background:#f0fdf4; color:#166534; }
    .operation-status.warn { border-color:#fde68a; background:#fffbeb; color:#92400e; }
    .operation-status.bad { border-color:#fecaca; background:#fef2f2; color:#991b1b; }
    .button-label { display:inline-flex; align-items:center; gap:6px; white-space:nowrap; }
    .mini-actions { display:flex; flex-wrap:wrap; gap:8px; margin-top:8px; }
    [hidden] { display:none !important; }
    button:hover { filter:brightness(.98); }
    button:focus-visible, input:focus-visible, textarea:focus-visible, select:focus-visible { outline:2px solid #78b5e3; outline-offset:2px; }
    .health { min-width:0; }
    .health > * { min-width:0; }
    .metric { overflow-wrap:anywhere; }
    @media (max-width: 1180px) {
      main { grid-template-columns:220px 1fr; }
      section { padding:18px; }
      .grid.four, .health-matrix { grid-template-columns:repeat(2,minmax(0,1fr)); }
      .grid.three, .trace, .stage-columns { grid-template-columns:repeat(2,minmax(0,1fr)); }
    }
    @media (max-width: 760px) {
      header { align-items:flex-start; padding:14px 16px; }
      header .meta { display:none; }
      main { grid-template-columns: 1fr; }
      nav { display:flex; overflow-x:auto; border-right:0; border-bottom:1px solid var(--line); gap:6px; padding:10px 12px; scrollbar-width:thin; scroll-snap-type:x proximity; scroll-padding-inline:12px; overscroll-behavior-inline:contain; }
      .nav-group { display:none; }
      nav button { width:auto; flex:0 0 auto; min-height:44px; margin:0; white-space:nowrap; scroll-snap-align:start; }
      section { padding:16px 14px 24px; }
      .grid, .grid.two, .grid.three, .grid.four, .trace, .stage-columns, .health-matrix, .lifecycle { grid-template-columns: 1fr; }
      .lifecycle-step { border-right:0; border-bottom:1px solid var(--line); }
      .lifecycle-step:last-child { border-bottom:0; }
      .funnel-row { grid-template-columns:72px 1fr 42px; }
      .quick-actions { align-items:stretch; }
      .quick-actions button { flex:1 1 145px; min-height:44px; }
      button.primary, button.secondary, button.danger { min-height:44px; }
    }
  </style>
</head>
<body>
  <header>
    <div><h1>MemGuardOS 记忆安全中心</h1><div class="subtitle">面向本机 OS Agent 的记忆采集、上下文治理、精准遗忘和系统维护</div></div>
    <div class="meta">
      <span id="namespaceMeta">当前资料库：加载中</span>
    </div>
  </header>
  <main>
    <nav>
      <div class="nav-group">普通用户</div>
      <button class="active" data-tab="overview">个人工作台</button>
      <button data-tab="ingest">记忆写入</button>
      <button data-tab="search">记忆检索</button>
      <button data-tab="context">上下文预览</button>
      <button data-tab="agent">Agent 工作台</button>
      <button data-tab="preferences">偏好设置</button>
      <button data-tab="memory-detail">记忆详情</button>
      <button data-tab="forget">遗忘验证</button>
      <button data-tab="notifications">通知中心</button>
      <div class="nav-group" data-min-role="admin">管理员视图</div>
      <button data-tab="audit" data-min-role="admin">审计记录</button>
      <button data-tab="diagnostics" data-min-role="admin">系统维护</button>
      <button data-tab="acceptance" data-min-role="admin">复核中心</button>
    </nav>
    <div>
      <div id="errorBox" class="error"></div>
      <div id="operationStatus" class="operation-status" role="status" aria-live="polite"></div>
      <section id="overview" class="active">
        <h2>个人工作台</h2>
        <p class="lead">这里汇总本机 Agent 记忆服务的健康状态、最近记忆数量、审计完整性和常用操作。普通用户可以完成写入、检索、上下文预览和遗忘预览；管理员可以进入系统维护查看服务、SDK、授权和资源状态。</p>
        <div class="statusline"><span id="healthBadge" class="badge">连接中</span><span id="lastUpdated">等待刷新</span></div>
        <div class="quick-actions" aria-label="常用快捷操作">
          <span class="label">快捷操作</span>
          <button class="primary" onclick="openTab('ingest')">写入记忆</button>
          <button class="secondary" onclick="openTab('context')">查看上下文</button>
          <button class="secondary" onclick="openTab('agent')">打开 Agent 工作台</button>
          <button class="secondary" onclick="openTab('forget')">预览遗忘</button>
          <button class="secondary" data-min-role="operator" onclick="runJourney(event)">验证记忆效果</button>
          <button class="secondary" data-min-role="admin" onclick="openTab('diagnostics')">系统维护</button>
          <span class="muted">普通操作在左侧前半区，管理员操作在【系统维护】中处理。</span>
        </div>
        <div class="grid two" style="margin-top:12px">
          <div class="panel emphasis">
            <div class="health"><div class="item-title">最近学到的内容</div><button class="secondary" onclick="loadFeedbackHome(event)">更新</button></div>
            <div id="recentMemoriesList" class="list" style="margin-top:10px"><div class="muted">正在读取最近变化…</div></div>
          </div>
          <div class="panel">
            <div class="health"><div class="item-title">最近需要关注</div><button class="secondary" onclick="openTab('notifications')">查看全部</button></div>
            <div id="homeNotifications" class="list" style="margin-top:10px"><div class="muted">正在读取通知…</div></div>
          </div>
        </div>
        <div class="grid four">
          <div class="panel emphasis"><div class="health-title"><span class="status-dot ok"></span><span>当前记忆</span></div><div class="metric" id="liveCount">-</div></div>
          <div class="panel"><div class="health-title"><span class="status-dot"></span><span>操作记录</span></div><div class="metric" id="auditCount">-</div></div>
          <div class="panel"><div class="health-title"><span class="status-dot"></span><span>当前资料库</span></div><div class="metric" id="namespaceCount">-</div></div>
          <div class="panel"><div class="health-title"><span class="status-dot"></span><span>数据位置</span></div><div id="dataLocation" class="metric compact">仅保存在本机</div></div>
        </div>
        <div class="health-matrix" aria-label="服务健康状态">
          <div class="health-tile"><div class="health-title"><span id="auditDot" class="status-dot"></span><span>审计链</span></div><span id="auditBadge" class="badge">待检查</span></div>
          <div class="health-tile"><div class="health-title"><span id="retrievalDot" class="status-dot"></span><span>记忆检索</span></div><span id="retrievalBadge" class="badge">待检查</span></div>
          <div class="health-tile"><div class="health-title"><span id="sdkDot" class="status-dot"></span><span>本机智能检索</span></div><span id="sdkBadge" class="badge">待检查</span></div>
          <div class="health-tile"><div class="health-title"><span id="osDot" class="status-dot"></span><span>系统资源</span></div><span id="osBadge" class="badge">待检查</span></div>
        </div>
        <div class="grid three" style="margin-top:12px">
          <div class="panel emphasis">
            <div class="item-title">系统健康度</div>
            <p class="muted">审计链可校验、检索后端可用、OS 资源可见、系统报告可导出时，本机记忆服务适合继续接入 Agent 使用。</p>
            <div class="progress"><span id="readinessProgress"></span></div>
            <div id="readinessText" class="muted" style="margin-top:8px">等待状态刷新</div>
          </div>
          <div class="panel">
            <div class="item-title">推荐操作路径</div>
            <div class="list">
              <div class="item"><div class="item-title">1. 刷新状态</div><div class="muted">查看审计链、检索后端、SDK 和 OS 资源红黄绿状态。</div></div>
              <div class="item"><div class="item-title">2. 写入并检索</div><div class="muted">写入一条 Agent 偏好，立即检索并构建生成上下文。</div></div>
              <div class="item"><div class="item-title">3. 预览遗忘</div><div class="muted">先查看候选和影响范围，再由管理员确认执行和记录审计。</div></div>
            </div>
          </div>
          <div class="panel">
            <div class="item-title">风险处置建议</div>
            <div id="riskAdvice" class="list">
              <div class="item"><div class="muted">刷新后会根据审计链、检索后端、SDK 和 OS 资源状态给出本机处置建议。</div></div>
            </div>
          </div>
        </div>
        <div id="refreshWarnings" class="panel" style="margin-top:12px; display:none"></div>
        <details class="overview-more">
          <summary>了解记忆治理流程和详细状态</summary>
          <div class="overview-more-body">
        <div class="grid two" style="margin-top:12px">
          <div class="panel">
            <div class="item-title">记忆治理流程</div>
            <div class="lifecycle" aria-label="记忆治理流程图">
              <div class="lifecycle-step ready"><strong><span class="status-dot ok"></span>采集</strong><div class="muted">等待输入，并接收用户与 Agent 交互、工具结果和文件导入。</div></div>
              <div class="lifecycle-step ready"><strong><span class="status-dot ok"></span>抽取</strong><div class="muted">形成偏好、知识和程序性经验，并标记敏感风险。</div></div>
              <div class="lifecycle-step ready"><strong><span class="status-dot ok"></span>上下文</strong><div class="muted">按规划、工具选择和回答阶段生成受控上下文。</div></div>
              <div class="lifecycle-step"><strong><span class="status-dot warn"></span>遗忘</strong><div class="muted">先预览影响范围，再由管理员确认后执行。</div></div>
            </div>
          </div>
          <div class="panel">
            <div class="item-title">数据保护</div>
            <div class="list" style="margin-top:12px">
              <div class="item"><div class="item-title">本机保存</div><div class="muted">记忆数据默认保存在当前设备，不上传到云端。</div></div>
              <div class="item"><div class="item-title">敏感阻断</div><div class="muted">检测到凭据或个人敏感内容时，默认不写入长期记忆。</div></div>
              <div class="item"><div class="item-title">操作可追溯</div><div class="muted">写入、使用和遗忘都会留下可校验的本机操作记录。</div></div>
            </div>
          </div>
        </div>
        <div class="grid two" style="margin-top:12px">
          <div class="panel emphasis">
            <div class="item-title">能力概览</div>
            <p class="muted">用于管理员快速了解当前部署提供的记忆治理、系统集成和安全审计能力。运行状态和数量以页面实时接口为准。</p>
            <div class="grid two">
              <div class="item"><div class="item-title">交互记忆治理</div><div class="muted">自动接收用户、Agent 和工具事件，形成可追溯的偏好与程序性记忆。</div></div>
              <div class="item"><div class="item-title">分阶段上下文</div><div class="muted">为规划、工具选择和回答生成提供不同的受控记忆集合。</div></div>
              <div class="item"><div class="item-title">精准遗忘</div><div class="muted">支持影响预览、二次确认、事件擦除、索引清理和遗忘后复检。</div></div>
              <div class="item"><div class="item-title">本机审计</div><div class="muted">记录关键操作、上下文选择和完整性校验，不依赖公网服务。</div></div>
            </div>
          </div>
          <div class="panel">
            <div class="item-title">常用工作流</div>
            <div class="list">
              <div class="item"><div class="item-title">1. 状态与红黄绿状态</div><div class="muted">刷新总览，确认审计链、检索后端、SDK 和 OS 资源状态。</div></div>
              <div class="item"><div class="item-title">2. 写入与抽取解释</div><div class="muted">写入偏好或真实故障文本，展示抽取类型、质量校验和敏感策略。</div></div>
              <div class="item"><div class="item-title">3. 生成上下文与精准遗忘</div><div class="muted">展示来源分布、候选选择轨迹、脱敏状态，再进行预览模式和确认遗忘。</div></div>
            </div>
          </div>
        </div>
        <div class="grid two" style="margin-top:12px">
          <div class="panel">
            <div class="item-title">治理闭环</div>
            <p class="muted">工作台按“采集交互、抽取偏好、构建上下文、确认遗忘、导出系统报告”的顺序组织能力，用户可以顺着页面完成一次完整的本机 Agent 记忆治理流程。</p>
            <div class="list">
              <div class="item"><div class="item-title">采集与抽取</div><div class="muted">写入用户和 Agent 交互文本，查看抽取依据、敏感标签和质量提示。</div></div>
              <div class="item"><div class="item-title">检索与注入</div><div class="muted">通过混合检索和生成上下文页确认哪些记忆进入回答前上下文。</div></div>
              <div class="item"><div class="item-title">遗忘与审计</div><div class="muted">先预览影响范围，再二次确认删除，并用审计链复验操作事实。</div></div>
            </div>
          </div>
          <div class="panel">
            <div class="item-title">今日工作流</div>
            <p class="muted">建议日常使用按以下顺序执行，能减少误删、漏审和上下文选择不透明的问题。</p>
            <div class="list">
              <div class="item"><div class="item-title">1. 刷新状态</div><div class="muted">确认红黄绿状态、存储目录、实时记忆和审计事件数量。</div></div>
              <div class="item"><div class="item-title">2. 运行一条真实偏好</div><div class="muted">写入“以后搜索代码优先使用 rg”一类交互，再查看 Agent 三阶段上下文。</div></div>
              <div class="item"><div class="item-title">3. 导出系统报告</div><div class="muted">在执行遗忘或复检后导出报告，保留策略、审计和检索状态。</div></div>
            </div>
          </div>
        </div>
        <div class="toolbar"><button class="primary" onclick="refresh(event)">刷新状态</button><button class="secondary" onclick="diagnostics(event)">检查系统状态</button><button class="secondary" onclick="exportDiagnostics(event)">导出系统报告</button></div>
        <div class="grid two">
          <div class="panel"><div class="item-title">状态摘要</div><div id="statusCards" class="list"></div></div>
          <div class="panel"><div class="item-title">系统报告说明</div><p class="muted">导出的系统报告包含命名空间、检索策略、冷热分层、Kylin SDK、审计链和最近操作记录，不包含记忆正文。</p></div>
        </div>
          </div>
        </details>
        <details><summary>详细记录：操作回执</summary><pre id="overviewOut" class="advanced-output"></pre></details>
      </section>
      <section id="ingest">
        <h2>写入记忆</h2>
        <div class="panel">
          <label for="ingestText">要保存的内容</label><textarea id="ingestText">以后回答 openKylin 服务问题时，先给 systemctl 命令，再说明风险。</textarea>
          <label for="sourceKind">内容来源</label><select id="sourceKind"><option value="manual">手动输入</option><option value="dashboard">本地控制台</option><option value="tool_result">工具执行结果</option><option value="user_behavior">用户与 Agent 交互</option><option value="os_event">系统事件</option></select>
          <label for="tier">保存时长</label><select id="tier"><option value="">自动选择</option><option value="working">临时工作记忆</option><option value="session">当前会话记忆</option><option value="long_term">长期记忆</option></select>
          <div class="actions"><button class="primary" data-min-role="operator" onclick="ingest(event)">保存为记忆</button><button class="secondary" onclick="explainExtraction(event)">查看提取依据</button></div>
        </div>
        <div id="ingestFeedback" class="list" style="margin-top:12px"></div>
        <details><summary>详细记录：操作回执</summary><pre id="ingestOut" class="advanced-output"></pre></details>
      </section>
      <section id="search">
        <h2>混合检索</h2>
        <p class="lead">检索结果会显示召回来源、分数、敏感等级和记忆层级，方便解释“为什么这条记忆被选中”。</p>
        <div class="panel">
          <label for="searchQuery">查询</label><input id="searchQuery" value="openKylin 服务内存限制">
          <div class="actions"><button class="primary" onclick="search(event)">检索</button><button class="secondary" onclick="graphSearch(event)">层次图谱</button></div>
        </div>
        <div id="searchCards" class="list" style="margin-top:12px"></div>
        <details><summary>详细记录：操作回执</summary><pre id="searchOut" class="advanced-output"></pre></details>
      </section>
      <section id="context">
        <h2>生成上下文</h2>
        <p class="lead">这里展示回答前会使用哪些记忆、内容来自哪里、长度是否合适，以及候选被纳入或过滤的原因。</p>
        <div class="panel">
          <label for="contextQuery">问题</label><input id="contextQuery" value="openKylin 服务内存限制">
          <label for="tokenBudget">上下文长度上限</label><input id="tokenBudget" type="number" value="1200">
          <div class="actions"><button class="primary" onclick="contextBuild(event)">生成预览</button></div>
        </div>
        <div id="contextVisual" style="margin-top:12px"></div>
        <details><summary>详细记录：操作回执</summary><pre id="contextOut" class="advanced-output"></pre></details>
      </section>
      <section id="agent">
        <h2>Agent 交互治理</h2>
        <p class="lead">查看用户与现有 Agent 的真实交互事件、自动形成的偏好依据，以及规划、工具选择、回答生成三个阶段实际获得的记忆。已遗忘事件只保留哈希和删除事实。</p>
        <div class="panel">
          <div class="grid two">
            <div><label for="agentSession">会话 ID（可选）</label><input id="agentSession" placeholder="留空查看最近事件"></div>
            <div><label for="agentTask">上下文预览任务</label><input id="agentTask" value="查找精准遗忘的实现位置"></div>
          </div>
          <div class="grid three">
            <div><label for="agentWorkspace">工作区</label><input id="agentWorkspace" value="当前命名空间" aria-label="Agent 工作区"></div>
            <div><label for="agentMode">运行方式</label><select id="agentMode"><option value="replay">本地回放（可复现）</option><option value="dry-run">只读预览</option><option value="openhands">真实 OpenHands（需外部模型）</option></select></div>
            <div><label for="agentName">Agent</label><select id="agentName"><option value="openhands">OpenHands</option></select></div>
          </div>
          <div class="actions"><button class="primary" data-min-role="operator" onclick="runAgentTask(event)">运行任务</button><button class="secondary" onclick="loadAgentOverview(event)">查看已有记录</button><button class="secondary" onclick="exportDiagnostics(event)">导出系统报告</button></div>
          <p class="muted">本地回放会真实写入并读取 MemGuardOS 事件，用于核验反馈链；只读预览不写入；真实 OpenHands 只在模型和工作区可用时运行，结果会明确标注执行方式。</p>
        </div>
        <div id="agentMetrics" class="grid four" style="margin-top:12px"></div>
        <div class="panel" style="margin-top:12px">
          <div class="item-title">记忆价值指标</div>
          <p class="muted">本页把记忆价值拆成可解释的操作指标：工具选择命中、重复尝试减少、高风险确认、跨会话复用和遗忘后残留。实时数值来自 Agent 事件、稳定偏好和三阶段上下文，完整回执可在详细记录中查看。</p>
          <div class="grid four">
            <div class="item"><div class="item-title">工具选择命中</div><div class="muted">偏好记忆帮助 Agent 选择 rg、journalctl、pytest 等用户确认过的工具。</div></div>
            <div class="item"><div class="item-title">重复尝试</div><div class="muted">程序性记忆记录失败命令和修复命令，减少跨会话重复踩坑。</div></div>
            <div class="item"><div class="item-title">安全确认</div><div class="muted">高风险命令进入确认或拒绝路径，不把危险操作当普通偏好执行。</div></div>
            <div class="item"><div class="item-title">遗忘残留</div><div class="muted">受管事件载荷被擦除后只保留哈希和审计事实，外部 Agent 副本单独提示。</div></div>
          </div>
        </div>
        <div class="grid two" style="margin-top:12px">
          <div class="panel"><div class="item-title">交互记录</div><div id="agentTimeline" class="timeline" style="margin-top:12px"></div></div>
          <div class="panel"><div class="item-title">偏好依据</div><div id="agentPreferences" class="list" style="margin-top:12px"></div></div>
        </div>
        <div class="panel" style="margin-top:12px"><div class="item-title">三阶段上下文对比</div><div id="agentContexts" class="stage-columns" style="margin-top:12px"></div></div>
        <div class="panel" style="margin-top:12px"><div class="item-title">偏好生效证明</div><p class="muted">只有观察到实际工具命令或回答内容时才会判定“通过”；没有真实行为时显示“暂无证据”，不会把上下文注入误当成 Agent 已遵循。</p><div id="preferenceProofs" class="list" style="margin-top:12px"></div></div>
        <div class="panel" style="margin-top:12px">
          <div class="item-title">按交互主题精准遗忘</div>
          <div class="grid two">
            <div><label for="agentForgetName">Agent</label><input id="agentForgetName" value="openhands"></div>
            <div><label for="agentForgetTopic">偏好主题</label><select id="agentForgetTopic"><option value="response_order">回答顺序</option><option value="code_search_tool">代码搜索工具</option><option value="response_language">回答语言</option><option value="response_verbosity">回答详细度</option><option value="high_risk_confirmation">高风险确认</option></select></div>
          </div>
          <div class="actions"><button class="secondary" data-min-role="operator" onclick="agentForget(event, false)">预览影响</button><button id="agentForgetConfirmButton" class="danger" data-min-role="admin" onclick="agentForget(event, true)" disabled>确认遗忘</button></div>
          <div id="agentForgetImpact" class="list" style="margin-top:12px"></div>
        </div>
        <details><summary>详细记录：操作回执</summary><pre id="agentOut" class="advanced-output"></pre></details>
      </section>
      <section id="preferences">
        <h2>偏好生效</h2>
        <p class="lead">这里展示当前生效的 Agent 偏好、作用范围和最近一次可验证行为。只注入上下文但没有观察到实际行为的偏好，会明确标记为“暂无证据”。</p>
        <div class="panel">
          <div class="grid two">
            <div><label for="proofTask">验证任务</label><input id="proofTask" value="查找 MemGuardService 的定义"></div>
            <div><label for="proofStage">验证阶段</label><select id="proofStage"><option value="tool_selection">工具选择</option><option value="planning">任务规划</option><option value="response_generation">回答生成</option></select></div>
          </div>
          <div class="actions"><button class="primary" onclick="loadPreferences(event)">刷新偏好</button><button class="secondary" onclick="runAgentTask(event)">运行验证任务</button></div>
        </div>
        <div id="preferenceList" class="list" style="margin-top:12px"></div>
        <details><summary>详细记录：偏好证明</summary><pre id="preferenceOut" class="advanced-output"></pre></details>
      </section>
      <section id="memory-detail">
        <h2>记忆详情</h2>
        <p class="lead">输入记忆编号后，可以查看摘要、来源事件、作用域、版本、图谱关系和最近生效阶段；正文和敏感字段仍遵循系统治理边界。</p>
        <div class="panel">
          <label for="memoryDetailId">记忆编号</label><input id="memoryDetailId" placeholder="从最近学到的内容或检索结果中选择">
          <div class="actions"><button class="primary" onclick="loadMemoryDetail(event)">查看详情</button><button class="danger" data-min-role="admin" onclick="forgetMemoryFromDetail(event)">遗忘这条记忆</button></div>
        </div>
        <div id="memoryDetailCards" class="list" style="margin-top:12px"></div>
        <details><summary>详细记录：记忆详情</summary><pre id="memoryDetailOut" class="advanced-output"></pre></details>
      </section>
      <section id="notifications">
        <h2>通知中心</h2>
        <p class="lead">通知中心记录重要记忆变化和风险事件。普通检索、缓存命中和本机智能检索降级不会频繁打扰桌面，只在这里保留状态变化。</p>
        <div class="toolbar"><button class="primary" onclick="loadNotifications(event)">刷新通知</button><button class="secondary" data-min-role="admin" onclick="openTab('diagnostics')">查看系统维护</button></div>
        <div id="notificationList" class="list"></div>
        <details><summary>详细记录：通知来源</summary><pre id="notificationOut" class="advanced-output"></pre></details>
      </section>
      <section id="forget">
        <h2>精准遗忘</h2>
        <p class="lead">先查看候选，再二次确认执行。候选会以卡片形式列出匹配关键词和删除风险，避免把不可逆操作藏在底层响应里。</p>
        <div class="panel">
          <div class="item-title">遗忘二次确认</div>
          <p class="muted">精准遗忘默认先做影响范围预览，只列出候选记忆、匹配关键词和删除后果。只有点击确认按钮后，系统才写入删除标记、整理存储文件并清理可访问索引。</p>
          <label for="forgetRequest">自然语言请求</label><input id="forgetRequest" value="忘掉 temporary project codename">
          <div class="actions"><button class="secondary" data-min-role="operator" onclick="forget(event, false)">预览影响</button><button id="forgetConfirmButton" class="danger" data-min-role="admin" onclick="forget(event, true)" disabled>确认遗忘并整理存储</button></div>
          <p class="warning">确认遗忘会写入删除标记并清理可访问索引。</p>
        </div>
        <div class="grid three" style="margin-top:12px">
          <div class="item"><div class="item-title">影响范围预览</div><div class="muted">展示候选、分数和关键词，帮助判断是否误删。</div></div>
          <div class="item"><div class="item-title">确认后执行</div><div class="muted">确认操作会修改本地存储和索引，适合在审计链正常时执行。</div></div>
          <div class="item"><div class="item-title">遗忘后复检</div><div class="muted">遗忘后建议重新检索和校验审计链，确认敏感载荷不可访问。</div></div>
        </div>
        <div id="forgetCards" class="list" style="margin-top:12px"></div>
        <details><summary>详细记录：操作回执</summary><pre id="forgetOut" class="advanced-output"></pre></details>
      </section>
      <section id="audit" data-min-role="admin">
        <h2>审计记录</h2>
        <div class="actions"><button class="primary" onclick="audit(event)">刷新</button></div>
        <details><summary>运行记录</summary><pre id="auditOut" class="advanced-output"></pre></details>
      </section>
      <section id="diagnostics" data-min-role="admin">
        <h2>系统维护</h2>
        <p class="lead">管理员视图用于本机运维和问题排查，聚合审计链、检索后端、记忆策略、端侧 SDK、cgroup 和本机检查信息，并支持一键导出。</p>
        <div class="panel">
          <div class="actions">
            <button class="primary" onclick="verifyAudit(event)">校验操作记录</button>
            <button class="secondary" onclick="retrievalStatus(event)">检查检索方式</button>
            <button class="secondary" onclick="memoryPolicy(event)">查看记忆策略</button>
            <button class="secondary" onclick="diagnostics(event)">聚合系统状态</button>
            <button class="secondary" onclick="exportDiagnostics(event)">导出系统报告</button>
          </div>
          <p class="muted">维护输出用于本机管理员定位审计、检索和冷热分层状态。敏感内容会按核心链路的脱敏规则处理。</p>
          <form id="dashboardAuthForm" onsubmit="event.preventDefault()">
            <label for="apiToken">访问凭证（仅在启用本地权限时需要）</label>
            <input id="apiToken" type="password" placeholder="输入管理员提供的访问凭证" autocomplete="off" spellcheck="false">
          </form>
        </div>
        <div class="panel" style="margin-top:12px">
          <div class="item-title">系统报告内容清单</div>
          <div class="grid four">
            <div class="item"><div class="item-title">本机检查</div><div class="muted">命名空间、运行模式和基础服务状态。</div></div>
            <div class="item"><div class="item-title">检索与策略</div><div class="muted">retrieval、memory-policy、temporal 与冷热层状态。</div></div>
            <div class="item"><div class="item-title">OS 与 SDK</div><div class="muted">cgroup、Kylin SDK 可探测状态和自动降级信息。</div></div>
            <div class="item"><div class="item-title">审计链</div><div class="muted">verify-audit 结果和最近审计事件，便于管理员复核。</div></div>
          </div>
        </div>
        <details><summary>详细记录：操作回执</summary><pre id="diagnosticsOut" class="advanced-output"></pre></details>
      </section>
      <section id="acceptance" data-min-role="admin">
        <h2>本机验收复核</h2>
        <p class="lead">复核中心把环境、记忆、安全、桌面集成、测试和证据目录整理成可点击步骤。自动项目由本地白名单检查执行，桌面菜单和弹窗类项目保留人工截图确认，便于管理员按同一顺序完成最终复核。</p>
        <div class="grid three">
          <div class="panel emphasis">
            <div class="item-title">复核进度</div>
            <div class="metric" id="acceptanceProgress">0/0</div>
            <div class="progress"><span id="acceptanceProgressBar"></span></div>
            <div id="acceptanceAdvice" class="muted" style="margin-top:8px">加载复核清单中</div>
          </div>
          <div class="panel">
            <div class="item-title">推荐顺序</div>
            <div class="list">
              <div class="item"><div class="item-title">1. 基础环境</div><div class="muted">先确认服务、资源和本机状态。</div></div>
              <div class="item"><div class="item-title">2. 记忆闭环</div><div class="muted">再检查写入、检索、上下文、遗忘和审计。</div></div>
              <div class="item"><div class="item-title">3. 桌面确认</div><div class="muted">最后补拍 Peony、UKUI 和托盘人工截图。</div></div>
            </div>
          </div>
          <div class="panel">
            <div class="item-title">结果说明</div>
            <p class="muted">“通过”表示本机检查满足通过标准；“需人工确认”表示必须在桌面会话里点开对应入口并保存截图；“警告”表示功能可继续使用，但建议管理员查看详细记录。</p>
          </div>
        </div>
        <div class="toolbar">
          <button class="primary" onclick="loadAcceptancePlan(event)">刷新复核清单</button>
          <button class="secondary" data-min-role="admin" onclick="runAcceptanceGroup(event)">运行快速检查</button>
          <button class="secondary" data-min-role="admin" onclick="exportAcceptanceReport(event)">生成复核报告</button>
        </div>
        <div class="grid two">
          <div class="panel">
            <div class="item-title">自动复核项</div>
            <div id="acceptanceList" class="list" style="margin-top:12px"></div>
          </div>
          <div class="panel">
            <div class="item-title">人工截图项</div>
            <div id="acceptanceManualList" class="list" style="margin-top:12px"></div>
          </div>
        </div>
        <details><summary>详细记录：复核回执</summary><pre id="acceptanceOut" class="advanced-output"></pre></details>
      </section>
    </div>
  </main>
  <script>
    const pretty = obj => JSON.stringify(obj, null, 2);
    const esc = value => String(value ?? '').replace(/[&<>"']/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
    const loadedTabs = new Set(['overview']);
    document.querySelectorAll('nav button').forEach(btn => btn.addEventListener('click', () => {
      openTab(btn.dataset.tab);
    }));
    async function openTab(tab) {
      const requestedButton = Array.from(document.querySelectorAll('nav button'))
        .find(item => item.dataset.tab === tab);
      const requestedSection = document.getElementById(tab);
      if ((requestedButton && requestedButton.hidden) || (requestedSection && requestedSection.hidden)) {
        operationFeedback('当前访问权限不足，已返回个人工作台。', 'warn');
        if (tab !== 'overview')
          return openTab('overview');
        return;
      }
      document.querySelectorAll('nav button').forEach(item => item.classList.toggle('active', item.dataset.tab === tab));
      document.querySelectorAll('section').forEach(item => item.classList.toggle('active', item.id === tab));
      if (requestedButton) requestedButton.scrollIntoView({block:'nearest', inline:'nearest'});
      const section = requestedSection;
      if (section) section.scrollIntoView({block:'start'});
      if (window.location.hash !== '#' + tab) window.history.replaceState(null, '', '#' + tab);
      await loadTabData(tab, null);
    }
    async function loadTabData(tab, event) {
      if (!tab || loadedTabs.has(tab)) return;
      const loaders = {
        audit: audit,
        agent: loadAgentOverview,
        preferences: loadPreferences,
        notifications: loadNotifications,
        acceptance: loadAcceptancePlan,
      };
      const loader = loaders[tab];
      if (!loader) {
        loadedTabs.add(tab);
        return;
      }
      const result = await loader(event);
      if (result !== null)
        loadedTabs.add(tab);
    }
    function openHashTab() {
      const tab = window.location.hash.replace(/^#/, '');
      if (!tab || !document.getElementById(tab)) return;
      openTab(tab);
    }
    const bootstrapParams = new URLSearchParams(window.location.hash.slice(1));
    const bootstrapToken = bootstrapParams.get('token') || '';
    const bootstrapTab = bootstrapParams.get('tab') || '';
    if (bootstrapToken)
      sessionStorage.setItem('memguardos_api_token', bootstrapToken);
    if (bootstrapParams.has('token')) {
      const cleanLocation = window.location.pathname + window.location.search
        + (bootstrapTab && document.getElementById(bootstrapTab) ? '#' + encodeURIComponent(bootstrapTab) : '');
      window.history.replaceState(null, '', cleanLocation);
    }
    window.addEventListener('hashchange', openHashTab);
    const tokenInput = document.getElementById('apiToken');
    tokenInput.value = sessionStorage.getItem('memguardos_api_token') || '';
    tokenInput.addEventListener('change', async () => {
      sessionStorage.setItem('memguardos_api_token', tokenInput.value);
      loadedTabs.clear();
      loadedTabs.add('overview');
      try {
        await loadSessionCapabilities();
        await refresh();
      } catch (err) {
        handleInitialLoadFailure(err);
      }
    });
    function authHeaders(extra) {
      const headers = Object.assign({}, extra || {});
      const token = tokenInput.value.trim();
      if (token) headers['Authorization'] = 'Bearer ' + token;
      return headers;
    }
    function showError(message) {
      const box = document.getElementById('errorBox');
      box.style.display = message ? 'block' : 'none';
      box.textContent = message || '';
    }
    function setOperationStatus(message, state) {
      const box = document.getElementById('operationStatus');
      if (!box) return;
      box.style.display = message ? 'block' : 'none';
      box.className = 'operation-status ' + (state || '');
      box.textContent = message || '';
    }
    function buttonText(button) {
      return button ? button.textContent.replace(/\\s+/g, ' ').trim() : '操作';
    }
    function requestError(message) {
      const error = new Error(String(message || '请求没有完成，请重试。'));
      error.userMessage = error.message;
      return error;
    }
    let operationSequence = 0;
    let activeMutation = null;
    let currentRoleRank = 0;
    const roleRanks = {anonymous:0, viewer:1, operator:2, admin:3};
    function privilegedActionMarkup(label, tab) {
      if (currentRoleRank < roleRanks.admin)
        return '<div class="muted">此操作需要管理员处理。</div>';
      return '<div class="mini-actions"><button class="secondary" onclick="openTab(\\''
        + esc(tab) + '\\')">' + esc(label) + '</button></div>';
    }
    function operationFeedback(message, state) {
      return {operationFeedback:{message:String(message || ''), state:state || 'ok'}};
    }
    async function withBusy(event, work) {
      const operationId = ++operationSequence;
      const button = event && event.currentTarget ? event.currentTarget : null;
      const label = button ? button.textContent : '';
      showError('');
      if (button) {
        button.disabled = true;
        button.setAttribute('aria-busy', 'true');
        button.innerHTML = '<span class="button-label">处理中</span>';
      }
      try {
        setOperationStatus('正在处理：' + buttonText({textContent: label}) + '。请稍候，页面可继续查看其他信息。', 'warn');
        const result = await work();
        if (operationId === operationSequence) {
          const feedback = result && result.operationFeedback;
          setOperationStatus(
            feedback ? feedback.message : '操作完成。页面内容已更新，请查看对应区域的结果摘要。',
            feedback ? feedback.state : 'ok'
          );
        }
        return result;
      } catch (err) {
        const message = err && err.userMessage
          ? err.userMessage
          : '操作没有完成。请重试，或进入系统维护导出报告。';
        if (operationId === operationSequence) {
          showError(message);
          setOperationStatus(message, 'bad');
        }
        return null;
      } finally {
        if (button) {
          button.disabled = false;
          button.removeAttribute('aria-busy');
          button.textContent = label;
        }
      }
    }
    async function withMutation(event, name, work) {
      const button = event && event.currentTarget ? event.currentTarget : null;
      const minimumRole = button ? String(button.dataset.minRole || 'operator') : 'operator';
      if (currentRoleRank < (roleRanks[minimumRole] || roleRanks.operator)) {
        const message = '当前访问权限不足。请在系统维护中填写具有相应权限的访问凭证。';
        showError(message);
        setOperationStatus(message, 'bad');
        return null;
      }
      if (activeMutation) {
        const message = '当前已有一项修改正在处理：' + activeMutation + '。完成后再执行下一项修改。';
        setOperationStatus(message, 'warn');
        return null;
      }
      activeMutation = name;
      try {
        return await withBusy(event, work);
      } finally {
        activeMutation = null;
      }
    }
    async function fetchWithTimeout(url, options, timeoutMs) {
      const controller = new AbortController();
      const timeout = window.setTimeout(() => controller.abort(), timeoutMs || 90000);
      try {
        return await fetch(url, Object.assign({}, options || {}, {signal:controller.signal}));
      } finally {
        window.clearTimeout(timeout);
      }
    }
    async function getJson(url) {
      try {
        const r = await fetchWithTimeout(url, {headers: authHeaders()}, 90000);
        const data = await r.json();
        if (!data || typeof data !== 'object') {
          throw requestError('本地服务返回内容格式异常。请稍后重试，或进入系统维护导出报告。');
        }
        if (!r.ok || data.ok === false) {
          const message = userErrorMessage(data, '请求没有完成。');
          throw requestError(message);
        }
        return data;
      } catch (err) {
        if (err && err.userMessage) throw err;
        const message = err && err.name === 'AbortError'
          ? '操作等待时间过长。请稍后重试，或进入系统维护导出报告。'
          : (err && err.name === 'SyntaxError'
            ? '本地服务返回内容格式异常。请稍后重试，或进入系统维护导出报告。'
            : '无法连接本地服务。请确认 MemGuardOS 正在运行后重试。');
        throw requestError(message);
      }
    }
    async function postJson(url, data) {
      try {
        const r = await fetchWithTimeout(url, {method:'POST', headers:authHeaders({'Content-Type':'application/json'}), body:JSON.stringify(data)}, 180000);
        const payload = await r.json();
        if (!payload || typeof payload !== 'object') {
          throw requestError('本地服务返回内容格式异常。请稍后重试，或进入系统维护导出报告。');
        }
        if (!r.ok || payload.ok === false) {
          const message = userErrorMessage(payload, '请求没有完成。');
          throw requestError(message);
        }
        return payload;
      } catch (err) {
        if (err && err.userMessage) throw err;
        const message = err && err.name === 'AbortError'
          ? '操作等待时间过长。请稍后重试，或进入系统维护导出报告。'
          : (err && err.name === 'SyntaxError'
            ? '本地服务返回内容格式异常。请稍后重试，或进入系统维护导出报告。'
            : '无法连接本地服务。请确认 MemGuardOS 正在运行后重试。');
        throw requestError(message);
      }
    }
    function handleInitialLoadFailure(err) {
      const message = err && err.userMessage
        ? err.userMessage
        : '初始状态没有加载完成。请刷新页面，或进入系统维护导出报告。';
      showError(message);
      setOperationStatus(message, 'bad');
    }
    function userErrorMessage(payload, fallback) {
      const code = String((payload || {}).error || '');
      const messages = {
        unauthorized:'当前操作需要管理员访问凭证。请在系统维护页填写后重试。',
        forbidden:'当前访问权限不足。请在系统维护中填写具有相应权限的访问凭证。',
        forget_preview_required:'请先重新预览影响范围，再确认遗忘。',
        forget_preview_stale:'遗忘条件或候选范围已经变化，请先重新预览影响范围。',
        invalid_json:'请求内容格式不正确。请刷新页面后重试。',
        payload_too_large:'提交内容过大，请缩短内容或拆分后重试。',
        not_found:'没有找到请求的内容，请返回上一步重新选择。',
        method_not_allowed:'当前操作不可用，请刷新页面后重试。'
      };
      return messages[code] || fallback || '操作没有完成，请重试。';
    }
    function applySessionCapabilities(payload) {
      const result = (payload || {}).result || {};
      const role = String(result.role || 'anonymous');
      currentRoleRank = roleRanks[role] || 0;
      document.querySelectorAll('[data-min-role]').forEach(element => {
        const minimum = roleRanks[String(element.dataset.minRole || 'viewer')] || roleRanks.viewer;
        const allowed = currentRoleRank >= minimum;
        element.hidden = !allowed;
        element.setAttribute('aria-hidden', allowed ? 'false' : 'true');
      });
      const active = document.querySelector('section.active');
      const activeButton = active ? document.querySelector('nav button[data-tab="' + active.id + '"]') : null;
      if (activeButton && activeButton.hidden)
        openTab('overview');
    }
    async function loadSessionCapabilities() {
      const payload = await getJson('/api/session');
      applySessionCapabilities(payload);
      return payload;
    }
    function badge(id, state, text) {
      const el = document.getElementById(id);
      el.className = 'badge ' + state;
      el.textContent = text;
    }
    function dot(id, state) {
      const el = document.getElementById(id);
      if (el) el.className = 'status-dot ' + state;
    }
    function sourceLabel(value) {
      const labels = {
        manual:'手动输入',
        dashboard:'本地控制台',
        tool_result:'工具执行结果',
        user_behavior:'用户与 Agent 交互',
        agent_interaction:'用户与 Agent 交互',
        os_event:'系统事件',
        web_replay:'Agent 任务回放',
        agent_event:'Agent 交互',
        evidencebench_v2:'系统验证'
      };
      return labels[String(value || '')] || '其他来源';
    }
    function tierLabel(value) {
      const labels = {working:'临时工作记忆', session:'当前会话记忆', long_term:'长期记忆'};
      return labels[String(value || '')] || '自动管理';
    }
    function sensitivityLabel(value) {
      const labels = {none:'未发现敏感风险', low:'低风险', medium:'中等风险', high:'高风险', critical:'严重风险'};
      return labels[String(value || '').toLowerCase()] || '待确认';
    }
    function matchSourceLabel(value) {
      const labels = {hybrid:'综合匹配', text:'文本匹配', graph:'关联匹配', vector:'语义匹配', exact:'精确匹配'};
      return labels[String(value || '').toLowerCase()] || '综合匹配';
    }
    function selectionReasonLabel(value) {
      const labels = {
        selected_by_hybrid_graph_or_text_search:'与问题内容和关联信息匹配',
        applicable_scoped_preference_for_tool_selection:'与当前工具选择任务和作用范围匹配',
        applicable_scoped_preference_for_planning:'与当前规划任务和作用范围匹配',
        applicable_scoped_preference_for_response_generation:'与当前回答任务和作用范围匹配',
        filtered_by_token_budget:'超过当前上下文长度上限',
        filtered_by_scope:'不属于当前任务的适用范围',
        filtered_by_sensitivity:'因敏感内容策略未纳入'
      };
      const key = String(value || '').trim().toLowerCase().replaceAll(' ', '_');
      return labels[key] || (key ? '根据相关性、作用范围和安全策略处理' : '暂无说明');
    }
    function stageLabel(value) {
      const key = String(value || '');
      const labels = {
        planning:'规划',
        tool_selection:'工具选择',
        response_generation:'回答生成',
        '暂无证据':'暂无使用证据'
      };
      return labels[key] || (key ? '其他使用阶段' : '暂无使用证据');
    }
    function scopeLabel(value) {
      const labels = {global:'全部会话', session:'当前会话', agent:'当前 Agent', project:'当前项目', user:'当前用户', default:'当前记忆空间'};
      return labels[String(value || '')] || '按当前规则';
    }
    function eventRoleLabel(value) {
      const labels = {user:'用户', assistant:'Agent', tool:'工具', system:'系统'};
      return labels[String(value || '')] || '系统';
    }
    function eventTypeLabel(value) {
      const labels = {user_message:'用户消息', assistant_message:'Agent 回复', tool_call:'工具调用', tool_result:'工具结果', system_message:'系统消息'};
      return labels[String(value || '')] || '交互记录';
    }
    function topicLabel(value) {
      const labels = {
        code_search_tool:'代码搜索工具',
        test_runner_command:'测试运行命令',
        service_log_tool:'服务日志工具',
        answer_order:'回答顺序',
        response_order:'回答顺序',
        response_language:'回答语言',
        verbosity:'回答详细度',
        response_verbosity:'回答详细度',
        risk_confirmation:'高风险确认',
        high_risk_confirmation:'高风险确认',
        tool_preference:'工具偏好'
      };
      return labels[String(value || '')] || '其他偏好';
    }
    function memoryTypeLabel(value) {
      const labels = {preference:'偏好记忆', sensitive_policy:'安全策略', knowledge:'知识记忆', procedure:'操作经验', event:'事件记忆', memory:'记忆'};
      return labels[String(value || '')] || '其他记忆';
    }
    function memorySubtypeLabel(value) {
      const labels = {
        general:'通用记忆',
        fact:'事实知识',
        tool_preference:'工具偏好',
        tool_failure_case:'工具失败经验',
        procedural:'操作流程',
        procedural_trace:'操作过程',
        tool_trace:'工具使用记录',
        os_event:'系统事件',
        behavior_risk:'高风险行为',
        behavior_trace:'行为记录',
        security_policy:'安全策略',
        output_style:'回答风格',
        user_habit:'用户习惯',
        template:'模板',
        case:'案例',
        workflow:'工作流程'
      };
      return labels[String(value || '')] || '其他分类';
    }
    function memorySummaryLabel(item) {
      const row = item || {};
      const preferenceSummary = row.preference_topic && row.preference_value
        ? topicLabel(row.preference_topic) + '：' + row.preference_value
        : '';
      return row.summary_for_user || preferenceSummary || row.summary || row.content || row.memory_id || '未命名记忆';
    }
    function renderStatusSummary(status, diagnosticsPayload) {
      const result = status.result || {};
      const diag = diagnosticsPayload || {};
      const policy = diag.memory_policy || {};
      const tiers = policy.tiers || {};
      const items = [
        ['长期记忆', tiers.long_term ? tiers.long_term.count : '-'],
        ['会话记忆', tiers.session ? tiers.session.count : '-'],
        ['工作记忆', tiers.working ? tiers.working.count : '-'],
        ['淘汰候选', Array.isArray(policy.eviction_candidates) ? policy.eviction_candidates.length : '-'],
        ['段文件', policy.storage ? ((policy.storage.segment_bytes || 0) + ' bytes') : '-'],
        ['SQLite', policy.storage ? ((policy.storage.sqlite_bytes || 0) + ' bytes') : '-']
      ];
      document.getElementById('statusCards').innerHTML = items.map(([k,v]) => '<div class="item"><div class="item-title">' + esc(k) + '</div><div class="muted">' + esc(v) + '</div></div>').join('');
      document.getElementById('namespaceCount').textContent = result.namespace || '-';
    }
    function renderSearchCards(payload) {
      const rows = payload.result || [];
      if (!rows.length) {
        document.getElementById('searchCards').innerHTML =
          '<div class="item"><div class="item-title">没有找到相关记忆</div>'
          + '<div class="muted">可以调整关键词或先写入一条记忆，再重新检索。</div></div>';
        return;
      }
      document.getElementById('searchCards').innerHTML = rows.slice(0, 8).map((row, idx) => {
        const sens = row.sensitivity_level || (row.sensitivity && row.sensitivity.level) || 'none';
        return '<div class="item"><div class="item-title">#' + (idx + 1) + ' ' + esc(memorySummaryLabel(row)) + '</div>'
          + '<div class="muted">相关度 ' + esc(row.score) + ' · ' + esc(matchSourceLabel(row.match_source)) + ' · ' + esc(tierLabel(row.tier)) + ' · ' + esc(sensitivityLabel(sens)) + '</div></div>';
      }).join('');
    }
    function renderContextVisual(payload) {
      const result = payload.result || {};
      const budget = result.budget || {};
      const explain = result.explainability || {};
      const pct = Math.min(100, Math.round(((budget.estimated_context_tokens || 0) / Math.max(1, budget.token_budget || 1)) * 100));
      const source = explain.source_distribution || {};
      const tier = explain.tier_distribution || {};
      const trace = explain.selection_trace || [];
      document.getElementById('contextVisual').innerHTML =
        '<div class="grid three">'
        + '<div class="panel"><div class="muted">上下文长度</div><div class="metric">' + esc(budget.estimated_context_tokens || 0) + '</div><div class="progress"><span style="width:' + pct + '%"></span></div><div class="muted">预算 ' + esc(budget.token_budget || '-') + '，使用 ' + pct + '%</div></div>'
        + '<div class="panel"><div class="muted">来源分布</div>' + renderKeyValues(source, sourceLabel) + '</div>'
        + '<div class="panel"><div class="muted">保存时长分布</div>' + renderKeyValues(tier, tierLabel) + '</div></div>'
        + '<div class="trace">' + trace.slice(0, 6).map((item, idx) => '<div class="step"><b>' + (item.included ? '纳入' : '过滤') + ' #' + (idx + 1) + '</b><span class="muted">' + esc(selectionReasonLabel(item.reason || '')) + '</span></div>').join('') + '</div>';
    }
    function renderKeyValues(obj, labeler) {
      const entries = Object.entries(obj || {});
      if (!entries.length) return '<div class="muted">暂无数据</div>';
      return entries.map(([k,v]) => '<div class="health"><span>' + esc(labeler ? labeler(k) : k) + '</span><b>' + esc(v) + '</b></div>').join('');
    }
    let acceptanceItems = [];
    let acceptanceResults = {};
    function resultLabel(status) {
      if (status === 'passed') return '通过';
      if (status === 'failed') return '不通过';
      if (status === 'warning') return '警告';
      if (status === 'manual') return '需人工确认';
      if (status === 'skipped') return '已跳过';
      return '未运行';
    }
    function renderAcceptancePlan(plan) {
      acceptanceItems = (plan.items || []);
      const automatic = acceptanceItems.filter(item => item.mode !== 'manual');
      const manual = acceptanceItems.filter(item => item.mode === 'manual');
      document.getElementById('acceptanceList').innerHTML = automatic.map(item => renderAcceptanceItem(item, false)).join('');
      document.getElementById('acceptanceManualList').innerHTML = manual.map(item => renderAcceptanceItem(item, true)).join('');
      updateAcceptanceSummary();
    }
    function renderAcceptanceItem(item, manual) {
      const result = acceptanceResults[item.id] || {};
      const status = result.status || (manual ? 'manual' : 'idle');
      const badgeClass = status === 'passed' ? 'ok' : (status === 'failed' ? 'bad' : 'warn');
      const action = manual
        ? '<button class="secondary" onclick="markManualAcceptance(event,\\'' + esc(item.id) + '\\',\\'passed\\')">标记通过</button><button class="secondary" onclick="markManualAcceptance(event,\\'' + esc(item.id) + '\\',\\'warning\\')">待复核</button>'
        : '<button class="secondary" data-min-role="admin" onclick="runAcceptanceItem(event,\\'' + esc(item.id) + '\\')">运行检查</button>';
      return '<div class="item acceptance-item" data-acceptance-id="' + esc(item.id) + '">'
        + '<div class="health-title"><span class="status-dot ' + badgeClass + '"></span><span class="item-title">' + esc(item.title) + '</span><span class="badge ' + badgeClass + '">' + esc(resultLabel(status)) + '</span></div>'
        + '<div class="muted">' + esc(item.purpose) + '</div>'
        + '<div class="muted">通过标准：' + esc(item.pass_standard) + '</div>'
        + '<div class="actions">' + action + '</div>'
        + (result.summary ? '<div class="muted" style="margin-top:8px">' + esc(result.summary) + '</div>' : '')
        + '</div>';
    }
    let forgetPreview = null;
    let agentForgetPreview = null;
    const forgetConfirmButton = document.getElementById('forgetConfirmButton');
    const agentForgetConfirmButton = document.getElementById('agentForgetConfirmButton');
    function invalidateForgetPreview() {
      forgetPreview = null;
      forgetConfirmButton.disabled = true;
      forgetConfirmButton.textContent = '确认遗忘并整理存储';
    }
    function invalidateAgentForgetPreview() {
      agentForgetPreview = null;
      agentForgetConfirmButton.disabled = true;
      agentForgetConfirmButton.textContent = '确认遗忘';
    }
    function agentForgetIdentity() {
      return JSON.stringify({
        agent_name:agentForgetName.value,
        preference_topic:agentForgetTopic.value,
        session_id:agentSession.value.trim()
      });
    }
    forgetRequest.addEventListener('input', invalidateForgetPreview);
    agentForgetName.addEventListener('input', invalidateAgentForgetPreview);
    agentForgetTopic.addEventListener('change', invalidateAgentForgetPreview);
    agentSession.addEventListener('input', invalidateAgentForgetPreview);
    function rememberForgetPreview(payload) {
      const result = (payload || {}).result || {};
      const count = Number(result.preview_candidate_count || 0);
      forgetPreview = {
        request:forgetRequest.value,
        previewToken:String(result.preview_token || ''),
        candidateCount:count
      };
      forgetConfirmButton.disabled = !forgetPreview.previewToken || count < 1 || currentRoleRank < roleRanks.admin;
      forgetConfirmButton.textContent = count > 0
        ? '确认遗忘（候选数量：' + count + '）'
        : '没有可遗忘候选';
    }
    function rememberAgentForgetPreview(payload) {
      const result = (payload || {}).result || {};
      const count = Number(result.preview_candidate_count || 0);
      agentForgetPreview = {
        identity:agentForgetIdentity(),
        previewToken:String(result.preview_token || ''),
        candidateCount:count
      };
      agentForgetConfirmButton.disabled = !agentForgetPreview.previewToken || count < 1 || currentRoleRank < roleRanks.admin;
      agentForgetConfirmButton.textContent = count > 0
        ? '确认遗忘（候选数量：' + count + '）'
        : '没有可遗忘候选';
    }
    function updateAcceptanceSummary() {
      const total = acceptanceItems.length;
      const results = Object.values(acceptanceResults);
      const passed = results.filter(item => item.status === 'passed').length;
      const failed = results.filter(item => item.status === 'failed').length;
      const manual = acceptanceItems.filter(item => item.mode === 'manual' && !acceptanceResults[item.id]).length;
      const pct = total ? Math.round((passed / total) * 100) : 0;
      document.getElementById('acceptanceProgress').textContent = passed + '/' + total;
      document.getElementById('acceptanceProgressBar').style.width = pct + '%';
      document.getElementById('acceptanceAdvice').textContent = failed
        ? '存在不通过项目，请先查看详细回执并修复。'
        : (manual ? '还有 ' + manual + ' 项桌面截图需要人工确认。' : '复核项目已完成，可导出报告。');
    }
    async function loadAcceptancePlan(event) {
      await withBusy(event, async () => {
        const payload = await getJson('/api/acceptance-plan');
        renderAcceptancePlan(payload.result || {});
        acceptanceOut.textContent = pretty(payload);
      });
    }
    async function runAcceptanceItem(event, itemId) {
      return withMutation(event, '运行复核检查', async () => {
        const payload = await postJson('/api/acceptance-run', {item_id:itemId});
        if (payload.result) acceptanceResults[itemId] = payload.result;
        renderAcceptancePlan({items: acceptanceItems});
        acceptanceOut.textContent = pretty(payload);
      });
    }
    async function runAcceptanceGroup(event) {
      return withMutation(event, '运行复核检查', async () => {
        if (!acceptanceItems.length) {
          const payload = await getJson('/api/acceptance-plan');
          acceptanceItems = (payload.result || {}).items || [];
        }
        const quickIds = new Set(['A02_DOCTOR', 'A03_SERVICE', 'A04_CGROUP', 'A06_OPENKYLIN_STATUS', 'A13_INGEST_PREF', 'A14_SEARCH_PREF', 'A15_BUILD_CONTEXT', 'A21_VERIFY_AUDIT']);
        const quick = acceptanceItems.filter(item => quickIds.has(item.id));
        const results = [];
        for (const item of quick) {
          const payload = await postJson('/api/acceptance-run', {item_id:item.id});
          if (payload.result) {
            acceptanceResults[item.id] = payload.result;
            results.push(payload.result);
          }
          renderAcceptancePlan({items: acceptanceItems});
        }
        acceptanceOut.textContent = pretty({ok:true, result:{ran:results.length, results:results}});
      });
    }
    function markManualAcceptance(event, itemId, status) {
      acceptanceResults[itemId] = {
        id:itemId,
        status:status,
        ok:status !== 'failed',
        summary:status === 'passed' ? '已由人工确认并保存截图。' : '已标记为待复核，请补充截图或备注。'
      };
      renderAcceptancePlan({items: acceptanceItems});
      if (event && event.currentTarget) event.currentTarget.blur();
    }
    async function exportAcceptanceReport(event) {
      await withBusy(event, async () => {
        const payload = await postJson('/api/acceptance-report', {results:Object.values(acceptanceResults), format:'markdown'});
        acceptanceOut.textContent = (payload.result && payload.result.markdown) || pretty(payload);
      });
    }
    function renderForgetCards(payload, confirmed) {
      const plan = payload.result || {};
      const candidates = plan.candidates || [];
      const candidateHtml = candidates.length ? candidates.map((item, idx) =>
        '<div class="item"><div class="item-title">候选 #' + (idx + 1) + ' · ' + esc(memoryTypeLabel(item.type || 'memory')) + '</div>'
        + '<div class="muted">' + esc(item.summary || item.memory_id) + '</div>'
        + '<div class="muted">匹配依据：' + esc((item.matched_keywords || []).join(', ') || '语义和作用域匹配') + '</div>'
        + '<div class="mini-actions"><button class="secondary" onclick="openMemoryDetail(\\'' + esc(item.memory_id || '') + '\\')">查看记忆</button></div></div>'
      ).join('') : '<div class="item"><div class="item-title">' + (confirmed ? '已执行' : '未发现候选') + '</div><div class="muted">请展开详细记录查看操作回执。</div></div>';
      document.getElementById('forgetCards').innerHTML = candidateHtml + renderVerificationReport(plan.verification_report);
    }
    function renderVerificationReport(report) {
      if (!report) return '';
      const steps = report.verification_steps || [];
      const statusText = {passed:'已通过', failed:'需要处理', skipped:'未执行'};
      const statusClass = {passed:'ok', failed:'bad', skipped:'warn'};
      return '<div class="panel" style="margin-top:12px"><div class="item-title">遗忘验证链</div>'
        + '<p class="muted">' + esc(report.user_summary || '') + '</p>'
        + '<div class="list">' + steps.map(step => '<div class="item"><div class="health"><span>' + esc(step.name) + '</span><span class="badge ' + (statusClass[step.status] || 'warn') + '">' + esc(statusText[step.status] || step.status) + '</span></div><div class="muted">' + esc(step.detail || '') + '</div></div>').join('') + '</div>'
        + '<div class="item" style="margin-top:10px"><div class="item-title">外部边界</div><div class="muted">OpenHands 原始日志、模型服务日志、shell history、虚拟机快照和文件备份不属于 MemGuardOS 受管范围，需要按各自系统单独清理。</div></div></div>';
    }
    function renderAgentOverview(payload) {
      const result = payload.result || {};
      const summary = result.summary || {};
      const metricItems = [
        ['交互事件', summary.event_count || 0],
        ['会话数量', summary.session_count || 0],
        ['工具调用', summary.tool_call_count || 0],
        ['稳定偏好', summary.stable_preference_count || 0]
      ];
      document.getElementById('agentMetrics').innerHTML = metricItems.map(([name,value]) =>
        '<div class="panel"><div class="muted">' + esc(name) + '</div><div class="metric">' + esc(value) + '</div></div>'
      ).join('');
      const timeline = result.timeline || [];
      document.getElementById('agentTimeline').innerHTML = timeline.length ? timeline.map(item => {
        const classes = ['timeline-row'];
        if (item.event_type === 'tool_call' || item.event_type === 'tool_result') classes.push('tool');
        if (item.forgotten) classes.push('forgotten');
        const body = item.forgotten ? '该事件载荷已执行遗忘，仅保留哈希和审计事实。'
          : (item.content || item.tool_result || (item.tool_args && Object.keys(item.tool_args).length ? pretty(item.tool_args) : '无文本载荷'));
        return '<div class="' + classes.join(' ') + '"><span class="timeline-dot"></span>'
          + '<div class="timeline-meta">第 ' + esc(item.sequence) + ' 步 · ' + esc(eventRoleLabel(item.role)) + ' · ' + esc(eventTypeLabel(item.event_type)) + '</div>'
          + '<div class="timeline-body">' + esc(body) + '</div></div>';
      }).join('') : '<div class="muted">当前筛选范围内没有 Agent 交互事件。</div>';
      const preferences = result.preference_aggregates || [];
      document.getElementById('agentPreferences').innerHTML = preferences.length ? preferences.map(item =>
        '<div class="item"><div class="item-title">' + esc(topicLabel(item.topic)) + '：' + esc(item.value) + '</div>'
        + '<div class="muted">适用范围：' + esc(scopeLabel(item.preference_scope)) + ' · 依据 ' + esc(item.evidence_count) + ' 条 · 来自 ' + esc(item.distinct_sessions) + ' 个会话 · ' + (item.stable ? '已稳定' : '待观察') + '</div>'
        + (item.promoted_memory_id ? '<div class="actions"><button class="secondary" onclick="loadPreferenceEvidence(event,\\'' + esc(item.promoted_memory_id) + '\\')">查看依据</button></div>' : '')
        + '</div>'
      ).join('') : '<div class="muted">尚未形成偏好候选。</div>';
      const labels = {planning:'规划', tool_selection:'工具选择', response_generation:'回答生成'};
      const contexts = result.contexts || {};
      document.getElementById('agentContexts').innerHTML = ['planning','tool_selection','response_generation'].map(stage => {
        const value = contexts[stage] || {};
        const memories = value.memories || [];
        return '<div class="stage-column"><h3>' + labels[stage] + '</h3><div class="muted">' + memories.length + ' 条记忆 · 上下文长度 ' + esc((value.budget || {}).estimated_context_tokens || 0) + '</div>'
          + (memories.length ? memories.map(item => '<div class="item" style="margin-top:8px"><b>' + esc(topicLabel(item.preference_topic || item.subtype || item.type)) + '</b><div class="muted">' + esc(selectionReasonLabel(item.selection_reason || '')) + '</div></div>').join('') : '<div class="muted" style="margin-top:8px">无适用记忆</div>')
          + '</div>';
      }).join('');
      renderPreferenceProofs(result.preference_effect_proofs || []);
    }
    function renderPreferenceProofs(proofs) {
      const box = document.getElementById('preferenceProofs');
      if (!box) return;
      if (!proofs.length) {
        box.innerHTML = '<div class="item"><div class="item-title">暂无可验证偏好</div><div class="muted">先运行回放任务或接入真实 Agent，再查看工具和回答是否遵循偏好。</div></div>';
        return;
      }
      const labels = {passed:'已观察到并通过', failed:'未遵循', not_applicable:'暂无实际行为证据'};
      const classes = {passed:'ok', failed:'bad', not_applicable:'warn'};
      box.innerHTML = proofs.map(proof => '<div class="item"><div class="health"><div class="item-title">' + esc(topicLabel((proof.preference || {}).topic || '')) + '：' + esc((proof.preference || {}).value || '') + '</div><span class="badge ' + (classes[proof.compliance] || 'warn') + '">' + esc(labels[proof.compliance] || proof.compliance) + '</span></div>'
        + '<div class="muted">使用阶段：' + esc(stageLabel(proof.stage)) + ' · 已提供相关记忆：' + (proof.injected ? '是' : '否') + '</div>'
        + '<div class="muted">实际行为：' + esc((proof.actual_behavior || {}).command || (proof.actual_behavior || {}).response || '尚未观察到') + '</div>'
        + (proof.failure_reason ? '<div class="warning">' + esc(proof.failure_reason) + '</div>' : '') + '</div>').join('');
    }
    async function loadAgentOverview(event) {
      await withBusy(event, async () => {
        const url = '/api/agent-overview?session_id=' + encodeURIComponent(agentSession.value)
          + '&task=' + encodeURIComponent(agentTask.value) + '&limit=100';
        const payload = await getJson(url);
        renderAgentOverview(payload);
        agentOut.textContent = pretty(payload);
      });
    }
    async function loadPreferenceEvidence(event, memoryId) {
      await withBusy(event, async () => {
        agentOut.textContent = pretty(await getJson('/api/agent-evidence?memory_id=' + encodeURIComponent(memoryId)));
      });
    }
    async function runAgentTask(event) {
      return withMutation(event, '运行 Agent 任务', async () => {
        const payload = await postJson('/api/agent-run', {
          task: document.getElementById('agentTask').value,
          mode: document.getElementById('agentMode').value,
          session_id: document.getElementById('agentSession').value
        });
        const result = payload.result || {};
        if (result.session_id) document.getElementById('agentSession').value = result.session_id;
        document.getElementById('agentOut').textContent = pretty(payload);
        if (result.truthful_note) setOperationStatus(result.truthful_note, result.ok ? 'ok' : 'warn');
        if (result.preference_effect_proofs) renderPreferenceProofs(result.preference_effect_proofs);
        if (result.ok) await loadAgentOverview(null);
        await loadFeedbackHome(null);
      });
    }
    async function runJourney(event) {
      document.getElementById('agentTask').value = '查找 MemGuardService 的定义并说明如何验证记忆遗忘';
      document.getElementById('agentMode').value = 'replay';
      openTab('agent');
      await runAgentTask(event);
    }
    async function loadFeedbackHome(event) {
      const work = async () => {
        const payload = await getJson('/api/feedback-home?limit=8');
        const result = payload.result || {};
        const memories = result.recent_memories || [];
        document.getElementById('recentMemoriesList').innerHTML = memories.length ? memories.map(item => '<div class="item"><div class="item-title">' + esc(memorySummaryLabel(item)) + '</div><div class="muted">' + esc(memoryTypeLabel(item.type || 'memory')) + ' · ' + esc(tierLabel(item.tier || '')) + '</div><div class="mini-actions"><button class="secondary" onclick="openMemoryDetail(\\'' + esc(item.memory_id || '') + '\\')">查看详情</button></div></div>').join('') : '<div class="muted">还没有新的可展示记忆。</div>';
        renderNotifications(result.notifications || [], 'homeNotifications');
      };
      if (event) return withBusy(event, work);
      return work();
    }
    function renderNotifications(items, targetId) {
      const box = document.getElementById(targetId || 'notificationList');
      if (!box) return;
      if (!items.length) {
        box.innerHTML = '<div class="item"><div class="item-title">暂无需要关注的变化</div><div class="muted">重要记忆变化、风险阻断和遗忘结果会显示在这里。</div></div>';
        return;
      }
      const labels = {critical:'需要处理', warning:'建议确认', info:'已完成'};
      const classes = {critical:'bad', warning:'warn', info:'ok'};
      box.innerHTML = items.map(item => {
        const action = String(item.action || '');
        const targetRequiresAdmin = action.indexOf('记录') >= 0
          || (!action.includes('记忆') && !action.includes('Agent') && !action.includes('遗忘'));
        const actionMarkup = targetRequiresAdmin && currentRoleRank < roleRanks.admin
          ? '<div class="muted">此操作需要管理员处理。</div>'
          : '<div class="mini-actions"><button class="secondary" onclick="openNotificationTarget(\\''
            + esc(action) + '\\')">查看处理</button></div>';
        return '<div class="item"><div class="health"><div class="item-title">' + esc(item.title || '')
          + '</div><span class="badge ' + (classes[item.level] || '') + '">' + esc(labels[item.level] || item.level)
          + '</span></div><div class="muted">' + esc(item.summary || '') + '</div>' + actionMarkup + '</div>';
      }).join('');
    }
    function openNotificationTarget(action) {
      if (action.indexOf('记忆') >= 0) openTab('memory-detail');
      else if (action.indexOf('Agent') >= 0) openTab('agent');
      else if (action.indexOf('遗忘') >= 0) openTab('forget');
      else if (action.indexOf('记录') >= 0) openTab('audit');
      else openTab('diagnostics');
    }
    async function loadNotifications(event) {
      await withBusy(event, async () => {
        const payload = await getJson('/api/notifications?limit=50');
        renderNotifications((payload.result || []), 'notificationList');
        document.getElementById('notificationOut').textContent = pretty(payload);
      });
    }
    async function loadPreferences(event) {
      await withBusy(event, async () => {
        const payload = await getJson('/api/feedback-home?limit=30');
        const items = (payload.result || {}).current_preferences || [];
        const box = document.getElementById('preferenceList');
        box.innerHTML = items.length ? items.map(item => '<div class="item"><div class="health"><div class="item-title">' + esc(topicLabel(item.topic)) + '：' + esc(item.value || '') + '</div><span class="badge ' + (item.stable ? 'ok' : 'warn') + '">' + (item.stable ? '稳定' : '候选') + '</span></div><div class="muted">适用范围：' + esc(scopeLabel(item.scope || 'global')) + '</div><div class="mini-actions">' + (item.memory_id ? '<button class="secondary" onclick="loadPreferenceProof(event,\\'' + esc(item.memory_id) + '\\')">查看生效证明</button><button class="secondary" onclick="openMemoryDetail(\\'' + esc(item.memory_id) + '\\')">查看记忆</button>' : '<span class="muted">暂无对应记忆编号</span>') + '</div></div>').join('') : '<div class="item"><div class="item-title">暂无稳定偏好</div><div class="muted">通过 Agent 工作台运行任务后，这里会显示可复用偏好。</div></div>';
        document.getElementById('preferenceOut').textContent = pretty(payload);
      });
    }
    async function loadPreferenceProof(event, memoryId) {
      await withBusy(event, async () => {
        const url = '/api/preference-proof?memory_id=' + encodeURIComponent(memoryId) + '&task=' + encodeURIComponent(document.getElementById('proofTask').value) + '&stage=' + encodeURIComponent(document.getElementById('proofStage').value);
        const payload = await getJson(url);
        document.getElementById('preferenceOut').textContent = pretty(payload);
        renderPreferenceProofs((payload.result || {}).proof ? [(payload.result || {}).proof] : []);
        openTab('agent');
      });
    }
    function openMemoryDetail(memoryId) {
      document.getElementById('memoryDetailId').value = memoryId || '';
      openTab('memory-detail');
      loadMemoryDetail(null);
    }
    async function loadMemoryDetail(event) {
      const id = document.getElementById('memoryDetailId').value.trim();
      if (!id) {
        setOperationStatus('请先选择一条记忆。', 'warn');
        return null;
      }
      await withBusy(event, async () => {
        const payload = await getJson('/api/memory-detail?memory_id=' + encodeURIComponent(id));
        const result = payload.result || {};
        const memory = result.memory || {};
        document.getElementById('memoryDetailCards').innerHTML = result.found ? '<div class="item"><div class="item-title">' + esc(memorySummaryLabel(memory)) + '</div><div class="muted">' + esc(memoryTypeLabel(memory.type || 'memory')) + ' · ' + esc(memorySubtypeLabel(memory.subtype || memory.type)) + ' · ' + esc(tierLabel(memory.tier || '')) + '</div><div class="muted">适用范围：' + esc(scopeLabel(memory.preference_scope || memory.scope || '')) + ' · 来源：' + esc(sourceLabel(memory.source_kind || (memory.source || {}).kind || '')) + '</div></div><div class="grid two"><div class="panel"><div class="item-title">来源与版本</div><div class="muted">来源事件 ' + esc((result.source_events || []).length) + ' 条 · 版本 ' + esc((result.versions || []).length) + ' 个</div><div class="muted">最近使用阶段：' + esc(stageLabel((result.agent_usage || {}).last_stage || '')) + '</div></div><div class="panel"><div class="item-title">关联信息</div><div class="muted">实体 ' + esc(((result.graph || {}).entities || []).length) + ' 个 · 关系 ' + esc(((result.graph || {}).relations || []).length) + ' 条</div></div></div>' : '<div class="item"><div class="item-title">没有找到这条记忆</div><div class="muted">请从首页最近学到的内容或检索结果中重新选择。</div></div>';
        document.getElementById('memoryDetailOut').textContent = pretty(payload);
      });
    }
    async function forgetMemoryFromDetail(event) {
      const id = document.getElementById('memoryDetailId').value.trim();
      if (!id || !window.confirm('确认遗忘这条记忆？系统会先执行受管记录验证，并保留外部日志边界说明。')) return;
      return withMutation(event, '遗忘记忆', async () => {
        const payload = await postJson('/api/forget-memory', {memory_ids:[id], reason:'用户从记忆详情发起遗忘', compact:true});
        document.getElementById('memoryDetailOut').textContent = pretty(payload);
        setOperationStatus(payload.ok ? '这条记忆的遗忘验证已完成。' : '遗忘未完全完成，请查看验证报告。', payload.ok ? 'ok' : 'bad');
        await loadFeedbackHome(null);
      });
    }
    async function agentForget(event, confirmForget) {
      if (confirmForget && (
        !agentForgetPreview
        || agentForgetPreview.identity !== agentForgetIdentity()
        || !agentForgetPreview.previewToken
      )) {
        setOperationStatus('遗忘条件已经变化，请先重新预览影响范围。', 'warn');
        return null;
      }
      if (confirmForget && !window.confirm(
        '确认遗忘本次预览的 ' + agentForgetPreview.candidateCount
        + ' 项交互记录或记忆？外部 Agent 持久化副本需单独处理。'
      )) return null;
      return withMutation(event, 'Agent 交互遗忘', async () => {
        const filters = {agent_name:agentForgetName.value, preference_topic:agentForgetTopic.value};
        if (agentSession.value.trim()) filters.session_id = agentSession.value.trim();
        const payload = await postJson('/api/agent-forget', {
          filters:filters,
          confirm:confirmForget,
          compact:true,
          external_persistence:true,
          preview_token:confirmForget ? agentForgetPreview.previewToken : ''
        });
        const affected = (payload.result || {}).affected || {};
        const residue = (payload.result || {}).external_residue || {};
        document.getElementById('agentForgetImpact').innerHTML =
          '<div class="item"><div class="item-title">影响范围</div><div class="muted">事件 ' + esc((affected.event_ids || []).length)
          + ' · 记忆 ' + esc((affected.memory_ids || []).length) + ' · 观察 ' + esc(affected.observation_count || 0)
          + ' · 历史上下文使用 ' + esc(affected.context_usage_count || 0) + '</div></div>'
          + '<div class="item"><div class="item-title">外部残留</div><div class="muted">' + esc(residue.explanation || '未报告') + '</div></div>';
        agentOut.textContent = pretty(payload);
        if (confirmForget) {
          invalidateAgentForgetPreview();
          await loadAgentOverview(null);
        } else {
          rememberAgentForgetPreview(payload);
        }
        return operationFeedback(
          confirmForget ? 'Agent 交互遗忘已经完成。' : 'Agent 遗忘影响范围已生成，请核对后再确认。',
          'ok'
        );
      });
    }
    function renderStatusPayload(status) {
      document.getElementById('overviewOut').textContent = pretty(status);
      const result = status.result || {};
      document.getElementById('namespaceMeta').textContent = '当前资料库：' + (result.namespace || '-');
      document.getElementById('liveCount').textContent = result.live_record_count ?? '-';
      document.getElementById('auditCount').textContent = result.audit_event_count ?? '-';
      document.getElementById('dataLocation').textContent = '仅保存在本机';
      document.getElementById('healthBadge').textContent = status.ok ? '运行正常' : '状态异常';
      document.getElementById('healthBadge').className = 'badge ' + (status.ok ? 'ok' : 'bad');
      document.getElementById('lastUpdated').textContent = '状态已刷新';
    }
    function renderDiagnosticsPayload(diagResp, status) {
      const diag = diagResp.result || {};
      badge('auditBadge', diag.audit_chain && diag.audit_chain.ok ? 'ok' : 'bad', diag.audit_chain && diag.audit_chain.ok ? '通过' : '异常');
      dot('auditDot', diag.audit_chain && diag.audit_chain.ok ? 'ok' : 'bad');
      const retrieval = diag.retrieval_status || {};
      badge('retrievalBadge', retrieval.requested_backend ? 'ok' : 'warn', retrievalModeLabel(retrieval.last_backend_used || retrieval.requested_backend));
      dot('retrievalDot', retrieval.requested_backend ? 'ok' : 'warn');
      const sdk = diag.kylin_sdk || {};
      const kylinInfo = sdk.kylin || {};
      const sdkAvailable = Boolean(kylinInfo.available || kylinInfo.embedding_available || kylinInfo.vector_store_available);
      const sdkLabel = sdkAvailable ? '本机增强可用' : retrievalModeLabel(sdk.last_backend_used);
      badge('sdkBadge', sdkAvailable ? 'ok' : 'warn', sdkLabel);
      dot('sdkDot', sdkAvailable ? 'ok' : 'warn');
      const runtime = diag.runtime_status || {};
      badge('osBadge', runtime.cgroup_v2 ? 'ok' : 'warn', runtime.cgroup_v2 ? '资源限制正常' : '部分可用');
      dot('osDot', runtime.cgroup_v2 ? 'ok' : 'warn');
      renderDeliveryReadiness(diag, retrieval, runtime, sdkAvailable);
      renderStatusSummary(status || {ok:true, result:{}}, diag);
      renderRefreshWarnings(diag, retrieval, runtime, sdkAvailable);
    }
    async function refresh(event, options) {
      if (event) return withBusy(event, async () => refresh(null, options));
      const settings = options || {};
      const settled = await Promise.allSettled([
        getJson('/api/status'),
        getJson('/api/diagnostics'),
        loadFeedbackHome(null),
      ]);
      const statusResult = settled[0];
      const diagnosticsResult = settled[1];
      if (statusResult.status === 'fulfilled')
        renderStatusPayload(statusResult.value);
      if (diagnosticsResult.status === 'fulfilled')
        renderDiagnosticsPayload(
          diagnosticsResult.value,
          statusResult.status === 'fulfilled' ? statusResult.value : null
        );

      const coreFailures = [statusResult, diagnosticsResult].filter(item => item.status === 'rejected');
      if (coreFailures.length === 2)
        throw coreFailures[0].reason;

      const partial = settled.some(item => item.status === 'rejected');
      if (partial && !settings.silent)
        setOperationStatus('基础状态已显示，但部分信息暂未更新。可稍后重试，不影响已完成的操作。', 'warn');
      return operationFeedback(
        partial ? '基础状态已显示，但部分信息暂未更新。' : '状态已刷新。',
        partial ? 'warn' : 'ok'
      );
    }
    async function refreshAfterMutation(successMessage) {
      try {
        const result = await refresh(null, {silent:true});
        if (result && result.operationFeedback && result.operationFeedback.state === 'warn')
          return operationFeedback(successMessage + '，但部分概览暂未更新。', 'warn');
      } catch (err) {
        return operationFeedback(successMessage + '，但部分概览暂未更新。', 'warn');
      }
      return operationFeedback(successMessage + '。', 'ok');
    }
    function retrievalModeLabel(backend) {
      const value = String(backend || '').toLowerCase();
      if (value.includes('kylin') || value.includes('coreai') || value.includes('vector')) return '本机增强';
      if (value.includes('hash') || value.includes('sqlite') || value.includes('fts')) return '基础检索';
      return value ? '自动选择' : '待确认';
    }
    function renderRefreshWarnings(diag, retrieval, runtime, sdkAvailable) {
      const warnings = [];
      if (!(diag.audit_chain && diag.audit_chain.ok)) warnings.push('操作记录完整性需要复核。');
      if (!(retrieval.requested_backend || retrieval.last_backend_used)) warnings.push('检索方式尚未确认。');
      if (!sdkAvailable) warnings.push('本机智能检索当前使用稳定降级，不影响基础检索。');
      const box = document.getElementById('refreshWarnings');
      if (!warnings.length) {
        box.style.display = 'none';
        box.innerHTML = '';
        return;
      }
      box.style.display = 'block';
      box.innerHTML = '<div class="item-title">需要关注</div>'
        + warnings.map(item => '<div class="item"><div class="muted">' + esc(item) + '</div>'
          + privilegedActionMarkup('跳转处理', 'diagnostics') + '</div>').join('');
    }
    function renderDeliveryReadiness(diag, retrieval, runtime, sdkAvailable) {
      const checks = [
        {name:'操作记录完整性', ok: Boolean(diag.audit_chain && diag.audit_chain.ok), advice:'先进入系统维护，点击“校验操作记录”；仍异常时导出系统报告交给管理员。', tab:'diagnostics', label:'跳转处理'},
        {name:'检索方式', ok: Boolean(retrieval.requested_backend || retrieval.last_backend_used), advice:'进入系统维护点击“检查检索方式”，确认当前使用本机检索或自动降级。', tab:'diagnostics', label:'跳转处理'},
        {name:'OS 资源', ok: Boolean(runtime.platform || runtime.cgroup_v2), advice:'进入系统维护查看聚合系统状态；如果只在普通桌面使用，部分资源信息不可见不影响基础记忆功能。', tab:'diagnostics', label:'跳转处理'},
        {name:'系统报告', ok: true, advice:'需要交付或排障时进入系统维护导出系统报告。', tab:'diagnostics', label:'跳转处理'}
      ];
      if (!sdkAvailable) {
        checks.push({name:'本机智能检索', ok:true, advice:'本机智能检索不可用时会稳定使用基础检索；openKylin 上可在系统维护中重新检查。', tab:'diagnostics', label:'跳转处理'});
      }
      const passed = checks.filter(item => item.ok).length;
      const score = Math.round((passed / checks.length) * 100);
      document.getElementById('readinessProgress').style.width = score + '%';
      document.getElementById('readinessText').textContent = '当前就绪度 ' + score + '% · ' + passed + '/' + checks.length + ' 项满足';
      const advice = checks.filter(item => !item.ok);
      if (!advice.length) {
        document.getElementById('riskAdvice').innerHTML =
          '<div class="item"><div class="item-title">当前没有需要立即处理的风险</div>'
          + '<div class="muted">所有基础检查均已满足。需要交付或排障时，可从系统维护导出系统报告。</div></div>';
        return;
      }
      document.getElementById('riskAdvice').innerHTML = advice.map(item =>
        '<div class="item"><div class="item-title">处理建议 · ' + esc(item.name)
        + '</div><div class="muted">' + esc(item.advice) + '</div>'
        + privilegedActionMarkup(item.label || '跳转处理', item.tab || 'diagnostics') + '</div>'
      ).join('');
    }
    async function ingest(event) {
      return withMutation(event, '保存记忆', async () => {
        const data = {text: ingestText.value, source_kind: sourceKind.value, tier: tier.value};
        const payload = await postJson('/api/ingest', data);
        ingestOut.textContent = pretty(payload);
        renderIngestFeedback(payload);
        return refreshAfterMutation('保存已经完成');
      });
    }
    function renderIngestFeedback(payload) {
      const box = document.getElementById('ingestFeedback');
      const result = payload.result || {};
      const feedback = result.feedback || {};
      const notice = result.sensitive_notice || {};
      if (notice.user_summary) {
        box.innerHTML = '<div class="item"><div class="health"><div class="item-title">'
          + esc(notice.action === 'blocked' ? '敏感内容已保护' : '内容已脱敏')
          + '</div><span class="badge bad">未进入长期记忆</span></div><div class="muted">'
          + esc(notice.user_summary) + '</div>'
          + privilegedActionMarkup('查看操作记录', 'audit') + '</div>';
        return;
      }
      const item = (feedback.new_memories || [])[0] || (feedback.updated_memories || [])[0];
      if (!item) {
        box.innerHTML = '<div class="item"><div class="item-title">本次没有形成可保存记忆</div><div class="muted">' + esc(((feedback.skipped_items || [])[0] || {}).summary_for_user || '请检查输入内容和保存策略。') + '</div></div>';
        return;
      }
      box.innerHTML = '<div class="item"><div class="health"><div class="item-title">已学到：' + esc(item.summary_for_user) + '</div><span class="badge ok">已保存</span></div><div class="muted">为什么保存：' + esc(item.why_extracted) + '</div><div class="muted">适用范围：' + esc((item.where_it_will_apply || []).join('、')) + '</div><div class="mini-actions"><button class="secondary" onclick="openMemoryDetail(\\'' + esc(item.memory_id || '') + '\\')">查看详情</button><button class="secondary" onclick="loadPreferenceProof(event,\\'' + esc(item.memory_id || '') + '\\')">测试生效</button></div></div>';
    }
    async function explainExtraction(event) {
      await withBusy(event, async () => {
        const url = '/api/explain-extraction?text=' + encodeURIComponent(ingestText.value) + '&source_kind=' + encodeURIComponent(sourceKind.value);
        ingestOut.textContent = pretty(await getJson(url));
      });
    }
    async function search(event) {
      await withBusy(event, async () => {
        const payload = await getJson('/api/search?q=' + encodeURIComponent(searchQuery.value) + '&top_k=5');
        renderSearchCards(payload);
        searchOut.textContent = pretty(payload);
      });
    }
    async function graphSearch(event) {
      await withBusy(event, async () => {
        const payload = await getJson('/api/hierarchical-graph?q=' + encodeURIComponent(searchQuery.value) + '&limit=8');
        searchOut.textContent = pretty(payload);
      });
    }
    async function contextBuild(event) {
      await withBusy(event, async () => {
        const payload = await getJson('/api/context?q=' + encodeURIComponent(contextQuery.value) + '&token_budget=' + encodeURIComponent(tokenBudget.value));
        renderContextVisual(payload);
        contextOut.textContent = pretty(payload);
      });
    }
    async function forget(event, confirmForget) {
      if (confirmForget && (
        !forgetPreview
        || forgetPreview.request !== forgetRequest.value
        || !forgetPreview.previewToken
      )) {
        setOperationStatus('遗忘请求已经变化，请先重新预览影响范围。', 'warn');
        return null;
      }
      if (confirmForget && !window.confirm(
        '确认遗忘本次预览的 ' + forgetPreview.candidateCount
        + ' 条候选记忆并整理存储文件？'
      )) return null;
      return withMutation(event, '精准遗忘', async () => {
        const payload = await postJson('/api/forget', {
          request: forgetRequest.value,
          confirm: confirmForget,
          compact: confirmForget,
          preview_token:confirmForget ? forgetPreview.previewToken : ''
        });
        renderForgetCards(payload, confirmForget);
        forgetOut.textContent = pretty(payload);
        if (confirmForget) {
          invalidateForgetPreview();
          return refreshAfterMutation('遗忘已经完成');
        }
        rememberForgetPreview(payload);
        return operationFeedback('遗忘范围预览已生成。', 'ok');
      });
    }
    async function audit(event) {
      await withBusy(event, async () => { auditOut.textContent = pretty(await getJson('/api/audit?limit=30')); });
    }
    async function verifyAudit(event) {
      await withBusy(event, async () => {
        diagnosticsOut.textContent = pretty(await getJson('/api/verify-audit'));
        return refreshAfterMutation('校验已经完成');
      });
    }
    async function retrievalStatus(event) {
      await withBusy(event, async () => { diagnosticsOut.textContent = pretty(await getJson('/api/retrieval-status')); });
    }
    async function memoryPolicy(event) {
      await withBusy(event, async () => { diagnosticsOut.textContent = pretty(await getJson('/api/memory-policy')); });
    }
    async function diagnostics(event) {
      await withBusy(event, async () => {
        const payload = await getJson('/api/diagnostics');
        diagnosticsOut.textContent = pretty(payload);
      });
    }
    async function exportDiagnostics(event) {
      return withBusy(event, async () => {
        const response = await fetchWithTimeout('/api/export-diagnostics', {headers: authHeaders()}, 90000);
        if (!response.ok) {
          let payload = {};
          try {
            payload = await response.json();
          } catch (_err) {
            payload = {};
          }
          throw requestError(userErrorMessage(payload, '系统报告没有生成，请重试。'));
        }
        const blob = await response.blob();
        const disposition = response.headers.get('Content-Disposition') || '';
        const match = disposition.match(/filename="?([^";]+)"?/i);
        const filename = match && match[1] ? match[1] : 'memguardos-diagnostics.json';
        const objectUrl = URL.createObjectURL(blob);
        const link = document.createElement('a');
        link.href = objectUrl;
        link.download = filename;
        document.body.appendChild(link);
        link.click();
        link.remove();
        window.setTimeout(() => URL.revokeObjectURL(objectUrl), 0);
        return operationFeedback('系统报告已生成，浏览器已开始下载。', 'ok');
      });
    }
    openHashTab();
    loadSessionCapabilities().then(() => refresh()).catch(handleInitialLoadFailure);
  </script>
</body>
</html>"""
