from __future__ import annotations

from http.client import RemoteDisconnected
from pathlib import Path
from threading import Thread
from urllib.error import HTTPError
from urllib.request import Request, urlopen
import json
import os
import re
import time

import pytest

import memguard.web.dashboard as dashboard_module
from memguard.cli.memctl import main as memctl_main
from memguard.service import MemGuardService
from memguard.web.dashboard import run_dashboard


def test_dashboard_memory_workspace_prioritizes_identity_and_explanation():
    page = dashboard_module._dashboard_html()

    assert "memory-workspace" in page
    assert "memory-result-row" in page
    assert "shortMemoryId" in page
    assert "matchReasonLabel" in page
    assert "更多相关结果" in page
    assert "复制编号" in page
    assert "搜索与筛选" in page


def test_dashboard_overview_is_task_first_instead_of_health_card_wall():
    page = dashboard_module._dashboard_html()

    assert "overview-status-banner" in page
    assert "查看记忆" in page
    assert "搜索记忆" in page
    assert "精准遗忘" in page
    assert "overview-primary-actions" in page


def test_dashboard_separates_daily_actions_and_humanizes_agent_records():
    page = dashboard_module._dashboard_html()

    ordinary_start = page.index('<div class="nav-group">普通用户</div>')
    admin_start = page.index('<div class="nav-group" data-min-role="admin">管理员视图</div>')
    ordinary_navigation = page[ordinary_start:admin_start]

    assert "系统维护" not in ordinary_navigation
    assert "我的偏好" in ordinary_navigation
    assert "内容保护" in ordinary_navigation
    assert "最近动态" in page
    assert "<strong>需要关注</strong>" not in page
    assert "agentWorkspace" not in page
    assert "formatAgentEventBody" in page
    assert "执行命令：" in page
    assert "pretty(item.tool_args)" not in page


def test_evidence_page_uses_lightweight_status_and_groups_repeated_events():
    page = dashboard_module._dashboard_html()
    evidence_loader = page[page.index("async function loadEvidenceHub") : page.index("function auditEventLabel")]

    assert "getJson('/api/status')" in evidence_loader
    assert "getJson('/api/diagnostics')" not in evidence_loader
    assert "summarizeAuditEvents" in page
    assert "最近同类事件" in page


def _request_json(
    url: str,
    *,
    token: str = "",
    method: str = "GET",
    body: bytes | None = None,
    content_type: str | None = None,
) -> tuple[int, dict[str, object]]:
    headers: dict[str, str] = {}
    if token:
        headers["Authorization"] = "Bearer " + token
    if content_type is not None:
        headers["Content-Type"] = content_type
    request = Request(url, headers=headers, data=body, method=method)
    try:
        with urlopen(request, timeout=5) as response:
            return response.status, json.loads(response.read().decode("utf-8"))
    except HTTPError as exc:
        return exc.code, json.loads(exc.read().decode("utf-8"))
    except RemoteDisconnected as exc:
        raise AssertionError("dashboard disconnected without a JSON response") from exc


def _wait_for_state(path: Path, timeout: float = 5.0) -> dict[str, object]:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except (FileNotFoundError, json.JSONDecodeError):
            time.sleep(0.02)
            continue
        if payload.get("port"):
            return payload
        time.sleep(0.02)
    raise AssertionError("dashboard state was not written")


def _start_runtime_dashboard(
    tmp_path: Path,
    *,
    token: str = "desktop-secret",
    idle_timeout: float = 5.0,
) -> tuple[MemGuardService, Thread, Path, Path, dict[str, object]]:
    service = MemGuardService(
        store_path=tmp_path / "memory.segment",
        db_path=tmp_path / "memory.sqlite3",
    )
    state_file = tmp_path / "dashboard.json"
    lock_file = tmp_path / "dashboard.lock"
    thread = Thread(
        target=run_dashboard,
        kwargs={
            "service": service,
            "host": "127.0.0.1",
            "port": 0,
            "auth_token": token,
            "state_file": state_file,
            "lock_file": lock_file,
            "idle_timeout": idle_timeout,
        },
        daemon=True,
    )
    thread.start()
    return service, thread, state_file, lock_file, _wait_for_state(state_file)


def test_readyz_requires_bearer_and_never_returns_the_token(tmp_path):
    service, thread, _state_file, _lock_file, state = _start_runtime_dashboard(tmp_path)
    base = "http://127.0.0.1:%d" % state["port"]
    try:
        missing_status, missing = _request_json(base + "/readyz")
        ready_status, ready = _request_json(base + "/readyz", token="desktop-secret")

        assert missing_status == 401
        assert missing == {"ok": False, "error": "unauthorized"}
        assert ready_status == 200
        assert ready == {
            "ok": True,
            "status": "ready",
            "namespace": "default",
        }
        assert "token" not in json.dumps(ready).lower()
    finally:
        service.close()

    assert thread.is_alive()


def test_post_rejects_unauthenticated_and_wrong_content_type_without_writing(tmp_path):
    service, _thread, _state_file, _lock_file, state = _start_runtime_dashboard(tmp_path)
    base = "http://127.0.0.1:%d" % state["port"]
    try:
        unauth_status, unauth = _request_json(
            base + "/api/ingest",
            method="POST",
            body=b'{"text":"must not be stored"}',
            content_type="text/plain",
        )
        wrong_type_status, wrong_type = _request_json(
            base + "/api/ingest",
            token="desktop-secret",
            method="POST",
            body=b'{"text":"must not be stored"}',
            content_type="text/plain; charset=utf-8",
        )
        status_code, status = _request_json(base + "/api/status", token="desktop-secret")

        assert unauth_status == 401
        assert unauth["error"] == "unauthorized"
        assert wrong_type_status == 415
        assert wrong_type == {
            "ok": False,
            "error": "unsupported_media_type",
            "expected": "application/json",
        }
        assert status_code == 200
        assert status["result"]["live_record_count"] == 0
    finally:
        service.close()


