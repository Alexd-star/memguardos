from __future__ import annotations

from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any
from urllib.parse import parse_qs, urlparse
import json

from memguard.api.server import handle_request
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
        if parsed.path == "/api/status":
            self._send_api({"command": "namespace_status"})
            return
        if parsed.path == "/api/memory-policy":
            self._send_api({"command": "memory_policy"})
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
        payload = self._read_json()
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
        self.send_error(404, "not found")

    def log_message(self, format: str, *args: Any) -> None:
        return

    def _read_json(self) -> dict[str, Any]:
        length = _safe_int(self.headers.get("Content-Length", "0"), 0)
        if length <= 0:
            return {}
        raw = self.rfile.read(length).decode("utf-8", errors="replace")
        try:
            data = json.loads(raw)
        except json.JSONDecodeError:
            return {}
        return data if isinstance(data, dict) else {}

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
        self.send_header("Cache-Control", "no-store")
        self.send_header("Content-Length", str(len(raw)))
        self.end_headers()
        self.wfile.write(raw)

    def _send_html(self, html: str) -> None:
        raw = html.encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Cache-Control", "no-store")
        self.send_header("Content-Length", str(len(raw)))
        self.end_headers()
        self.wfile.write(raw)


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
    :root { color-scheme: light; --ink:#17202a; --muted:#667085; --line:#d0d5dd; --accent:#0f766e; --warn:#b42318; --bg:#f6f8fb; --panel:#ffffff; }
    * { box-sizing: border-box; }
    body { margin:0; font-family: "Segoe UI", "Microsoft YaHei", Arial, sans-serif; color:var(--ink); background:var(--bg); letter-spacing:0; }
    header { display:flex; align-items:center; justify-content:space-between; gap:16px; padding:14px 22px; background:#101828; color:white; }
    header h1 { margin:0; font-size:18px; font-weight:650; }
    header .meta { font-size:12px; color:#d0d5dd; }
    main { display:grid; grid-template-columns: 300px 1fr; min-height: calc(100vh - 52px); }
    nav { border-right:1px solid var(--line); background:#fff; padding:16px; }
    nav button { width:100%; min-height:36px; margin:0 0 8px; text-align:left; border:1px solid transparent; background:#fff; color:var(--ink); padding:8px 10px; border-radius:6px; cursor:pointer; }
    nav button.active, nav button:hover { border-color:var(--accent); background:#ecfdf5; }
    section { display:none; padding:18px 22px; }
    section.active { display:block; }
    h2 { font-size:20px; margin:0 0 14px; }
    .grid { display:grid; grid-template-columns: repeat(3, minmax(0, 1fr)); gap:12px; }
    .panel { background:var(--panel); border:1px solid var(--line); border-radius:8px; padding:14px; }
    label { display:block; font-size:13px; color:var(--muted); margin:10px 0 5px; }
    textarea, input, select { width:100%; border:1px solid var(--line); border-radius:6px; padding:9px 10px; font:14px/1.45 Consolas, "Microsoft YaHei", monospace; background:white; color:var(--ink); }
    textarea { min-height:120px; resize:vertical; }
    .actions { display:flex; flex-wrap:wrap; gap:8px; margin-top:12px; }
    button.primary { border:0; background:var(--accent); color:white; border-radius:6px; padding:9px 12px; cursor:pointer; min-height:36px; }
    button.secondary { border:1px solid var(--line); background:white; color:var(--ink); border-radius:6px; padding:9px 12px; cursor:pointer; min-height:36px; }
    pre { white-space:pre-wrap; word-break:break-word; background:#0b1220; color:#d1e9ff; border-radius:8px; padding:12px; min-height:220px; overflow:auto; }
    .metric { font-size:24px; font-weight:700; color:var(--accent); margin-top:4px; }
    .muted { color:var(--muted); font-size:13px; }
    .warning { color:var(--warn); font-weight:650; }
    @media (max-width: 900px) {
      main { grid-template-columns: 1fr; }
      nav { border-right:0; border-bottom:1px solid var(--line); display:grid; grid-template-columns: repeat(2, minmax(0,1fr)); gap:8px; }
      nav button { margin:0; }
      .grid { grid-template-columns: 1fr; }
    }
  </style>
</head>
<body>
  <header>
    <h1>MemGuardOS 控制台</h1>
    <div class="meta">
      <span id="namespaceMeta">namespace: loading</span>
      <input id="apiToken" type="password" placeholder="API Token" style="width:150px; min-height:28px; margin-left:10px; padding:4px 7px; font-size:12px;">
    </div>
  </header>
  <main>
    <nav>
      <button class="active" data-tab="overview">总览</button>
      <button data-tab="ingest">写入</button>
      <button data-tab="search">检索</button>
      <button data-tab="context">RAG 上下文</button>
      <button data-tab="forget">精准遗忘</button>
      <button data-tab="audit">审计</button>
    </nav>
    <div>
      <section id="overview" class="active">
        <h2>运行状态</h2>
        <div class="grid">
          <div class="panel"><div class="muted">实时记忆</div><div class="metric" id="liveCount">-</div></div>
          <div class="panel"><div class="muted">审计事件</div><div class="metric" id="auditCount">-</div></div>
          <div class="panel"><div class="muted">存储目录</div><div id="storageRoot" class="muted">-</div></div>
        </div>
        <pre id="overviewOut"></pre>
      </section>
      <section id="ingest">
        <h2>写入记忆</h2>
        <div class="panel">
          <label>文本</label><textarea id="ingestText">以后回答 openKylin 服务问题时，先给 systemctl 命令，再说明风险。</textarea>
          <label>来源类型</label><select id="sourceKind"><option>manual</option><option>dashboard</option><option>tool_result</option><option>user_behavior</option><option>os_event</option></select>
          <label>记忆层级</label><select id="tier"><option value="">自动</option><option>working</option><option>session</option><option>long_term</option></select>
          <div class="actions"><button class="primary" onclick="ingest()">写入</button><button class="secondary" onclick="explainExtraction()">解释抽取</button></div>
        </div>
        <pre id="ingestOut"></pre>
      </section>
      <section id="search">
        <h2>混合检索</h2>
        <div class="panel">
          <label>查询</label><input id="searchQuery" value="openKylin 服务内存限制">
          <div class="actions"><button class="primary" onclick="search()">检索</button></div>
        </div>
        <pre id="searchOut"></pre>
      </section>
      <section id="context">
        <h2>RAG 上下文</h2>
        <div class="panel">
          <label>问题</label><input id="contextQuery" value="openKylin 服务内存限制">
          <label>token 预算</label><input id="tokenBudget" type="number" value="1200">
          <div class="actions"><button class="primary" onclick="contextBuild()">构建</button></div>
        </div>
        <pre id="contextOut"></pre>
      </section>
      <section id="forget">
        <h2>精准遗忘</h2>
        <div class="panel">
          <label>自然语言请求</label><input id="forgetRequest" value="忘掉 temporary project codename">
          <div class="actions"><button class="secondary" onclick="forget(false)">Dry-run</button><button class="primary" onclick="forget(true)">确认遗忘并 compact</button></div>
          <p class="warning">确认遗忘会写入 tombstone 并清理可访问索引。</p>
        </div>
        <pre id="forgetOut"></pre>
      </section>
      <section id="audit">
        <h2>审计记录</h2>
        <div class="actions"><button class="primary" onclick="audit()">刷新</button></div>
        <pre id="auditOut"></pre>
      </section>
    </div>
  </main>
  <script>
    const pretty = obj => JSON.stringify(obj, null, 2);
    document.querySelectorAll('nav button').forEach(btn => btn.addEventListener('click', () => {
      document.querySelectorAll('nav button').forEach(item => item.classList.remove('active'));
      document.querySelectorAll('section').forEach(item => item.classList.remove('active'));
      btn.classList.add('active');
      document.getElementById(btn.dataset.tab).classList.add('active');
    }));
    const tokenInput = document.getElementById('apiToken');
    tokenInput.value = localStorage.getItem('memguardos_api_token') || '';
    tokenInput.addEventListener('change', () => localStorage.setItem('memguardos_api_token', tokenInput.value));
    function authHeaders(extra) {
      const headers = Object.assign({}, extra || {});
      const token = tokenInput.value.trim();
      if (token) headers['Authorization'] = 'Bearer ' + token;
      return headers;
    }
    async function getJson(url) { const r = await fetch(url, {headers: authHeaders()}); return await r.json(); }
    async function postJson(url, data) { const r = await fetch(url, {method:'POST', headers:authHeaders({'Content-Type':'application/json'}), body:JSON.stringify(data)}); return await r.json(); }
    async function refresh() {
      const status = await getJson('/api/status');
      document.getElementById('overviewOut').textContent = pretty(status);
      const result = status.result || {};
      document.getElementById('namespaceMeta').textContent = 'namespace: ' + (result.namespace || '-');
      document.getElementById('liveCount').textContent = result.live_record_count ?? '-';
      document.getElementById('auditCount').textContent = result.audit_event_count ?? '-';
      document.getElementById('storageRoot').textContent = result.storage_root || '-';
    }
    async function ingest() {
      const data = {text: ingestText.value, source_kind: sourceKind.value, tier: tier.value};
      ingestOut.textContent = pretty(await postJson('/api/ingest', data));
      await refresh();
    }
    async function explainExtraction() {
      const url = '/api/explain-extraction?text=' + encodeURIComponent(ingestText.value) + '&source_kind=' + encodeURIComponent(sourceKind.value);
      ingestOut.textContent = pretty(await getJson(url));
    }
    async function search() { searchOut.textContent = pretty(await getJson('/api/search?q=' + encodeURIComponent(searchQuery.value) + '&top_k=5')); }
    async function contextBuild() { contextOut.textContent = pretty(await getJson('/api/context?q=' + encodeURIComponent(contextQuery.value) + '&token_budget=' + encodeURIComponent(tokenBudget.value))); }
    async function forget(confirm) { forgetOut.textContent = pretty(await postJson('/api/forget', {request: forgetRequest.value, confirm, compact: confirm})); await refresh(); }
    async function audit() { auditOut.textContent = pretty(await getJson('/api/audit?limit=30')); }
    refresh(); audit();
  </script>
</body>
</html>"""
