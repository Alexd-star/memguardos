from threading import Thread
from urllib.error import HTTPError
from urllib.request import Request, urlopen
import json
import socket
import time

from memguard.service import MemGuardService
from memguard.web.dashboard import run_dashboard


def free_port():
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


def request_json(url, token="", method="GET", payload=None):
    headers = {}
    data = None
    if token:
        headers["Authorization"] = "Bearer " + token
    if payload is not None:
        headers["Content-Type"] = "application/json"
        data = json.dumps(payload).encode("utf-8")
    request = Request(url, headers=headers, data=data, method=method)
    try:
        with urlopen(request, timeout=5) as response:
            return response.status, json.loads(response.read().decode("utf-8"))
    except HTTPError as exc:
        return exc.code, json.loads(exc.read().decode("utf-8"))


def start_dashboard(tmp_path):
    service = MemGuardService(store_path=tmp_path / "memory.segment", db_path=tmp_path / "memory.sqlite3")
    port = free_port()
    tokens = {"viewer": "viewer-secret", "operator": "operator-secret", "admin": "admin-secret"}
    thread = Thread(target=run_dashboard, args=(service, "127.0.0.1", port, tokens), daemon=True)
    thread.start()
    for _ in range(100):
        try:
            status, _ = request_json("http://127.0.0.1:%d/healthz" % port)
            if status == 200:
                break
        except OSError:
            time.sleep(0.02)
    return service, "http://127.0.0.1:%d" % port


def test_health_and_readiness_are_minimal_and_public(tmp_path):
    service, base = start_dashboard(tmp_path)
    try:
        health_status, health = request_json(base + "/healthz")
        ready_status, ready = request_json(base + "/readyz")

        assert health_status == 200
        assert health == {"ok": True, "status": "alive"}
        assert ready_status == 200
        assert ready["ok"] is True
        assert ready["status"] == "ready"
        assert "db_path" not in ready
    finally:
        service.close()


def test_viewer_can_read_but_cannot_mutate(tmp_path):
    service, base = start_dashboard(tmp_path)
    try:
        read_status, _ = request_json(base + "/api/status", "viewer-secret")
        plan_status, plan = request_json(base + "/api/acceptance-plan", "viewer-secret")
        write_status, body = request_json(
            base + "/api/ingest",
            "viewer-secret",
            method="POST",
            payload={"text": "viewer must not write"},
        )

        assert read_status == 200
        assert plan_status == 200
        assert plan["ok"] is True
        assert plan["result"]["summary"]["total"] >= 10
        assert any(item["id"] == "A21_VERIFY_AUDIT" for item in plan["result"]["items"])
        assert write_status == 403
        assert body["error"] == "forbidden"
        assert body["required_role"] == "operator"
    finally:
        service.close()


def test_operator_can_ingest_and_preview_but_only_admin_confirms_forgetting(tmp_path):
    service, base = start_dashboard(tmp_path)
    try:
        ingest_status, ingest = request_json(
            base + "/api/ingest",
            "operator-secret",
            method="POST",
            payload={"text": "以后回答时使用中文。", "source_kind": "manual"},
        )
        preview_status, _ = request_json(
            base + "/api/forget",
            "operator-secret",
            method="POST",
            payload={"request": "忘掉回答语言偏好", "confirm": False},
        )
        confirm_status, forbidden = request_json(
            base + "/api/forget",
            "operator-secret",
            method="POST",
            payload={"request": "忘掉回答语言偏好", "confirm": True},
        )
        admin_status, _ = request_json(
            base + "/api/forget",
            "admin-secret",
            method="POST",
            payload={"request": "忘掉回答语言偏好", "confirm": True, "compact": True},
        )

        assert ingest_status == 200 and ingest["ok"] is True
        assert preview_status == 200
        assert confirm_status == 403 and forbidden["required_role"] == "admin"
        assert admin_status == 200
    finally:
        service.close()


def test_acceptance_run_is_admin_only_and_uses_whitelist(tmp_path):
    service, base = start_dashboard(tmp_path)
    try:
        operator_status, operator_body = request_json(
            base + "/api/acceptance-run",
            "operator-secret",
            method="POST",
            payload={"item_id": "A21_VERIFY_AUDIT"},
        )
        admin_status, admin_body = request_json(
            base + "/api/acceptance-run",
            "admin-secret",
            method="POST",
            payload={"item_id": "A21_VERIFY_AUDIT"},
        )
        unknown_status, unknown_body = request_json(
            base + "/api/acceptance-run",
            "admin-secret",
            method="POST",
            payload={"item_id": "rm -rf /"},
        )

        assert operator_status == 403
        assert operator_body["required_role"] == "admin"
        assert admin_status == 200
        assert admin_body["ok"] is True
        assert admin_body["result"]["id"] == "A21_VERIFY_AUDIT"
        assert admin_body["result"]["status"] == "passed"
        assert "command" not in admin_body["result"]
        assert unknown_status == 400
        assert unknown_body["error"] == "unknown_acceptance_item"
    finally:
        service.close()


def test_invalid_token_is_unauthorized_and_audited(tmp_path):
    service, base = start_dashboard(tmp_path)
    try:
        status, body = request_json(base + "/api/status", "wrong-secret")
        assert status == 401
        assert body["error"] == "unauthorized"
        deadline = time.time() + 2
        events = []
        while time.time() < deadline:
            verifier = MemGuardService(store_path=service.store.path, db_path=service.meta.path)
            try:
                events = verifier.audit(limit=10, event_type="web_authorization_denied")
            finally:
                verifier.close()
            if events:
                break
            time.sleep(0.02)
        assert events
        detail = json.loads(events[0]["detail"])
        assert detail["reason"] == "invalid_token"
        assert "token" not in detail
    finally:
        service.close()