def test_port_zero_writes_atomic_runtime_state_without_credentials(tmp_path):
    service, _thread, state_file, lock_file, state = _start_runtime_dashboard(tmp_path)
    try:
        assert state["schema"] == "memguardos-dashboard-runtime-v1"
        assert state["namespace"] == "default"
        assert 0 < int(state["port"]) <= 65535
        assert state["pid"] == os.getpid()
        assert lock_file.exists()
        raw = state_file.read_text(encoding="utf-8").lower()
        assert "token" not in raw
        assert "secret" not in raw
        assert not list(tmp_path.glob("*.tmp"))
    finally:
        service.close()


def test_duplicate_runtime_launch_reuses_existing_instance(tmp_path):
    service, first_thread, state_file, lock_file, first_state = _start_runtime_dashboard(tmp_path)
    second_service = MemGuardService(
        store_path=tmp_path / "second.segment",
        db_path=tmp_path / "second.sqlite3",
    )
    try:
        started = time.monotonic()
        result = run_dashboard(
            second_service,
            host="127.0.0.1",
            port=0,
            auth_token="desktop-secret",
            state_file=state_file,
            lock_file=lock_file,
            idle_timeout=5.0,
        )
        elapsed = time.monotonic() - started
        current_state = json.loads(state_file.read_text(encoding="utf-8"))

        assert result == {
            "status": "existing",
            "port": first_state["port"],
            "namespace": "default",
        }
        assert elapsed < 1.0
        assert current_state == first_state
        assert first_thread.is_alive()
    finally:
        second_service.close()
        service.close()


def test_idle_timeout_stops_server_and_cleans_owned_runtime_files(tmp_path):
    service, thread, state_file, lock_file, _state = _start_runtime_dashboard(
        tmp_path,
        idle_timeout=0.25,
    )
    try:
        thread.join(timeout=3)
        assert not thread.is_alive()
        assert not state_file.exists()
        assert not lock_file.exists()
    finally:
        service.close()


def test_state_write_failure_closes_bound_server_and_releases_owned_lock(
    tmp_path,
    monkeypatch,
):
    captured: dict[str, object] = {}

    class FakeServer:
        server_address = ("127.0.0.1", 43123)

        def __init__(self, address, handler_type):
            captured["address"] = address
            captured["handler_type"] = handler_type
            captured["closed"] = False

        def server_close(self):
            captured["closed"] = True

    def fail_state_write(path, payload):
        raise OSError("disk full")

    monkeypatch.setattr(dashboard_module, "_DashboardHTTPServer", FakeServer)
    monkeypatch.setattr(dashboard_module, "_write_runtime_state", fail_state_write)
    service = MemGuardService(
        store_path=tmp_path / "memory.segment",
        db_path=tmp_path / "memory.sqlite3",
    )
    lock_file = tmp_path / "dashboard.lock"
    try:
        with pytest.raises(OSError, match="disk full"):
            run_dashboard(
                service,
                host="127.0.0.1",
                port=0,
                auth_token="desktop-secret",
                state_file=tmp_path / "dashboard.json",
                lock_file=lock_file,
            )
    finally:
        service.close()

    assert captured["closed"] is True
    assert not lock_file.exists()


def test_bind_failure_releases_owned_runtime_lock(tmp_path, monkeypatch):
    class FailingServer:
        def __init__(self, address, handler_type):
            raise OSError("address unavailable")

    monkeypatch.setattr(dashboard_module, "_DashboardHTTPServer", FailingServer)
    service = MemGuardService(
        store_path=tmp_path / "memory.segment",
        db_path=tmp_path / "memory.sqlite3",
    )
    lock_file = tmp_path / "dashboard.lock"
    try:
        with pytest.raises(OSError, match="address unavailable"):
            run_dashboard(
                service,
                host="127.0.0.1",
                port=0,
                auth_token="desktop-secret",
                state_file=tmp_path / "dashboard.json",
                lock_file=lock_file,
            )
    finally:
        service.close()

    assert not lock_file.exists()


def test_memctl_web_uses_a_strong_temporary_token_and_prints_fragment_url_by_default(
    tmp_path,
    monkeypatch,
    capsys,
):
    monkeypatch.setenv("MEMGUARD_HOME", str(tmp_path / "home"))
    monkeypatch.delenv("MEMGUARD_NAMESPACE", raising=False)

    assert memctl_main(["web", "--host", "127.0.0.1", "--port", "0", "--idle-timeout", "0.1"]) == 0

    output = capsys.readouterr().out
    match = re.search(r"http://127\.0\.0\.1:\d+/#token=([A-Za-z0-9_-]+)", output)
    assert match is not None
    assert len(match.group(1)) >= 40
    assert "--auth-token" not in output
    assert "admin" not in output.lower()


def test_memctl_web_reports_invalid_token_file_without_traceback(tmp_path, monkeypatch, capsys):
    monkeypatch.setenv("MEMGUARD_HOME", str(tmp_path / "home"))
    monkeypatch.delenv("MEMGUARD_NAMESPACE", raising=False)

    assert memctl_main(["web", "--auth-token-file", str(tmp_path / "missing.token")]) == 2

    captured = capsys.readouterr()
    assert captured.out == ""
    assert "无法启动本地控制台" in captured.err
    assert "访问凭据文件" in captured.err
    assert "Traceback" not in captured.err
