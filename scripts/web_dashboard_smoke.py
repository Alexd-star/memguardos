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


def _agent_replay_ok(payload: dict[str, object]) -> bool:
    result = payload.get("result")
    result = result if isinstance(result, dict) else {}
    return bool(
        payload.get("ok") is True
        and result.get("execution_mode") == "replay"
        and result.get("truthful_note")
    )


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
        evidence_exported = _http_get_json(base + "/api/export-evidence", auth_token=auth_token)
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
        feedback_home = _http_get_json(base + "/api/feedback-home?limit=8", auth_token=auth_token)
        notifications = _http_get_json(base + "/api/notifications?limit=20", auth_token=auth_token)
        agent_run = _http_post(
            base + "/api/agent-run",
            {"task": "查找 MemGuardService 的定义", "mode": "replay"},
            auth_token=auth_token,
        )
        replay_memories = (agent_run.get("result") or {}).get("memory_changes") or []
        replay_memory_id = replay_memories[0].get("memory_id", "") if replay_memories else ""
        memory_detail = _http_get_json(
            base + "/api/memory-detail?memory_id=" + replay_memory_id,
            auth_token=auth_token,
        ) if replay_memory_id else {"ok": False}
        preference_proof = _http_get_json(
            base + "/api/preference-proof?memory_id=" + replay_memory_id,
            auth_token=auth_token,
        ) if replay_memory_id else {"ok": False}
        forget_memory = _http_post(
            base + "/api/forget-memory",
            {"memory_ids": [replay_memory_id], "reason": "dashboard smoke feedback contract", "compact": True},
            auth_token=auth_token,
        ) if replay_memory_id else {"ok": False}
        invalid_json_status, invalid_json = _http_post_raw(base + "/api/ingest", b"{", "application/json", auth_token=auth_token)
        large_status, large_body = _http_post_raw(base + "/api/ingest", b"x" * 262145, "application/json", auth_token=auth_token)
        result = {
            "page_ok": page_status == 200 and "MemGuardOS 记忆安全中心" in page,
            "diagnostics_tab_ok": "系统维护" in page and "管理员视图" in page,
            "diagnostics_export_button_ok": "导出系统报告" in page,
            "enterprise_overview_ok": "overview-status-banner" in page
            and "最近记忆" in page
            and "最近动态" in page,
            "delivery_readiness_ok": "overview-primary-actions" in page
            and "查看记忆" in page
            and "搜索记忆" in page
            and "精准遗忘" in page,
            "product_workbench_ok": "memory-workspace" in page
            and "memory-result-row" in page
            and "更多相关结果" in page,
            "consolidated_navigation_ok": "记忆中心" in page
            and "我的偏好" in page
            and "内容保护" in page
            and "审计与证据" in page,
            "sensitive_protection_ok": "敏感文本测试" in page and "最近敏感阻断" in page,
            "memory_value_visual_ok": "agentMetrics" in page
            and "交互记录" in page
            and "三阶段上下文对比" in page,
            "forget_confirmation_copy_ok": "遗忘二次确认" in page and "影响范围预览" in page,
            "competition_copy_absent": "benchmark 用例/种子" not in page and "能力证据总览" not in page and "演示" not in page,
            "acceptance_review_ok": "复核中心" in page
            and "acceptanceList" in page
            and acceptance_plan.get("ok") is True
            and acceptance_plan.get("result", {}).get("summary", {}).get("manual", 0) >= 1
            and acceptance_run.get("ok") is True
            and acceptance_run.get("result", {}).get("status") == "passed",
            "operation_feedback_ok": "operationStatus" in page
            and "AbortController" in page
            and "跳转处理" in page
            and "处理建议" in page
            and "最后刷新" not in page
            and "toLocaleTimeString" not in page,
            "traffic_light_status_ok": "overview-status-banner.warn" in page
            and "overview-status-banner.bad" in page,
            "rag_visual_ok": "renderContextVisual" in page and "selectionReasonLabel" in page,
            "forget_candidate_visual_ok": "候选编号、匹配依据和作用范围" in page,
            "agent_workspace_ok": "Agent 工作台" in page and "三阶段上下文对比" in page,
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
            "evidence_export_ok": evidence_exported.get("schema") == "memguardos-local-evidence-v1"
            and evidence_exported.get("privacy", {}).get("contains_memory_plaintext") is False,
            "agent_overview_ok": agent_overview.get("ok") is True
            and agent_overview.get("result", {}).get("summary", {}).get("event_count") == 1,
            "agent_forget_preview_ok": agent_forget.get("ok") is True
            and agent_forget.get("result", {}).get("affected", {}).get("event_ids") == ["evt-web-smoke-agent"],
            "feedback_home_ok": feedback_home.get("ok") is True
            and feedback_home.get("result", {}).get("schema") == "memguardos-feedback-home-v1",
            "notifications_ok": notifications.get("ok") is True and isinstance(notifications.get("result"), list),
            "agent_run_replay_ok": _agent_replay_ok(agent_run),
            "memory_detail_ok": memory_detail.get("ok") is True and memory_detail.get("result", {}).get("found") is True,
            "preference_proof_ok": preference_proof.get("ok") is True and "proof" in preference_proof.get("result", {}),
            "forget_memory_feedback_ok": forget_memory.get("ok") is True
            and forget_memory.get("result", {}).get("verification_report", {}).get("schema") == "memguardos-forget-verification-report-v1",
            "agent_run_memory_count": len(replay_memories),
            "memory_detail_reason": memory_detail.get("result", {}).get("reason", ""),
            "preference_proof_reason": preference_proof.get("result", {}).get("reason", ""),
            "forget_memory_reason": forget_memory.get("error", ""),
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
            and result["consolidated_navigation_ok"]
            and result["sensitive_protection_ok"]
            and result["memory_value_visual_ok"]
            and result["forget_confirmation_copy_ok"]
            and result["competition_copy_absent"]
            and result["acceptance_review_ok"]
            and result["operation_feedback_ok"]
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
            and result["evidence_export_ok"]
            and result["agent_overview_ok"]
            and result["agent_forget_preview_ok"]
            and result["feedback_home_ok"]
            and result["notifications_ok"]
            and result["agent_run_replay_ok"]
            and result["memory_detail_ok"]
            and result["preference_proof_ok"]
            and result["forget_memory_feedback_ok"]
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
    # Smoke requests are constructed from the script's loopback-only ephemeral server.
    with urlopen(request, timeout=3) as response:  # nosec B310
        return response.read().decode("utf-8")


def _http_get_json(url: str, auth_token: str = "") -> dict:
    return json.loads(_http_get(url, auth_token=auth_token))


def _http_get_json_status(url: str, auth_token: str = "") -> tuple[int, dict]:
    request = Request(url, headers=_auth_headers(auth_token), method="GET")
    try:
        with urlopen(request, timeout=3) as response:  # nosec B310
            return int(response.status), json.loads(response.read().decode("utf-8"))
    except HTTPError as exc:
        return int(exc.code), json.loads(exc.read().decode("utf-8"))


def _http_get_json_response_status(url: str, auth_token: str = "") -> tuple[int, dict, dict]:
    request = Request(url, headers=_auth_headers(auth_token), method="GET")
    try:
        with urlopen(request, timeout=3) as response:  # nosec B310
            headers = {key.lower(): value for key, value in response.headers.items()}
            return int(response.status), headers, json.loads(response.read().decode("utf-8"))
    except HTTPError as exc:
        headers = {key.lower(): value for key, value in exc.headers.items()}
        return int(exc.code), headers, json.loads(exc.read().decode("utf-8"))


def _http_get_text_status(url: str, auth_token: str = "") -> tuple[int, dict, str]:
    request = Request(url, headers=_auth_headers(auth_token), method="GET")
    with urlopen(request, timeout=3) as response:  # nosec B310
        headers = {key.lower(): value for key, value in response.headers.items()}
        return int(response.status), headers, response.read().decode("utf-8")


def _http_post(url: str, payload: dict, auth_token: str = "") -> dict:
    request = Request(
        url,
        data=json.dumps(payload).encode("utf-8"),
        headers=_auth_headers(auth_token, {"Content-Type": "application/json"}),
        method="POST",
    )
    with urlopen(request, timeout=3) as response:  # nosec B310
        return json.loads(response.read().decode("utf-8"))


def _http_post_raw(url: str, payload: bytes, content_type: str, auth_token: str = "") -> tuple[int, dict]:
    request = Request(
        url,
        data=payload,
        headers=_auth_headers(auth_token, {"Content-Type": content_type}),
        method="POST",
    )
    try:
        with urlopen(request, timeout=3) as response:  # nosec B310
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
