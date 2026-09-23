from http.client import RemoteDisconnected
from threading import Thread
from urllib.error import HTTPError
from urllib.request import Request, urlopen
import json
import socket
import time

import pytest

from memguard.service import MemGuardService
from memguard.web.acceptance import ACCEPTANCE_ITEMS
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
    except RemoteDisconnected as exc:
        raise AssertionError("dashboard disconnected without a JSON response") from exc


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


def complete_acceptance_results():
    return [{"id": item.id, "status": "passed", "ok": True, "details": {}} for item in ACCEPTANCE_ITEMS]


def test_health_is_public_but_readiness_requires_configured_authentication(tmp_path):
    service, base = start_dashboard(tmp_path)
    try:
        health_status, health = request_json(base + "/healthz")
        missing_status, missing = request_json(base + "/readyz")
        ready_status, ready = request_json(base + "/readyz", "viewer-secret")

        assert health_status == 200
        assert health == {"ok": True, "status": "alive"}
        assert missing_status == 401
        assert missing == {"ok": False, "error": "unauthorized"}
        assert ready_status == 200
        assert ready["ok"] is True
        assert ready["status"] == "ready"
        assert "db_path" not in ready
        assert "token" not in ready
    finally:
        service.close()


def test_anonymous_session_exposes_only_empty_capabilities(tmp_path):
    service, base = start_dashboard(tmp_path)
    try:
        status, body = request_json(base + "/api/session")

        assert status == 200
        assert body == {
            "ok": True,
            "result": {
                "role": "anonymous",
                "capabilities": {
                    "read": False,
                    "write_memory": False,
                    "preview_forgetting": False,
                    "confirm_forgetting": False,
                    "run_acceptance": False,
                    "administer": False,
                },
            },
        }
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


@pytest.mark.parametrize(
    ("token", "role", "can_write", "can_administer"),
    [
        ("viewer-secret", "viewer", False, False),
        ("operator-secret", "operator", True, False),
        ("admin-secret", "admin", True, True),
    ],
)
def test_session_capabilities_reflect_the_authenticated_role(
    tmp_path,
    token,
    role,
    can_write,
    can_administer,
):
    service, base = start_dashboard(tmp_path)
    try:
        status, body = request_json(base + "/api/session", token)

        assert status == 200
        assert body["ok"] is True
        assert body["result"]["role"] == role
        assert body["result"]["capabilities"] == {
            "read": True,
            "write_memory": can_write,
            "preview_forgetting": can_write,
            "confirm_forgetting": can_administer,
            "run_acceptance": can_administer,
            "administer": can_administer,
        }
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
        preview_status, preview = request_json(
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
            payload={
                "request": "忘掉回答语言偏好",
                "confirm": True,
                "compact": True,
                "selected_memory_ids": [
                    item["memory_id"] for item in preview["result"]["candidates"]
                ],
                "preview_token": preview["result"]["preview_token"],
            },
        )

        assert ingest_status == 200 and ingest["ok"] is True
        assert preview_status == 200
        assert confirm_status == 403 and forbidden["required_role"] == "admin"
        assert admin_status == 200
    finally:
        service.close()


def test_confirm_forgetting_requires_a_matching_unexpired_preview_receipt(tmp_path):
    service, base = start_dashboard(tmp_path)
    try:
        request_json(
            base + "/api/ingest",
            "operator-secret",
            method="POST",
            payload={"text": "临时代号 BlueRiver 只用于本次测试。", "source_kind": "manual"},
        )
        direct_status, direct = request_json(
            base + "/api/forget",
            "admin-secret",
            method="POST",
            payload={"request": "忘掉 BlueRiver", "confirm": True, "compact": True},
        )
        preview_status, preview = request_json(
            base + "/api/forget",
            "operator-secret",
            method="POST",
            payload={"request": "忘掉 BlueRiver", "confirm": False},
        )
        stale_status, stale = request_json(
            base + "/api/forget",
            "admin-secret",
            method="POST",
            payload={
                "request": "忘掉其他主题",
                "confirm": True,
                "compact": True,
                "preview_token": preview["result"]["preview_token"],
            },
        )
        confirm_status, confirmed = request_json(
            base + "/api/forget",
            "admin-secret",
            method="POST",
            payload={
                "request": "忘掉 BlueRiver",
                "confirm": True,
                "compact": True,
                "preview_token": preview["result"]["preview_token"],
            },
        )
        replay_status, replay = request_json(
            base + "/api/forget",
            "admin-secret",
            method="POST",
            payload={
                "request": "忘掉 BlueRiver",
                "confirm": True,
                "compact": True,
                "preview_token": preview["result"]["preview_token"],
            },
        )

        assert direct_status == 409
        assert direct["error"] == "forget_preview_required"
        assert preview_status == 200
        assert preview["result"]["preview_token"]
        assert preview["result"]["preview_candidate_count"] >= 1
        assert stale_status == 409
        assert stale["error"] == "forget_preview_stale"
        assert confirm_status == 200
        assert confirmed["ok"] is True
        assert replay_status == 409
        assert replay["error"] == "forget_preview_required"
    finally:
        service.close()


