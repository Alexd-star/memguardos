from __future__ import annotations

from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any
from urllib.parse import parse_qs, urlparse
import hmac
import json

from memguard.api.server import handle_request
from memguard.os_guard.cgroup import cgroup_status
from memguard.service import MemGuardService


def run_dashboard(
    service: MemGuardService,
    host: str = "127.0.0.1",
    port: int = 8765,
    auth_token: str = "",
) -> None:
    """Run a dependency-free local dashboard for local operation and inspection."""

    class Handler(_DashboardHandler):
        store_path = service.store.path
        db_path = service.meta.path
        namespace = service.namespace
        dashboard_auth_token = auth_token

    server = ThreadingHTTPServer((host, port), Handler)
    print("MemGuardOS dashboard listening on http://%s:%s" % (host, port), flush=True)
    try:
        server.serve_forever()
    finally:
        server.server_close()


class _DashboardHandler(BaseHTTPRequestHandler):
    store_path: object
    db_path: object
    namespace: str
    dashboard_auth_token: str = ""

    server_version = "MemGuardOSDashboard/0.1"

    def do_GET(self) -> None:
        parsed = urlparse(self.path)
        if parsed.path == "/":
            self._send_html(_dashboard_html())
            return
        if parsed.path.startswith("/api/") and not self._authorized():
            self._send_json({"ok": False, "error": "unauthorized"}, status=401)
            return
        if parsed.path == "/api/status":
            self._send_api({"command": "namespace_status"})
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
        if parsed.path.startswith("/api/") and not self._authorized():
            self._send_json({"ok": False, "error": "unauthorized"}, status=401)
            return
        payload, payload_error = self._read_json()
        if payload_error:
            self._send_json({"ok": False, "error": payload_error}, status=413 if payload_error == "payload_too_large" else 400)
            return
        if parsed.path == "/api/ingest":
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
        if parsed.path == "/api/forget":
            self._send_api(
                {
                    "command": "forget",
                    "request": str(payload.get("request", "")),
                    "confirm": bool(payload.get("confirm", False)),
                    "compact": bool(payload.get("compact", False)),
                }
            )
            return
        if parsed.path == "/api/agent-forget":
            self._send_api(
                {
                    "command": "forget_agent_interactions",
                    "filters": payload.get("filters") if isinstance(payload.get("filters"), dict) else {},
                    "confirm": bool(payload.get("confirm", False)),
                    "compact": bool(payload.get("compact", True)),
                    "external_persistence": bool(payload.get("external_persistence", True)),
                }
            )
            return
        self.send_error(404, "not found")

    def log_message(self, format: str, *args: Any) -> None:
        return

    def _authorized(self) -> bool:
        if not self.dashboard_auth_token:
            return True
        return hmac.compare_digest(_request_token(self.headers.get("Authorization", "")), self.dashboard_auth_token)

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
        token = _request_token(self.headers.get("Authorization", ""))
        service = self._service()
        try:
            response = handle_request(
                service,
                {**request, "token": token},
                auth_token=self.dashboard_auth_token or None,
            )
        finally:
            service.close()
        self._send_json(response, status=200 if response.get("ok") else 400)

    def _send_json(self, payload: dict[str, Any], status: int = 200) -> None:
        raw = json.dumps(payload, ensure_ascii=False, indent=2).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self._send_security_headers()
        self.send_header("Content-Length", str(len(raw)))
        self.end_headers()
        self.wfile.write(raw)

    def _send_json_attachment(self, payload: dict[str, Any], filename: str) -> None:
        raw = json.dumps(payload, ensure_ascii=False, indent=2).encode("utf-8")
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


