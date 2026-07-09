from __future__ import annotations

from pathlib import Path
from tempfile import TemporaryDirectory
from threading import Thread
from urllib.error import HTTPError
from urllib.request import Request, urlopen
import json
import socket
import sys
import time

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from memguard.service import MemGuardService  # noqa: E402
from memguard.integrations.events import AgentInteractionEvent  # noqa: E402
from memguard.web.dashboard import run_dashboard  # noqa: E402


def main() -> int:
    with TemporaryDirectory() as tmp:
        root = Path(tmp)
        service = MemGuardService(store_path=root / "memory.segment", db_path=root / "memguard.sqlite3")
        service.ingest_interaction(
            AgentInteractionEvent(
                agent_name="openhands",
                agent_version="1.28.1",
                session_id="web-smoke-session",
                turn_id="turn-1",
                sequence=1,
                event_type="user_message",
                role="user",
                content="以后搜索代码优先使用 rg。",
                event_id="evt-web-smoke-agent",
            )
        )
        port = _free_port()
        auth_token = "local-secret"
        thread = Thread(target=run_dashboard, args=(service, "127.0.0.1", port, auth_token), daemon=True)
        thread.start()
        base = "http://127.0.0.1:%s" % port
        _wait_until_ready(base, auth_token=auth_token)
        page_status, page_headers, page = _http_get_text_status(base + "/")
        unauthorized_status, unauthorized_body = _http_get_json_status(base + "/api/diagnostics")
        json_status, json_headers, _json_body = _http_get_json_response_status(base + "/api/status", auth_token=auth_token)
        ingest = _http_post(base + "/api/ingest", {"text": "以后回答 openKylin 服务问题时先给命令。", "source_kind": "dashboard"}, auth_token=auth_token)
        search = _http_get_json(base + "/api/search?q=openKylin%20%E6%9C%8D%E5%8A%A1", auth_token=auth_token)
        explain = _http_get_json(base + "/api/explain-extraction?text=%E8%AF%B7%E4%BB%A5%E5%90%8E%E7%94%A8%E4%B8%AD%E6%96%87%E7%AE%80%E6%B4%81%E5%9B%9E%E7%AD%94", auth_token=auth_token)
        status = _http_get_json(base + "/api/status", auth_token=auth_token)
        audit = _http_get_json(base + "/api/verify-audit", auth_token=auth_token)
        retrieval = _http_get_json(base + "/api/retrieval-status", auth_token=auth_token)
        policy = _http_get_json(base + "/api/memory-policy", auth_token=auth_token)
        diagnostics = _http_get_json(base + "/api/diagnostics", auth_token=auth_token)
        acceptance_plan = _http_get_json(base + "/api/acceptance-plan", auth_token=auth_token)
        acceptance_run = _http_post(
            base + "/api/acceptance-run",
            {"item_id": "A21_VERIFY_AUDIT"},
            auth_token=auth_token,
        )
        exported = _http_get_json(base + "/api/export-diagnostics", auth_token=auth_token)
        agent_overview = _http_get_json(
            base + "/api/agent-overview?session_id=web-smoke-session&task=%E6%9F%A5%E6%89%BE%E4%BB%A3%E7%A0%81",
            auth_token=auth_token,
        )
        agent_forget = _http_post(
            base + "/api/agent-forget",
            {
                "filters": {"agent_name": "openhands", "preference_topic": "code_search_tool"},
                "confirm": False,
            },
            auth_token=auth_token,
        )
        invalid_json_status, invalid_json = _http_post_raw(base + "/api/ingest", b"{", "application/json", auth_token=auth_token)
        large_status, large_body = _http_post_raw(base + "/api/ingest", b"x" * 262145, "application/json", auth_token=auth_token)
        result = {
            "page_ok": page_status == 200 and "MemGuardOS 记忆安全中心" in page,
            "diagnostics_tab_ok": "系统维护" in page and "管理员视图" in page,
            "diagnostics_export_button_ok": "导出系统报告" in page,
            "enterprise_overview_ok": "能力概览" in page and "常用工作流" in page,
            "delivery_readiness_ok": "系统健康度" in page and "推荐操作路径" in page and "风险处置建议" in page,
            "product_workbench_ok": "治理闭环" in page and "今日工作流" in page and "系统报告内容清单" in page,
            "memory_value_visual_ok": "记忆价值指标" in page and "重复尝试" in page and "工具选择命中" in page,
            "forget_confirmation_copy_ok": "遗忘二次确认" in page and "影响范围预览" in page,
            "competition_copy_absent": "benchmark 用例/种子" not in page and "能力证据总览" not in page and "演示" not in page,
            "acceptance_review_ok": "复核中心" in page
            and "acceptanceList" in page
            and acceptance_plan.get("ok") is True
            and acceptance_plan.get("result", {}).get("summary", {}).get("manual", 0) >= 1
            and acceptance_run.get("ok") is True
            and acceptance_run.get("result", {}).get("status") == "passed",
            "traffic_light_status_ok": "红黄绿状态" in page,
            "rag_visual_ok": "候选选择轨迹" in page,
            "forget_candidate_visual_ok": "候选会以卡片形式列出" in page,
            "agent_workspace_ok": "Agent 交互治理" in page and "三阶段上下文对比" in page,
            "role_split_ok": "普通用户" in page and "个人工作台" in page and "系统维护" in page and "详细记录" in page,
            "token_session_storage_ok": "sessionStorage" in page and "localStorage" not in page,
            "html_security_headers_ok": _security_headers_ok(page_headers, expect_csp=True),
            "json_security_headers_ok": json_status == 200 and _security_headers_ok(json_headers, expect_csp=False),
            "ingest_ok": bool(ingest.get("ok") and ingest.get("result", {}).get("stored")),
            "search_hits": len(search.get("result", [])),
            "extraction_type": explain.get("result", {}).get("extraction", {}).get("memory_type"),
            "live_record_count": status.get("result", {}).get("live_record_count"),
            "audit_ok": audit.get("ok") is True,
            "retrieval_ok": retrieval.get("ok") is True,
            "policy_ok": policy.get("ok") is True,
            "diagnostics_ok": diagnostics.get("ok") is True and "audit_chain" in diagnostics.get("result", {}),
            "export_ok": "doctor" in exported and "audit_chain" in exported,
            "agent_overview_ok": agent_overview.get("ok") is True
            and agent_overview.get("result", {}).get("summary", {}).get("event_count") == 1,
            "agent_forget_preview_ok": agent_forget.get("ok") is True
            and agent_forget.get("result", {}).get("affected", {}).get("event_ids") == ["evt-web-smoke-agent"],
            "auth_required_for_diagnostics": unauthorized_status == 401 and unauthorized_body.get("error") == "unauthorized",
            "invalid_json_rejected": invalid_json_status == 400 and invalid_json.get("error") == "invalid_json",
            "large_payload_rejected": large_status == 413 and large_body.get("error") == "payload_too_large",
            "url": base,
        }
        print(json.dumps(result, ensure_ascii=False, indent=2))
        service.close()
        ok = (
            result["page_ok"]
            and result["diagnostics_tab_ok"]
            and result["diagnostics_export_button_ok"]
            and result["enterprise_overview_ok"]
            and result["delivery_readiness_ok"]
            and result["product_workbench_ok"]
            and result["memory_value_visual_ok"]
            and result["forget_confirmation_copy_ok"]
            and result["competition_copy_absent"]
            and result["acceptance_review_ok"]
            and result["traffic_light_status_ok"]
            and result["rag_visual_ok"]
            and result["forget_candidate_visual_ok"]
            and result["agent_workspace_ok"]
            and result["role_split_ok"]
            and result["token_session_storage_ok"]
            and result["html_security_headers_ok"]
            and result["json_security_headers_ok"]
            and result["ingest_ok"]
            and result["search_hits"] >= 1
            and result["audit_ok"]
            and result["retrieval_ok"]
            and result["policy_ok"]
            and result["diagnostics_ok"]
            and result["export_ok"]
            and result["agent_overview_ok"]
            and result["agent_forget_preview_ok"]
            and result["auth_required_for_diagnostics"]
            and result["invalid_json_rejected"]
            and result["large_payload_rejected"]
        )
        return 0 if ok else 2