def test_web_forget_rejects_updated_memory_after_preview(tmp_path):
    service, base = start_dashboard(tmp_path)
    try:
        memory_id = service.ingest_text("BlueRiver 项目部署目录是 /srv/old。")["record"].memory_id
        preview_status, preview = request_json(
            base + "/api/forget", "operator-secret", method="POST",
            payload={"request": "忘掉 BlueRiver", "confirm": False},
        )
        assert preview_status == 200
        assert memory_id in {item["memory_id"] for item in preview["result"]["candidates"]}

        service.update_memory(memory_id, "BlueRiver 项目部署目录是 /srv/new。")
        status, result = request_json(
            base + "/api/forget", "admin-secret", method="POST",
            payload={
                "request": "忘掉 BlueRiver", "confirm": True,
                "preview_token": preview["result"]["preview_token"],
            },
        )
        assert status == 409
        assert result["error"] == "forget_preview_stale"
        assert service.meta.get_memory(memory_id)["deleted"] == 0
    finally:
        service.close()


def test_web_forgetting_requires_and_honors_subset_selection(tmp_path):
    service, base = start_dashboard(tmp_path)
    try:
        for text in (
            "记住：蓝河项目部署目录是 /srv/blue-river。",
            "记住：蓝河项目日志目录是 /var/log/blue-river。",
        ):
            status, body = request_json(
                base + "/api/ingest",
                "operator-secret",
                method="POST",
                payload={"text": text, "source_kind": "manual"},
            )
            assert status == 200 and body["ok"] is True

        preview_status, preview = request_json(
            base + "/api/forget",
            "operator-secret",
            method="POST",
            payload={"request": "忘掉蓝河项目", "confirm": False},
        )
        candidates = preview["result"]["candidates"]
        token = preview["result"]["preview_token"]
        no_selection_status, no_selection = request_json(
            base + "/api/forget",
            "admin-secret",
            method="POST",
            payload={
                "request": "忘掉蓝河项目",
                "confirm": True,
                "compact": True,
                "preview_token": token,
            },
        )
        selected_id = candidates[0]["memory_id"]
        confirm_status, confirmed = request_json(
            base + "/api/forget",
            "admin-secret",
            method="POST",
            payload={
                "request": "忘掉蓝河项目",
                "confirm": True,
                "compact": True,
                "selected_memory_ids": [selected_id],
                "preview_token": token,
            },
        )

        assert preview_status == 200
        assert len(candidates) == 2
        assert no_selection_status == 409
        assert no_selection["error"] == "forget_selection_required"
        assert confirm_status == 200
        assert confirmed["result"]["forgotten_memory_ids"] == [selected_id]
        assert len(confirmed["result"]["requested_memory_ids"]) == 1
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


def test_acceptance_report_returns_v2_report_for_valid_admin_results(tmp_path):
    service, base = start_dashboard(tmp_path)
    try:
        status, body = request_json(
            base + "/api/acceptance-report",
            "admin-secret",
            method="POST",
            payload={"results": complete_acceptance_results()},
        )

        assert status == 200
        assert body["ok"] is True
        assert body["result"]["schema"] == "memguardos-acceptance-report-v2"
        assert body["result"]["summary"]["ready_for_delivery"] is True
    finally:
        service.close()


@pytest.mark.parametrize(
    "results",
    [
        pytest.param({}, id="results-not-list"),
        pytest.param([None], id="element-not-object"),
        pytest.param([{"id": "UNKNOWN_ITEM", "status": "passed"}], id="unknown-id"),
        pytest.param(
            [
                {"id": ACCEPTANCE_ITEMS[0].id, "status": "passed", "ok": True, "details": {}},
                {"id": ACCEPTANCE_ITEMS[0].id, "status": "passed", "ok": True, "details": {}},
            ],
            id="duplicate-id",
        ),
        pytest.param(
            [{"id": ACCEPTANCE_ITEMS[0].id, "status": "unexpected"}],
            id="invalid-status",
        ),
        pytest.param(
            [{"id": ACCEPTANCE_ITEMS[0].id, "status": "passed", "ok": 1, "details": {}}],
            id="invalid-ok-type",
        ),
        pytest.param(
            [{"id": ACCEPTANCE_ITEMS[0].id, "status": "passed", "ok": False, "details": {}}],
            id="contradictory-ok",
        ),
        pytest.param(
            [{"id": ACCEPTANCE_ITEMS[0].id, "status": "passed", "ok": True, "details": []}],
            id="invalid-details",
        ),
        pytest.param(
            [{"id": ACCEPTANCE_ITEMS[0].id, "status": "passed", "details": {}}],
            id="missing-ok",
        ),
        pytest.param(
            [{"id": ACCEPTANCE_ITEMS[0].id, "status": "passed", "ok": True}],
            id="missing-details",
        ),
    ],
)
def test_acceptance_report_returns_structured_400_for_invalid_results(tmp_path, results):
    service, base = start_dashboard(tmp_path)
    try:
        status, body = request_json(
            base + "/api/acceptance-report",
            "admin-secret",
            method="POST",
            payload={"results": results},
        )

        assert status == 400
        assert body["ok"] is False
        assert body["error"] == "invalid_acceptance_results"
        assert isinstance(body["message"], str)
        assert body["message"]
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
