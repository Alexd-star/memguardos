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

from memguard import __version__
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

    server_version = f"MemGuardOSDashboard/{__version__}"

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
        if parsed.path.startswith("/api/") and not self._require_role("viewer"):
            return
        if parsed.path == "/api/status":
            self._send_api({"command": "namespace_status"})
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
        if parsed.path == "/api/memory-decay":
            params = parse_qs(parsed.query)
            self._send_api({
                "command": "decay_plan",
                "as_of": params.get("as_of", [""])[0],
                "max_age_days": _safe_int(params.get("max_age_days", ["30"])[0], 30),
                "score_threshold": params.get("score_threshold", ["0.35"])[0],
                "limit": _safe_int(params.get("limit", ["200"])[0], 200),
            })
            return
        if parsed.path == "/api/memory-budget":
            params = parse_qs(parsed.query)
            self._send_api({
                "command": "budget_plan",
                "target_bytes": _safe_int(params.get("target_bytes", ["1048576"])[0], 1048576),
                "as_of": params.get("as_of", [""])[0],
                "limit": _safe_int(params.get("limit", ["1000"])[0], 1000),
            })
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
        if parsed.path == "/api/agent-action-impact":
            params = parse_qs(parsed.query, keep_blank_values=True)
            self._send_api({
                "command": "action_impact_report",
                "memory_id": params.get("memory_id", [""])[0],
                "agent_name": params.get("agent_name", [""])[0],
                "project_root": params.get("project_root", [""])[0],
                "task_type": params.get("task_type", [""])[0],
                "limit": _safe_int(params.get("limit", ["5000"])[0], 5000),
            })
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
        if parsed.path == "/api/value-evidence":
            service = self._service()
            try:
                result = service.memory_value_evidence()
            finally:
                service.close()
            self._send_json({"ok": True, "result": result})
            return
        if parsed.path == "/api/value-comparison":
            params = parse_qs(parsed.query)
            self._send_api(
                {
                    "command": "memory_value_comparison",
                    "task": params.get("task", [""])[0],
                    "agent_name": params.get("agent_name", ["openhands"])[0],
                    "session_id": params.get("session_id", [""])[0],
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
                    "light": params.get("light", ["0"])[0] in {"1", "true", "yes"},
                }
            )
            return
        if parsed.path == "/api/memory-lifecycle":
            params = parse_qs(parsed.query)
            self._send_api(
                {
                    "command": "lifecycle_snapshot",
                    "memory_id": params.get("memory_id", [""])[0],
                    "include_events": params.get("include_events", ["1"])[0] in {"1", "true", "yes"},
                }
            )
            return
        if parsed.path == "/api/lifecycle-overview":
            params = parse_qs(parsed.query)
            self._send_api(
                {
                    "command": "lifecycle_overview",
                    "limit": _safe_int(params.get("limit", ["500"])[0], 500),
                }
            )
            return
        if parsed.path == "/api/fact-conflicts":
            params = parse_qs(parsed.query)
            self._send_api(
                {
                    "command": "fact_conflicts",
                    "status": params.get("status", ["pending"])[0],
                    "limit": _safe_int(params.get("limit", ["100"])[0], 100),
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
        if parsed.path == "/api/export-evidence":
            self._send_json_attachment(
                self._evidence_payload(),
                filename="memguardos-local-evidence.json",
            )
            return
        if parsed.path == "/api/search":
            params = parse_qs(parsed.query, keep_blank_values=True)
            scope = {"project_root": params["project_root"][0]} if "project_root" in params else {}
            query = params.get("q", [""])[0]
            limit = _safe_int(params.get("top_k", ["5"])[0], 5)
            self._send_api({"command": "search", "query": query, "limit": limit, "session_id": params.get("session_id", [""])[0], **scope})
            return
        if parsed.path == "/api/context":
            params = parse_qs(parsed.query, keep_blank_values=True)
            scope = {"project_root": params["project_root"][0]} if "project_root" in params else {}
            query = params.get("q", [""])[0]
            budget = _safe_int(params.get("token_budget", ["1200"])[0], 1200)
            self._send_api({"command": "build_context", "query": query, "limit": 5, "token_budget": budget, "session_id": params.get("session_id", [""])[0], **scope})
            return
        if parsed.path == "/api/hierarchical-graph":
            params = parse_qs(parsed.query, keep_blank_values=True)
            scope = {"project_root": params["project_root"][0]} if "project_root" in params else {}
            entity = params.get("q", [""])[0]
            limit = _safe_int(params.get("limit", ["8"])[0], 8)
            self._send_api({"command": "hierarchical_graph_search", "entity": entity, "limit": limit, "session_id": params.get("session_id", [""])[0], **scope})
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
            "/api/evict-session": "admin",
            "/api/agent-run": "operator",
            "/api/journey": "operator",
            "/api/forget-memory": "admin",
            "/api/forget": "operator",
            "/api/agent-forget": "operator",
            "/api/resolve-fact-conflict": "admin",
            "/api/governance-review": "admin",
            "/api/apply-decay": "admin",
            "/api/apply-budget": "admin",
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
                    "natural": bool(payload.get("natural", True)),
                    "session_id": str(payload.get("session_id", "")),
                    **({"project_root": payload["project_root"]} if "project_root" in payload else {}),
                }
            )
            return
        if parsed.path == "/api/evict-session":
            self._send_api({
                "command": "evict_session", "session_id": str(payload.get("session_id", "")),
                "confirm": bool(payload.get("confirm", False)),
                "expected_versions": payload.get("expected_versions"),
            })
            return
        if parsed.path == "/api/apply-decay":
            self._send_api({
                "command": "apply_decay",
                "plan": payload.get("plan"),
                "selected_memory_ids": payload.get("selected_memory_ids"),
                "target_session_id": str(payload.get("target_session_id", "")),
                "confirm": bool(payload.get("confirm", False)),
            })
            return
        if parsed.path == "/api/apply-budget":
            self._send_api({
                "command": "apply_budget",
                "plan": payload.get("plan"),
                "selected_memory_ids": payload.get("selected_memory_ids"),
                "target_session_id": str(payload.get("target_session_id", "")),
                "confirm": bool(payload.get("confirm", False)),
            })
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
                    "expected_versions": payload.get("expected_versions"),
                }
            )
            return
        if parsed.path == "/api/resolve-fact-conflict":
            if not self._require_role("admin"):
                return
            self._send_api(
                {
                    "command": "resolve_fact_conflict",
                    "conflict_id": str(payload.get("conflict_id", "")),
                    "decision": str(payload.get("decision", "")),
                    "memory_id": str(payload.get("memory_id", "")),
                    "expected_version_id": str(payload.get("expected_version_id", "")),
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
                    "compact": bool(payload.get("confirm", False)),
                    "selected_memory_ids": (
                        payload.get("selected_memory_ids")
                        if isinstance(payload.get("selected_memory_ids"), list)
                        else []
                    ),
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
                    token = self._store_preview_receipt(
                        kind, identity, item_ids, _preview_memory_versions(result)
                    )
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
        current_versions = _preview_memory_versions(current_result if isinstance(current_result, dict) else {})
        if (
            not current.get("ok")
            or tuple(current_ids) != tuple(receipt.get("item_ids", ()))
            or current_versions != receipt.get("memory_versions", {})
        ):
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

        execution_request = request
        if kind == "forget":
            current_memory_ids = [
                item[len("memory:"):]
                for item in current_ids
                if item.startswith("memory:")
            ]
            raw_selected = request.get("selected_memory_ids")
            selected_ids = (
                list(dict.fromkeys(str(item).strip() for item in raw_selected if str(item).strip()))
                if isinstance(raw_selected, list)
                else []
            )
            if not current_memory_ids:
                self._send_json(
                    {
                        "ok": False,
                        "error": "forget_no_candidates",
                        "message": "当前预览没有可遗忘候选，请调整条件后重新预览。",
                    },
                    status=409,
                )
                return
            if not selected_ids and len(current_memory_ids) == 1:
                selected_ids = list(current_memory_ids)
            if not selected_ids:
                self._send_json(
                    {
                        "ok": False,
                        "error": "forget_selection_required",
                        "message": "检测到多条候选，请先选择要遗忘的记忆。",
                        "candidate_count": len(current_memory_ids),
                    },
                    status=409,
                )
                return
            invalid_ids = [item for item in selected_ids if item not in current_memory_ids]
            if invalid_ids:
                self._send_json(
                    {
                        "ok": False,
                        "error": "forget_selection_invalid",
                        "message": "所选内容不属于本次预览，请重新预览后选择。",
                    },
                    status=409,
                )
                return
            execution_request = {
                "command": "forget_memory_ids",
                "memory_ids": selected_ids,
                "reason": str(request.get("request") or "user forget"),
                "compact": bool(request.get("compact", True)),
                "expected_versions": {memory_id: current_versions[memory_id] for memory_id in selected_ids},
            }

        self._discard_preview_receipt(preview_token)
        response = self._api_response(execution_request)
        self._send_json(response, status=200 if response.get("ok") else 400)

    def _store_preview_receipt(
        self,
        kind: str,
        identity: dict[str, Any],
        item_ids: tuple[str, ...],
        memory_versions: dict[str, str] | None = None,
    ) -> str:
        token = secrets.token_urlsafe(32)
        now = time.monotonic()
        with self.dashboard_preview_receipts_lock:
            self._purge_preview_receipts_locked(now)
            self.dashboard_preview_receipts[token] = {
                "kind": kind,
                "identity": _canonical_preview_identity(identity),
                "item_ids": tuple(item_ids),
                "memory_versions": dict(memory_versions or {}),
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

    def _evidence_payload(self) -> dict[str, Any]:
        diagnostics = self._diagnostics_payload(probe_sdk=False)
        return {
            "schema": "memguardos-local-evidence-v1",
            "ok": bool(diagnostics.get("ok")),
            "scope": "local_dashboard_export",
            "privacy": {
                "contains_memory_plaintext": False,
                "contains_credentials": False,
                "purpose": "local acceptance, delivery review and troubleshooting",
            },
            "diagnostics": diagnostics,
            "acceptance_plan": acceptance_plan(),
        }


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


def _preview_memory_versions(result: dict[str, Any]) -> dict[str, str]:
    return {
        str(item["memory_id"]): str(item.get("version_id") or "")
        for item in result.get("candidates", []) or []
        if isinstance(item, dict) and item.get("memory_id")
    }


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
      --bg:#eff2f7; --panel:#ffffff; --soft:#f6f8fb;
      --ink:#1b2537; --muted:#5c6b80; --faint:#8494a8;
      --line:#dfe5ee; --line-strong:#cbd5e3;
      --accent:#2563c4; --accent-deep:#1b4c9c; --accent-soft:#e9f1fb; --accent-line:#bed6f2;
      --ok:#177a53; --ok-soft:#e4f5ed; --ok-line:#b7e2ce;
      --warn:#9a6200; --warn-soft:#fdf3dd; --warn-line:#ecd9a8;
      --bad:#b3261e; --bad-soft:#fdecea; --bad-line:#f2c5c1;
      --radius:10px; --radius-sm:8px;
      --shadow:0 1px 2px rgba(21,32,52,.05), 0 2px 6px rgba(21,32,52,.06);
      --shadow-raise:0 2px 4px rgba(21,32,52,.07), 0 6px 16px rgba(21,32,52,.08);
    }
    * { box-sizing:border-box; }
    html { scroll-behavior:smooth; }
    body { margin:0; font-family:system-ui,"Noto Sans CJK SC","Source Han Sans SC","Microsoft YaHei",sans-serif; font-size:14px; line-height:1.65; color:var(--ink); background:var(--bg); -webkit-font-smoothing:antialiased; }

    /* ====== 顶栏 ====== */
    header { display:flex; align-items:center; justify-content:space-between; gap:16px; min-height:60px; padding:10px 24px; background:var(--panel); border-bottom:1px solid var(--line); position:relative; z-index:20; }
    .brand { display:flex; align-items:center; gap:12px; min-width:0; }
    .brand-mark { flex:none; width:36px; height:36px; display:grid; place-items:center; border-radius:9px; background:linear-gradient(160deg,#2f6fd0,#1b4c9c); color:#fff; box-shadow:inset 0 1px 0 rgba(255,255,255,.25); }
    .brand-mark .ui-icon { width:20px; height:20px; }
    header h1 { margin:0; font-size:16px; font-weight:700; letter-spacing:.2px; }
    header .subtitle { margin-top:1px; font-size:12px; color:var(--muted); }
    header .meta { flex:none; display:flex; align-items:center; gap:8px; font-size:12px; color:var(--muted); }
    .meta-chip { display:inline-flex; align-items:center; gap:7px; min-height:28px; padding:3px 12px; border:1px solid var(--line); border-radius:999px; background:var(--soft); white-space:nowrap; }
    .meta-chip::before { content:""; width:7px; height:7px; border-radius:50%; background:var(--ok); }

    /* ====== 布局骨架 ====== */
    main { display:grid; grid-template-columns:232px minmax(0,1fr); min-height:calc(100vh - 60px); }
    nav { position:sticky; top:0; align-self:start; min-height:calc(100vh - 60px); padding:16px 12px 24px; background:#f7f9fc; border-right:1px solid var(--line); }
    .shell-content { min-width:0; }
    .nav-group { margin:14px 6px 6px; font-size:11px; font-weight:700; letter-spacing:.08em; color:var(--faint); }
    .nav-group:first-child { margin-top:2px; }
    nav button { position:relative; width:100%; min-height:40px; margin:0 0 2px; display:flex; align-items:center; gap:10px; padding:8px 12px; border:0; border-radius:8px; background:transparent; color:var(--muted); font:inherit; font-weight:600; text-align:left; cursor:pointer; transition:background .15s ease, color .15s ease; }
    nav button .ui-icon { width:17px; height:17px; flex:none; opacity:.85; }
    nav button:hover:not(.active) { background:#eef2f8; color:var(--ink); }
    nav button.active { background:var(--panel); color:var(--accent-deep); box-shadow:var(--shadow); }
    nav button.active::before { content:""; position:absolute; left:0; top:9px; bottom:9px; width:3px; border-radius:3px; background:var(--accent); }

    /* ====== 页面区块 ====== */
    section {
      display:none;
      width:100%;
      max-width:1680px;
      margin-inline:auto;
      padding:22px clamp(24px,2.2vw,40px) 40px;
    }
    section.active { display:block; animation:pageIn .18s ease; }
    @keyframes pageIn { from { opacity:0; transform:translateY(6px); } to { opacity:1; transform:none; } }
    h2 { margin:0; font-size:20px; font-weight:700; letter-spacing:.2px; }
    .lead { margin:4px 0 0; max-width:720px; font-size:13px; line-height:1.7; color:var(--muted); }
    .page-header { display:flex; align-items:flex-start; justify-content:space-between; gap:20px; margin-bottom:18px; }
    .page-header .actions { margin-top:2px; flex:none; }
    .crumbs { display:flex; align-items:center; gap:10px; margin:-4px 0 14px; font-size:13px; color:var(--muted); }
    .crumbs .ghost { padding-left:8px; }
    .crumbs-path { display:inline-flex; align-items:center; gap:6px; }
    .crumbs-path b { color:var(--ink); font-weight:600; }

    /* ====== 卡片与分区 ====== */
    .panel { min-width:0; background:var(--panel); border:1px solid var(--line); border-radius:var(--radius); box-shadow:var(--shadow); padding:16px 18px; }
    .panel.emphasis { border-color:var(--accent-line); background:#f9fbfe; }
    .panel + .panel { margin-top:14px; }
    .section-block { min-width:0; background:var(--panel); border:1px solid var(--line); border-radius:var(--radius); box-shadow:var(--shadow); overflow:hidden; }
    .section-block-header { display:flex; align-items:center; justify-content:space-between; gap:12px; min-height:48px; padding:10px 16px; border-bottom:1px solid var(--line); background:var(--panel); }
    .section-block-header strong { font-size:14px; }
    .section-block-body { padding:10px 16px 14px; }
    .result-section { background:var(--panel); border:1px solid var(--line); border-radius:var(--radius); box-shadow:var(--shadow); overflow:hidden; }
    .result-section > .section-block-header { background:var(--soft); }
    .grid { display:grid; grid-template-columns:repeat(3,minmax(0,1fr)); gap:14px; }
    .grid.two { grid-template-columns:repeat(2,minmax(0,1fr)); }
    .grid.three { grid-template-columns:repeat(3,minmax(0,1fr)); }
    .grid.four { grid-template-columns:repeat(4,minmax(0,1fr)); }
    .content-split { display:grid; grid-template-columns:minmax(0,1.4fr) minmax(280px,.6fr); gap:14px; }

    /* ====== 文字与状态 ====== */
    .muted { color:var(--muted); font-size:13px; }
    .warning { color:var(--warn); font-size:13px; font-weight:600; }
    .metric { margin-top:2px; font-size:24px; font-weight:750; color:var(--accent-deep); font-variant-numeric:tabular-nums; overflow-wrap:anywhere; }
    .metric.compact { font-size:15px; line-height:1.5; font-weight:650; }
    .badge { display:inline-flex; align-items:center; gap:5px; min-height:22px; padding:1px 9px; border-radius:999px; border:1px solid var(--line); background:var(--soft); color:var(--muted); font-size:12px; font-weight:600; white-space:nowrap; }
    .badge.ok { border-color:var(--ok-line); background:var(--ok-soft); color:var(--ok); }
    .badge.warn { border-color:var(--warn-line); background:var(--warn-soft); color:var(--warn); }
    .badge.bad { border-color:var(--bad-line); background:var(--bad-soft); color:var(--bad); }
    .status-dot { flex:none; width:9px; height:9px; border-radius:50%; background:var(--faint); box-shadow:0 0 0 3px rgba(132,148,168,.14); }
    .status-dot.ok { background:var(--ok); box-shadow:0 0 0 3px rgba(23,122,83,.16); }
    .status-dot.warn { background:var(--warn); box-shadow:0 0 0 3px rgba(154,98,0,.16); }
    .status-dot.bad { background:var(--bad); box-shadow:0 0 0 3px rgba(179,38,30,.16); }
    .health { display:flex; align-items:center; justify-content:space-between; gap:10px; min-width:0; }
    .health > * { min-width:0; }
    .health strong { font-size:14px; }
    .health-title { display:flex; align-items:center; gap:8px; font-weight:700; min-width:0; }

    /* ====== 列表 ====== */
    .list { display:grid; gap:8px; }
    .item { border:1px solid var(--line); border-radius:var(--radius-sm); background:var(--panel); padding:11px 13px; }
    .item-title { font-weight:700; margin-bottom:3px; }
    .list-row { display:flex; align-items:flex-start; justify-content:space-between; gap:16px; padding:11px 0; border-bottom:1px solid var(--line); }
    .list-row:last-child { border-bottom:0; }
    .linear-list { margin-top:6px; }
    .linear-list .list-row { min-height:54px; }
    .panel .list > .item { border-left:0; border-right:0; border-top:0; border-radius:0; padding-left:0; padding-right:0; }
    .notification-list { display:block; }
    .notification-list .list-row { min-height:64px; align-items:center; }
    .notification-actions { flex:none; display:flex; align-items:center; justify-content:flex-end; flex-wrap:wrap; gap:8px; }
    .status-list { background:var(--panel); border:1px solid var(--line); border-radius:var(--radius); box-shadow:var(--shadow); overflow:hidden; }
    .status-row { display:grid; grid-template-columns:minmax(150px,.8fr) minmax(0,1.2fr) auto; gap:14px; align-items:center; min-height:52px; padding:10px 16px; border-bottom:1px solid var(--line); }
    .status-row:last-child { border-bottom:0; }
    .empty-state { display:flex; flex-direction:column; align-items:center; gap:6px; padding:28px 20px; text-align:center; color:var(--muted); font-size:13px; }
    .empty-state strong { color:var(--ink); font-size:14px; }
    .empty-state button { margin-top:6px; }

    /* ====== 表单 ====== */
    label { display:block; margin:12px 0 5px; font-size:13px; font-weight:600; color:var(--ink); }
    label:first-child { margin-top:0; }
    .field-hint { margin:4px 0 0; font-size:12px; color:var(--faint); }
    textarea, input, select { width:100%; padding:8px 11px; border:1px solid var(--line-strong); border-radius:var(--radius-sm); background:var(--panel); color:var(--ink); font:inherit; line-height:1.5; transition:border-color .15s ease, box-shadow .15s ease; }
    textarea { min-height:104px; resize:vertical; }
    textarea:hover, input:hover, select:hover { border-color:#a9bbd2; }
    textarea:focus-visible, input:focus-visible, select:focus-visible { outline:0; border-color:var(--accent); box-shadow:0 0 0 3px rgba(37,99,196,.15); }

    /* ====== 按钮 ====== */
    button { font:inherit; }
    .actions { display:flex; flex-wrap:wrap; gap:8px; margin-top:14px; }
    .toolbar { display:flex; flex-wrap:wrap; gap:8px; align-items:center; margin:14px 0; }
    button.primary, button.secondary, button.danger, button.ghost { display:inline-flex; align-items:center; justify-content:center; gap:6px; min-height:36px; padding:7px 14px; border-radius:var(--radius-sm); font-size:14px; font-weight:600; cursor:pointer; transition:background .15s ease, border-color .15s ease, box-shadow .15s ease, transform .08s ease; }
    button.primary { border:1px solid var(--accent-deep); background:var(--accent); color:#fff; box-shadow:inset 0 1px 0 rgba(255,255,255,.18); }
    button.primary:hover:not(:disabled) { background:#2b6ed4; }
    button.secondary { border:1px solid var(--line-strong); background:var(--panel); color:var(--ink); }
    button.secondary:hover:not(:disabled) { border-color:#a9bbd2; background:var(--soft); }
    button.danger { border:1px solid #932019; background:var(--bad); color:#fff; }
    button.danger:hover:not(:disabled) { background:#c02c24; }
    button.ghost { border:1px solid transparent; background:transparent; color:var(--accent); padding-inline:10px; }
    button.ghost:hover:not(:disabled) { background:var(--accent-soft); }
    button.primary:active, button.secondary:active, button.danger:active, button.ghost:active { transform:translateY(1px); }
    button:disabled { opacity:.55; cursor:not-allowed; }
    button:focus-visible { outline:2px solid #7fb0ea; outline-offset:2px; }
    .button-label { display:inline-flex; align-items:center; gap:6px; white-space:nowrap; }
    .mini-actions { display:flex; flex-wrap:wrap; gap:8px; margin-top:10px; }
    .mini-actions button, .result-actions button { min-height:32px; padding:5px 11px; font-size:13px; }

    /* ====== 总览 ====== */
    .overview-status-banner { display:grid; grid-template-columns:auto minmax(0,1fr) auto; gap:14px; align-items:center; padding:16px 18px; border:1px solid var(--ok-line); border-radius:var(--radius); background:linear-gradient(180deg,#f7fcfa,#f2faf6); box-shadow:var(--shadow); }
    .overview-status-banner.warn { border-color:var(--warn-line); background:linear-gradient(180deg,#fffaf0,#fdf6e6); }
    .overview-status-banner.bad { border-color:var(--bad-line); background:linear-gradient(180deg,#fff7f6,#fdefed); }
    .status-mark { width:36px; height:36px; display:grid; place-items:center; border-radius:50%; background:var(--ok-soft); color:var(--ok); font-weight:800; font-size:16px; }
    .overview-status-banner.warn .status-mark { background:var(--warn-soft); color:var(--warn); }
    .overview-status-banner.bad .status-mark { background:var(--bad-soft); color:var(--bad); }
    .status-copy { min-width:0; }
    .status-copy strong { display:block; font-size:15px; margin-bottom:2px; }
    .overview-primary-actions { display:grid; grid-template-columns:repeat(3,minmax(0,1fr)); gap:12px; margin:14px 0; }
    .task-action { display:grid; grid-template-columns:38px minmax(0,1fr) auto; align-items:center; gap:12px; min-height:70px; padding:13px 15px; border:1px solid var(--line); border-radius:var(--radius); background:var(--panel); box-shadow:var(--shadow); color:var(--ink); font:inherit; text-align:left; cursor:pointer; transition:border-color .15s ease, box-shadow .15s ease, transform .12s ease; }
    .task-action:hover { border-color:var(--accent-line); box-shadow:var(--shadow-raise); transform:translateY(-1px); }
    .task-action:focus-visible { outline:2px solid #7fb0ea; outline-offset:2px; }
    .task-action strong { display:block; margin-bottom:2px; font-size:14px; }
    .task-action span { color:var(--muted); font-size:12px; line-height:1.5; }
    .task-action b { color:var(--accent); font-size:13px; font-weight:700; }
    .task-icon { width:38px; height:38px; display:grid; place-items:center; border-radius:9px; background:var(--accent-soft); color:var(--accent-deep); }
    .task-icon .ui-icon { width:20px; height:20px; }
    .ui-icon { width:18px; height:18px; flex:none; stroke:currentColor; stroke-width:1.8; fill:none; stroke-linecap:round; stroke-linejoin:round; }
    .metric-strip { display:grid; grid-template-columns:repeat(3,minmax(0,1fr)); margin-bottom:14px; background:var(--panel); border:1px solid var(--line); border-radius:var(--radius); box-shadow:var(--shadow); overflow:hidden; }
    .metric-strip > div { padding:13px 18px; border-right:1px solid var(--line); }
    .metric-strip > div:last-child { border-right:0; }

    /* ====== 记忆中心 ====== */
    .memory-workspace { display:grid; gap:14px; }
    .search-toolbar { display:grid; grid-template-columns:minmax(240px,1fr) 132px auto auto; gap:10px; align-items:end; padding:14px 16px; background:var(--panel); border:1px solid var(--line); border-radius:var(--radius); box-shadow:var(--shadow); }
    .search-toolbar label { margin-top:0; }
    .search-summary { min-height:20px; font-size:13px; color:var(--muted); }
    .memory-result-list { display:grid; gap:0; background:var(--panel); border:1px solid var(--line); border-radius:var(--radius); box-shadow:var(--shadow); overflow:hidden; }
    .memory-result-row { display:grid; grid-template-columns:112px minmax(0,1fr) auto; gap:14px; align-items:start; padding:14px 16px; border-bottom:1px solid var(--line); transition:background .15s ease; }
    .memory-result-row:hover { background:var(--soft); }
    .memory-result-row:last-child { border-bottom:0; }
    .memory-id { font-family:ui-monospace,SFMono-Regular,Consolas,monospace; font-size:12px; color:#3e5a7c; overflow-wrap:anywhere; }
    .memory-summary { font-weight:700; line-height:1.5; margin-bottom:5px; }
    .result-meta { display:flex; flex-wrap:wrap; gap:4px 12px; font-size:12px; color:var(--muted); }
    .result-reason { margin-top:6px; font-size:13px; color:#41546c; }
    .result-actions { display:flex; flex-wrap:wrap; justify-content:flex-end; gap:7px; }
    .more-results { margin-top:0; }

    /* ====== Agent 工作台 ====== */
    .timeline { position:relative; display:grid; gap:0; }
    .timeline::before { content:""; position:absolute; left:11px; top:8px; bottom:8px; width:1px; background:var(--line); }
    .timeline-row { position:relative; padding:0 0 14px 32px; min-width:0; }
    .timeline-dot { position:absolute; left:5px; top:4px; width:13px; height:13px; border:3px solid var(--panel); border-radius:50%; background:var(--accent); box-shadow:0 0 0 1px var(--line); }
    .timeline-row.tool .timeline-dot { background:var(--warn); }
    .timeline-row.forgotten .timeline-dot { background:var(--faint); }
    .timeline-meta { margin-bottom:3px; font-size:12px; color:var(--muted); }
    .timeline-body { padding-bottom:10px; border-bottom:1px solid #edf1f6; line-height:1.6; word-break:break-word; font-size:13px; }
    .session-list { display:grid; gap:10px; }
    .session-group { border:1px solid var(--line); border-radius:8px; background:#fff; overflow:hidden; }
    .session-group > summary { display:flex; align-items:center; gap:12px; min-height:58px; padding:10px 14px; cursor:pointer; list-style:none; }
    .session-group > summary::-webkit-details-marker { display:none; }
    .session-group > summary::before { content:'›'; color:var(--muted); font-size:20px; line-height:1; transition:transform .16s ease; }
    .session-group[open] > summary::before { transform:rotate(90deg); }
    .session-title { min-width:0; flex:1; }
    .session-title strong { display:block; overflow:hidden; text-overflow:ellipsis; white-space:nowrap; font-size:14px; }
    .session-title span { display:block; margin-top:3px; color:var(--muted); font-size:12px; }
    .session-body { padding:2px 14px 4px; border-top:1px solid var(--line); }
    .session-body .timeline { margin-top:12px; }
    .stage-columns { display:grid; grid-template-columns:repeat(3,minmax(0,1fr)); gap:10px; }
    .stage-column { min-width:0; padding:12px; border:1px solid var(--line); border-top:3px solid var(--accent); border-radius:0 0 var(--radius-sm) var(--radius-sm); background:var(--panel); }
    .stage-column h3 { margin:0 0 6px; font-size:14px; }
    .trace { display:grid; grid-template-columns:repeat(3,minmax(0,1fr)); gap:10px; margin-top:12px; }
    .trace .step { border:1px solid var(--line); border-radius:var(--radius-sm); background:var(--panel); padding:10px 12px; font-size:13px; }
    .step b { display:block; margin-bottom:3px; color:var(--accent-deep); }

    /* ====== 精准遗忘 ====== */
    .workflow-steps { display:grid; grid-template-columns:repeat(3,minmax(0,1fr)); gap:0; margin:16px 0; padding:0; list-style:none; background:var(--panel); border:1px solid var(--line); border-radius:var(--radius); box-shadow:var(--shadow); overflow:hidden; }
    .workflow-steps li { display:grid; grid-template-columns:28px minmax(0,1fr); gap:10px; padding:13px 16px; border-right:1px solid var(--line); }
    .workflow-steps li:last-child { border-right:0; }
    .workflow-step-number { width:24px; height:24px; display:grid; place-items:center; border-radius:50%; background:var(--accent-soft); color:var(--accent-deep); font-size:12px; font-weight:750; }
    .workflow-steps strong { display:block; margin-bottom:2px; font-size:13px; }
    .workflow-steps .muted { font-size:12px; }

    /* ====== 进度与骨架 ====== */
    .progress { height:8px; margin-top:8px; border-radius:999px; background:#e6ebf2; overflow:hidden; }
    .progress span { display:block; height:100%; width:0%; border-radius:999px; background:var(--accent); transition:width .3s ease; }
    .skeleton { min-height:72px; border-radius:var(--radius-sm); background:linear-gradient(90deg,#eef2f7 25%,#f8fafc 45%,#eef2f7 65%); background-size:240% 100%; animation:skeleton 1.2s ease-in-out infinite; }
    .spinner { width:14px; height:14px; display:inline-block; margin-right:7px; border:2px solid currentColor; border-right-color:transparent; border-radius:50%; animation:spin .7s linear infinite; vertical-align:-2px; }
    @keyframes spin { to { transform:rotate(360deg); } }
    @keyframes skeleton { to { background-position:-120% 0; } }

    /* ====== 反馈条与错误 ====== */
    .error { display:none; margin:14px 24px 0; padding:10px 14px; border:1px solid var(--bad-line); border-radius:var(--radius-sm); background:var(--bad-soft); color:var(--bad); font-size:13px; }
    .operation-status { display:none; position:relative; margin:14px 24px 0; padding:10px 14px; border:1px solid var(--accent-line); border-radius:var(--radius-sm); background:var(--accent-soft); color:var(--accent-deep); font-size:13px; animation:pageIn .18s ease; }
    .operation-status.ok { border-color:var(--ok-line); background:var(--ok-soft); color:var(--ok); }
    .operation-status.warn { border-color:var(--warn-line); background:var(--warn-soft); color:var(--warn); }
    .operation-status.bad { border-color:var(--bad-line); background:var(--bad-soft); color:var(--bad); }

    /* ====== 折叠详情 ====== */
    details { margin-top:12px; border:1px solid var(--line); border-radius:var(--radius-sm); background:var(--panel); }
    details summary { padding:10px 14px; font-size:13px; font-weight:600; color:var(--muted); cursor:pointer; user-select:none; transition:color .15s ease; }
    details summary:hover { color:var(--ink); }
    details[open] summary { border-bottom:1px solid var(--line); color:var(--ink); }
    pre { white-space:pre-wrap; word-break:break-word; margin:0; padding:12px 14px; min-height:120px; background:#101828; color:#cfe3f7; font-size:12px; border-radius:0 0 var(--radius-sm) var(--radius-sm); overflow:auto; }
    .advanced-output { margin:0; }
    .overview-more-body { padding:10px 14px 14px; }
    .overview-more > summary { padding:11px 14px; }
    .auth-settings { max-width:520px; }


    /* ====== 价值主视觉 ====== */
    .value-hero { display:grid; grid-template-columns:minmax(0,1.5fr) minmax(260px,1fr); gap:20px; align-items:center; padding:22px 24px; border:1px solid var(--accent-line); border-radius:var(--radius); background:linear-gradient(135deg,#f4f9ff 0%,#eef5fd 100%); box-shadow:var(--shadow); }
    .value-hero-eyebrow { font-size:12px; font-weight:700; letter-spacing:.08em; color:var(--accent); margin-bottom:6px; }
    .value-hero-title { display:block; font-size:21px; font-weight:750; line-height:1.4; color:var(--ink); }
    .value-hero-detail { margin:8px 0 0; font-size:14px; line-height:1.65; color:var(--muted); }
    .value-hero-actions { display:flex; flex-wrap:wrap; gap:10px; margin-top:16px; }
    button.primary.large { min-height:44px; padding:10px 20px; font-size:15px; font-weight:700; box-shadow:0 2px 8px rgba(37,99,196,.28); }
    button.primary.large:hover:not(:disabled) { box-shadow:0 4px 12px rgba(37,99,196,.34); }
    .value-hero-stats { display:grid; gap:10px; }
    .value-stat { display:flex; align-items:baseline; gap:10px; padding:10px 14px; border:1px solid var(--line); border-radius:var(--radius-sm); background:rgba(255,255,255,.75); }
    .value-stat-num { font-size:24px; font-weight:750; color:var(--accent-deep); font-variant-numeric:tabular-nums; min-width:44px; }
    .value-stat-label { font-size:13px; color:var(--muted); }

    /* ====== 学到→生效 配对 ====== */
    .pair-row { display:grid; grid-template-columns:minmax(0,1fr) 34px minmax(0,1fr) auto; gap:12px; align-items:center; padding:13px 0; border-bottom:1px solid var(--line); }
    .pair-row:last-child { border-bottom:0; }
    .pair-cell strong { display:block; font-size:14px; margin-bottom:3px; }
    .pair-cell span { font-size:12px; color:var(--muted); }
    .pair-arrow { display:grid; place-items:center; width:34px; height:34px; border-radius:50%; background:var(--accent-soft); color:var(--accent-deep); font-weight:700; }
    .pair-actions { display:flex; gap:7px; flex-wrap:wrap; justify-content:flex-end; }

    /* ====== 记忆效果对比 ====== */
    .compare-verdict { display:grid; grid-template-columns:auto minmax(0,1fr); gap:14px; align-items:center; padding:16px 18px; border:1px solid var(--ok-line); border-radius:var(--radius); background:var(--ok-soft); }
    .compare-verdict.info { border-color:var(--accent-line); background:var(--accent-soft); }
    .compare-verdict.warn { border-color:var(--warn-line); background:var(--warn-soft); }
    .compare-verdict-mark { width:38px; height:38px; display:grid; place-items:center; border-radius:50%; background:rgba(255,255,255,.7); font-size:19px; }
    .compare-verdict strong { display:block; font-size:15px; margin-bottom:3px; }
    .compare-grid { display:grid; grid-template-columns:minmax(140px,.8fr) minmax(0,1fr) minmax(0,1fr); background:var(--panel); border:1px solid var(--line); border-radius:var(--radius); box-shadow:var(--shadow); overflow:hidden; }
    .compare-grid > div { padding:12px 15px; border-bottom:1px solid var(--line); min-width:0; }
    .compare-grid .compare-head { background:var(--soft); font-weight:700; font-size:13px; }
    .compare-grid .compare-head.without { color:var(--muted); }
    .compare-grid .compare-head.with { color:var(--accent-deep); }
    .compare-grid > div:nth-last-child(-n+3) { border-bottom:0; }
    .compare-cell-changed { background:#f4fbf7; }
    .compare-value { font-weight:650; }
    .compare-value.muted-value { color:var(--muted); font-weight:500; }
    .compare-cmd { display:block; margin-top:5px; font-family:ui-monospace,SFMono-Regular,Consolas,monospace; font-size:12px; color:#3e5a7c; overflow-wrap:anywhere; }
    .compare-badge { display:inline-flex; align-items:center; gap:4px; margin-left:6px; padding:1px 7px; border-radius:999px; background:var(--ok-soft); border:1px solid var(--ok-line); color:var(--ok); font-size:11px; font-weight:700; }
    .measured-tag { display:inline-block; padding:1px 7px; border-radius:999px; font-size:11px; font-weight:700; }
    .measured-tag.measured { background:var(--ok-soft); border:1px solid var(--ok-line); color:var(--ok); }
    .measured-tag.modelled { background:var(--warn-soft); border:1px solid var(--warn-line); color:var(--warn); }
    @media (max-width: 900px) {
      .value-hero { grid-template-columns:1fr; }
      .value-hero-stats { grid-template-columns:repeat(3,1fr); }
      .pair-row { grid-template-columns:1fr; }
      .pair-arrow { display:none; }
      .compare-grid { grid-template-columns:1fr; }
      .compare-grid .compare-head { border-top:1px solid var(--line); }
    }

    /* ====== 其他 ====== */
    .acceptance-item .actions { margin-top:10px; }
    .hint-card { display:grid; gap:10px; }
    .hint-card .hint-row { display:flex; gap:10px; align-items:flex-start; font-size:13px; color:var(--muted); }
    .hint-row .hint-index { flex:none; width:22px; height:22px; display:grid; place-items:center; margin-top:1px; border-radius:50%; background:var(--accent-soft); color:var(--accent-deep); font-size:12px; font-weight:700; }
    [hidden] { display:none !important; }
    ::selection { background:#cfe2f8; }

    @media (prefers-reduced-motion: reduce) {
      *, *::before, *::after { animation-duration:.01ms !important; animation-iteration-count:1 !important; transition-duration:.01ms !important; scroll-behavior:auto !important; }
    }
    @media (max-width: 1180px) {
      main { grid-template-columns:208px minmax(0,1fr); }
      section { padding:18px 20px 32px; }
      .grid.four { grid-template-columns:repeat(2,minmax(0,1fr)); }
      .grid.three, .trace, .stage-columns { grid-template-columns:repeat(2,minmax(0,1fr)); }
      .content-split { grid-template-columns:1fr; }
      .memory-result-row { grid-template-columns:104px minmax(0,1fr); }
      .result-actions { grid-column:2; justify-content:flex-start; }
    }
    @media (max-width: 760px) {
      header { align-items:flex-start; padding:12px 16px; }
      header .meta { display:none; }
      main { grid-template-columns:1fr; }
      nav { position:sticky; top:0; z-index:10; display:flex; align-items:center; min-height:auto; overflow-x:auto; gap:4px; padding:8px 12px; border-right:0; border-bottom:1px solid var(--line); }
      .nav-group { display:none; }
      nav button { width:auto; min-width:max-content; min-height:42px; margin:0; flex:0 0 auto; justify-content:center; white-space:nowrap; text-align:center; }
      nav button.active::before { display:none; }
      section { padding:16px 14px 28px; scroll-margin-top:72px; }
      .grid, .grid.two, .grid.three, .grid.four, .trace, .stage-columns { grid-template-columns:1fr; }
      .page-header { display:block; }
      .page-header .actions { margin-top:10px; }
      .overview-status-banner { grid-template-columns:auto minmax(0,1fr); }
      .overview-status-banner > button { grid-column:1 / -1; width:100%; }
      .overview-primary-actions, .metric-strip { grid-template-columns:1fr; }
      .metric-strip > div { border-right:0; border-bottom:1px solid var(--line); }
      .metric-strip > div:last-child { border-bottom:0; }
      .search-toolbar { grid-template-columns:1fr; }
      .memory-result-row { grid-template-columns:1fr; }
      .result-actions { grid-column:auto; justify-content:flex-start; }
      .workflow-steps { grid-template-columns:1fr; }
      .workflow-steps li { border-right:0; border-bottom:1px solid var(--line); }
      .workflow-steps li:last-child { border-bottom:0; }
      .status-row { grid-template-columns:minmax(0,1fr) auto; gap:5px 10px; align-items:center; }
      .status-row > .muted { grid-column:1 / -1; grid-row:2; }
      button.primary, button.secondary, button.danger { min-height:44px; }
      .error, .operation-status { margin:12px 14px 0; }
    }
    @media (max-width: 480px) { nav button { padding-inline:10px; } }
  </style>
</head>
<body>
  <header>
    <div class="brand">
      <span class="brand-mark" data-lucide="shield" aria-hidden="true"></span>
      <div><h1>MemGuardOS 记忆安全中心</h1><div class="subtitle">帮助本机 OS Agent 记住有用信息、保护敏感内容并按需遗忘</div></div>
    </div>
    <div class="meta">
      <span id="namespaceMeta" class="meta-chip">记忆空间：加载中</span>
      <span class="meta-chip">仅保存在本机</span>
    </div>
  </header>
  <main>
    <nav aria-label="功能导航">
      <div class="nav-group">普通用户</div>
      <button class="active" data-tab="overview">总览</button>
      <button data-tab="agent">Agent 工作台</button>
      <button data-tab="memory">记忆中心</button>
      <button data-tab="preferences">我的偏好</button>
      <button data-tab="sensitive">内容保护</button>
      <button data-tab="forget">精准遗忘</button>
      <div class="nav-group" data-min-role="admin">管理员视图</div>
      <button data-tab="diagnostics" data-min-role="admin">系统维护</button>
      <button data-tab="evidence" data-min-role="admin">审计与证据</button>
      <button data-tab="acceptance" data-min-role="admin">系统检查</button>
    </nav>
    <div class="shell-content">
      <div id="errorBox" class="error" role="alert"></div>
      <div id="operationStatus" class="operation-status" role="status" aria-live="polite"></div>

      <section id="overview" class="active">
        <div class="page-header">
          <div><h2>总览</h2><p class="lead">这里回答一个问题：记忆到底帮到了什么。下面展示当前记忆空间的真实状态和可复现效果参考。</p></div>
          <div class="actions"><button class="secondary" onclick="refresh(event)">重新检查</button></div>
        </div>

        <div class="value-hero">
          <div class="value-hero-main">
            <div class="value-hero-eyebrow">记忆价值</div>
            <strong id="valueHeadline" class="value-hero-title">正在读取记忆生效情况…</strong>
            <p id="valueDetail" class="value-hero-detail">完成后会显示哪些偏好已自动生效、影响了哪些任务动作。</p>
            <div class="value-hero-actions">
              <button class="primary large" onclick="openTab('compare')"><span class="button-label">▶ 运行记忆效果对比</span></button>
              <button class="ghost" onclick="openTab('preferences')">查看生效偏好</button>
            </div>
          </div>
          <div class="value-hero-stats">
            <div class="value-stat"><span class="value-stat-num" id="statPreferences">-</span><span class="value-stat-label">条偏好已生效</span></div>
            <div class="value-stat"><span class="value-stat-num" id="statMemories">-</span><span class="value-stat-label">条记忆在管</span></div>
            <div class="value-stat"><span class="value-stat-num" id="statProtected">-</span><span class="value-stat-label">次敏感保护</span></div>
          </div>
        </div>

        <div class="section-block" style="margin-top:14px">
          <div class="section-block-header"><strong>学到 → 下次生效</strong><span class="muted">每一条都可点开看依据</span></div>
          <div id="learningPairs" class="section-block-body"><div class="empty-state">正在读取记忆与偏好的配对关系…</div></div>
        </div>

        <div class="content-split" style="margin-top:14px">
          <div class="section-block">
            <div class="section-block-header"><strong>可复现效果参考</strong><span class="muted" id="valueEvidenceSource">固定场景回归</span></div>
            <div id="valueEvidencePanel" class="section-block-body"><div class="empty-state">正在读取实验证据…</div></div>
          </div>
          <div class="section-block">
            <div class="section-block-header"><strong>最近动态</strong><button class="ghost" onclick="openTab('notifications')">查看全部</button></div>
            <div id="homeNotifications" class="section-block-body"><div class="empty-state">正在读取保护与遗忘结果…</div></div>
          </div>
        </div>

        <div id="overviewStatusBanner" class="overview-status-banner" style="margin-top:14px">
          <div class="status-mark" aria-hidden="true">✓</div>
          <div class="status-copy"><strong id="overviewStateTitle">正在连接记忆服务</strong><span id="overviewStateText" class="muted">完成后会显示是否影响 Agent 使用。</span></div>
          <button class="secondary" data-min-role="admin" onclick="openTab('diagnostics')">系统维护</button>
        </div>

        <div class="overview-primary-actions">
          <button class="task-action" onclick="openTab('memory')"><i class="task-icon" data-lucide="database" aria-hidden="true"></i><span><strong>记忆中心</strong>浏览、搜索并核对每条记忆的来源</span><b>进入</b></button>
          <button class="task-action" onclick="openTab('agent')"><i class="task-icon" data-lucide="bot" aria-hidden="true"></i><span><strong>Agent 工作台</strong>查看交互、偏好依据与生效证明</span><b>进入</b></button>
          <button class="task-action" onclick="openTab('forget')"><i class="task-icon" data-lucide="trash" aria-hidden="true"></i><span><strong>精准遗忘</strong>先预览影响，再确认执行</span><b>进入</b></button>
        </div>

        <details class="overview-more"><summary>查看系统技术状态</summary><div class="overview-more-body"><div id="statusCards" class="list"></div><pre id="overviewOut" class="advanced-output"></pre></div></details>
      </section>

      <section id="compare">
        <div class="crumbs"><button class="ghost" onclick="openTab('overview')">← 返回总览</button><span class="crumbs-path">总览 / <b>记忆效果对比</b></span></div>
        <div class="page-header">
          <div><h2>记忆效果对比：带记忆 vs 不带记忆</h2><p class="lead">同一个任务分别在"没有任何记忆"和"使用当前记忆空间"两种条件下构建上下文，把差异直接摆出来。差异完全由你当前真实存在的记忆决定。</p></div>
        </div>
        <div class="panel">
          <label for="compareTask">任务描述</label>
          <input id="compareTask" value="查找 build_agent_context 的定义" placeholder="例如：查找某个函数的定义、运行测试、排查服务日志">
          <p class="field-hint">建议使用与已有偏好相关的任务（代码搜索、测试、日志、高风险操作），差异会更明显。</p>
          <div class="actions"><button class="primary" onclick="runValueComparison(event)">运行对比</button><button class="secondary" onclick="openTab('preferences')">先看有哪些偏好</button></div>
        </div>
        <div id="compareVerdict" style="margin-top:14px"></div>
        <div id="compareTable" style="margin-top:14px"></div>
        <div id="compareCost" style="margin-top:14px"></div>
        <details><summary>详细记录：对比原始结果</summary><pre id="compareOut" class="advanced-output"></pre></details>
      </section>

      <section id="memory">
        <div class="page-header">
          <div><h2>记忆中心</h2><p class="lead">查看已保存内容，按相关度搜索，并从同一列表进入详情、验证或遗忘。</p></div>
          <div class="actions"><button class="primary" data-min-role="operator" onclick="openTab('ingest')">添加记忆</button><button class="secondary" onclick="openTab('context')">上下文预览</button></div>
        </div>
        <div class="memory-workspace">
          <div class="search-toolbar" aria-label="搜索与筛选">
            <div><label for="memorySearchQuery">搜索与筛选</label><input id="memorySearchQuery" placeholder="输入主题、工具、项目或记忆内容" autocomplete="off"></div>
            <div><label for="memorySearchLimit">结果数量</label><select id="memorySearchLimit"><option value="3">3 条</option><option value="5" selected>5 条</option><option value="10">10 条</option></select></div>
            <button class="primary" onclick="searchMemoryWorkspace(event)">搜索记忆</button>
            <button class="secondary" onclick="clearMemorySearch(event)">清除筛选</button>
          </div>
          <div id="memorySearchSummary" class="search-summary">默认展示最近记忆；搜索后优先显示最相关的三条。</div>
          <div id="memoryWorkspaceResults" class="memory-result-list"><div class="empty-state">正在读取记忆列表…</div></div>
          <details id="memoryMoreResults" class="more-results" hidden><summary>更多相关结果</summary><div id="memoryMoreResultsBody" class="memory-result-list"></div></details>
          <div class="section-block" style="margin-top:14px"><div class="section-block-header"><strong>事实核对队列</strong><span class="muted">新值不会悄悄覆盖已有事实</span></div><div id="factConflictPanel" class="section-block-body"><div class="empty-state">正在检查待核对事实…</div></div></div>
          <details><summary>查看完整技术结果</summary><pre id="memoryWorkspaceOut" class="advanced-output"></pre></details>
          <div class="actions" style="justify-content:flex-end"><button class="secondary" data-min-role="admin" onclick="endBrowserMemorySession(event)">结束当前会话</button></div>
        </div>
      </section>

      <section id="ingest">
        <div class="crumbs"><button class="ghost" onclick="openTab('memory')">← 返回记忆中心</button><span class="crumbs-path">记忆中心 / <b>添加记忆</b></span></div>
        <div class="page-header"><div><h2>添加记忆</h2><p class="lead">保存前会先检查内容质量和敏感风险；相同内容不会重复保存。</p></div></div>
        <div class="content-split">
          <div class="panel">
            <label for="ingestText">要保存的内容</label><textarea id="ingestText">以后回答 openKylin 服务问题时，先给 systemctl 命令，再说明风险。</textarea>
            <label for="sourceKind">内容来源</label><select id="sourceKind"><option value="manual">手动输入</option><option value="dashboard">本地控制台</option><option value="tool_result">工具执行结果</option><option value="user_behavior">用户与 Agent 交互</option><option value="os_event">系统事件</option></select>
            <label for="tier">保存时长</label><select id="tier"><option value="">自动选择</option><option value="working">临时工作记忆</option><option value="session">当前会话记忆</option><option value="long_term">长期记忆</option></select>
            <div class="actions"><button class="primary" data-min-role="operator" onclick="ingest(event)">保存为记忆</button><button class="secondary" onclick="explainExtraction(event)">查看提取依据</button></div>
          </div>
          <div class="panel hint-card" aria-label="保存流程说明">
            <div class="item-title">保存前会发生什么</div>
            <div class="hint-row"><span class="hint-index">1</span><span>内容质量检查：过短、无信息量的文本不会形成记忆。</span></div>
            <div class="hint-row"><span class="hint-index">2</span><span>敏感检测：凭据、密钥等高风险内容会被阻止进入长期记忆。</span></div>
            <div class="hint-row"><span class="hint-index">3</span><span>去重合并：与已有记忆相同的内容会复用原编号，不会重复保存。</span></div>
          </div>
        </div>
        <div id="ingestFeedback" class="list" style="margin-top:14px"></div>
        <details><summary>详细记录：操作回执</summary><pre id="ingestOut" class="advanced-output"></pre></details>
      </section>

      <section id="search">
        <div class="crumbs"><button class="ghost" onclick="openTab('memory')">← 返回记忆中心</button><span class="crumbs-path">记忆中心 / <b>详细检索</b></span></div>
        <div class="page-header"><div><h2>详细检索</h2><p class="lead">查看召回来源和完整技术结果；日常搜索建议直接使用记忆中心。</p></div></div>
        <div class="panel">
          <label for="searchQuery">查询</label><input id="searchQuery" value="openKylin 服务内存限制">
          <div class="actions"><button class="primary" onclick="search(event)">检索</button><button class="secondary" onclick="graphSearch(event)">层次图谱</button></div>
        </div>
        <div id="searchCards" class="list" style="margin-top:14px"></div>
        <details><summary>详细记录：操作回执</summary><pre id="searchOut" class="advanced-output"></pre></details>
      </section>

      <section id="context">
        <div class="crumbs"><button class="ghost" onclick="openTab('memory')">← 返回记忆中心</button><span class="crumbs-path">记忆中心 / <b>上下文预览</b></span></div>
        <div class="page-header"><div><h2>上下文预览</h2><p class="lead">查看回答前会使用哪些记忆、内容来自哪里，以及候选被纳入或过滤的原因。</p></div></div>
        <div class="panel">
          <label for="contextQuery">问题</label><input id="contextQuery" value="openKylin 服务内存限制">
          <label for="tokenBudget">上下文长度上限</label><input id="tokenBudget" type="number" value="1200">
          <div class="actions"><button class="primary" onclick="contextBuild(event)">生成预览</button></div>
        </div>
        <div id="contextVisual" style="margin-top:14px"></div>
        <details><summary>详细记录：操作回执</summary><pre id="contextOut" class="advanced-output"></pre></details>
      </section>

      <section id="agent">
        <div class="page-header">
          <div><h2>Agent 工作台</h2><p class="lead">查看 Agent 在任务中使用了哪些记忆、形成了哪些偏好，以及这些偏好是否真正影响工具选择和回答。已遗忘内容只保留必要的核验记录。</p></div>
        </div>
        <div class="panel">
          <div class="grid two">
            <div><label for="agentSession">会话编号（可选）</label><input id="agentSession" placeholder="留空查看最近记录"></div>
            <div><label for="agentTask">任务内容</label><input id="agentTask" value="查找精准遗忘的实现位置"></div>
          </div>
          <div class="grid two">
            <div><label for="agentMode">运行方式</label><select id="agentMode"><option value="replay">本地回放（可复现）</option><option value="dry-run">只读预览</option><option value="openhands" disabled>真实 OpenHands（请从 Agent 接入入口运行）</option></select></div>
            <div><label for="agentName">Agent</label><select id="agentName"><option value="openhands">OpenHands</option></select></div>
          </div>
          <div class="actions"><button class="primary" data-min-role="operator" onclick="runAgentTask(event)">运行任务</button><button class="secondary" onclick="loadAgentOverview(event)">查看已有记录</button><button class="secondary" onclick="loadAgentActionImpact(event)">动作质量</button><button class="secondary" onclick="exportDiagnostics(event)">导出系统报告</button></div>
          <p class="muted">本地回放用于检查完整记忆流程；只读预览不会写入新内容。真实 OpenHands 由已配置的 Agent 接入入口运行，本页不会把回放结果标成真实模型结果。</p>
        </div>
        <div id="agentRunFeedback" class="list" style="margin-top:14px"></div>
        <div id="agentMetrics" class="grid four" style="margin-top:14px"></div>
        <div class="grid two" style="margin-top:14px">
          <div class="panel"><div class="health"><div><div class="item-title">最近会话</div><div class="muted">按任务归组，展开后查看用户请求、实际操作和最终答复</div></div></div><div id="agentTimeline" class="session-list" style="margin-top:12px"></div></div>
          <div class="panel"><div class="health"><div class="item-title">偏好依据</div></div><div id="agentPreferences" class="list" style="margin-top:12px"></div></div>
        </div>
        <div class="panel" style="margin-top:14px"><div class="item-title">三阶段上下文对比</div><div id="agentContexts" class="stage-columns" style="margin-top:12px"></div></div>
        <div class="panel" style="margin-top:14px"><div class="item-title">偏好生效证明</div><p class="muted">只有观察到实际工具命令或回答内容时才会判定“通过”；没有真实行为时显示“暂无证据”，不会把上下文注入误当成 Agent 已遵循。</p><div id="preferenceProofs" class="list" style="margin-top:12px"></div></div>
        <div class="panel" style="margin-top:14px"><div class="health"><div><div class="item-title">记忆驱动动作质量</div><div class="muted">依据真实 Agent 工具结果评估成功下界、失败与偏离风险；同类基线仅作观察性比较，不替代对照实验。</div></div><button class="secondary" onclick="loadAgentActionImpact(event)">刷新</button></div><div id="agentActionImpact" class="list" style="margin-top:12px"><div class="muted">点击“动作质量”查看记忆是否真正改善了工具行为。</div></div></div>
        <div class="panel" style="margin-top:14px">
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
        <div class="page-header">
          <div><h2>我的偏好</h2><p class="lead">查看 Agent 已记住的使用偏好、适用范围和实际使用结果。只有观察到工具或回答确实遵循偏好时，才会显示为已验证。</p></div>
        </div>
        <div class="panel">
          <div class="grid two">
            <div><label for="proofTask">验证任务</label><input id="proofTask" value="查找 MemGuardService 的定义"></div>
            <div><label for="proofStage">验证阶段</label><select id="proofStage"><option value="tool_selection">工具选择</option><option value="planning">任务规划</option><option value="response_generation">回答生成</option></select></div>
          </div>
          <div class="actions"><button class="primary" onclick="loadPreferences(event)">刷新偏好</button><button class="secondary" onclick="runAgentTask(event)">运行验证任务</button></div>
        </div>
        <div id="preferenceList" class="list" style="margin-top:14px"></div>
        <details><summary>详细记录：偏好证明</summary><pre id="preferenceOut" class="advanced-output"></pre></details>
      </section>

      <section id="memory-detail">
        <div class="crumbs"><button class="ghost" onclick="returnFromMemoryDetail()">← 返回上一页</button><span class="crumbs-path">记忆中心 / <b>记忆详情</b></span></div>
        <div class="page-header"><div><h2>记忆详情</h2><p class="lead">核对编号、摘要、来源事件、作用范围、版本和最近使用证据。</p></div></div>
        <div class="panel">
          <label for="memoryDetailId">记忆编号</label><input id="memoryDetailId" placeholder="从最近学到的内容或检索结果中选择">
          <div class="actions"><button class="primary" onclick="loadMemoryDetail(event)">查看详情</button><button class="danger" data-min-role="admin" onclick="forgetMemoryFromDetail(event)">遗忘这条记忆</button></div>
        </div>
        <div id="memoryDetailCards" class="list" style="margin-top:14px"></div>
        <details><summary>详细记录：记忆详情</summary><pre id="memoryDetailOut" class="advanced-output"></pre></details>
      </section>

      <section id="sensitive">
        <div class="page-header">
          <div><h2>内容保护</h2><p class="lead">敏感内容不会默认进入长期记忆或 Agent 上下文。这里可以查看最近保护结果，并在不保存文本的情况下测试检测策略。</p></div>
        </div>
        <div class="grid two">
          <div class="panel">
            <div class="health"><div class="item-title">保护状态</div><span class="badge ok">默认保护</span></div>
            <div class="list" style="margin-top:12px">
              <div class="item"><div class="item-title">高风险凭据</div><div class="muted">检测到 API Key、令牌或密码特征时，默认阻止进入长期记忆。</div></div>
              <div class="item"><div class="item-title">进入 Agent 上下文</div><div class="muted">被阻止的原文不会进入 RAG；脱敏内容只保留受控摘要。</div></div>
              <div class="item"><div class="item-title">处理记录</div><div class="muted">保护动作会写入操作记录，管理员可以在审计与证据中复核。</div></div>
            </div>
          </div>
          <div class="panel">
            <div class="item-title">敏感文本测试</div>
            <p class="muted">测试只返回检测摘要，不会把输入保存为记忆。</p>
            <label for="sensitiveTestText">待检查文本</label>
            <textarea id="sensitiveTestText" placeholder="输入一段需要检查的文本"></textarea>
            <div class="actions"><button class="primary" onclick="testSensitiveText(event)">检查保护策略</button></div>
            <div id="sensitiveTestResult" class="list" style="margin-top:12px"><div class="muted">尚未执行检查。</div></div>
          </div>
        </div>
        <div class="section-block" style="margin-top:14px">
          <div class="section-block-header"><strong>最近敏感阻断</strong><button class="ghost" onclick="loadSensitiveProtection(event)">刷新</button></div>
          <div id="sensitiveBlockList" class="section-block-body"><div class="muted">正在读取最近保护结果…</div></div>
        </div>
        <details><summary>查看完整技术详情</summary><pre id="sensitiveOut" class="advanced-output"></pre></details>
      </section>

      <section id="notifications">
        <div class="crumbs"><button class="ghost" onclick="openTab('overview')">← 返回总览</button><span class="crumbs-path">总览 / <b>通知中心</b></span></div>
        <div class="page-header">
          <div><h2>通知中心</h2><p class="lead">通知中心记录重要记忆变化和风险事件。普通检索、缓存命中和本机智能检索降级不会频繁打扰桌面，只在这里保留状态变化。</p></div>
          <div class="actions"><button class="primary" onclick="loadNotifications(event)">刷新通知</button><button class="secondary" data-min-role="admin" onclick="openTab('diagnostics')">查看系统维护</button></div>
        </div>
        <div id="notificationList" class="list"></div>
        <details><summary>详细记录：通知来源</summary><pre id="notificationOut" class="advanced-output"></pre></details>
      </section>

      <section id="forget">
        <div class="page-header">
          <div><h2>精准遗忘</h2><p class="lead">先预览会影响哪些记忆，再确认执行。系统会列出候选编号、匹配依据和作用范围，帮助避免误删。</p></div>
        </div>
        <ol class="workflow-steps" aria-label="精准遗忘步骤">
          <li><span class="workflow-step-number">1</span><div><strong>预览影响</strong><span class="muted">核对候选编号和匹配依据。</span></div></li>
          <li><span class="workflow-step-number">2</span><div><strong>确认遗忘</strong><span class="muted">确认后移除选中的受管记忆。</span></div></li>
          <li><span class="workflow-step-number">3</span><div><strong>自动复检</strong><span class="muted">复查检索、存储和操作记录。</span></div></li>
        </ol>
        <div class="panel">
          <div class="item-title">遗忘二次确认</div>
          <p class="muted">只有点击确认按钮后，选中的记忆才会从可查询内容和相关索引中移除，并保留必要的操作记录。</p>
          <label for="forgetRequest">自然语言请求</label><input id="forgetRequest" value="忘掉临时项目代号 blue-river">
          <div class="actions"><button class="secondary" data-min-role="operator" onclick="forget(event, false)">预览影响</button><button id="forgetConfirmButton" class="danger" data-min-role="admin" onclick="forget(event, true)" disabled>确认遗忘并整理存储</button></div>
          <p class="warning">确认后，相关内容将不能再被 Agent 查询。此操作不能在页面中直接撤销。</p>
        </div>
        <div id="forgetCards" class="list" style="margin-top:14px"></div>
        <div class="section-block" style="margin-top:14px">
          <div class="section-block-header"><strong>最近遗忘验证</strong><button class="ghost" onclick="loadRecentForgetting(event, true)">刷新</button></div>
          <div id="recentForgetList" class="section-block-body"><div class="empty-state">正在读取最近验证结果…</div></div>
        </div>
        <details><summary>详细记录：操作回执</summary><pre id="forgetOut" class="advanced-output"></pre></details>
      </section>

      <section id="evidence" data-min-role="admin">
        <div class="page-header">
          <div><h2>审计与证据</h2><p class="lead">集中查看操作记录完整性、最近关键事件和本机状态证据。导出内容默认不包含记忆正文，适合交付复核和问题排查。</p></div>
        </div>
        <div class="grid two">
          <div class="panel">
            <div class="health"><div class="item-title">审计链状态</div><span id="evidenceAuditBadge" class="badge">待检查</span></div>
            <p id="evidenceAuditSummary" class="muted">打开页面后会校验操作记录完整性。</p>
            <div class="actions"><button class="secondary" onclick="loadEvidenceHub(event)">重新校验</button></div>
          </div>
          <div class="panel">
            <div class="item-title">证据导出</div>
            <p class="muted">包含本机状态、审计链、检索策略、资源状态和复核项目说明，不包含记忆正文。</p>
            <div class="actions"><button class="primary" onclick="exportEvidence(event)">导出证据包</button><button class="secondary" onclick="openTab('audit')">查看详细记录</button></div>
          </div>
        </div>
        <div class="panel" style="margin-top:14px">
          <div class="item-title">最近关键事件</div>
          <div id="evidenceEventList" class="list" style="margin-top:12px"><div class="muted">正在读取最近事件…</div></div>
        </div>
        <details><summary>查看完整技术详情</summary><pre id="evidenceOut" class="advanced-output"></pre></details>
      </section>

      <section id="audit" data-min-role="admin">
        <div class="crumbs"><button class="ghost" onclick="openTab('evidence')">← 返回审计与证据</button><span class="crumbs-path">审计与证据 / <b>审计记录</b></span></div>
        <div class="page-header">
          <div><h2>审计记录</h2><p class="lead">最近的写入、检索、保护与遗忘操作记录，按时间倒序显示。</p></div>
          <div class="actions"><button class="primary" onclick="audit(event)">刷新</button></div>
        </div>
        <details><summary>运行记录</summary><pre id="auditOut" class="advanced-output"></pre></details>
      </section>

      <section id="diagnostics">
        <div class="page-header">
          <div><h2>系统维护</h2><p class="lead">管理员视图用于本机维护和问题排查，集中查看操作记录、检索状态、记忆策略、本机 AI 能力和资源限制，并支持一键导出系统报告。</p></div>
        </div>
        <div class="panel">
          <div class="actions" style="margin-top:0">
            <button class="primary" data-min-role="admin" onclick="verifyAudit(event)">校验操作记录</button>
            <button class="secondary" data-min-role="admin" onclick="retrievalStatus(event)">检查检索方式</button>
            <button class="secondary" data-min-role="admin" onclick="memoryPolicy(event)">查看记忆策略</button>
            <button class="secondary" data-min-role="admin" onclick="diagnostics(event)">聚合系统状态</button>
            <button class="secondary" data-min-role="admin" onclick="exportDiagnostics(event)">导出系统报告</button>
          </div>
          <p class="muted">维护结果用于定位操作记录、检索和记忆分层问题。导出前会按保护规则处理敏感内容。</p>
          <details class="auth-settings"><summary>访问设置</summary><form id="dashboardAuthForm" class="overview-more-body" onsubmit="event.preventDefault()">
            <label for="apiToken">访问凭证（仅在启用本地权限时需要）</label>
            <input id="apiToken" type="password" placeholder="输入管理员提供的访问凭证" autocomplete="off" spellcheck="false">
          </form></details>
        </div>
        <div class="status-list" style="margin-top:14px" aria-label="系统组件状态">
          <div class="status-row"><div class="health-title"><span id="auditDot" class="status-dot"></span><span>操作记录</span></div><span class="muted">写入、遗忘和安全操作是否可追溯</span><span id="auditBadge" class="badge">待检查</span></div>
          <div class="status-row"><div class="health-title"><span id="retrievalDot" class="status-dot"></span><span>记忆检索</span></div><span class="muted">当前使用本机增强或稳定基础检索</span><span id="retrievalBadge" class="badge">待检查</span></div>
          <div class="status-row"><div class="health-title"><span id="sdkDot" class="status-dot"></span><span>本机智能检索</span></div><span class="muted">Kylin AI 能力不可用时自动降级</span><span id="sdkBadge" class="badge">待检查</span></div>
          <div class="status-row"><div class="health-title"><span id="osDot" class="status-dot"></span><span>系统资源</span></div><span class="muted">服务资源限制和运行环境状态</span><span id="osBadge" class="badge">待检查</span></div>
        </div>
        <div class="panel" style="margin-top:14px" data-min-role="admin">
          <div class="health"><div class="item-title">价值感知记忆预算</div><span class="badge">可逆维护</span></div>
          <p class="muted">按价值、治理风险和估算字节成本生成长期记忆降级预览。显式偏好和敏感内容始终受保护，确认后只追加 session 版本。</p>
          <div class="search-toolbar">
            <div><label for="budgetTargetBytes">长期层目标字节数</label><input id="budgetTargetBytes" type="number" min="128" value="1048576"></div>
            <button class="primary" onclick="loadMemoryBudget(event)">生成预算预览</button>
            <button id="applyBudgetButton" class="danger" onclick="applyMemoryBudget(event)" disabled>确认执行建议</button>
          </div>
          <div id="memoryBudgetSummary" class="list" style="margin-top:12px"><div class="muted">输入目标后生成预览。</div></div>
          <details><summary>预算计划明细</summary><pre id="memoryBudgetOut" class="advanced-output"></pre></details>
        </div>
        <div id="refreshWarnings" class="list" style="display:none;margin-top:14px"></div>
        <div class="result-section" style="margin-top:14px">
          <div class="section-block-header"><strong>系统报告包含的内容</strong><span class="muted">不包含记忆正文</span></div>
          <div class="section-block-body">
          <div class="linear-list">
            <div class="list-row"><div><div class="item-title">本机运行状态</div><div class="muted">记忆空间、后台服务和运行方式。</div></div><span class="badge">基础信息</span></div>
            <div class="list-row"><div><div class="item-title">检索与记忆策略</div><div class="muted">当前检索方式、有效期和冷热分层状态。</div></div><span class="badge">策略信息</span></div>
            <div class="list-row"><div><div class="item-title">资源与本机智能检索</div><div class="muted">资源限制、Kylin AI 能力和自动降级状态。</div></div><span class="badge">运行信息</span></div>
            <div class="list-row"><div><div class="item-title">操作记录</div><div class="muted">完整性校验和最近关键事件，便于管理员复核。</div></div><span class="badge">安全信息</span></div>
          </div>
          </div>
        </div>
        <details><summary>详细记录：操作回执</summary><pre id="diagnosticsOut" class="advanced-output"></pre></details>
      </section>

      <section id="acceptance" data-min-role="admin">
        <div class="page-header">
          <div><h2>系统检查</h2><p class="lead">按顺序检查运行环境、记忆流程、安全保护和桌面集成。自动项目由本机白名单执行；需要观察桌面菜单或弹窗的项目会明确标记为人工确认。</p></div>
        </div>
        <div class="grid three">
          <div class="panel emphasis">
            <div class="item-title">复核进度</div>
            <div class="metric" id="acceptanceProgress">0/0</div>
            <div class="progress"><span id="acceptanceProgressBar"></span></div>
            <div id="acceptanceAdvice" class="muted" style="margin-top:8px">加载复核清单中</div>
          </div>
          <div class="panel">
            <div class="item-title">推荐顺序</div>
            <div class="list" style="margin-top:10px">
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
    // Paths are from Lucide and are embedded so the local console works offline.
    const lucidePaths = {
      home:'<path d="M3 11.5 12 4l9 7.5"/><path d="M5 10.5V20h14v-9.5"/><path d="M9 20v-6h6v6"/>',
      bot:'<rect width="16" height="12" x="4" y="8" rx="2"/><path d="M9 12h.01M15 12h.01M9 16h6M12 4v4"/>',
      database:'<ellipse cx="12" cy="5" rx="8" ry="3"/><path d="M4 5v6c0 1.7 3.6 3 8 3s8-1.3 8-3V5M4 11v6c0 1.7 3.6 3 8 3s8-1.3 8-3v-6"/>',
      sliders:'<path d="M4 21v-7M4 10V3M12 21v-9M12 8V3M20 21v-5M20 12V3M1 14h6M9 8h6M17 16h6"/>',
      shield:'<path d="M20 13c0 5-3.5 7.5-8 9-4.5-1.5-8-4-8-9V5l8-3 8 3v8Z"/><path d="m9 12 2 2 4-4"/>',
      trash:'<path d="M3 6h18M8 6V4h8v2M19 6l-1 14H6L5 6M10 11v5M14 11v5"/>',
      wrench:'<path d="M14.7 6.3a4 4 0 0 0-5-5l2.1 2.1-2.4 2.4-2.1-2.1a4 4 0 0 0 5 5L20 16.4a2.1 2.1 0 0 1-3 3l-7.7-7.7"/>',
      evidence:'<path d="M14 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V8Z"/><path d="M14 2v6h6M9 15l2 2 4-4"/>',
      clipboard:'<rect width="14" height="18" x="5" y="4" rx="2"/><path d="M9 4V2h6v2M9 12l2 2 4-4"/>',
      search:'<circle cx="11" cy="11" r="7"/><path d="m20 20-4-4"/>'
    };
    function lucideIcon(name) {
      return '<svg class="ui-icon" viewBox="0 0 24 24" aria-hidden="true">' + (lucidePaths[name] || lucidePaths.home) + '</svg>';
    }
    const navIcons = {overview:'home', agent:'bot', memory:'database', preferences:'sliders', sensitive:'shield', forget:'trash', diagnostics:'wrench', evidence:'evidence', acceptance:'clipboard'};
    document.querySelectorAll('nav button[data-tab]').forEach(button => {
      button.insertAdjacentHTML('afterbegin', lucideIcon(navIcons[button.dataset.tab] || 'home'));
    });
    document.querySelectorAll('[data-lucide]').forEach(node => {
      node.innerHTML = lucideIcon(node.dataset.lucide);
    });
    const tabLoadedAt = new Map([['overview', Date.now()]]);
    const dynamicTabTtlMs = 5000;
    const tabParents = {ingest:'memory', search:'memory', context:'memory', 'memory-detail':'memory', notifications:'overview', audit:'evidence', compare:'overview'};
    const feedbackHomeCache = {at:0, promise:null};
    function fetchFeedbackHome(limit, timeoutMs, force) {
      const now = Date.now();
      if (!force && feedbackHomeCache.promise && now - feedbackHomeCache.at < 10000)
        return feedbackHomeCache.promise;
      const promise = getJson('/api/feedback-home?limit=' + (limit || 30), timeoutMs || 10000);
      feedbackHomeCache.at = now;
      feedbackHomeCache.promise = promise;
      promise.catch(() => { if (feedbackHomeCache.promise === promise) feedbackHomeCache.promise = null; });
      return promise;
    }
    const rawOutputs = new Map();
    function setRawOutput(id, payload) {
      const node = document.getElementById(id);
      if (!node) return;
      rawOutputs.set(id, payload);
      const details = node.closest('details');
      if (!details || details.open) {
        node.textContent = typeof payload === 'string' ? payload : pretty(payload);
        return;
      }
      node.textContent = '';
      if (!details.dataset.lazyBound) {
        details.dataset.lazyBound = '1';
        details.addEventListener('toggle', () => {
          if (!details.open) return;
          const value = rawOutputs.get(id);
          node.textContent = typeof value === 'string' ? value : pretty(value ?? {});
        });
      }
    }
    let activeTab = 'overview';
    let memoryDetailReturnTab = 'memory';
    let memoryDetailPreview = null;
    document.querySelectorAll('nav button').forEach(btn => btn.addEventListener('click', () => {
      openTab(btn.dataset.tab, {force:btn.dataset.tab === activeTab});
    }));
    document.addEventListener('click', event => {
      const button = event.target.closest('button[data-action]');
      if (!button) return;
      const action = button.dataset.action;
      const actionEvent = {currentTarget:button};
      if (action === 'open-tab') openTab(button.dataset.tabTarget || 'overview');
      else if (action === 'open-memory-detail') openMemoryDetail(button.dataset.memoryId || '');
      else if (action === 'copy-memory-id') copyMemoryId(button.dataset.memoryId || '');
      else if (action === 'preference-proof') loadPreferenceProof(actionEvent, button.dataset.memoryId || '');
      else if (action === 'preference-evidence') loadPreferenceEvidence(actionEvent, button.dataset.memoryId || '');
      else if (action === 'resolve-fact-conflict') resolveFactConflict(actionEvent, button.dataset.conflictId || '', button.dataset.decision || '', button.dataset.memoryId || '', button.dataset.versionId || '');
      else if (action === 'notification-target') openNotificationTarget(button.dataset.notificationAction || '');
      else if (action === 'diagnostic-warning') runDiagnosticWarning(actionEvent, button.dataset.warningAction || '');
      else if (action === 'return-memory-detail') returnFromMemoryDetail();
      else if (action === 'retry-memory-detail') loadMemoryDetail(actionEvent);
    });
    async function openTab(tab, options) {
      const settings = options || {};
      const requestedButton = Array.from(document.querySelectorAll('nav button'))
        .find(item => item.dataset.tab === tab);
      const requestedSection = document.getElementById(tab);
      if ((requestedButton && requestedButton.hidden) || (requestedSection && requestedSection.hidden)) {
        operationFeedback('当前访问权限不足，已返回个人工作台。', 'warn');
        if (tab !== 'overview')
          return openTab('overview', {history:'replace'});
        return;
      }
      setOperationStatus('', '');
      const navTab = tabParents[tab] || tab;
      document.querySelectorAll('nav button').forEach(item => item.classList.toggle('active', item.dataset.tab === navTab));
      document.querySelectorAll('section').forEach(item => item.classList.toggle('active', item.id === tab));
      activeTab = tab;
      const navButton = requestedButton
        || Array.from(document.querySelectorAll('nav button')).find(item => item.dataset.tab === navTab);
      if (navButton) navButton.scrollIntoView({block:'nearest', inline:'nearest'});
      window.scrollTo({top:0, behavior:'auto'});
      if (settings.history !== 'none' && window.location.hash !== '#' + tab) {
        const method = settings.history === 'replace' ? 'replaceState' : 'pushState';
        window.history[method]({tab:tab}, '', '#' + tab);
      }
      await loadTabData(tab, null, {force:Boolean(settings.force)});
    }
    async function loadTabData(tab, event, options) {
      if (!tab) return;
      const settings = options || {};
      const lastLoaded = Number(tabLoadedAt.get(tab) || 0);
      if (!settings.force && lastLoaded && Date.now() - lastLoaded < dynamicTabTtlMs) return;
      const loaders = {
        overview: loadOverviewEvidence,
        audit: audit,
        agent: loadAgentOverview,
        memory: loadMemoryWorkspace,
        preferences: loadPreferences,
        sensitive: loadSensitiveProtection,
        evidence: loadEvidenceHub,
        notifications: loadNotifications,
        acceptance: loadAcceptancePlan,
        forget: loadRecentForgetting,
        diagnostics: diagnostics,
      };
      const loader = loaders[tab];
      if (!loader) {
        tabLoadedAt.set(tab, Date.now());
        return;
      }
      try {
        const result = await loader(event);
        if (result !== null)
          tabLoadedAt.set(tab, Date.now());
      } catch (err) {
        const message = err && err.userMessage
          ? err.userMessage
          : '页面内容暂时没有加载出来。请点击本页的刷新按钮重试。';
        showError(message);
      }
    }
    function openHashTab() {
      const tab = window.location.hash.replace(/^#/, '');
      if (!tab || !document.getElementById(tab)) return;
      openTab(tab, {history:'none', force:true});
    }
    const bootstrapParams = new URLSearchParams(window.location.hash.slice(1));
    const bootstrapToken = bootstrapParams.get('token') || '';
    const bootstrapTab = bootstrapParams.get('tab') || '';
    let memorySessionId = sessionStorage.getItem('memguardos_memory_session') || crypto.randomUUID();
    sessionStorage.setItem('memguardos_memory_session', memorySessionId);
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
      tabLoadedAt.clear();
      tabLoadedAt.set('overview', Date.now());
      try {
        const capabilities = await loadSessionCapabilities();
        await refreshForSession(capabilities);
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
      if (state === 'bad') showError('');
      box.style.display = message ? 'block' : 'none';
      box.className = 'operation-status ' + (state || '');
      box.textContent = message || '';
    }
    function buttonText(button) {
      return button ? button.textContent.replace(/\\s+/g, ' ').trim() : '操作';
    }
    function requestError(message, payload) {
      const error = new Error(String(message || '请求没有完成，请重试。'));
      error.userMessage = error.message;
      error.payload = payload || null;
      return error;
    }
    let operationSequence = 0;
    let activeMutation = null;
    let currentRoleRank = 0;
    const roleRanks = {anonymous:0, viewer:1, operator:2, admin:3};
    function privilegedActionMarkup(label, tab) {
      if (currentRoleRank < roleRanks.admin)
        return '<div class="muted">此操作需要管理员处理。</div>';
      return '<div class="mini-actions"><button class="secondary" data-action="open-tab" data-tab-target="'
        + esc(tab) + '">' + esc(label) + '</button></div>';
    }
    function operationFeedback(message, state) {
      return {operationFeedback:{message:String(message || ''), state:state || 'ok'}};
    }
    async function withBusy(event, work) {
      const operationId = ++operationSequence;
      const button = event && event.currentTarget ? event.currentTarget : null;
      const label = button ? button.textContent : '';
      const wasDisabled = button ? button.disabled : false;
      showError('');
      if (button) {
        button.disabled = true;
        button.setAttribute('aria-busy', 'true');
        button.innerHTML = '<span class="button-label"><span class="spinner" aria-hidden="true"></span>' + esc(label) + '</span>';
      }
      try {
        if (button)
          setOperationStatus('正在处理：' + buttonText({textContent: label}) + '。请稍候，页面可继续查看其他信息。', 'warn');
        const result = await work();
        if (button && operationId === operationSequence) {
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
          const preserveState = button.dataset.preserveBusyState === 'true';
          delete button.dataset.preserveBusyState;
          button.removeAttribute('aria-busy');
          if (!preserveState) {
            button.disabled = wasDisabled;
            button.textContent = label;
          }
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
    async function getJson(url, timeoutMs) {
      try {
        const r = await fetchWithTimeout(url, {headers: authHeaders()}, timeoutMs || 8000);
        const data = await r.json();
        if (!data || typeof data !== 'object') {
          throw requestError('本地服务返回内容格式异常。请稍后重试，或进入系统维护导出报告。');
        }
        if (!r.ok || data.ok === false) {
          const message = userErrorMessage(data, '请求没有完成。');
          throw requestError(message, data);
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
          throw requestError(message, payload);
        }
        feedbackHomeCache.promise = null;
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
        stale_preview:'会话记忆在预览后发生变化，请重新结束当前会话以核对影响范围。',
        invalid_json:'请求内容格式不正确。请刷新页面后重试。',
        payload_too_large:'提交内容过大，请缩短内容或拆分后重试。',
        not_found:'没有找到请求的内容，请返回上一步重新选择。',
        memory_not_found:'这条记忆已不存在、已完成遗忘，或不属于当前记忆空间。请返回原列表重新选择。',
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
    async function refreshForSession(payload) {
      const result = (payload || {}).result || {};
      if (result.role === 'anonymous') {
        openTab('diagnostics');
        setOperationStatus('请先填写本机访问凭证。验证通过后，系统状态和可用操作会自动显示。', 'warn');
        return null;
      }
      setOperationStatus('', '');
      return refresh();
    }
    function badge(id, state, text) {
      const el = document.getElementById(id);
      if (!el) return;
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
        user_cli:'终端自然语言输入',
        dashboard:'本地控制台',
        tool_result:'工具执行结果',
        verified_procedure:'已验证的流程经验',
        user_behavior:'用户与 Agent 交互',
        agent_interaction:'用户与 Agent 交互',
        agent:'Agent 自动形成',
        dashboard_acceptance:'系统检查',
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
      const labels = {none:'未发现敏感风险', low:'低风险', medium:'中等风险', high:'高风险', critical:'严重风险', password_secret:'密码或口令', weak_password_hint_cn:'密码或口令', one_time_code:'验证码或一次性口令'};
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
    function namespaceLabel(value) {
      const namespace = String(value || '').trim();
      if (!namespace) return '未加载';
      if (namespace === 'default') return '默认记忆空间';
      return namespace;
    }
    function eventRoleLabel(value) {
      const labels = {user:'用户', assistant:'Agent', tool:'工具', system:'系统'};
      return labels[String(value || '')] || '系统';
    }
    function eventTypeLabel(value) {
      const labels = {user_message:'用户消息', assistant_message:'Agent 回复', tool_call:'工具调用', tool_result:'工具结果', system_message:'系统消息'};
      return labels[String(value || '')] || '交互记录';
    }
    function formatAgentEventBody(item) {
      const event = item || {};
      if (event.forgotten) {
        const excerpt = String(event.source_excerpt || '').trim();
        return excerpt
          ? '共享事件原文已清理；当前记忆的独立来源片段：' + excerpt
          : '该内容已完成遗忘，仅保留不可还原的核验信息。';
      }
      if (event.event_type === 'tool_action') {
        const command = String(event.command || '').trim();
        const state = Number(event.exit_code || 0) === 0 ? '执行成功' : '执行未完成';
        return state + (command ? '：' + command : '。');
      }
      if (event.event_type === 'tool_call') {
        const args = event.tool_args && typeof event.tool_args === 'object' ? event.tool_args : {};
        const command = String(args.command || '').trim();
        if (command) return '执行命令：' + command;
        const toolName = String(event.tool_name || '').trim();
        return toolName ? '调用工具：' + toolName : 'Agent 发起了一次工具调用。';
      }
      if (event.event_type === 'tool_result') {
        const result = String(event.tool_result || '').trim();
        const prefix = Number(event.exit_code || 0) === 0 ? '执行成功：' : '执行未完成：';
        return prefix + (result || '工具没有返回可展示内容。');
      }
      return String(event.content || '').trim() || '没有可展示的文本内容。';
    }
    function agentTimelineMarkup(items) {
      return items.map(item => {
        const classes = ['timeline-row'];
        if (item.event_type === 'tool_call' || item.event_type === 'tool_result' || item.event_type === 'tool_action') classes.push('tool');
        if (item.forgotten) classes.push('forgotten');
        const body = formatAgentEventBody(item);
        const displayBody = body.length > 360 ? body.slice(0, 360) + '…' : body;
        return '<div class="' + classes.join(' ') + '"><span class="timeline-dot"></span>'
          + '<div class="timeline-meta">' + esc(eventRoleLabel(item.role)) + ' · ' + esc(item.event_type === 'tool_action' ? '实际操作' : eventTypeLabel(item.event_type)) + '</div>'
          + '<div class="timeline-body">' + esc(displayBody) + '</div></div>';
      }).join('');
    }
    function visibleSessionEvents(events) {
      const ordered = events.slice().sort((left, right) =>
        Number(left.sequence || 0) - Number(right.sequence || 0)
          || String(left.timestamp || '').localeCompare(String(right.timestamp || ''))
      );
      const visible = [];
      const hasAssistantReply = ordered.some(item => item.event_type === 'assistant_message');
      ordered.forEach((item, index) => {
        if (item.event_type === 'policy_decision' || item.event_type === 'system_message' || item.event_type === 'tool_result') return;
        if (item.event_type === 'tool_call') {
          const toolName = String(item.tool_name || '');
          const result = ordered.slice(index + 1).find(candidate =>
            candidate.event_type === 'tool_result' && String(candidate.tool_name || '') === toolName
          );
          if (toolName === 'finish') {
            if (!hasAssistantReply && result && Number(result.exit_code || 0) === 0) {
              visible.push({event_type:'assistant_message', role:'assistant', content:String(result.tool_result || (item.tool_args || {}).message || ''), sequence:item.sequence});
            }
            return;
          }
          visible.push({
            event_type:'tool_action', role:'tool', sequence:item.sequence,
            command:String(((item.tool_args || {}).command) || ''),
            exit_code:result ? result.exit_code : 1
          });
          return;
        }
        if (item.event_type === 'user_message' || item.event_type === 'assistant_message') visible.push(item);
      });
      return visible;
    }
    function sessionStatus(events) {
      const completed = events.some(item => item.event_type === 'assistant_message')
        || events.some(item => item.event_type === 'tool_result' && item.tool_name === 'finish' && Number(item.exit_code || 0) === 0);
      if (completed) return {label:'已完成', css:'ok'};
      const failed = events.some(item => item.event_type === 'tool_result' && Number(item.exit_code || 0) !== 0);
      return failed ? {label:'需要处理', css:'bad'} : {label:'进行中', css:'warn'};
    }
    function agentSessionMarkup(items) {
      const sessions = new Map();
      items.forEach(item => {
        const id = String(item.session_id || '未分配会话');
        if (!sessions.has(id)) sessions.set(id, []);
        sessions.get(id).push(item);
      });
      const groups = Array.from(sessions.entries()).map(([id, events]) => {
        const ordered = events.slice().sort((left, right) => String(left.timestamp || '').localeCompare(String(right.timestamp || '')));
        const userEvent = ordered.find(item => item.event_type === 'user_message');
        const task = String((userEvent || {}).content || 'Agent 交互任务').trim();
        return {id, events:ordered, task, latest:String((ordered[ordered.length - 1] || {}).timestamp || '')};
      }).sort((left, right) => right.latest.localeCompare(left.latest));
      const shown = groups.slice(0, 4);
      const markup = shown.map((group, index) => {
        const status = sessionStatus(group.events);
        const visible = visibleSessionEvents(group.events);
        const title = group.task.length > 52 ? group.task.slice(0, 52) + '…' : group.task;
        return '<details class="session-group"' + (index === 0 ? ' open' : '') + '><summary>'
          + '<div class="session-title"><strong>' + esc(title) + '</strong><span>会话 ' + esc(shortMemoryId(group.id)) + ' · ' + esc(visible.length) + ' 项可见记录</span></div>'
          + '<span class="badge ' + status.css + '">' + status.label + '</span></summary>'
          + '<div class="session-body"><div class="timeline">' + agentTimelineMarkup(visible) + '</div></div></details>';
      }).join('');
      const older = groups.length - shown.length;
      return markup + (older > 0 ? '<div class="muted" style="padding:4px 2px">另有 ' + esc(older) + ' 个较早会话，可通过会话编号筛选查看。</div>' : '');
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
    function memoryCategoryLabel(item) {
      const row = item || {};
      const topic = String(row.preference_topic || '');
      const subtype = String(row.subtype || '');
      if (['code_search_tool','test_runner_command','service_log_tool'].includes(topic)) return '工具偏好';
      if (['response_order','response_language','response_verbosity'].includes(topic)) return '回答偏好';
      if (topic === 'high_risk_confirmation' || row.type === 'sensitive_policy') return '安全策略';
      if (['procedural','procedural_trace','tool_failure_case','tool_trace','workflow'].includes(subtype)) return '操作经验';
      if (subtype === 'user_habit') return '用户偏好';
      return memoryTypeLabel(row.type || 'memory');
    }
    function memorySummaryLabel(item) {
      const row = item || {};
      const preferenceSummary = row.preference_topic && row.preference_value
        ? topicLabel(row.preference_topic) + '：' + row.preference_value
        : '';
      return row.summary_for_user || preferenceSummary || row.summary || row.content || row.memory_id || '未命名记忆';
    }
    function shortMemoryId(value) {
      const id = String(value || '');
      if (!id) return '暂无编号';
      return id.length > 16 ? id.slice(0, 12) + '…' : id;
    }
    function matchReasonLabel(row) {
      if (row && row.match_reason) return String(row.match_reason);
      const source = String((row || {}).match_source || '');
      if (source === 'graph') return '与相关实体或关系直接关联';
      if (source === 'text') return '正文或摘要包含查询关键词';
      return '综合比较关键词、语义和适用范围后命中';
    }
    function relevanceLabel(row, index) {
      const rank = Number((row || {}).rank || index + 1);
      if (rank <= 1) return '最相关';
      if (rank <= 3) return '较相关';
      return '补充结果';
    }
    function memoryResultMarkup(row, index, mode) {
      const item = row || {};
      const memoryId = String(item.memory_id || '');
      const source = sourceLabel(item.source_kind || ((item.source || {}).kind) || '');
      const sens = item.sensitivity_level || (item.sensitivity && item.sensitivity.level) || 'none';
      const duplicateCount = Math.max(1, Number(item.duplicate_count || 1));
      const reason = mode === 'recent' ? '最近保存到当前记忆空间' : matchReasonLabel(item);
      const rankBadge = mode === 'recent'
        ? '<span class="badge">最近记忆</span>'
        : '<span class="badge ' + (Number(item.rank || index + 1) <= 3 ? 'ok' : '') + '">' + esc(relevanceLabel(item, index)) + '</span>';
      const duplicateBadge = duplicateCount > 1
        ? '<span class="badge warn">已折叠 ' + esc(duplicateCount) + ' 条相似记录</span>'
        : '';
      return '<div class="memory-result-row">'
        + '<div><div class="memory-id" title="' + esc(memoryId) + '">' + esc(shortMemoryId(memoryId)) + '</div><div style="margin-top:7px">' + rankBadge + '</div></div>'
        + '<div><div class="memory-summary">' + esc(memorySummaryLabel(item)) + '</div>'
        + '<div class="result-meta"><span>' + esc(memoryCategoryLabel(item)) + '</span><span>' + esc(source) + '</span><span>' + esc(scopeLabel(item.scope || item.preference_scope || 'default')) + '</span><span>' + esc(tierLabel(item.tier || '')) + '</span><span>' + esc(sensitivityLabel(sens)) + '</span>' + duplicateBadge + '</div>'
        + '<div class="result-reason">命中原因：' + esc(reason) + '</div></div>'
        + '<div class="result-actions"><button class="secondary" data-action="open-memory-detail" data-memory-id="' + esc(memoryId) + '">查看详情</button><button class="secondary" data-action="copy-memory-id" data-memory-id="' + esc(memoryId) + '">复制编号</button></div></div>';
    }
    function renderMemoryWorkspaceRows(rows, mode) {
      const all = Array.isArray(rows) ? rows : [];
      const primary = all.slice(0, 3);
      const more = all.slice(3);
      const main = document.getElementById('memoryWorkspaceResults');
      const moreBox = document.getElementById('memoryMoreResults');
      const moreBody = document.getElementById('memoryMoreResultsBody');
      if (!main) return;
      if (!all.length) {
        main.innerHTML = '<div class="empty-state"><strong>没有找到相关记忆</strong><div style="margin-top:6px">可以换一个更具体的关键词，或先添加一条记忆。</div></div>';
        if (moreBox) moreBox.hidden = true;
        return;
      }
      main.innerHTML = primary.map((row, index) => memoryResultMarkup(row, index, mode)).join('');
      if (moreBox && moreBody) {
        moreBox.hidden = !more.length;
        moreBox.open = false;
        moreBody.innerHTML = more.map((row, index) => memoryResultMarkup(row, index + 3, mode)).join('');
      }
    }
    async function copyMemoryId(memoryId) {
      if (!memoryId) return;
      try {
        await navigator.clipboard.writeText(memoryId);
        setOperationStatus('记忆编号已复制。', 'ok');
      } catch (_err) {
        setOperationStatus('无法自动复制，请在记忆详情中选择完整编号。', 'warn');
      }
    }
    async function openMemorySearch() {
      await openTab('memory');
      const input = document.getElementById('memorySearchQuery');
      if (input) input.focus();
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
      const cards = document.getElementById('statusCards');
      if (cards) cards.innerHTML = items.map(([k,v]) => '<div class="item"><div class="item-title">' + esc(k) + '</div><div class="muted">' + esc(v) + '</div></div>').join('');
      const namespace = document.getElementById('namespaceCount');
      if (namespace) namespace.textContent = namespaceLabel(result.namespace);
    }
    function renderSearchCards(payload) {
      const rows = payload.result || [];
      if (!rows.length) {
        document.getElementById('searchCards').innerHTML =
          '<div class="item"><div class="item-title">没有找到相关记忆</div>'
          + '<div class="muted">可以调整关键词或先写入一条记忆，再重新检索。</div></div>';
        return;
      }
      document.getElementById('searchCards').innerHTML = rows.slice(0, 8).map((row, idx) => memoryResultMarkup(row, idx, 'search')).join('');
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
      if (forgetConfirmButton.getAttribute('aria-busy') === 'true')
        forgetConfirmButton.dataset.preserveBusyState = 'true';
    }
    function invalidateAgentForgetPreview() {
      agentForgetPreview = null;
      agentForgetConfirmButton.disabled = true;
      agentForgetConfirmButton.textContent = '确认遗忘';
      if (agentForgetConfirmButton.getAttribute('aria-busy') === 'true')
        agentForgetConfirmButton.dataset.preserveBusyState = 'true';
    }
    function agentForgetIdentity() {
      return JSON.stringify({
        agent_name:agentForgetName.value,
        preference_topic:agentForgetTopic.value,
        session_id:agentSession.value.trim()
      });
    }
    function externalResidueMessage(residue) {
      if (residue && residue.user_explanation)
        return String(residue.user_explanation);
      if (residue && (residue.requires_separate_cleanup || residue.risk === 'external_agent_persistence'))
        return 'OpenHands 自身保存的会话副本不在 MemGuardOS 管理范围内；如果已启用该功能，还需要在 OpenHands 中单独删除。';
      return '当前没有需要单独清理的外部 Agent 会话副本。';
    }
    forgetRequest.addEventListener('input', invalidateForgetPreview);
    agentForgetName.addEventListener('input', invalidateAgentForgetPreview);
    agentForgetTopic.addEventListener('change', invalidateAgentForgetPreview);
    agentSession.addEventListener('input', invalidateAgentForgetPreview);
    function rememberForgetPreview(payload) {
      const result = (payload || {}).result || {};
      const count = Number(result.preview_candidate_count || 0);
      const candidateIds = (result.candidates || []).map(item => String(item.memory_id || '')).filter(Boolean);
      forgetPreview = {
        request:forgetRequest.value,
        previewToken:String(result.preview_token || ''),
        candidateCount:count,
        candidateIds:candidateIds,
        selectedIds:count === 1 ? candidateIds.slice(0, 1) : []
      };
      updateForgetSelectionState();
    }
    function updateForgetSelectionState() {
      if (!forgetPreview) {
        forgetConfirmButton.disabled = true;
        forgetConfirmButton.textContent = '确认遗忘并整理存储';
        return;
      }
      const selectedCount = (forgetPreview.selectedIds || []).length;
      forgetConfirmButton.disabled = !forgetPreview.previewToken || selectedCount < 1 || currentRoleRank < roleRanks.admin;
      forgetConfirmButton.textContent = forgetPreview.candidateCount > 0
        ? '确认遗忘（已选 ' + selectedCount + ' 条）'
        : '没有可遗忘候选';
    }
    function toggleForgetCandidate(input) {
      if (!forgetPreview) return;
      const memoryId = String(input.dataset.forgetMemoryId || '');
      const selected = new Set(forgetPreview.selectedIds || []);
      if (input.checked) selected.add(memoryId); else selected.delete(memoryId);
      forgetPreview.selectedIds = forgetPreview.candidateIds.filter(id => selected.has(id));
      updateForgetSelectionState();
    }
    function selectAllForgetCandidates(selectAll) {
      if (!forgetPreview) return;
      forgetPreview.selectedIds = selectAll ? forgetPreview.candidateIds.slice() : [];
      document.querySelectorAll('[data-forget-memory-id]').forEach(input => { input.checked = Boolean(selectAll); });
      updateForgetSelectionState();
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
        setRawOutput('acceptanceOut', payload);
      });
    }
    async function runAcceptanceItem(event, itemId) {
      return withMutation(event, '运行复核检查', async () => {
        const payload = await postJson('/api/acceptance-run', {item_id:itemId});
        if (payload.result) acceptanceResults[itemId] = payload.result;
        renderAcceptancePlan({items: acceptanceItems});
        setRawOutput('acceptanceOut', payload);
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
        setRawOutput('acceptanceOut', {ok:true, result:{ran:results.length, results:results}});
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
        setRawOutput('acceptanceOut', (payload.result && payload.result.markdown) || payload);
      });
    }
    function renderForgetCards(payload, confirmed) {
      const plan = payload.result || {};
      const candidates = plan.candidates || [];
      const candidateHtml = candidates.length ? candidates.map((item, idx) =>
        '<div class="item"><div class="item-title">候选 #' + (idx + 1) + ' · ' + esc(memoryTypeLabel(item.type || 'memory')) + '</div>'
        + '<div class="memory-id" title="' + esc(item.memory_id || '') + '">记忆编号 ' + esc(shortMemoryId(item.memory_id || '')) + '</div>'
        + '<div class="muted">' + esc(item.summary || item.memory_id) + '</div>'
        + '<div class="muted">匹配依据：' + esc(item.derived ? '依赖被撤回的来源；需一并确认' : ((item.matched_keywords || []).join(', ') || '语义和作用域匹配')) + '</div>'
        + '<div class="mini-actions">'
        + (!confirmed ? '<span class="secondary"><input type="checkbox" aria-label="选择候选记忆 ' + (idx + 1) + '" data-forget-memory-id="' + esc(item.memory_id || '') + '" onchange="toggleForgetCandidate(this)"' + (candidates.length === 1 ? ' checked' : '') + '> 选择此记忆</span>' : '')
        + '<button class="secondary" data-action="open-memory-detail" data-memory-id="' + esc(item.memory_id || '') + '">查看记忆</button></div></div>'
      ).join('') : (confirmed ? '' : '<div class="empty-state"><strong>未发现候选</strong><span>可以换用更具体的主题、项目名称或时间范围。</span></div>');
      const selectionTools = !confirmed && candidates.length > 1
        ? '<div class="actions" style="margin-bottom:10px"><button class="secondary" onclick="selectAllForgetCandidates(true)">全选候选</button><button class="secondary" onclick="selectAllForgetCandidates(false)">清空选择</button><span class="muted">请只选择确实需要遗忘的内容。</span></div>'
        : '';
      document.getElementById('forgetCards').innerHTML = selectionTools + candidateHtml + renderVerificationReport(plan.verification_report);
    }
    function renderVerificationReport(report) {
      if (!report) return '';
      const steps = report.verification_steps || [];
      const statusText = {passed:'已通过', failed:'需要处理', skipped:'未执行'};
      const statusClass = {passed:'ok', failed:'bad', skipped:'warn'};
      const allSkipped = steps.length > 0 && steps.every(step => step.status === 'skipped');
      const labels = {
        main_record:'主记忆记录', agent_event_payload:'Agent 交互记录', sqlite_fts:'文本检索索引',
        graph_relations:'关联关系', fact_slot:'事实属性索引', search_cache:'检索缓存', mmap_segment_compact:'本机存储文件',
        plaintext_probe:'原文残留检查', agent_context_absence:'Agent 上下文复检', audit_chain:'操作记录完整性'
      };
      const body = allSkipped
        ? '<div class="list-row"><div><div class="item-title">确认后自动复检</div><div class="muted">将检查记忆记录、检索索引、关联关系、缓存、上下文和操作记录。</div></div><span class="badge">' + esc(steps.length) + ' 项</span></div>'
        : '<div class="linear-list">' + steps.map(step => '<div class="list-row"><div><div class="item-title">' + esc(labels[step.name] || '受管数据复检') + '</div><div class="muted">' + esc(step.detail || '') + '</div></div><span class="badge ' + (statusClass[step.status] || 'warn') + '">' + esc(statusText[step.status] || step.status) + '</span></div>').join('') + '</div>';
      return '<div class="result-section" style="margin-top:12px"><div class="section-block-header"><strong>遗忘复检</strong><span class="badge ' + (allSkipped ? 'warn' : 'ok') + '">' + (allSkipped ? '等待确认' : '已执行') + '</span></div>'
        + '<div class="section-block-body"><p class="muted">' + esc(report.user_summary || '') + '</p>' + body
        + '<details><summary>查看管理范围说明</summary><div class="overview-more-body muted">OpenHands 原始日志、模型服务日志、shell history、虚拟机快照和文件备份不属于 MemGuardOS 受管范围，需要按各自系统单独清理。</div></details></div></div>';
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
      const timeline = (result.timeline || []).slice();
      document.getElementById('agentTimeline').innerHTML = timeline.length
        ? agentSessionMarkup(timeline)
        : '<div class="muted">当前筛选范围内没有 Agent 交互记录。</div>';
      const preferences = result.preference_aggregates || [];
      document.getElementById('agentPreferences').innerHTML = preferences.length ? preferences.map(item =>
        '<div class="item"><div class="item-title">' + esc(topicLabel(item.topic)) + '：' + esc(item.value) + '</div>'
        + '<div class="muted">适用范围：' + esc(scopeLabel(item.preference_scope)) + ' · 依据 ' + esc(item.evidence_count) + ' 条 · 来自 ' + esc(item.distinct_sessions) + ' 个会话 · ' + (item.stable ? '已稳定' : '待观察') + '</div>'
        + (item.promoted_memory_id ? '<div class="actions"><button class="secondary" data-action="preference-evidence" data-memory-id="' + esc(item.promoted_memory_id) + '">查看依据</button></div>' : '')
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
        setRawOutput('agentOut', payload);
      });
    }
    function actionImpactRecommendation(value) {
      return {
        retain_reinforced:'保留并强化',
        retain_observe:'保留并继续观察',
        collect_more_evidence:'继续收集证据',
        review_or_demote:'需要人工复核',
        observe:'尚无足够行为证据'
      }[String(value || '')] || String(value || '待观察');
    }
    function renderAgentActionImpact(payload) {
      const box = document.getElementById('agentActionImpact');
      if (!box) return;
      const result = payload.result || {};
      if (!payload.ok || result.schema !== 'memguardos-agent-action-impact-report-v1') {
        box.innerHTML = '<div class="empty-state"><strong>动作质量报告暂时不可用</strong><span>' + esc(payload.error || '请先产生一条真实 Agent 工具结果。') + '</span></div>';
        return;
      }
      const summary = result.summary || {};
      const gate = String(summary.quality_gate || 'observe');
      const gateClass = gate === 'pass' ? 'ok' : gate === 'review' ? 'bad' : 'warn';
      const head = '<div class="compare-verdict ' + (gate === 'pass' ? '' : gate === 'review' ? 'warn' : 'info') + '"><div class="compare-verdict-mark">' + (gate === 'pass' ? '✓' : gate === 'review' ? '!' : '·') + '</div><div><strong>' + esc(gate === 'pass' ? '当前记忆已观察到稳定的动作收益' : gate === 'review' ? '存在失败或偏离信号，需要人工复核' : '当前证据不足以给出稳定结论') + '</strong><span>执行 ' + esc(summary.executed_count || 0) + ' 次 · 成功率 ' + esc(summary.success_rate || 0) + ' · 成功下界 ' + esc(summary.wilson_success_lower_bound || 0) + '</span></div></div>';
      const rows = (result.records || []).slice(0, 12).map(item => {
        const metrics = item.metrics || {};
        const recommendation = actionImpactRecommendation(metrics.recommendation);
        const cls = metrics.quality_gate === 'pass' ? 'ok' : metrics.quality_gate === 'review' ? 'bad' : 'warn';
        const lift = metrics.observed_success_lift == null ? '暂无同类基线' : '同类差值 ' + metrics.observed_success_lift;
        return '<div class="item"><div class="health"><div class="item-title">' + esc(shortMemoryId(item.memory_id)) + ' · ' + esc(item.memory_subtype || item.memory_type) + '</div><span class="badge ' + cls + '">' + esc(recommendation) + '</span></div><div class="muted">成功 ' + esc(metrics.success_count || 0) + ' · 失败 ' + esc(metrics.failure_count || 0) + ' · 偏离 ' + esc(metrics.deviation_count || 0) + ' · 遵循率 ' + esc(metrics.adherence_rate || 0) + ' · ' + esc(lift) + '</div><div class="muted">当前层级：' + esc(item.current_tier) + ' · 作用域：' + esc((item.scope || {}).task_type || '未分类') + '</div></div>';
      }).join('');
      box.innerHTML = head + '<div class="list-row"><div><div class="item-title">质量门控</div><div class="muted">' + esc(actionImpactRecommendation(gate === 'pass' ? 'retain_reinforced' : gate === 'review' ? 'review_or_demote' : 'observe')) + '；报告只提供复核依据，不自动改变记忆生命周期。</div></div><span class="badge ' + gateClass + '">' + esc(gate) + '</span></div>' + (rows || '<div class="empty-state"><strong>暂无动作反馈</strong><span>先通过 Agent 接入完成一次预检、工具执行和结果回传。</span></div>');
    }
    async function loadAgentActionImpact(event) {
      return withBusy(event, async () => {
        const params = new URLSearchParams({
          agent_name: document.getElementById('agentName').value || '',
          project_root: '',
          task_type: '',
          limit: '5000'
        });
        const payload = await getJson('/api/agent-action-impact?' + params.toString());
        renderAgentActionImpact(payload);
        setRawOutput('agentOut', payload);
        return operationFeedback(payload.ok ? '动作质量报告已更新。' : '动作质量报告暂时没有生成。', payload.ok ? 'ok' : 'warn');
      });
    }
    async function loadPreferenceEvidence(event, memoryId) {
      return withBusy(event, async () => {
        const payload = await getJson('/api/agent-evidence?memory_id=' + encodeURIComponent(memoryId));
        setRawOutput('agentOut', payload);
        const events = (payload.result || {}).events || [];
        const box = document.getElementById('agentPreferences');
        if (!box) return operationFeedback('偏好依据已载入。', 'ok');
        box.innerHTML = '<div class="section-block"><div class="section-block-header"><strong>偏好依据：'
          + esc(shortMemoryId(memoryId)) + '</strong><span class="badge">' + events.length + ' 条来源事件</span></div>'
          + '<div class="section-block-body">'
          + (events.length
              ? '<div class="timeline">' + agentTimelineMarkup(events.slice(0, 8)) + '</div>'
              : '<div class="empty-state"><strong>没有找到来源事件</strong><span>该偏好可能来自手动写入，或来源事件已被遗忘。</span></div>')
          + '<div class="mini-actions"><button class="secondary" data-action="open-memory-detail" data-memory-id="'
          + esc(memoryId) + '">查看记忆详情</button></div></div></div>';
        return operationFeedback(
          events.length ? '已展示支撑该偏好的来源事件。' : '该偏好没有可展示的来源事件。',
          events.length ? 'ok' : 'warn'
        );
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
        setRawOutput('agentOut', payload);
        renderAgentRunFeedback(result);
        if (result.truthful_note) setOperationStatus(result.truthful_note, result.ok ? 'ok' : 'warn');
        if (result.preference_effect_proofs) renderPreferenceProofs(result.preference_effect_proofs);
        if (result.ok) await loadAgentOverview(null);
        await loadFeedbackHome(null);
      });
    }
    function renderAgentRunFeedback(result) {
      const box = document.getElementById('agentRunFeedback');
      if (!box) return;
      const feedback = result.memory_change_summary || {};
      const items = (feedback.new_memories || []).concat(feedback.updated_memories || []);
      const blocked = feedback.blocked_items || [];
      const observed = (feedback.observed_preferences || []).filter(item => ['candidate','reinforced','negative_observation'].includes(item.status));
      if (!items.length && !blocked.length && !observed.length) {
        const mode = result.execution_mode === 'dry-run' ? '本次为只读预览，没有写入记忆。' : '任务已完成，本轮没有形成新的稳定偏好或可复用经验。';
        box.innerHTML = '<div class="item"><div class="health"><div class="item-title">本次记忆结果</div><span class="badge">无新增</span></div><div class="muted">' + esc(mode) + '</div></div>';
        return;
      }
      const itemMarkup = items.map(item => '<div class="list-row"><div><div class="item-title">'
        + esc((item.category_label || '可复用记忆') + '：' + (item.summary_for_user || '已处理'))
        + '</div><div class="muted">来源：' + esc(item.evidence_label || '已验证内容')
        + ' · 后续用于：' + esc((item.where_it_will_apply || []).join('、') || '相关任务')
        + '</div></div><span class="badge ok">' + esc(item.status_label || '已保存') + '</span></div>').join('');
      const blockedMarkup = blocked.length
        ? '<div class="list-row"><div><div class="item-title">内容保护</div><div class="muted">敏感内容没有进入长期记忆或检索上下文。</div></div><span class="badge bad">已阻止 ' + blocked.length + ' 项</span></div>'
        : '';
      const observedMarkup = observed.map(item => '<div class="list-row"><div><div class="item-title">检测到' + esc(item.category_label || '用户偏好') + '：' + esc(item.summary_for_user || '') + '</div><div class="muted">依据 ' + esc(item.evidence_count || 1) + ' 条 · 来自 ' + esc(item.distinct_sessions || 1) + ' 个会话</div></div><span class="badge warn">' + esc(item.status_label || '正在观察') + '</span></div>').join('');
      box.innerHTML = '<div class="section-block"><div class="section-block-header"><strong>本次记忆结果</strong><span class="badge ok">已处理 ' + (items.length + observed.length) + ' 项</span></div><div class="section-block-body"><div class="linear-list">' + itemMarkup + observedMarkup + blockedMarkup + '</div></div></div>';
    }
    async function runJourney(event) {
      document.getElementById('agentTask').value = '查找 MemGuardService 的定义并说明如何验证记忆遗忘';
      document.getElementById('agentMode').value = 'replay';
      openTab('agent');
      await runAgentTask(event);
    }
    function renderValueHero(feedback, comparisonHint) {
      const result = (feedback || {}).result || {};
      const preferences = result.current_preferences || [];
      const stable = preferences.filter(item => item.stable);
      const memories = result.recent_memories || [];
      const blocks = result.recent_sensitive_blocks || [];
      const setText = (id, value) => { const node = document.getElementById(id); if (node) node.textContent = value; };
      setText('statPreferences', stable.length);
      setText('statMemories', result.live_memory_count ?? memories.length);
      setText('statProtected', blocks.length);
      const headline = document.getElementById('valueHeadline');
      const detail = document.getElementById('valueDetail');
      if (!headline || !detail) return;
      if (stable.length) {
        const names = stable.slice(0, 3).map(item => topicLabel(item.topic) + '：' + (item.value || '')).join('；');
        headline.textContent = 'Agent 已经记住 ' + stable.length + ' 条你的使用习惯';
        detail.textContent = '下次任务会自动应用：' + names + (stable.length > 3 ? ' 等' : '') + '。不需要你再交代一遍。';
      } else if (memories.length) {
        headline.textContent = '已保存 ' + memories.length + ' 条记忆，还没有形成稳定偏好';
        detail.textContent = '同一习惯在不同会话中重复出现，或明确说明一次（例如"以后查代码优先用 rg"），就会晋升为稳定偏好。';
      } else {
        headline.textContent = '还没有可用记忆';
        detail.textContent = '通过 Agent 工作台运行一次任务，或在记忆中心手动添加一条，系统会自动提取可复用的偏好。';
      }
    }
    function renderLearningPairs(feedback) {
      const box = document.getElementById('learningPairs');
      if (!box) return;
      const result = (feedback || {}).result || {};
      const preferences = (result.current_preferences || []).filter(item => item.stable);
      if (!preferences.length) {
        box.innerHTML = '<div class="empty-state"><strong>还没有"学到 → 生效"的配对</strong>'
          + '<span>稳定偏好形成后，这里会展示它来自哪次交互、会在哪个阶段生效。</span>'
          + '<button class="secondary" data-action="open-tab" data-tab-target="agent">去 Agent 工作台运行一次任务</button></div>';
        return;
      }
      const stageOf = topic => ({
        code_search_tool: '工具选择阶段',
        test_runner_command: '工具选择阶段',
        service_log_tool: '工具选择阶段',
        response_order: '回答生成阶段',
        response_language: '回答生成阶段',
        response_verbosity: '回答生成阶段',
        high_risk_confirmation: '任务规划阶段'
      }[topic] || '相关任务阶段');
      box.innerHTML = preferences.slice(0, 6).map(item => {
        const memoryId = String(item.memory_id || '');
        return '<div class="pair-row">'
          + '<div class="pair-cell"><strong>学到：' + esc(topicLabel(item.topic)) + ' = ' + esc(item.value || '') + '</strong>'
          + '<span>作用范围 ' + esc(scopeLabel(item.scope || 'global')) + '</span></div>'
          + '<div class="pair-arrow" aria-hidden="true">→</div>'
          + '<div class="pair-cell"><strong>生效：' + esc(stageOf(item.topic)) + '自动应用</strong>'
          + '<span>无需重复交代，Agent 直接按此执行</span></div>'
          + '<div class="pair-actions">'
          + (memoryId ? '<button class="secondary" data-action="preference-proof" data-memory-id="' + esc(memoryId) + '">生效证明</button>'
                        + '<button class="secondary" data-action="open-memory-detail" data-memory-id="' + esc(memoryId) + '">来源</button>'
                      : '<span class="muted">暂无记忆编号</span>')
          + '</div></div>';
      }).join('');
    }
    function renderValueEvidencePanel(reference) {
      const box = document.getElementById('valueEvidencePanel');
      if (!box) return;
      if (!reference || !reference.available) {
        box.innerHTML = '<div class="empty-state"><strong>暂无实验证据文件</strong>'
          + '<span>完成本机效果评估后，这里会显示可复现的对照结果。</span></div>';
        return;
      }
      const groups = reference.groups || {};
      const memguard = groups.memguard || {};
      const noMemory = groups.no_memory || {};
      const fullHistory = groups.full_history || {};
      const pct = value => (value === null || value === undefined) ? '-' : Math.round(Number(value) * 100) + '%';
      const num = value => (value === null || value === undefined) ? '-' : Number(value).toFixed(2);
      const source = document.getElementById('valueEvidenceSource');
      if (source) source.textContent = (reference.case_count || 0) + ' 组固定场景回归';
      const rows = [
        ['任务完成率', pct(noMemory.task_completion_rate), pct(memguard.task_completion_rate)],
        ['首选工具正确率', pct(noMemory.first_tool_accuracy), pct(memguard.first_tool_accuracy)],
        ['平均交互轮数', num(noMemory.mean_interaction_turns), num(memguard.mean_interaction_turns)],
        ['平均失败命令数', num(noMemory.mean_failed_commands), num(memguard.mean_failed_commands)]
      ];
      const tokenRow = ['平均提示 tokens', num(fullHistory.mean_prompt_tokens), num(memguard.mean_prompt_tokens)];
      box.innerHTML = '<div class="compare-grid">'
        + '<div class="compare-head">指标</div><div class="compare-head without">无记忆 / 全历史</div><div class="compare-head with">MemGuardOS</div>'
        + rows.concat([tokenRow]).map(row =>
            '<div>' + esc(row[0]) + '</div>'
            + '<div class="compare-value muted-value">' + esc(row[1]) + '</div>'
            + '<div class="compare-value">' + esc(row[2]) + '</div>').join('')
        + '</div>'
        + '<p class="muted" style="margin-top:10px">' + esc(reference.evidence_note || '固定场景用于可复现机制回归，不替代真实 Agent 独立运行证据。') + '</p>';
    }
    async function runValueComparison(event) {
      return withBusy(event, async () => {
        const task = String(document.getElementById('compareTask').value || '').trim();
        if (!task) {
          return operationFeedback('请先填写任务描述。', 'warn');
        }
        document.getElementById('compareVerdict').innerHTML = '<div class="skeleton" aria-label="正在运行对比"></div>';
        document.getElementById('compareTable').innerHTML = '';
        document.getElementById('compareCost').innerHTML = '';
        const payload = await getJson('/api/value-comparison?task=' + encodeURIComponent(task), 30000);
        renderValueComparison(payload.result || {});
        setRawOutput('compareOut', payload);
        const applied = ((payload.result || {}).measured || {}).applied_preference_count || 0;
        return operationFeedback(
          applied ? '对比完成：有 ' + applied + ' 项偏好因记忆而自动生效。' : '对比完成：本次任务未产生动作差异。',
          applied ? 'ok' : 'warn'
        );
      });
    }
    function renderValueComparison(result) {
      const measured = result.measured || {};
      const modelled = result.modelled || {};
      const verdict = result.verdict || {};
      const verdictBox = document.getElementById('compareVerdict');
      const marks = {ok:'✓', info:'i', warn:'!'};
      verdictBox.innerHTML = '<div class="compare-verdict ' + esc(verdict.state || 'info') + '">'
        + '<div class="compare-verdict-mark" aria-hidden="true">' + esc(marks[verdict.state] || 'i') + '</div>'
        + '<div><strong>' + esc(verdict.headline || '') + '</strong><span class="muted">' + esc(verdict.detail || '') + '</span></div></div>';

      const differences = measured.differences || [];
      const tableBox = document.getElementById('compareTable');
      if (!differences.length) {
        tableBox.innerHTML = '<div class="empty-state"><strong>该任务没有可对比的偏好维度</strong><span>换一个涉及代码搜索、测试、日志或高风险操作的任务再试。</span></div>';
      } else {
        tableBox.innerHTML = '<div class="compare-grid">'
          + '<div class="compare-head">任务中的决策点</div><div class="compare-head without">不带记忆</div><div class="compare-head with">带记忆</div>'
          + differences.map(item => {
              const changed = item.changed;
              return '<div>' + esc(item.topic_label) + '</div>'
                + '<div class="compare-value muted-value">' + esc(item.without_memory_label)
                + (item.naive_command ? '<code class="compare-cmd">' + esc(item.naive_command) + '</code>' : '') + '</div>'
                + '<div class="compare-value' + (changed ? ' compare-cell-changed' : '') + '">' + esc(item.with_memory_label)
                + (changed ? '<span class="compare-badge">记忆生效</span>' : '')
                + (item.memory_command ? '<code class="compare-cmd">' + esc(item.memory_command) + '</code>' : '') + '</div>';
            }).join('')
          + '</div>';
      }

      const costBox = document.getElementById('compareCost');
      const injected = measured.injected_memories || [];
      costBox.innerHTML = '<div class="section-block"><div class="section-block-header"><strong>成本与依据</strong>'
        + '<span class="measured-tag measured">measured 实测</span></div><div class="section-block-body">'
        + '<div class="linear-list">'
        + '<div class="list-row"><div><div class="item-title">本轮注入上下文</div><div class="muted">'
        + esc(measured.context_cost_note || '') + '</div></div><span class="badge">' + esc(measured.memguard_context_tokens || 0) + ' tokens</span></div>'
        + '<div class="list-row"><div><div class="item-title">注入的记忆</div><div class="muted">'
        + (injected.length ? injected.slice(0, 4).map(item => esc(shortMemoryId(item.memory_id)) + '（' + esc(item.stage) + '）').join('、') : '本次没有命中记忆')
        + '</div></div><span class="badge">' + injected.length + ' 条</span></div>'
        + '</div>'
        + '<div class="list-row" style="border-top:1px solid var(--line);margin-top:8px;padding-top:12px"><div>'
        + '<div class="item-title">推论：省下的重复说明 <span class="measured-tag modelled">modelled 推论</span></div>'
        + '<div class="muted">' + esc(modelled.assumption || '') + '</div></div>'
        + '<span class="badge">' + esc(modelled.corrections_avoided || 0) + ' 次</span></div>'
        + '<p class="muted" style="margin-top:10px">' + esc(result.truthful_note || '') + '</p>'
        + '</div></div>';
    }
    let valueEvidenceLoaded = false;
    async function loadOverviewEvidence(event) {
      if (valueEvidenceLoaded) return null;
      try {
        const payload = await getJson('/api/value-evidence', 10000);
        renderValueEvidencePanel((payload.result || {}));
        valueEvidenceLoaded = true;
      } catch (err) {
        renderValueEvidencePanel(null);
      }
      return null;
    }
    async function loadFeedbackHome(event) {
      const work = async () => {
        const payload = await fetchFeedbackHome(30, 10000, Boolean(event));
        const result = payload.result || {};
        const memories = result.recent_memories || [];
        const recentBox = document.getElementById('recentMemoriesList');
        if (recentBox) {
          recentBox.innerHTML = memories.length
            ? memories.slice(0, 4).map((item, index) => memoryResultMarkup(item, index, 'recent')).join('')
            : '<div class="empty-state">还没有可展示的记忆。完成一次 Agent 交互或手动添加后会显示在这里。</div>';
        }
        renderValueHero(payload);
        renderLearningPairs(payload);
        renderNotifications(result.notifications || [], 'homeNotifications');
        return payload;
      };
      if (event) return withBusy(event, work);
      return work();
    }
    async function loadMemoryWorkspace(event) {
      const work = async () => {
        const main = document.getElementById('memoryWorkspaceResults');
        if (main) main.innerHTML = '<div class="skeleton" aria-label="正在读取记忆"></div><div class="skeleton" aria-hidden="true"></div>';
        const payload = await fetchFeedbackHome(30, 10000, Boolean(event));
        const memories = (payload.result || {}).recent_memories || [];
        renderMemoryWorkspaceRows(memories, 'recent');
        try {
          const conflictPayload = await getJson('/api/fact-conflicts?status=pending&limit=30');
          renderFactConflicts(conflictPayload);
        } catch (_err) {
          const conflictBox = document.getElementById('factConflictPanel');
          if (conflictBox) conflictBox.innerHTML = '<div class="empty-state"><strong>事实核对队列暂时无法读取</strong><span>记忆列表仍可正常使用，请稍后重新检查。</span></div>';
        }
        const summary = document.getElementById('memorySearchSummary');
        if (summary) summary.textContent = memories.length
          ? '共读取 ' + memories.length + ' 条最近记忆，完整编号可复制，点击可查看来源和作用范围。'
          : '当前记忆空间还没有可展示记忆。';
        setRawOutput('memoryWorkspaceOut', payload);
        return operationFeedback('记忆列表已更新。', 'ok');
      };
      if (event) return withBusy(event, work);
      return work();
    }
    function renderFactConflicts(payload) {
      const box = document.getElementById('factConflictPanel');
      if (!box) return;
      const cases = ((payload || {}).result || {}).cases || [];
      if (!cases.length) {
        box.innerHTML = '<div class="empty-state"><strong>暂无待核对事实</strong><span>当新事实与已有属性冲突时，系统会先停在这里，等待明确决定。</span></div>';
        return;
      }
      box.innerHTML = cases.map(item => {
        const candidates = item.candidates || [];
        const candidate = candidates[0] || {};
        const candidateId = String(candidate.memory_id || (item.candidate_memory_ids || [])[0] || '');
        const versionId = String(candidate.version_id || (item.candidate_version_ids || {})[candidateId] || '');
        const candidateMarkup = candidateId
          ? '<button class="secondary" data-action="open-memory-detail" data-memory-id="' + esc(candidateId) + '">查看现有事实</button>'
          : '<span class="muted">现有候选已不可查看</span>';
        const adminMarkup = currentRoleRank >= roleRanks.admin && candidateId
          ? '<button class="secondary" data-action="resolve-fact-conflict" data-conflict-id="' + esc(item.conflict_id || '') + '" data-decision="keep_existing" data-memory-id="' + esc(candidateId) + '" data-version-id="' + esc(versionId) + '">保留原值</button><button class="primary" data-action="resolve-fact-conflict" data-conflict-id="' + esc(item.conflict_id || '') + '" data-decision="accept_incoming" data-memory-id="' + esc(candidateId) + '" data-version-id="' + esc(versionId) + '">接受新值并生成版本</button>'
          : '<span class="muted">需要管理员确认处理</span>';
        return '<div class="item"><div class="health"><div class="item-title">' + esc(item.subject || '未命名事实') + ' · ' + esc(item.predicate || '属性') + '</div><span class="badge warn">待核对</span></div>'
          + '<div class="muted">新值：' + esc(item.incoming_value || '内容已被保护') + ' · 候选：' + esc(item.candidate_count || 0) + ' 条 · 核对编号：' + esc(shortMemoryId(item.conflict_id || '')) + '</div>'
          + '<div class="mini-actions">' + candidateMarkup + adminMarkup + '</div></div>';
      }).join('');
    }
    async function resolveFactConflict(event, conflictId, decision, memoryId, versionId) {
      const actionText = decision === 'accept_incoming' ? '接受新值并生成新版本' : '保留已有事实';
      if (!window.confirm('确认' + actionText + '？系统会校验候选版本，避免覆盖刚刚发生的更新。')) return null;
      return withMutation(event, actionText, async () => {
        const payload = await postJson('/api/resolve-fact-conflict', {
          conflict_id: conflictId, decision: decision, memory_id: memoryId, expected_version_id: versionId
        });
        setRawOutput('memoryWorkspaceOut', payload);
        await loadMemoryWorkspace(null);
        const reasons = {
          candidate_version_changed: '候选记忆已更新或遗忘，本次没有覆盖现有内容。请核对刷新后的记录。',
          conflict_already_closed: '这条核对记录已经处理，本次没有再次修改记忆。',
          conflict_not_found: '这条核对记录已不可用，请查看刷新后的队列。'
        };
        const reason = (payload.result || {}).reason || '';
        return operationFeedback(payload.ok ? actionText + '已完成。' : (reasons[reason] || '事实核对没有完成，请重新读取候选。'), payload.ok ? 'ok' : 'warn');
      });
    }
    async function searchMemoryWorkspace(event) {
      return withBusy(event, async () => {
        const query = String(document.getElementById('memorySearchQuery').value || '').trim();
        if (!query) {
          await loadMemoryWorkspace(null);
          return operationFeedback('已恢复显示最近记忆。', 'ok');
        }
        const limit = Number(document.getElementById('memorySearchLimit').value || 5);
        const main = document.getElementById('memoryWorkspaceResults');
        main.innerHTML = '<div class="skeleton" aria-label="正在搜索记忆"></div><div class="skeleton" aria-hidden="true"></div>';
        const payload = await getJson('/api/search?q=' + encodeURIComponent(query) + '&top_k=' + encodeURIComponent(limit) + '&session_id=' + encodeURIComponent(memorySessionId), 5000);
        const rows = payload.result || [];
        renderMemoryWorkspaceRows(rows, 'search');
        document.getElementById('memorySearchSummary').textContent = rows.length
          ? '找到 ' + rows.length + ' 条结果，已按相关度排序并折叠重复内容。' + (rows.length > 3 ? '优先展示前三条，其余结果可展开查看。' : '')
          : '没有找到相关记忆。建议使用更具体的主题、工具或项目名称。';
        setRawOutput('memoryWorkspaceOut', payload);
        return operationFeedback(rows.length ? '记忆搜索已完成。' : '未找到相关记忆。', rows.length ? 'ok' : 'warn');
      });
    }
    async function clearMemorySearch(event) {
      const input = document.getElementById('memorySearchQuery');
      if (input) input.value = '';
      return loadMemoryWorkspace(event);
    }
    function renderNotifications(items, targetId) {
      const box = document.getElementById(targetId || 'notificationList');
      if (!box) return;
      box.classList.add('notification-list');
      if (!items.length) {
        box.innerHTML = '<div class="empty-state"><strong>暂无需要关注的变化</strong><span>重要记忆变化、风险阻断和遗忘结果会显示在这里。</span></div>';
        return;
      }
      const labels = {critical:'需要处理', warning:'建议确认', info:'已完成'};
      const classes = {critical:'bad', warning:'warn', info:'ok'};
      box.innerHTML = items.map(item => {
        const action = String(item.action || '');
        const targetRequiresAdmin = action.indexOf('记录') >= 0
          || (!action.includes('记忆') && !action.includes('Agent') && !action.includes('遗忘'));
        const actionMarkup = targetRequiresAdmin && currentRoleRank < roleRanks.admin
          ? '<span class="muted">需要管理员处理</span>'
          : '<button class="secondary" data-action="notification-target" data-notification-action="'
            + esc(action) + '">查看处理</button>';
        return '<div class="list-row"><div><div class="item-title">' + esc(item.title || '')
          + '</div><div class="muted">' + esc(item.summary || '') + '</div></div><div class="notification-actions"><span class="badge '
          + (classes[item.level] || '') + '">' + esc(labels[item.level] || item.level) + '</span>' + actionMarkup + '</div></div>';
      }).join('');
    }
    function openNotificationTarget(action) {
      if (action.indexOf('内容保护') >= 0) openTab('sensitive', {force:true});
      else if (action.indexOf('记忆') >= 0) openTab('memory', {force:true});
      else if (action.indexOf('Agent') >= 0) openTab('agent');
      else if (action.indexOf('遗忘') >= 0) openTab('forget', {force:true});
      else if (action.indexOf('记录') >= 0) openTab('audit');
      else openTab('diagnostics');
    }
    async function loadNotifications(event) {
      await withBusy(event, async () => {
        const payload = await getJson('/api/notifications?limit=50');
        renderNotifications((payload.result || []), 'notificationList');
        setRawOutput('notificationOut', payload);
        return operationFeedback('通知列表已更新。', 'ok');
      });
    }
    async function loadSensitiveProtection(event) {
      const work = async () => {
        const payload = await fetchFeedbackHome(30, 10000, Boolean(event));
        const blocks = (payload.result || {}).recent_sensitive_blocks || [];
        const box = document.getElementById('sensitiveBlockList');
        if (!blocks.length) {
          box.innerHTML = '<div class="item"><div class="item-title">最近没有需要展示的保护事件</div><div class="muted">系统仍会在每次写入和 Agent 交互前执行敏感检查。</div></div>';
        } else {
          renderNotifications(blocks, 'sensitiveBlockList');
        }
        setRawOutput('sensitiveOut', payload);
        return operationFeedback('敏感保护状态已更新。', 'ok');
      };
      return event ? withBusy(event, work) : work();
    }
    async function loadRecentForgetting(event, force) {
      const work = async () => {
        const box = document.getElementById('recentForgetList');
        if (box) box.innerHTML = '<div class="skeleton" aria-label="正在读取最近遗忘验证"></div>';
        const payload = await fetchFeedbackHome(30, 10000, Boolean(event));
        const items = (payload.result || {}).recent_forgetting || [];
        if (box) {
          box.innerHTML = items.length ? items.map(item =>
            '<div class="list-row"><div><div class="item-title">' + esc(item.title || '遗忘验证已完成')
            + '</div><div class="muted">' + esc(item.summary || '受管记录和检索路径已完成复核。')
            + '</div></div><span class="badge ' + (item.level === 'critical' ? 'bad' : 'ok') + '">'
            + (item.level === 'critical' ? '需要处理' : '已验证') + '</span></div>'
          ).join('') : '<div class="empty-state"><strong>还没有遗忘记录</strong><span>完成一次精准遗忘后，受管记录、索引和上下文复检结果会显示在这里。</span></div>';
        }
        return operationFeedback(items.length ? '最近遗忘验证已更新。' : '当前没有遗忘记录。', items.length ? 'ok' : 'warn');
      };
      if (force) tabLoadedAt.delete('forget');
      return event ? withBusy(event, work) : work();
    }
    async function testSensitiveText(event) {
      const text = document.getElementById('sensitiveTestText').value.trim();
      if (!text) {
        setOperationStatus('请输入需要检查的文本。', 'warn');
        return null;
      }
      return withBusy(event, async () => {
        const payload = await getJson('/api/explain-extraction?text=' + encodeURIComponent(text) + '&source_kind=manual');
        const result = payload.result || {};
        const sensitivity = result.sensitivity || {};
        const labels = sensitivity.labels || [];
        const level = String(sensitivity.level || 'none').toLowerCase();
        const blocked = level === 'high' || level === 'critical';
        const action = blocked ? '阻止进入长期记忆' : (labels.length ? '脱敏后再评估' : '允许进入受控抽取');
        document.getElementById('sensitiveTestResult').innerHTML = '<div class="item"><div class="health"><div class="item-title">检测结果：' + esc(sensitivityLabel(level)) + '</div><span class="badge ' + (blocked ? 'bad' : (labels.length ? 'warn' : 'ok')) + '">' + esc(action) + '</span></div><div class="muted">检测类型：' + esc(labels.length ? labels.join('、') : '未发现已知敏感特征') + '</div><div class="muted">本次测试不会保存输入文本，也不会写入 Agent 上下文。</div></div>';
        setRawOutput('sensitiveOut', payload);
        return operationFeedback(blocked ? '检测到高风险内容，系统会阻止保存。' : '敏感检查已完成。', blocked ? 'warn' : 'ok');
      });
    }
    function summarizeAuditEvents(events) {
      const groups = [];
      const byType = new Map();
      (events || []).forEach(item => {
        const key = String(item.event_type || 'event');
        const existing = byType.get(key);
        if (existing) {
          existing.count += 1;
          return;
        }
        const group = {item:item, count:1};
        byType.set(key, group);
        groups.push(group);
      });
      return groups.slice(0, 6);
    }
    async function loadEvidenceHub(event) {
      const work = async () => {
        const settled = await Promise.all([getJson('/api/verify-audit'), getJson('/api/audit?limit=30'), getJson('/api/status')]);
        const auditCheck = settled[0];
        const events = settled[1].result || [];
        const statusPayload = settled[2];
        const auditOk = auditCheck.ok === true && (auditCheck.result || {}).ok === true;
        const badgeNode = document.getElementById('evidenceAuditBadge');
        badgeNode.className = 'badge ' + (auditOk ? 'ok' : 'bad');
        badgeNode.textContent = auditOk ? '完整' : '需要处理';
        document.getElementById('evidenceAuditSummary').textContent = auditOk
          ? '操作记录校验通过，可以用于本机复核和证据导出。'
          : '操作记录校验未通过，请先进入系统维护处理异常。';
        const eventGroups = summarizeAuditEvents(events);
        document.getElementById('evidenceEventList').innerHTML = eventGroups.length ? eventGroups.map(group => {
          const item = group.item || {};
          const countText = group.count > 1 ? '最近同类事件 ' + group.count + ' 次' : '已记录';
          return '<div class="item"><div class="health"><div class="item-title">' + esc(auditEventLabel(item.event_type || 'event')) + '</div><span class="badge">' + esc(countText) + '</span></div><div class="muted">最近记录编号 ' + esc(item.audit_id || '-') + ' · 关联记忆 ' + esc(item.memory_id || '无') + '</div></div>';
        }).join('') : '<div class="item"><div class="item-title">暂无关键事件</div><div class="muted">完成记忆写入、上下文构建或遗忘后会在这里显示。</div></div>';
        setRawOutput('evidenceOut', {audit:auditCheck, events:settled[1], status:statusPayload});
        return operationFeedback(auditOk ? '审计与证据状态已更新。' : '审计状态需要处理。', auditOk ? 'ok' : 'bad');
      };
      return event ? withBusy(event, work) : work();
    }
    function auditEventLabel(value) {
      const labels = {
        ingest:'记忆写入', interaction_ingest:'Agent 交互', interaction_status:'Agent 状态检查',
        context_build:'上下文构建', agent_context_build:'Agent 上下文构建',
        preference_select:'偏好选择', preference_evidence:'偏好生效核验',
        sensitive_block:'敏感保护', forget:'精准遗忘', forget_memory_ids:'精准遗忘',
        compact:'存储整理', verify_audit:'审计校验', namespace_status:'资料库状态检查',
        retrieval_status:'检索状态检查', memory_policy_status:'记忆策略检查',
        temporal_status:'记忆有效期检查'
      };
      return labels[String(value || '')] || '系统操作';
    }
    async function loadPreferences(event) {
      await withBusy(event, async () => {
        const payload = await fetchFeedbackHome(30, 10000, Boolean(event));
        const items = (payload.result || {}).current_preferences || [];
        const box = document.getElementById('preferenceList');
        box.innerHTML = items.length ? items.map(item =>
          '<div class="item"><div class="health"><div class="item-title">' + esc(topicLabel(item.topic)) + '：' + esc(item.value || '')
          + '</div><span class="badge ' + (item.stable ? 'ok' : 'warn') + '">' + (item.stable ? '稳定' : '候选') + '</span></div>'
          + (item.memory_id ? '<div class="memory-id" title="' + esc(item.memory_id) + '">记忆编号 ' + esc(shortMemoryId(item.memory_id)) + '</div>' : '')
          + '<div class="muted">适用范围：' + esc(scopeLabel(item.scope || 'global')) + '</div><div class="mini-actions">'
          + (item.memory_id
            ? '<button class="secondary" data-action="preference-proof" data-memory-id="' + esc(item.memory_id) + '">查看生效证明</button><button class="secondary" data-action="open-memory-detail" data-memory-id="' + esc(item.memory_id) + '">查看记忆</button><button class="secondary" data-action="copy-memory-id" data-memory-id="' + esc(item.memory_id) + '">复制编号</button>'
            : '<span class="muted">暂无对应记忆编号</span>')
          + '</div></div>'
        ).join('') : '<div class="item"><div class="item-title">暂无稳定偏好</div><div class="muted">通过 Agent 工作台运行任务后，这里会显示可复用偏好。</div></div>';
        setRawOutput('preferenceOut', payload);
        return operationFeedback(items.length ? '当前偏好已更新。' : '当前没有稳定偏好。', items.length ? 'ok' : 'warn');
      });
    }
    async function loadPreferenceProof(event, memoryId) {
      await withBusy(event, async () => {
        const url = '/api/preference-proof?memory_id=' + encodeURIComponent(memoryId) + '&task=' + encodeURIComponent(document.getElementById('proofTask').value) + '&stage=' + encodeURIComponent(document.getElementById('proofStage').value);
        const payload = await getJson(url);
        setRawOutput('preferenceOut', payload);
        renderPreferenceProofs((payload.result || {}).proof ? [(payload.result || {}).proof] : []);
        openTab('agent');
      });
    }
    async function openMemoryDetail(memoryId, returnTab) {
      memoryDetailReturnTab = returnTab || (activeTab === 'memory-detail' ? memoryDetailReturnTab : activeTab) || 'memory';
      document.getElementById('memoryDetailId').value = memoryId || '';
      await openTab('memory-detail', {force:true});
      return loadMemoryDetail(null);
    }
    function returnFromMemoryDetail() {
      return openTab(memoryDetailReturnTab || 'memory', {force:true});
    }
    function renderDerivationPanel(result) {
      const deps = result.dependencies || [];
      const impact = (result.derivation_impact || {}).derived_memories || [];
      const memory = result.memory || {};
      let body = '';
      if (memory.source_kind === 'verified_procedure') {
        let spec;
        try { spec = JSON.parse(memory.content || '{}'); } catch (_err) { spec = {}; }
        const stats = result.procedure_feedback || {};
        body += '<div class="section-block-header"><strong>已验证流程经验</strong><span class="badge">' + esc(memory.governance_state === 'source_invalidated' ? '来源已变化 · 已停用' : '形成时检查通过') + '</span></div>'
          + '<div class="section-block-body"><div class="item-title">' + esc(spec.goal || memory.summary || '') + '</div>'
          + '<ol>' + (spec.steps || []).map(step => '<li>' + esc(step) + '</li>').join('') + '</ol>'
          + '<div class="muted">参数：' + esc(Object.entries(spec.parameters || {}).map(([name,value]) => name + ' = ' + (value === null ? '本次任务需提供' : value)).join('；') || '无需额外参数') + '</div>'
          + '<div class="muted">约束：' + esc((spec.failure_guards || []).join('；') || '未声明额外约束') + '</div>'
          + '<div class="linear-list">' + (spec.postconditions || []).map(check => '<div class="list-row"><span>' + esc(({path_exists:'文件存在', file_contains:'文件包含', file_not_contains:'文件不包含'})[check.type] || check.type) + ' · ' + esc(check.path || '') + '</span><span>' + esc(check.value || '') + '</span></div>').join('') + '</div>'
          + '<div class="muted">后续实测：通过 ' + esc(stats.pass || 0) + ' 次 · 未通过 ' + esc(stats.fail || 0) + ' 次 · 未确认 ' + esc(stats.unknown || 0) + ' 次</div></div>';
      }
      if (deps.length || impact.length) {
        body += '<div class="section-block-header"><strong>来源与派生影响</strong><span class="badge">版本绑定</span></div><div class="section-block-body">'
          + deps.map(dep => '<div class="list-row"><span>来源 ' + esc(dep.parent_memory_id) + '</span><span class="muted">版本 ' + esc(dep.parent_version_id) + '</span></div>').join('')
          + impact.map(item => '<div class="list-row"><span>' + esc(item.summary || item.memory_id) + '</span><span class="badge warn">撤回来源时同步清理</span></div>').join('') + '</div>';
      }
      return body ? '<div class="section-block" style="margin-top:14px">' + body + '</div>' : '';
    }
    function renderLifecyclePanel(lifecycle) {
      const value = lifecycle || {};
      if (!value.found) return '';
      const atom = value.atom || {};
      const stateText = {active:'可使用', source_invalidated:'来源已变化 · 已停用', quarantined:'待核对', rejected:'已拒绝', forgotten:'已遗忘', superseded:'旧版本'};
      const counts = value.counts || {};
      const episodes = value.episodes || [];
      const supports = value.supports || [];
      const operations = value.operations || [];
      const episodeText = episodes.length
        ? episodes.slice(0, 4).map(item => '<div class="list-row"><div><div class="item-title">情节 · ' + esc(item.agent_name || '未知 Agent') + ' / ' + esc(item.session_id || '未知会话') + '</div><div class="muted">' + esc(item.event_count || 0) + ' 个事件 · ' + esc(item.task_type || '未标注任务') + '</div></div><span class="badge">' + esc((item.event_types || []).join('、')) + '</span></div>').join('')
        : '<div class="muted">暂无可展示的来源情节。</div>';
      const operationText = operations.length
        ? operations.slice(-5).reverse().map(item => '<div class="list-row"><div><div class="item-title">' + esc(item.label || item.operation) + '</div><div class="muted">' + esc(item.created_at || '') + '</div></div><span class="badge ' + (item.status === 'blocked' ? 'bad' : (item.status === 'review_required' ? 'warn' : 'ok')) + '">' + esc(item.status || 'completed') + '</span></div>').join('')
        : '<div class="muted">暂无受管操作记录。</div>';
      return '<div class="section-block" style="margin-top:14px"><div class="section-block-header"><strong>生命周期证据</strong><span class="badge">原子 · 情节 · 证据 · 操作</span></div><div class="section-block-body">'
        + '<div class="grid two"><div class="item"><div class="item-title">当前原子</div><div class="muted">' + esc(memoryTypeLabel(atom.kind || 'memory')) + ' · ' + esc(stateText[atom.lifecycle_state] || atom.lifecycle_state || '') + ' · ' + esc(tierLabel(atom.tier || '')) + '</div><div class="muted">版本 ' + esc(atom.version_id || '') + ' · 来源 ' + esc(sourceLabel(atom.source_kind || '')) + '</div></div><div class="item"><div class="item-title">受管影响面</div><div class="muted">' + esc((value.forgetting_boundary || {}).managed_surfaces ? (value.forgetting_boundary || {}).managed_surfaces.length : 0) + ' 个受管派生面</div><div class="muted">' + esc((value.forgetting_boundary || {}).state || '') + '</div></div></div>'
        + '<div class="item" style="margin-top:10px"><div class="health"><div class="item-title">形成与使用</div><span class="badge">' + esc(counts.episodes || 0) + ' 情节 · ' + esc(counts.supports || 0) + ' 支持 · ' + esc(counts.context_uses || 0) + ' 次使用</span></div><div class="linear-list" style="margin-top:8px">' + episodeText + '</div></div>'
        + '<details style="margin-top:10px"><summary>查看受管操作链</summary><div class="linear-list" style="margin-top:8px">' + operationText + '</div></details>'
        + '<div class="muted" style="margin-top:10px">来源支持片段：' + esc(supports.length) + ' 条；遗忘只处理受管数据，外部 Agent 日志和备份需在对应系统中单独清理。</div>'
        + '</div></div>';
    }
    async function loadMemoryDetail(event) {
      const idInput = document.getElementById('memoryDetailId');
      const id = String(idInput.value || '').replace(/[\\s\\u2026]+/g, '');
      idInput.value = id;
      if (!id) {
        setOperationStatus('请先选择一条记忆。', 'warn');
        return null;
      }
      return withBusy(event, async () => {
        const box = document.getElementById('memoryDetailCards');
        memoryDetailPreview = null;
        box.innerHTML = '<div class="skeleton" aria-label="正在读取记忆详情"></div>';
        let payload;
        try {
          payload = await getJson('/api/memory-detail?memory_id=' + encodeURIComponent(id) + '&light=1', 30000);
        } catch (err) {
          const errorCode = String(((err || {}).payload || {}).error || '');
          if (errorCode !== 'memory_not_found') {
            box.innerHTML = '<div class="empty-state"><strong>详情暂时没有加载出来</strong><span>'
              + esc(err && err.userMessage ? err.userMessage : '本地服务暂时没有响应。')
              + '</span><div class="mini-actions"><button class="secondary" data-action="retry-memory-detail">重新查询</button><button class="secondary" data-action="return-memory-detail">返回原列表</button></div></div>';
            throw err;
          }
          document.getElementById('memoryDetailCards').innerHTML = '<div class="empty-state"><strong>这条记忆当前不可查看</strong><span>'
            + esc(err.userMessage || '它可能已完成遗忘，或不属于当前记忆空间。')
            + '</span><div class="mini-actions"><button class="secondary" data-action="retry-memory-detail">重新查询</button><button class="secondary" data-action="return-memory-detail">返回原列表</button></div></div>';
          setRawOutput('memoryDetailOut', (err || {}).payload || {});
          return operationFeedback('没有找到这条记忆，请返回原列表重新选择。', 'warn');
        }
        const result = payload.result || {};
        const memory = result.memory || {};
        if (memory.project_root && !memory.preference_topic) memory.preference_scope = 'project';
        memoryDetailPreview = result.found ? result : null;
        const detailHtml = result.found
          ? '<div class="item"><div class="health"><div><div class="item-title">' + esc(memorySummaryLabel(memory)) + '</div><div class="memory-id">完整编号 ' + esc(memory.memory_id || id) + '</div></div><button class="secondary" onclick="copyMemoryId(\\'' + esc(memory.memory_id || id) + '\\')">复制编号</button></div><div class="muted">' + esc(memoryTypeLabel(memory.type || 'memory')) + ' · ' + esc(memorySubtypeLabel(memory.subtype || memory.type)) + ' · ' + esc(tierLabel(memory.tier || '')) + '</div><div class="muted">适用范围：' + esc(scopeLabel(memory.preference_scope || memory.scope || '')) + ' · 来源：' + esc(sourceLabel(memory.source_kind || (memory.source || {}).kind || '')) + '</div></div><div class="grid two"><div class="panel"><div class="item-title">来源与版本</div><div class="muted">来源事件 ' + esc((result.source_events || []).length) + ' 条 · 版本 ' + esc((result.versions || []).length) + ' 个</div><div class="muted">最近使用阶段：' + esc(stageLabel((result.agent_usage || {}).last_stage || '')) + '</div></div><div class="panel"><div class="item-title">关联信息</div><div class="muted">实体 ' + esc(((result.graph || {}).entities || []).length) + ' 个 · 关系 ' + esc(((result.graph || {}).relations || []).length) + ' 条</div></div></div>'
            + renderLifecyclePanel(result.lifecycle)
            + renderDerivationPanel(result)
          : '<div class="item"><div class="item-title">没有找到这条记忆</div><div class="muted">请从记忆中心重新选择。</div></div>';
        document.getElementById('memoryDetailCards').innerHTML = detailHtml;
        setRawOutput('memoryDetailOut', payload);
        return operationFeedback(result.found ? '记忆详情已加载。' : '没有找到这条记忆。', result.found ? 'ok' : 'warn');
      });
    }
    async function forgetMemoryFromDetail(event) {
      const id = document.getElementById('memoryDetailId').value.trim();
      if (!id || !memoryDetailPreview || (memoryDetailPreview.memory || {}).memory_id !== id) {
        return operationFeedback('请先加载最新的记忆详情再确认遗忘。', 'warn');
      }
      const derived = (memoryDetailPreview.derivation_impact || {}).derived_memories || [];
      const versions = Object.assign({}, (memoryDetailPreview.derivation_impact || {}).expected_versions || {});
      versions[id] = (memoryDetailPreview.memory || {}).version_id;
      if (!window.confirm('确认遗忘这条记忆及其 ' + derived.length + ' 条派生记忆？系统将复核受管数据，其他独立记忆继续保留。')) return;
      return withMutation(event, '遗忘记忆', async () => {
        const payload = await postJson('/api/forget-memory', {memory_ids:[id], reason:'用户从记忆详情发起遗忘', compact:true, expected_versions:versions});
        setRawOutput('memoryDetailOut', payload);
        const result = payload.result || {};
        const forgottenIds = result.forgotten_memory_ids || [];
        const verified = payload.ok === true && result.ok === true;
        if (forgottenIds.includes(id)) {
          memoryDetailPreview = null;
          document.getElementById('memoryDetailCards').innerHTML = '<div class="empty-state"><strong>'
            + (verified ? '遗忘已完成' : '记忆已移除，复检仍需处理') + '</strong><span>已移除 '
            + esc(forgottenIds.length) + ' 条记录，其中包含 ' + esc((result.derived_memory_ids || []).length)
            + ' 条派生记忆。删除前的内容不再展示，其他独立记忆继续保留。</span><div class="mini-actions">'
            + '<button class="secondary" data-action="return-memory-detail">返回原列表</button></div></div>';
        } else if (result.reason === 'stale_preview') {
          await loadMemoryDetail(null);
        }
        await loadFeedbackHome(null);
        const feedback = operationFeedback(verified ? '这条记忆的遗忘验证已完成。'
          : (result.reason === 'stale_preview' ? '记忆或派生范围已变化，请核对更新后的详情再确认。' : '遗忘未完全完成，请查看验证报告。'), verified ? 'ok' : 'warn');
        setOperationStatus(feedback.operationFeedback.message, feedback.operationFeedback.state);
        return feedback;
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
          + '<div class="item"><div class="item-title">外部残留</div><div class="muted">'
          + esc(externalResidueMessage(residue)) + '</div></div>';
        setRawOutput('agentOut', payload);
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
      setRawOutput('overviewOut', status);
      const result = status.result || {};
      document.getElementById('namespaceMeta').textContent = '记忆空间：' + namespaceLabel(result.namespace);
      const memoryCount = document.getElementById('statMemories');
      if (memoryCount) memoryCount.textContent = result.live_record_count ?? '-';
      const banner = document.getElementById('overviewStatusBanner');
      const title = document.getElementById('overviewStateTitle');
      const text = document.getElementById('overviewStateText');
      banner.className = 'overview-status-banner ' + (status.ok ? '' : 'bad');
      title.textContent = status.ok ? 'Agent 记忆服务可以正常使用' : '记忆服务需要处理';
      text.textContent = status.ok
        ? '可以查看、搜索和遗忘本机记忆；敏感内容仍会在写入前检查。'
        : '当前状态可能影响 Agent 读写记忆，请进入系统维护检查服务。';
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
      renderStatusSummary(status || {ok:true, result:{}}, diag);
      renderRefreshWarnings(diag, retrieval, runtime, sdkAvailable);
    }
    async function refresh(event, options) {
      if (event) return withBusy(event, async () => refresh(null, options));
      const settings = options || {};
      const settled = await Promise.allSettled([
        getJson('/api/status', 5000),
        loadFeedbackHome(null),
        loadOverviewEvidence(null),
      ]);
      const statusResult = settled[0];
      if (statusResult.status === 'fulfilled')
        renderStatusPayload(statusResult.value);
      if (statusResult.status === 'rejected')
        throw statusResult.reason;

      const partial = settled.some(item => item.status === 'rejected');
      if (partial && !settings.silent)
        setOperationStatus('服务状态已显示，最近记忆暂未更新；可以继续使用搜索和遗忘。', 'warn');
      return operationFeedback(
        partial ? '服务状态已显示，最近记忆暂未更新。' : '状态已刷新。',
        partial ? 'warn' : 'ok'
      );
    }
    async function refreshAfterMutation(successMessage, state) {
      try {
        const result = await refresh(null, {silent:true});
        if (result && result.operationFeedback && result.operationFeedback.state === 'warn')
          return operationFeedback(successMessage + '，但部分概览暂未更新。', 'warn');
      } catch (err) {
        return operationFeedback(successMessage + '，但部分概览暂未更新。', 'warn');
      }
      return operationFeedback(successMessage + '。', state || 'ok');
    }
    function retrievalModeLabel(backend) {
      const value = String(backend || '').toLowerCase();
      if (value.includes('kylin') || value.includes('coreai') || value.includes('vector')) return '本机增强';
      if (value.includes('hash') || value.includes('sqlite') || value.includes('fts')) return '基础检索';
      return value ? '自动选择' : '待确认';
    }
    function renderRefreshWarnings(diag, retrieval, runtime, sdkAvailable) {
      const warnings = [];
      if (!(diag.audit_chain && diag.audit_chain.ok)) warnings.push({text:'操作记录完整性需要复核。', label:'立即校验', action:'audit'});
      if (!(retrieval.requested_backend || retrieval.last_backend_used)) warnings.push({text:'检索方式尚未确认。', label:'检查检索方式', action:'retrieval'});
      if (!sdkAvailable) warnings.push({text:'本机智能检索当前使用稳定降级，不影响基础检索。', label:'查看降级状态', action:'retrieval'});
      const box = document.getElementById('refreshWarnings');
      if (!warnings.length) {
        box.style.display = 'none';
        box.innerHTML = '';
        return;
      }
      box.style.display = 'block';
      box.innerHTML = '<div class="item-title">需要关注</div>'
        + warnings.map(item => '<div class="list-row"><div class="muted">' + esc(item.text) + '</div>'
          + '<button class="secondary" data-action="diagnostic-warning" data-warning-action="' + esc(item.action) + '">' + esc(item.label) + '</button></div>').join('');
    }
    function runDiagnosticWarning(event, action) {
      if (action === 'audit') return verifyAudit(event);
      return retrievalStatus(event);
    }
    async function ingest(event) {
      return withMutation(event, '保存记忆', async () => {
        const data = {text: ingestText.value, source_kind: sourceKind.value, tier: tier.value, natural: true, session_id: memorySessionId};
        const payload = await postJson('/api/ingest', data);
        setRawOutput('ingestOut', payload);
        renderIngestFeedback(payload);
        const result = payload.result || {};
        return refreshAfterMutation(
          result.partial_conflict ? '部分内容已处理，事实冲突需核对'
            : (result.stored ? (result.updated ? '记忆已更新' : '记忆已保存') : '本次未新增记忆，请查看下方处理结果'),
          result.stored && !result.partial_conflict ? 'ok' : 'warn'
        );
      });
    }
    async function endBrowserMemorySession(event) {
      return withMutation(event, '结束当前会话', async () => {
        const preview = await postJson('/api/evict-session', {session_id: memorySessionId, confirm: false});
        const count = Number((preview.result || {}).candidate_count || 0);
        if (count && !window.confirm('结束当前会话将清理 ' + count + ' 条临时记忆。长期记忆不会删除。确定继续吗？'))
          return operationFeedback('已取消，会话记忆没有改变。', 'warn');
        const completed = await postJson('/api/evict-session', {
          session_id: memorySessionId, confirm: true,
          expected_versions: (preview.result || {}).expected_versions || {}
        });
        if (!(completed.result || {}).evicted)
          throw new Error('会话清理未完成，请检查操作记录。');
        memorySessionId = crypto.randomUUID();
        sessionStorage.setItem('memguardos_memory_session', memorySessionId);
        await loadMemoryWorkspace(null);
        return operationFeedback(count ? '当前会话已结束，临时记忆已清理；长期记忆保持可用。' : '当前会话已结束，没有需要清理的临时记忆。', 'ok');
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
      if (result.duplicate && result.memory_id) {
        const memoryId = result.memory_id;
        box.innerHTML = '<div class="item"><div class="health"><div class="item-title">相同记忆已经存在</div><span class="badge warn">未重复保存</span></div>'
          + '<div class="muted">系统继续使用原有记忆编号 ' + esc(shortMemoryId(memoryId)) + '，不会产生新的重复记录。</div>'
          + '<div class="mini-actions"><button class="secondary" data-action="open-memory-detail" data-memory-id="' + esc(memoryId) + '">查看原记忆</button><button class="secondary" data-action="copy-memory-id" data-memory-id="' + esc(memoryId) + '">复制编号</button></div></div>';
        return;
      }
      const items = (feedback.updated_memories || []).concat(feedback.new_memories || []);
      const candidates = result.candidate_memory_ids || [];
      const conflict = result.conflict || {};
      const conflictLabel = result.conflict_id ? '核对编号：' + shortMemoryId(result.conflict_id) + '。' : '';
      const conflictValue = conflict.incoming_value ? '待写入的新值：' + conflict.incoming_value + '。' : '';
      const conflictBanner = candidates.length ? '<div class="item"><div class="health"><div class="item-title">事实内容需要核对</div><span class="badge warn">未覆盖旧值</span></div><div class="muted">同一属性已有不同记录。' + esc(conflictLabel + conflictValue) + '确认后请明确提出更正。</div><div class="mini-actions">'
        + candidates.map(id => '<button class="secondary" data-action="open-memory-detail" data-memory-id="' + esc(id) + '">核对原记忆 ' + esc(shortMemoryId(id)) + '</button>').join('') + '</div></div>' : '';
      if (!items.length) {
        box.innerHTML = '<div class="item"><div class="item-title">本次没有新增记忆</div><div class="muted">'
          + esc(((feedback.skipped_items || [])[0] || {}).summary_for_user || '请检查输入内容和保存策略。') + '</div>'
          + '</div>' + conflictBanner;
        return;
      }
      box.innerHTML = conflictBanner + items.slice(0, 5).map(item =>
        '<div class="item"><div class="health"><div class="item-title">' + esc(item.category_label || '可复用记忆') + '：' + esc(item.summary_for_user) + '</div><span class="badge ok">' + esc(item.status_label || '已保存') + '</span></div><div class="muted">来源：' + esc(item.evidence_label || item.why_extracted) + '</div><div class="muted">后续用于：' + esc((item.where_it_will_apply || []).join('、')) + '</div><div class="mini-actions"><button class="secondary" data-action="open-memory-detail" data-memory-id="' + esc(item.memory_id || '') + '">查看详情</button>'
        + (item.preference_topic ? '<button class="secondary" data-action="preference-proof" data-memory-id="' + esc(item.memory_id || '') + '">测试生效</button>' : '') + '</div></div>'
      ).join('') + (items.length > 5 ? '<div class="muted">另有 ' + esc(items.length - 5) + ' 项可在最近记忆中查看。</div>' : '');
    }
    async function explainExtraction(event) {
      return withBusy(event, async () => {
        const url = '/api/explain-extraction?text=' + encodeURIComponent(ingestText.value) + '&source_kind=' + encodeURIComponent(sourceKind.value);
        const payload = await getJson(url);
        setRawOutput('ingestOut', payload);
        const result = payload.result || {};
        const extraction = result.extraction || {};
        const quality = result.quality || {};
        const sensitivity = result.sensitivity || {};
        const signals = extraction.signals || [];
        const blocked = String(sensitivity.level) === 'high';
        document.getElementById('ingestFeedback').innerHTML =
          '<div class="section-block"><div class="section-block-header"><strong>提取依据（未写入记忆）</strong>'
          + '<span class="badge">' + esc(memoryTypeLabel(extraction.memory_type || extraction.type || '')) + '</span></div>'
          + '<div class="section-block-body"><div class="linear-list">'
          + '<div class="list-row"><div><div class="item-title">判定类型</div><div class="muted">'
          + esc(memoryTypeLabel(extraction.memory_type || extraction.type || '')) + ' / '
          + esc(memorySubtypeLabel(extraction.subtype || '')) + '</div></div>'
          + '<span class="badge">置信度 ' + esc(extraction.confidence != null ? extraction.confidence : '-') + '</span></div>'
          + '<div class="list-row"><div><div class="item-title">判定信号</div><div class="muted">'
          + esc(signals.length ? signals.join('、') : '未识别到明显信号') + '</div></div>'
          + '<span class="badge">' + signals.length + ' 条</span></div>'
          + '<div class="list-row"><div><div class="item-title">内容质量</div><div class="muted">'
          + esc(quality.accepted ? '通过质量门槛' : '未通过：' + ((quality.issues || []).join('、') || '内容信息量不足'))
          + '</div></div><span class="badge ' + (quality.accepted ? 'ok' : 'warn') + '">'
          + (quality.accepted ? '可保存' : '需修改') + '</span></div>'
          + '<div class="list-row"><div><div class="item-title">敏感检查</div><div class="muted">'
          + esc(sensitivityLabel(sensitivity.level || 'none')) + '（' + esc((sensitivity.labels || []).join('、') || '未发现敏感特征') + '）'
          + '</div></div><span class="badge ' + (blocked ? 'bad' : 'ok') + '">'
          + (blocked ? '会被阻断' : '允许保存') + '</span></div>'
          + '</div><p class="muted" style="margin-top:10px">这是保存前的预判，本次没有写入任何记忆。</p></div></div>';
        return operationFeedback('已显示这段文本会被如何提取。', 'ok');
      });
    }
    async function search(event) {
      await withBusy(event, async () => {
        const payload = await getJson('/api/search?q=' + encodeURIComponent(searchQuery.value) + '&top_k=5&session_id=' + encodeURIComponent(memorySessionId));
        renderSearchCards(payload);
        setRawOutput('searchOut', payload);
      });
    }
    async function graphSearch(event) {
      return withBusy(event, async () => {
        const payload = await getJson('/api/hierarchical-graph?q=' + encodeURIComponent(searchQuery.value) + '&limit=8');
        setRawOutput('searchOut', payload);
        const layers = (payload.result || {}).layers || {};
        const box = document.getElementById('searchCards');
        const names = [['summary', '摘要层'], ['entity_relation', '实体关系层'], ['chunk', '原文片段层']];
        const total = names.reduce((sum, item) => sum + ((layers[item[0]] || []).length), 0);
        if (!total) {
          box.innerHTML = '<div class="empty-state"><strong>没有找到关联结果</strong>'
            + '<span>换一个实体名（例如 systemd、cgroup、journalctl）再试。</span></div>';
          return operationFeedback('层次图谱没有命中关联记忆。', 'warn');
        }
        box.innerHTML = names.map(function (pair) {
          const rows = layers[pair[0]] || [];
          const body = rows.length
            ? rows.slice(0, 6).map(function (row) {
                return '<div class="list-row"><div><div class="item-title">'
                  + esc(row.summary || row.relation || row.content_preview || row.memory_id || '关联项')
                  + '</div><div class="muted">'
                  + esc(row.source && row.target ? (row.source + ' → ' + row.target) : shortMemoryId(row.memory_id || ''))
                  + '</div></div><span class="badge">' + esc(pair[1]) + '</span></div>';
              }).join('')
            : '<div class="muted">该层没有命中。</div>';
          return '<div class="section-block" style="margin-bottom:12px"><div class="section-block-header"><strong>'
            + esc(pair[1]) + '</strong><span class="badge">' + rows.length + ' 项</span></div>'
            + '<div class="section-block-body"><div class="linear-list">' + body + '</div></div></div>';
        }).join('');
        return operationFeedback('层次图谱已展开三层关联结果。', 'ok');
      });
    }
    async function contextBuild(event) {
      await withBusy(event, async () => {
        const payload = await getJson('/api/context?q=' + encodeURIComponent(contextQuery.value) + '&token_budget=' + encodeURIComponent(tokenBudget.value) + '&session_id=' + encodeURIComponent(memorySessionId));
        renderContextVisual(payload);
        setRawOutput('contextOut', payload);
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
        '确认遗忘已选择的 ' + forgetPreview.selectedIds.length
        + ' 条记忆并整理存储文件？未选择的候选将继续保留。'
      )) return null;
      return withMutation(event, '精准遗忘', async () => {
        const payload = await postJson('/api/forget', {
          request: forgetRequest.value,
          confirm: confirmForget,
          compact: confirmForget,
          selected_memory_ids:confirmForget ? forgetPreview.selectedIds : [],
          preview_token:confirmForget ? forgetPreview.previewToken : ''
        });
        renderForgetCards(payload, confirmForget);
        setRawOutput('forgetOut', payload);
        if (confirmForget) {
          invalidateForgetPreview();
          tabLoadedAt.delete('memory');
          await loadRecentForgetting(null, true);
          return refreshAfterMutation('遗忘已经完成，验证结果已更新');
        }
        rememberForgetPreview(payload);
        return operationFeedback('遗忘范围预览已生成。', 'ok');
      });
    }
    async function audit(event) {
      await withBusy(event, async () => { setRawOutput('auditOut', await getJson('/api/audit?limit=30')); return operationFeedback('操作记录已更新。', 'ok'); });
    }
    async function verifyAudit(event) {
      await withBusy(event, async () => {
        const payload = await getJson('/api/verify-audit');
        setRawOutput('diagnosticsOut', payload);
        const ok = Boolean((payload.result || {}).ok);
        badge('auditBadge', ok ? 'ok' : 'bad', ok ? '通过' : '异常');
        dot('auditDot', ok ? 'ok' : 'bad');
        return operationFeedback(ok ? '操作记录校验通过。' : '操作记录存在异常，请导出系统报告。', ok ? 'ok' : 'bad');
      });
    }
    async function retrievalStatus(event) {
      await withBusy(event, async () => {
        const payload = await getJson('/api/retrieval-status');
        setRawOutput('diagnosticsOut', payload);
        const retrieval = payload.result || {};
        const ready = Boolean(retrieval.requested_backend || retrieval.last_backend_used);
        badge('retrievalBadge', ready ? 'ok' : 'warn', retrievalModeLabel(retrieval.last_backend_used || retrieval.requested_backend));
        dot('retrievalDot', ready ? 'ok' : 'warn');
        return operationFeedback(ready ? '检索方式检查已完成。' : '检索方式尚未确认，请重新检查服务。', ready ? 'ok' : 'warn');
      });
    }
    async function memoryPolicy(event) {
      await withBusy(event, async () => { setRawOutput('diagnosticsOut', await getJson('/api/memory-policy')); return operationFeedback('记忆策略已加载。', 'ok'); });
    }
    let memoryBudgetPlan = null;
    async function loadMemoryBudget(event) {
      await withBusy(event, async () => {
        const raw = document.getElementById('budgetTargetBytes').value;
        const target = Math.max(128, Number.parseInt(raw || '0', 10) || 0);
        const payload = await getJson('/api/memory-budget?target_bytes=' + encodeURIComponent(target));
        memoryBudgetPlan = (payload.result || null);
        setRawOutput('memoryBudgetOut', payload);
        const summary = (memoryBudgetPlan || {}).summary || {};
        const candidates = (memoryBudgetPlan || {}).candidates || [];
        const selected = new Set((memoryBudgetPlan || {}).demotion_ids || []);
        const box = document.getElementById('memoryBudgetSummary');
        if (!payload.ok || !memoryBudgetPlan || memoryBudgetPlan.schema !== 'memguardos-memory-budget-plan-v1') {
          box.innerHTML = '<div class="item"><div class="item-title">预算预览没有生成</div><div class="muted">' + esc(payload.error || '请检查目标字节数和管理员权限。') + '</div></div>';
          document.getElementById('applyBudgetButton').disabled = true;
          return operationFeedback('预算预览没有生成，请检查输入。', 'warn');
        }
        const head = '<div class="item"><div class="health"><div class="item-title">长期层预计占用 ' + esc(summary.current_bytes || 0) + ' bytes</div><span class="badge ' + (summary.target_reached ? 'ok' : 'warn') + '">' + (summary.target_reached ? '目标可达' : '保护边界下不可完全达到') + '</span></div><div class="muted">目标 ' + esc(summary.target_bytes || target) + ' bytes · 预计回收 ' + esc(summary.bytes_to_reclaim || 0) + ' bytes · 保护 ' + esc(summary.protected_count || 0) + ' 条 · 建议降级 ' + esc(summary.demotion_count || 0) + ' 条</div></div>';
        const rows = candidates.slice(0, 12).map(item => '<div class="item"><div class="health"><div class="item-title">' + esc(shortMemoryId(item.memory_id)) + '</div><span class="badge ' + (item.recommended_action === 'retain_protected' ? 'ok' : 'warn') + '">' + esc(item.recommended_action === 'retain_protected' ? '受保护' : '可逆降级') + '</span></div><div class="muted">价值 ' + esc(item.value_score) + ' · 风险 ' + esc(item.risk_score) + ' · 估算 ' + esc(item.storage_bytes) + ' bytes · 单位价值 ' + esc(item.value_per_byte) + '</div></div>').join('');
        box.innerHTML = head + (rows || '<div class="item"><div class="muted">当前没有长期记忆候选。</div></div>');
        document.getElementById('applyBudgetButton').disabled = selected.size === 0;
        return operationFeedback('预算预览已生成，请核对受保护项和降级候选。', 'ok');
      });
    }
    async function applyMemoryBudget(event) {
      if (!memoryBudgetPlan || !(memoryBudgetPlan.demotion_ids || []).length) return operationFeedback('当前没有可执行的预算建议。', 'warn');
      if (!window.confirm('确认将建议候选降级到临时会话层？系统不会删除正文，但会追加新版本。')) return null;
      await withBusy(event, async () => {
        const payload = await postJson('/api/apply-budget', {
          plan: memoryBudgetPlan,
          selected_memory_ids: memoryBudgetPlan.demotion_ids,
          target_session_id: 'web-budget-review',
          confirm: true,
        });
        setRawOutput('memoryBudgetOut', payload);
        document.getElementById('applyBudgetButton').disabled = true;
        return operationFeedback(payload.ok && (payload.result || {}).applied ? '预算建议已执行，普通记忆已可逆降级。' : '预算建议未执行，请查看回执。', payload.ok && (payload.result || {}).applied ? 'ok' : 'warn');
      });
    }
    async function diagnostics(event) {
      return withBusy(event, async () => {
        const payload = await getJson('/api/diagnostics');
        setRawOutput('diagnosticsOut', payload);
        renderDiagnosticsPayload(payload, {ok:payload.ok, result:{namespace:(payload.result || {}).namespace}});
        return operationFeedback('系统状态检查已完成。', 'ok');
      });
    }
    async function exportDiagnostics(event) {
      return downloadJsonExport(event, '/api/export-diagnostics', 'memguardos-diagnostics.json', '系统报告');
    }
    async function exportEvidence(event) {
      return downloadJsonExport(event, '/api/export-evidence', 'memguardos-local-evidence.json', '证据包');
    }
    async function downloadJsonExport(event, url, fallbackFilename, label) {
      return withBusy(event, async () => {
        const response = await fetchWithTimeout(url, {headers: authHeaders()}, 90000);
        if (!response.ok) {
          let payload = {};
          try {
            payload = await response.json();
          } catch (_err) {
            payload = {};
          }
          throw requestError(userErrorMessage(payload, label + '没有生成，请重试。'));
        }
        const blob = await response.blob();
        const disposition = response.headers.get('Content-Disposition') || '';
        const match = disposition.match(/filename="?([^";]+)"?/i);
        const filename = match && match[1] ? match[1] : fallbackFilename;
        const objectUrl = URL.createObjectURL(blob);
        const link = document.createElement('a');
        link.href = objectUrl;
        link.download = filename;
        document.body.appendChild(link);
        link.click();
        link.remove();
        window.setTimeout(() => URL.revokeObjectURL(objectUrl), 0);
        return operationFeedback(label + '已生成，浏览器已开始下载。', 'ok');
      });
    }
    openHashTab();
    if (bootstrapTab && document.getElementById(bootstrapTab)) {
      const navSource = Array.from(document.querySelectorAll('nav button'))
        .find(item => item.dataset.tab === (tabParents[bootstrapTab] || bootstrapTab));
      const sectionTitle = document.querySelector('#' + bootstrapTab + ' h2');
      const pageTitle = sectionTitle ? sectionTitle.textContent.trim() : (navSource ? navSource.textContent.trim() : bootstrapTab);
      setOperationStatus('已按桌面入口打开「' + pageTitle + '」，下方内容加载完成后即可查看。', 'ok');
    }
    document.getElementById('memorySearchQuery').addEventListener('keydown', event => {
      if (event.key === 'Enter') {
        event.preventDefault();
        searchMemoryWorkspace(event);
      }
    });
    loadSessionCapabilities().then(payload => refreshForSession(payload)).catch(handleInitialLoadFailure);
  </script>
</body>
</html>"""