def _free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


def _wait_until_ready(base: str, auth_token: str = "") -> None:
    deadline = time.time() + 5
    while time.time() < deadline:
        try:
            _http_get(base + "/api/status", auth_token=auth_token)
            return
        except OSError:
            time.sleep(0.05)
    raise RuntimeError("dashboard did not start")


def _http_get(url: str, auth_token: str = "") -> str:
    request = Request(url, headers=_auth_headers(auth_token), method="GET")
    with urlopen(request, timeout=3) as response:
        return response.read().decode("utf-8")


def _http_get_json(url: str, auth_token: str = "") -> dict:
    return json.loads(_http_get(url, auth_token=auth_token))


def _http_get_json_status(url: str, auth_token: str = "") -> tuple[int, dict]:
    request = Request(url, headers=_auth_headers(auth_token), method="GET")
    try:
        with urlopen(request, timeout=3) as response:
            return int(response.status), json.loads(response.read().decode("utf-8"))
    except HTTPError as exc:
        return int(exc.code), json.loads(exc.read().decode("utf-8"))


def _http_get_json_response_status(url: str, auth_token: str = "") -> tuple[int, dict, dict]:
    request = Request(url, headers=_auth_headers(auth_token), method="GET")
    try:
        with urlopen(request, timeout=3) as response:
            headers = {key.lower(): value for key, value in response.headers.items()}
            return int(response.status), headers, json.loads(response.read().decode("utf-8"))
    except HTTPError as exc:
        headers = {key.lower(): value for key, value in exc.headers.items()}
        return int(exc.code), headers, json.loads(exc.read().decode("utf-8"))


