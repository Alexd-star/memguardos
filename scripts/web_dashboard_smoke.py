from __future__ import annotations

from pathlib import Path
from tempfile import TemporaryDirectory
from threading import Thread
from urllib.error import HTTPError
from urllib.request import Request, urlopen
import json
import socket
import time

from memguard.service import MemGuardService
from memguard.web.dashboard import run_dashboard


def main() -> int:
    with TemporaryDirectory() as tmp:
        root = Path(tmp)
        service = MemGuardService(store_path=root / "memory.segment", db_path=root / "memguard.sqlite3")
        port = _free_port()
        thread = Thread(target=run_dashboard, args=(service, "127.0.0.1", port, ""), daemon=True)
        thread.start()
        base = "http://127.0.0.1:%s" % port
        _wait_until_ready(base)
        page = _http_get(base + "/")
        ingest = _http_post(base + "/api/ingest", {"text": "以后回答 openKylin 服务问题时先给命令。", "source_kind": "dashboard"})
        search = _http_get_json(base + "/api/search?q=openKylin%20%E6%9C%8D%E5%8A%A1")
        explain = _http_get_json(base + "/api/explain-extraction?text=%E8%AF%B7%E4%BB%A5%E5%90%8E%E7%94%A8%E4%B8%AD%E6%96%87%E7%AE%80%E6%B4%81%E5%9B%9E%E7%AD%94")
        status = _http_get_json(base + "/api/status")
        audit = _http_get_json(base + "/api/verify-audit")
        retrieval = _http_get_json(base + "/api/retrieval-status")
        policy = _http_get_json(base + "/api/memory-policy")
        diagnostics = _http_get_json(base + "/api/diagnostics")
        exported = _http_get_json(base + "/api/export-diagnostics")
        invalid_json_status, invalid_json = _http_post_raw(base + "/api/ingest", b"{", "application/json")
        large_status, large_body = _http_post_raw(base + "/api/ingest", b"x" * 262145, "application/json")
        result = {
            "page_ok": "MemGuardOS 控制台" in page,
            "diagnostics_tab_ok": "系统诊断" in page,
            "diagnostics_export_button_ok": "导出诊断包" in page,
            "capability_scoreboard_ok": "能力证据总览" in page and "一键演示流程" in page,
            "traffic_light_status_ok": "红黄绿状态" in page,
            "rag_visual_ok": "候选选择轨迹" in page,
            "forget_candidate_visual_ok": "候选会以卡片形式列出" in page,
            "ingest_ok": bool(ingest.get("ok") and ingest.get("result", {}).get("stored")),
            "search_hits": len(search.get("result", [])),
            "extraction_type": explain.get("result", {}).get("extraction", {}).get("memory_type"),
            "live_record_count": status.get("result", {}).get("live_record_count"),
            "audit_ok": audit.get("ok") is True,
            "retrieval_ok": retrieval.get("ok") is True,
            "policy_ok": policy.get("ok") is True,
            "diagnostics_ok": diagnostics.get("ok") is True and "audit_chain" in diagnostics.get("result", {}),
            "export_ok": "doctor" in exported and "audit_chain" in exported,
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
            and result["capability_scoreboard_ok"]
            and result["traffic_light_status_ok"]
            and result["rag_visual_ok"]
            and result["forget_candidate_visual_ok"]
            and result["ingest_ok"]
            and result["search_hits"] >= 1
            and result["audit_ok"]
            and result["retrieval_ok"]
            and result["policy_ok"]
            and result["diagnostics_ok"]
            and result["export_ok"]
            and result["invalid_json_rejected"]
            and result["large_payload_rejected"]
        )
        return 0 if ok else 2


def _free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


def _wait_until_ready(base: str) -> None:
    deadline = time.time() + 5
    while time.time() < deadline:
        try:
            _http_get(base + "/api/status")
            return
        except OSError:
            time.sleep(0.05)
    raise RuntimeError("dashboard did not start")


def _http_get(url: str) -> str:
    with urlopen(url, timeout=3) as response:
        return response.read().decode("utf-8")


def _http_get_json(url: str) -> dict:
    return json.loads(_http_get(url))


def _http_post(url: str, payload: dict) -> dict:
    request = Request(
        url,
        data=json.dumps(payload).encode("utf-8"),
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    with urlopen(request, timeout=3) as response:
        return json.loads(response.read().decode("utf-8"))


def _http_post_raw(url: str, payload: bytes, content_type: str) -> tuple[int, dict]:
    request = Request(
        url,
        data=payload,
        headers={"Content-Type": content_type},
        method="POST",
    )
    try:
        with urlopen(request, timeout=3) as response:
            return int(response.status), json.loads(response.read().decode("utf-8"))
    except HTTPError as exc:
        return int(exc.code), json.loads(exc.read().decode("utf-8"))


if __name__ == "__main__":
    raise SystemExit(main())