def _dashboard_html() -> str:
    return """<!doctype html>
<html lang="zh-CN">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>MemGuardOS 控制台</title>
  <style>
    :root {
      color-scheme: light;
      --ink:#17202a; --muted:#667085; --line:#d0d5dd; --accent:#0f766e; --accent-2:#2563eb;
      --warn:#b45309; --bad:#b42318; --ok:#047857; --bg:#f5f7fa; --panel:#ffffff; --soft:#eef6f4;
    }
    * { box-sizing: border-box; }
    body { margin:0; font-family:"Segoe UI","Microsoft YaHei",Arial,sans-serif; color:var(--ink); background:var(--bg); letter-spacing:0; }
    header { display:flex; align-items:center; justify-content:space-between; gap:16px; padding:16px 24px; background:#111827; color:white; border-bottom:4px solid #0f766e; }
    header h1 { margin:0; font-size:19px; font-weight:700; }
    header .subtitle { margin-top:4px; font-size:12px; color:#cbd5e1; }
    header .meta { font-size:12px; color:#d0d5dd; }
    main { display:grid; grid-template-columns:280px 1fr; min-height:calc(100vh - 72px); }
    nav { border-right:1px solid var(--line); background:#fff; padding:18px 14px; }
    nav button { width:100%; min-height:40px; margin:0 0 8px; text-align:left; border:1px solid transparent; background:#fff; color:var(--ink); padding:9px 11px; border-radius:8px; cursor:pointer; font-weight:600; }
    nav button.active, nav button:hover { border-color:#99f6e4; background:#ecfdf5; color:#115e59; }
    section { display:none; padding:20px 24px 28px; }
    section.active { display:block; }
    h2 { font-size:22px; margin:0 0 8px; }
    .lead { color:var(--muted); margin:0 0 16px; max-width:900px; line-height:1.65; }
    .grid { display:grid; grid-template-columns:repeat(3,minmax(0,1fr)); gap:12px; }
    .grid.two { grid-template-columns:repeat(2,minmax(0,1fr)); }
    .grid.three { grid-template-columns:repeat(3,minmax(0,1fr)); }
    .grid.four { grid-template-columns:repeat(4,minmax(0,1fr)); }
    .panel { min-width:0; overflow:hidden; background:var(--panel); border:1px solid var(--line); border-radius:8px; padding:14px; }
    .panel.emphasis { border-color:#99f6e4; background:linear-gradient(180deg,#ffffff,#f0fdfa); }
    .toolbar { display:flex; flex-wrap:wrap; gap:8px; align-items:center; margin:12px 0; }
    label { display:block; font-size:13px; color:var(--muted); margin:10px 0 5px; }
    textarea, input, select { width:100%; border:1px solid var(--line); border-radius:6px; padding:9px 10px; font:14px/1.45 Consolas, "Microsoft YaHei", monospace; background:white; color:var(--ink); }
    textarea { min-height:120px; resize:vertical; }
    .actions { display:flex; flex-wrap:wrap; gap:8px; margin-top:12px; }
    button.primary { border:0; background:var(--accent); color:white; border-radius:6px; padding:9px 12px; cursor:pointer; min-height:36px; }
    button.secondary { border:1px solid var(--line); background:white; color:var(--ink); border-radius:6px; padding:9px 12px; cursor:pointer; min-height:36px; }
    button.danger { border:0; background:#b42318; color:white; border-radius:6px; padding:9px 12px; cursor:pointer; min-height:36px; }
    button:disabled { opacity:.55; cursor:progress; }
    pre { white-space:pre-wrap; word-break:break-word; background:#0b1220; color:#d1e9ff; border-radius:8px; padding:12px; min-height:200px; overflow:auto; }
    .metric { font-size:26px; font-weight:750; color:var(--accent); margin-top:4px; }
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
    .progress { height:8px; background:#e5e7eb; border-radius:999px; overflow:hidden; margin-top:8px; }
    .progress span { display:block; height:100%; background:var(--accent); width:0%; }
    .trace { display:grid; grid-template-columns:repeat(3,minmax(0,1fr)); gap:10px; margin-top:12px; }
    .trace .step { border:1px solid var(--line); background:#fff; border-radius:8px; padding:10px; }
    .step b { display:block; color:#115e59; margin-bottom:4px; }
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
    #storageRoot { word-break:break-all; overflow-wrap:anywhere; }
    @media (max-width: 900px) {
      main { grid-template-columns: 1fr; }
      nav { border-right:0; border-bottom:1px solid var(--line); display:grid; grid-template-columns: repeat(2, minmax(0,1fr)); gap:8px; }
      nav button { margin:0; }
      .grid, .grid.two, .grid.three, .grid.four, .trace, .stage-columns { grid-template-columns: 1fr; }
    }
  </style>
</head>
<body>
  <header>
    <div><h1>MemGuardOS 控制台</h1><div class="subtitle">面向本机 OS Agent 的记忆、安全、审计和诊断工作台</div></div>
    <div class="meta">
      <span id="namespaceMeta">namespace: loading</span>
      <input id="apiToken" type="password" placeholder="API Token" autocomplete="off" spellcheck="false" style="width:150px; min-height:28px; margin-left:10px; padding:4px 7px; font-size:12px;">
    </div>
  </header>
  <main>
    <nav>
      <button class="active" data-tab="overview">总览</button>
      <button data-tab="ingest">写入</button>
      <button data-tab="search">检索</button>
      <button data-tab="context">RAG 上下文</button>
      <button data-tab="agent">Agent 交互</button>
      <button data-tab="forget">精准遗忘</button>
      <button data-tab="audit">审计</button>
      <button data-tab="diagnostics">诊断</button>
    </nav>
    <div>
      <div id="errorBox" class="error"></div>
      <section id="overview" class="active">
        <h2>运行状态</h2>
        <p class="lead">总览页聚合本机命名空间、记忆数量、审计链、检索后端、端侧 SDK 和 OS 运行状态，用于管理员快速判断系统是否处于可交付、可运行、可审计状态。</p>
        <div class="statusline"><span id="healthBadge" class="badge">连接中</span><span id="lastUpdated">等待刷新</span></div>
        <div class="grid four">
          <div class="panel emphasis"><div class="muted">实时记忆</div><div class="metric" id="liveCount">-</div></div>
          <div class="panel"><div class="muted">审计事件</div><div class="metric" id="auditCount">-</div></div>
          <div class="panel"><div class="muted">命名空间</div><div class="metric" id="namespaceCount">-</div></div>
          <div class="panel"><div class="muted">存储目录</div><div id="storageRoot" class="muted">-</div></div>
        </div>
        <div class="grid four" style="margin-top:12px">
          <div class="panel health"><strong>审计链</strong><span id="auditBadge" class="badge">待检查</span></div>
          <div class="panel health"><strong>检索后端</strong><span id="retrievalBadge" class="badge">待检查</span></div>
          <div class="panel health"><strong>Kylin SDK</strong><span id="sdkBadge" class="badge">待检查</span></div>
          <div class="panel health"><strong>OS 资源</strong><span id="osBadge" class="badge">待检查</span></div>
        </div>
        <div class="grid three" style="margin-top:12px">
          <div class="panel emphasis">
            <div class="item-title">交付就绪度</div>
            <p class="muted">面向本机管理员的交付判断：审计链可校验、检索后端可用、OS 资源可见、诊断包可导出时，系统可以进入演示或验收流程。</p>
            <div class="progress"><span id="readinessProgress"></span></div>
            <div id="readinessText" class="muted" style="margin-top:8px">等待状态刷新</div>
          </div>
          <div class="panel">
            <div class="item-title">一键验收路径</div>
            <div class="list">
              <div class="item"><div class="item-title">1. 刷新状态</div><div class="muted">查看审计链、检索后端、SDK 和 OS 资源红黄绿状态。</div></div>
              <div class="item"><div class="item-title">2. 写入并检索</div><div class="muted">写入一条 Agent 偏好，立即检索并构建 RAG 上下文。</div></div>
              <div class="item"><div class="item-title">3. 预览遗忘</div><div class="muted">先 dry-run 查看候选，再确认 compact 和审计记录。</div></div>
            </div>
          </div>
          <div class="panel">
            <div class="item-title">风险处置建议</div>
            <div id="riskAdvice" class="list">
              <div class="item"><div class="muted">刷新后会根据审计链、检索后端、SDK 和 OS 资源状态给出本机处置建议。</div></div>
            </div>
          </div>
        </div>
        <div class="grid two" style="margin-top:12px">
          <div class="panel emphasis">
            <div class="item-title">系统能力概览</div>
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
              <div class="item"><div class="item-title">3. RAG 与精准遗忘</div><div class="muted">展示来源分布、selection trace、脱敏状态，再进行 dry-run 和确认遗忘。</div></div>
            </div>
          </div>
        </div>
        <div class="toolbar"><button class="primary" onclick="refresh(event)">刷新状态</button><button class="secondary" onclick="diagnostics(event)">运行诊断</button><button class="secondary" onclick="exportDiagnostics(event)">导出诊断包</button></div>
        <div class="grid two">
          <div class="panel"><div class="item-title">状态摘要</div><div id="statusCards" class="list"></div></div>
          <div class="panel"><div class="item-title">诊断说明</div><p class="muted">一键导出的诊断包包含 doctor、namespace、retrieval、memory-policy、temporal、Kylin SDK、audit chain 和最近审计事件，可直接作为现场排查附件。</p></div>
        </div>
        <pre id="overviewOut"></pre>
      </section>
      <section id="ingest">
        <h2>写入记忆</h2>
        <div class="panel">
          <label>文本</label><textarea id="ingestText">以后回答 openKylin 服务问题时，先给 systemctl 命令，再说明风险。</textarea>
          <label>来源类型</label><select id="sourceKind"><option>manual</option><option>dashboard</option><option>tool_result</option><option>user_behavior</option><option>os_event</option></select>
          <label>记忆层级</label><select id="tier"><option value="">自动</option><option>working</option><option>session</option><option>long_term</option></select>
          <div class="actions"><button class="primary" onclick="ingest(event)">写入</button><button class="secondary" onclick="explainExtraction(event)">解释抽取</button></div>
        </div>
        <pre id="ingestOut"></pre>
      </section>
      <section id="search">
        <h2>混合检索</h2>
        <p class="lead">检索结果会显示召回来源、分数、敏感等级和记忆层级，方便解释“为什么这条记忆被选中”。</p>
        <div class="panel">
          <label>查询</label><input id="searchQuery" value="openKylin 服务内存限制">
          <div class="actions"><button class="primary" onclick="search(event)">检索</button><button class="secondary" onclick="graphSearch(event)">层次图谱</button></div>
        </div>
        <div id="searchCards" class="list" style="margin-top:12px"></div>
        <pre id="searchOut"></pre>
      </section>
      <section id="context">
        <h2>RAG 上下文</h2>
        <p class="lead">这里展示生成前上下文包的来源分布、token 预算、脱敏状态和候选选择轨迹，用来回答关于“效果、成本和安全边界”的追问。</p>
        <div class="panel">
          <label>问题</label><input id="contextQuery" value="openKylin 服务内存限制">
          <label>token 预算</label><input id="tokenBudget" type="number" value="1200">
          <div class="actions"><button class="primary" onclick="contextBuild(event)">构建</button></div>
        </div>
        <div id="contextVisual" style="margin-top:12px"></div>
        <pre id="contextOut"></pre>
      </section>
      <section id="agent">
        <h2>Agent 交互治理</h2>
        <p class="lead">查看用户与现有 Agent 的真实交互事件、自动形成的偏好依据，以及规划、工具选择、回答生成三个阶段实际获得的记忆。已遗忘事件只保留哈希和删除事实。</p>
        <div class="panel">
          <div class="grid two">
            <div><label>会话 ID（可选）</label><input id="agentSession" placeholder="留空查看最近事件"></div>
            <div><label>上下文预览任务</label><input id="agentTask" value="查找精准遗忘的实现位置"></div>
          </div>
          <div class="actions"><button class="primary" onclick="loadAgentOverview(event)">刷新 Agent 状态</button><button class="secondary" onclick="exportDiagnostics(event)">导出诊断包</button></div>
        </div>
        <div id="agentMetrics" class="grid four" style="margin-top:12px"></div>
        <div class="grid two" style="margin-top:12px">
          <div class="panel"><div class="item-title">交互事件时间线</div><div id="agentTimeline" class="timeline" style="margin-top:12px"></div></div>
          <div class="panel"><div class="item-title">偏好与证据</div><div id="agentPreferences" class="list" style="margin-top:12px"></div></div>
        </div>
        <div class="panel" style="margin-top:12px"><div class="item-title">三阶段上下文对比</div><div id="agentContexts" class="stage-columns" style="margin-top:12px"></div></div>
        <div class="panel" style="margin-top:12px">
          <div class="item-title">按交互主题精准遗忘</div>
          <div class="grid two">
            <div><label>Agent</label><input id="agentForgetName" value="openhands"></div>
            <div><label>偏好主题</label><select id="agentForgetTopic"><option value="response_order">回答顺序</option><option value="code_search_tool">代码搜索工具</option><option value="response_language">回答语言</option><option value="response_verbosity">回答详细度</option><option value="high_risk_confirmation">高风险确认</option></select></div>
          </div>
          <div class="actions"><button class="secondary" onclick="agentForget(event, false)">预览影响</button><button class="danger" onclick="agentForget(event, true)">确认遗忘</button></div>
          <div id="agentForgetImpact" class="list" style="margin-top:12px"></div>
        </div>
        <pre id="agentOut"></pre>
      </section>
      <section id="forget">
        <h2>精准遗忘</h2>
        <p class="lead">先 dry-run 查看候选，再二次确认执行。候选会以卡片形式列出匹配关键词和删除风险，避免把不可逆操作藏在长 JSON 里。</p>
        <div class="panel">
          <label>自然语言请求</label><input id="forgetRequest" value="忘掉 temporary project codename">
          <div class="actions"><button class="secondary" onclick="forget(event, false)">Dry-run</button><button class="danger" onclick="forget(event, true)">确认遗忘并 compact</button></div>
          <p class="warning">确认遗忘会写入 tombstone 并清理可访问索引。</p>
        </div>
        <div id="forgetCards" class="list" style="margin-top:12px"></div>
        <pre id="forgetOut"></pre>
      </section>
      <section id="audit">
        <h2>审计记录</h2>
        <div class="actions"><button class="primary" onclick="audit(event)">刷新</button></div>
        <pre id="auditOut"></pre>
      </section>
      <section id="diagnostics">
        <h2>系统诊断</h2>
        <p class="lead">诊断页用于本机运维和现场排查，聚合审计链、检索后端、记忆策略、端侧 SDK、cgroup 和 doctor 信息，并支持一键导出。</p>
        <div class="panel">
          <div class="actions">
            <button class="primary" onclick="verifyAudit(event)">校验审计链</button>
            <button class="secondary" onclick="retrievalStatus(event)">检索后端</button>
            <button class="secondary" onclick="memoryPolicy(event)">记忆策略</button>
            <button class="secondary" onclick="diagnostics(event)">聚合诊断</button>
            <button class="secondary" onclick="exportDiagnostics(event)">导出诊断包</button>
          </div>
          <p class="muted">诊断输出用于本机管理员定位审计、检索和冷热分层状态。敏感内容会按核心链路的脱敏规则处理。</p>
        </div>
        <pre id="diagnosticsOut"></pre>
      </section>
    </div>
  </main>
  <script>
    const pretty = obj => JSON.stringify(obj, null, 2);
    const esc = value => String(value ?? '').replace(/[&<>"']/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
    document.querySelectorAll('nav button').forEach(btn => btn.addEventListener('click', () => {
      document.querySelectorAll('nav button').forEach(item => item.classList.remove('active'));
      document.querySelectorAll('section').forEach(item => item.classList.remove('active'));
      btn.classList.add('active');
      document.getElementById(btn.dataset.tab).classList.add('active');
    }));
    const tokenInput = document.getElementById('apiToken');
    tokenInput.value = sessionStorage.getItem('memguardos_api_token') || '';
    tokenInput.addEventListener('change', () => sessionStorage.setItem('memguardos_api_token', tokenInput.value));
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
    async function withBusy(event, work) {
      const button = event && event.currentTarget ? event.currentTarget : null;
      const label = button ? button.textContent : '';
      if (button) {
        button.disabled = true;
        button.textContent = '处理中...';
      }
      try {
        return await work();
      } finally {
        if (button) {
          button.disabled = false;
          button.textContent = label;
        }
      }
    }
    async function getJson(url) {
      try {
        const r = await fetch(url, {headers: authHeaders()});
        const data = await r.json();
        if (!r.ok || data.ok === false) showError(data.error ? (data.error + (data.detail ? ': ' + data.detail : '')) : '请求失败');
        else showError('');
        return data;
      } catch (err) {
        showError('无法连接本地控制台服务：' + err);
        return {ok:false, error:String(err)};
      }
    }
    async function postJson(url, data) {
      try {
        const r = await fetch(url, {method:'POST', headers:authHeaders({'Content-Type':'application/json'}), body:JSON.stringify(data)});
        const payload = await r.json();
        if (!r.ok || payload.ok === false) showError(payload.error ? (payload.error + (payload.detail ? ': ' + payload.detail : '')) : '请求失败');
        else showError('');
        return payload;
      } catch (err) {
        showError('无法连接本地控制台服务：' + err);
        return {ok:false, error:String(err)};
      }
    }
    function badge(id, state, text) {
      const el = document.getElementById(id);
      el.className = 'badge ' + state;
      el.textContent = text;
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
      document.getElementById('searchCards').innerHTML = rows.slice(0, 8).map((row, idx) => {
        const sens = row.sensitivity_level || (row.sensitivity && row.sensitivity.level) || 'none';
        return '<div class="item"><div class="item-title">#' + (idx + 1) + ' ' + esc(row.summary || row.memory_id) + '</div>'
          + '<div class="muted">score=' + esc(row.score) + ' · source=' + esc(row.match_source) + ' · tier=' + esc(row.tier) + ' · sensitivity=' + esc(sens) + '</div></div>';
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
        + '<div class="panel"><div class="muted">上下文 tokens</div><div class="metric">' + esc(budget.estimated_context_tokens || 0) + '</div><div class="progress"><span style="width:' + pct + '%"></span></div><div class="muted">预算 ' + esc(budget.token_budget || '-') + '，使用 ' + pct + '%</div></div>'
        + '<div class="panel"><div class="muted">来源分布</div>' + renderKeyValues(source) + '</div>'
        + '<div class="panel"><div class="muted">层级分布</div>' + renderKeyValues(tier) + '</div></div>'
        + '<div class="trace">' + trace.slice(0, 6).map((item, idx) => '<div class="step"><b>' + (item.included ? '纳入' : '过滤') + ' #' + (idx + 1) + '</b><span class="muted">' + esc(item.reason || item.memory_id || '') + '</span></div>').join('') + '</div>';
    }
    function renderKeyValues(obj) {
      const entries = Object.entries(obj || {});
      if (!entries.length) return '<div class="muted">暂无数据</div>';
      return entries.map(([k,v]) => '<div class="health"><span>' + esc(k) + '</span><b>' + esc(v) + '</b></div>').join('');
    }
    function renderForgetCards(payload, confirmed) {
      const plan = payload.result || {};
      const candidates = plan.candidates || [];
      document.getElementById('forgetCards').innerHTML = candidates.length ? candidates.map((item, idx) =>
        '<div class="item"><div class="item-title">候选 #' + (idx + 1) + ' · ' + esc(item.type || 'memory') + '</div>'
        + '<div class="muted">' + esc(item.summary || item.memory_id) + '</div>'
        + '<div class="muted">score=' + esc(item.score) + ' · matched=' + esc((item.matched_keywords || []).join(', ')) + '</div></div>'
      ).join('') : '<div class="item"><div class="item-title">' + (confirmed ? '已执行' : '未发现候选') + '</div><div class="muted">请查看下方 JSON 证据详情。</div></div>';
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
          + '<div class="timeline-meta">' + esc(item.sequence) + ' · ' + esc(item.role) + ' · ' + esc(item.event_type) + ' · ' + esc(item.turn_id) + '</div>'
          + '<div class="timeline-body">' + esc(body) + '</div></div>';
      }).join('') : '<div class="muted">当前筛选范围内没有 Agent 交互事件。</div>';
      const preferences = result.preference_aggregates || [];
      document.getElementById('agentPreferences').innerHTML = preferences.length ? preferences.map(item =>
        '<div class="item"><div class="item-title">' + esc(item.topic) + ' = ' + esc(item.value) + '</div>'
        + '<div class="muted">范围 ' + esc(item.preference_scope) + ' · 证据 ' + esc(item.evidence_count) + ' · 会话 ' + esc(item.distinct_sessions) + ' · 状态 ' + (item.stable ? '稳定' : '候选') + '</div>'
        + (item.promoted_memory_id ? '<div class="actions"><button class="secondary" onclick="loadPreferenceEvidence(event,\\'' + esc(item.promoted_memory_id) + '\\')">查看证据</button></div>' : '')
        + '</div>'
      ).join('') : '<div class="muted">尚未形成偏好候选。</div>';
      const labels = {planning:'规划', tool_selection:'工具选择', response_generation:'回答生成'};
      const contexts = result.contexts || {};
      document.getElementById('agentContexts').innerHTML = ['planning','tool_selection','response_generation'].map(stage => {
        const value = contexts[stage] || {};
        const memories = value.memories || [];
        return '<div class="stage-column"><h3>' + labels[stage] + '</h3><div class="muted">' + memories.length + ' 条记忆 · ' + esc((value.budget || {}).estimated_context_tokens || 0) + ' tokens</div>'
          + (memories.length ? memories.map(item => '<div class="item" style="margin-top:8px"><b>' + esc(item.preference_topic || item.subtype || item.type) + '</b><div class="muted">' + esc(item.selection_reason || '') + '</div></div>').join('') : '<div class="muted" style="margin-top:8px">无适用记忆</div>')
          + '</div>';
      }).join('');
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
    async function agentForget(event, confirmForget) {
      if (confirmForget && !window.confirm('确认遗忘该交互偏好并清理受管事件载荷？外部 Agent 持久化副本需单独处理。')) return;
      await withBusy(event, async () => {
        const filters = {agent_name:agentForgetName.value, preference_topic:agentForgetTopic.value};
        if (agentSession.value.trim()) filters.session_id = agentSession.value.trim();
        const payload = await postJson('/api/agent-forget', {
          filters:filters, confirm:confirmForget, compact:true, external_persistence:true
        });
        const affected = (payload.result || {}).affected || {};
        const residue = (payload.result || {}).external_residue || {};
        document.getElementById('agentForgetImpact').innerHTML =
          '<div class="item"><div class="item-title">影响范围</div><div class="muted">事件 ' + esc((affected.event_ids || []).length)
          + ' · 记忆 ' + esc((affected.memory_ids || []).length) + ' · 观察 ' + esc(affected.observation_count || 0)
          + ' · 历史上下文使用 ' + esc(affected.context_usage_count || 0) + '</div></div>'
          + '<div class="item"><div class="item-title">外部残留</div><div class="muted">' + esc(residue.explanation || '未报告') + '</div></div>';
        agentOut.textContent = pretty(payload);
        if (confirmForget) await loadAgentOverview(null);
      });
    }
    async function refresh(event) {
      if (event) return withBusy(event, async () => refresh(null));
      const status = await getJson('/api/status');
      const diagResp = await getJson('/api/diagnostics');
      const diag = diagResp.result || {};
      document.getElementById('overviewOut').textContent = pretty(status);
      const result = status.result || {};
      document.getElementById('namespaceMeta').textContent = 'namespace: ' + (result.namespace || '-');
      document.getElementById('liveCount').textContent = result.live_record_count ?? '-';
      document.getElementById('auditCount').textContent = result.audit_event_count ?? '-';
      document.getElementById('storageRoot').textContent = result.storage_root || '-';
      document.getElementById('healthBadge').textContent = status.ok ? '运行正常' : '状态异常';
      document.getElementById('healthBadge').className = 'badge ' + (status.ok ? 'ok' : 'bad');
      badge('auditBadge', diag.audit_chain && diag.audit_chain.ok ? 'ok' : 'bad', diag.audit_chain && diag.audit_chain.ok ? '通过' : '异常');
      const retrieval = diag.retrieval_status || {};
      badge('retrievalBadge', retrieval.requested_backend ? 'ok' : 'warn', retrieval.last_backend_used || retrieval.requested_backend || '未知');
      const sdk = diag.kylin_sdk || {};
      const kylinInfo = sdk.kylin || {};
      const sdkAvailable = Boolean(kylinInfo.available || kylinInfo.embedding_available || kylinInfo.vector_store_available);
      const sdkLabel = sdkAvailable ? '可探测' : (sdk.last_backend_used || 'fallback');
      badge('sdkBadge', sdkAvailable ? 'ok' : 'warn', sdkLabel);
      const runtime = diag.runtime_status || {};
      badge('osBadge', runtime.cgroup_v2 ? 'ok' : 'warn', runtime.cgroup_v2 ? 'cgroup v2' : '部分可用');
      renderDeliveryReadiness(diag, retrieval, runtime, sdkAvailable);
      document.getElementById('lastUpdated').textContent = '最后刷新：' + new Date().toLocaleTimeString();
      renderStatusSummary(status, diag);
    }
    function renderDeliveryReadiness(diag, retrieval, runtime, sdkAvailable) {
      const checks = [
        {name:'审计链', ok: Boolean(diag.audit_chain && diag.audit_chain.ok), advice:'审计链异常时先运行“校验审计链”，必要时核对 namespace 和备份。'},
        {name:'检索后端', ok: Boolean(retrieval.requested_backend || retrieval.last_backend_used), advice:'检索后端未知时运行“检索后端”，确认 FTS/hash/graph 或 Kylin 后端状态。'},
        {name:'OS 资源', ok: Boolean(runtime.platform || runtime.cgroup_v2), advice:'OS 资源不可见时检查 systemd/cgroup 运行环境，普通 Windows 本机属于部分可用。'},
        {name:'诊断导出', ok: true, advice:'导出诊断包后可作为验收、交付复核和现场排查附件。'}
      ];
      if (!sdkAvailable) {
        checks.push({name:'端侧 SDK', ok:true, advice:'Kylin SDK 不可用时保持稳定 fallback；openKylin 复验环境可运行 sdk-status 探针。'});
      }
      const passed = checks.filter(item => item.ok).length;
      const score = Math.round((passed / checks.length) * 100);
      document.getElementById('readinessProgress').style.width = score + '%';
      document.getElementById('readinessText').textContent = '当前就绪度 ' + score + '% · ' + passed + '/' + checks.length + ' 项满足';
      const advice = checks.filter(item => !item.ok).concat(checks.filter(item => item.ok).slice(0, 1));
      document.getElementById('riskAdvice').innerHTML = advice.map(item =>
        '<div class="item"><div class="item-title">' + esc(item.name) + '</div><div class="muted">' + esc(item.advice) + '</div></div>'
      ).join('');
    }
    async function ingest(event) {
      await withBusy(event, async () => {
        const data = {text: ingestText.value, source_kind: sourceKind.value, tier: tier.value};
        ingestOut.textContent = pretty(await postJson('/api/ingest', data));
        await refresh();
      });
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
      if (confirmForget && !window.confirm('确认执行精准遗忘并压缩段文件？该操作会写入 tombstone 并清理可访问索引。')) return;
      await withBusy(event, async () => {
        const payload = await postJson('/api/forget', {request: forgetRequest.value, confirm: confirmForget, compact: confirmForget});
        renderForgetCards(payload, confirmForget);
        forgetOut.textContent = pretty(payload);
        await refresh();
      });
    }
    async function audit(event) {
      await withBusy(event, async () => { auditOut.textContent = pretty(await getJson('/api/audit?limit=30')); });
    }
    async function verifyAudit(event) {
      await withBusy(event, async () => {
        diagnosticsOut.textContent = pretty(await getJson('/api/verify-audit'));
        await refresh();
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
    function exportDiagnostics(event) {
      if (event && event.currentTarget) event.currentTarget.blur();
      window.location.href = '/api/export-diagnostics';
    }
    refresh(); audit(null); loadAgentOverview(null);
  </script>
</body>
</html>"""