def _http_get_text_status(url: str, auth_token: str = "") -> tuple[int, dict, str]:
    request = Request(url, headers=_auth_headers(auth_token), method="GET")
    with urlopen(request, timeout=3) as response:
        headers = {key.lower(): value for key, value in response.headers.items()}
        return int(response.status), headers, response.read().decode("utf-8")


def _http_post(url: str, payload: dict, auth_token: str = "") -> dict:
    request = Request(
        url,
        data=json.dumps(payload).encode("utf-8"),
        headers=_auth_headers(auth_token, {"Content-Type": "application/json"}),
        method="POST",
    )
    with urlopen(request, timeout=3) as response:
        return json.loads(response.read().decode("utf-8"))


def _http_post_raw(url: str, payload: bytes, content_type: str, auth_token: str = "") -> tuple[int, dict]:
    request = Request(
        url,
        data=payload,
        headers=_auth_headers(auth_token, {"Content-Type": content_type}),
        method="POST",
    )
    try:
        with urlopen(request, timeout=3) as response:
            return int(response.status), json.loads(response.read().decode("utf-8"))
    except HTTPError as exc:
        return int(exc.code), json.loads(exc.read().decode("utf-8"))


def _auth_headers(auth_token: str, headers=None) -> dict:
    result = dict(headers or {})
    if auth_token:
        result["Authorization"] = "Bearer " + auth_token
    return result


def _security_headers_ok(headers: dict, expect_csp: bool) -> bool:
    required = {
        "cache-control": "no-store",
        "x-content-type-options": "nosniff",
        "referrer-policy": "no-referrer",
        "x-frame-options": "DENY",
        "cross-origin-opener-policy": "same-origin",
    }
    for key, value in required.items():
        if headers.get(key) != value:
            return False
    if "camera=()" not in headers.get("permissions-policy", ""):
        return False
    if expect_csp and "default-src 'self'" not in headers.get("content-security-policy", ""):
        return False
    return True


if __name__ == "__main__":
    raise SystemExit(main())
