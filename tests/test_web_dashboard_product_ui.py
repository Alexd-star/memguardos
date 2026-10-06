import io
import inspect
import json
import re
import shutil
import subprocess
import pytest

from memguard.web.dashboard import _DashboardHandler, _dashboard_html


def test_dashboard_uses_role_oriented_product_navigation_without_contest_copy():
    page = _dashboard_html()

    assert "个人工作台" in page
    assert "普通用户" in page
    assert "系统维护" in page
    assert "管理员视图" in page
    assert "系统检查" in page
    assert "详细记录" in page
    assert "MemGuardOS 控制台" not in page
    assert "一键验收路径" not in page
    assert "演示" not in page
    assert "benchmark" not in page.lower()
    assert "JSON 证据" not in page
    assert "证据详情" not in page
    assert "详细证据" not in page


def test_dashboard_keeps_raw_payloads_in_advanced_details_instead_of_main_flow():
    page = _dashboard_html()

    assert page.count("<details") >= 6
    assert "overviewOut" in page
    assert 'class="advanced-output"' in page
    assert "运行记录" in page
    assert "操作回执" in page
    assert "高级信息" not in page
    assert "原始响应" not in page
    assert "调试" not in page
    assert "耗时" not in page
    assert "<pre id=\"overviewOut\"" in page
    assert "<pre id=\"agentOut\"" in page


def test_dashboard_has_visual_product_affordances_not_only_text_blocks():
    page = _dashboard_html()

    assert "task-action" in page
    assert "metric-strip" in page
    assert "workflow-steps" in page
    assert "overview-status-banner" in page
    assert "status-dot" in page
    assert "memory-funnel" not in page
    assert "Agent 记忆服务可以正常使用" in page
    assert "查看记忆" in page and "搜索记忆" in page and "精准遗忘" in page
    assert "acceptanceList" in page
    assert "acceptanceManualList" in page
    assert "acceptanceProgress" in page
    assert "任意命令" not in page


def test_dashboard_uses_available_desktop_width_without_unbounded_ultrawide_stretch():
    page = _dashboard_html()

    assert "width:100%;" in page
    assert "max-width:1680px;" in page
    assert "margin-inline:auto;" in page
    assert "max-width:1180px;" not in page


def test_agent_history_is_grouped_by_session_and_hides_internal_policy_rows():
    page = _dashboard_html()

    assert "最近会话" in page
    assert "function agentSessionMarkup(items)" in page
    assert "function visibleSessionEvents(events)" in page
    assert "function sessionStatus(events)" in page
    assert "item.event_type === 'policy_decision'" in page
    assert "实际操作" in page
    assert "已完成" in page


def test_dashboard_operations_have_user_feedback_without_debug_timestamps():
    page = _dashboard_html()

    assert "operationStatus" in page
    assert "setOperationStatus" in page
    assert "AbortController" in page
    assert "timeoutMs" in page
    assert "refreshWarnings" in page
    assert "需要关注" in page
    assert "立即校验" in page
    assert "最后刷新" not in page
    assert "toLocaleTimeString" not in page
    assert "new Date()" not in page
    assert "正在执行请稍后" not in page


def test_dashboard_initial_load_is_lightweight_and_tab_data_is_lazy_loaded():
    page = _dashboard_html()

    assert "loadTabData" in page
    assert "tabLoadedAt" in page
    assert "dynamicTabTtlMs" in page
    assert "loadSessionCapabilities().then(payload => refreshForSession(payload)).catch(handleInitialLoadFailure);" in page
    assert "refresh(); audit(null)" not in page
    assert "audit(null); loadAgentOverview(null)" not in page
    assert "loadAgentOverview(null); loadAcceptancePlan(null)" not in page


def test_dashboard_declares_click_handlers_for_all_inline_buttons():
    page = _dashboard_html()
    handlers = set()
    for marker in ('onclick="', "onclick='"):
        start = 0
        while True:
            index = page.find(marker, start)
            if index < 0:
                break
            value_start = index + len(marker)
            value_end = page.find(marker[-1], value_start)
            value = page[value_start:value_end]
            name = value.split("(", 1)[0].strip()
            if name:
                handlers.add(name)
            start = value_end + 1

    for handler in handlers:
        assert f"function {handler}" in page or f"async function {handler}" in page


def test_dashboard_inline_script_has_valid_javascript_syntax(tmp_path):
    page = _dashboard_html()
    match = re.search(r"<script>(?P<script>[\s\S]*?)</script>", page)
    assert match is not None

    node = shutil.which("node")
    if node is None:
        pytest.skip("Node.js is unavailable for the browser script syntax gate")

    script = tmp_path / "dashboard-inline.js"
    script.write_text(match.group("script"), encoding="utf-8")
    completed = subprocess.run(
        [node, "--check", str(script)],
        capture_output=True,
        text=True,
        check=False,
    )
    assert completed.returncode == 0, completed.stderr


def test_dashboard_declares_an_inline_favicon_to_avoid_console_noise():
    page = _dashboard_html()

    assert '<link rel="icon" href="data:,">' in page


def test_dashboard_uses_product_language_and_collapses_technical_receipts():
    page = _dashboard_html()

    assert "记忆空间：加载中" in page
    assert "命名空间：加载中" not in page
    assert "运行任务" in page
    assert "一键体验流程" not in page
    assert '<option value="manual">手动输入</option>' in page
    assert '<option value="dashboard">本地控制台</option>' in page
    assert '<option value="tool_result">工具执行结果</option>' in page
    assert '<option value="user_behavior">用户与 Agent 交互</option>' in page
    assert '<option value="os_event">系统事件</option>' in page
    assert '<option value="working">临时工作记忆</option>' in page
    assert '<option value="session">当前会话记忆</option>' in page
    assert '<option value="long_term">长期记忆</option>' in page
    assert "<details open>" not in page


def test_dashboard_keeps_access_token_in_admin_settings_not_header():
    page = _dashboard_html()
    header = re.search(r"<header>(?P<header>[\s\S]*?)</header>", page)
    diagnostics = re.search(r'<section id="diagnostics"[^>]*>(?P<section>[\s\S]*?)</section>', page)

    assert header is not None
    assert diagnostics is not None
    assert 'id="apiToken"' not in header.group("header")
    assert 'id="apiToken"' in diagnostics.group("section")


def test_dashboard_bootstraps_bearer_from_fragment_then_immediately_removes_credentials():
    page = _dashboard_html()

    assert "new URLSearchParams(window.location.hash.slice(1))" in page
    assert "bootstrapParams.get('token')" in page
    assert "sessionStorage.setItem('memguardos_api_token', bootstrapToken)" in page
    assert "window.history.replaceState(null, '', cleanLocation)" in page
    assert "bootstrapParams.get('tab')" in page
    assert "tokenInput.value = sessionStorage.getItem('memguardos_api_token') || ''" in page
    assert "?token=" not in page
    assert "launch_token" not in page


def test_dashboard_adapts_status_grids_before_labels_wrap_vertically():
    page = _dashboard_html()

    assert "@media (max-width: 1180px)" in page
    assert ".grid.four { grid-template-columns:repeat(2,minmax(0,1fr)); }" in page
    assert ".content-split { grid-template-columns:1fr; }" in page
    assert "@media (max-width: 760px)" in page
    assert "nav { position:sticky; top:0; z-index:10; display:flex;" in page
    assert "overflow-x:auto" in page
    assert "min-width:max-content" in page
    assert "@media (max-width: 480px)" in page
    assert ".nav-group { display:none; }" in page
    assert "nav button { width:auto; min-width:max-content; min-height:42px;" in page
    assert ".status-row > .muted { grid-column:1 / -1; grid-row:2; }" in page


def test_dashboard_password_field_is_grouped_in_a_non_submitting_form():
    page = _dashboard_html()
    diagnostics = re.search(r'<section id="diagnostics"[^>]*>(?P<section>[\s\S]*?)</section>', page)

    assert diagnostics is not None
    assert '<form id="dashboardAuthForm" class="overview-more-body" onsubmit="event.preventDefault()">' in diagnostics.group("section")
    assert '<details class="auth-settings"><summary>访问设置</summary>' in diagnostics.group("section")
    assert diagnostics.group("section").index('<form id="dashboardAuthForm"') < diagnostics.group("section").index('id="apiToken"')
    assert diagnostics.group("section").index('id="apiToken"') < diagnostics.group("section").index("</form>")


def test_dashboard_uses_user_facing_status_labels_instead_of_backend_names():
    page = _dashboard_html()

    assert "function retrievalModeLabel" in page
    assert "基础检索" in page
    assert "本机增强" in page
    assert "runtime.cgroup_v2 ? 'cgroup v2'" not in page


def test_dashboard_translates_internal_enums_before_rendering_user_results():
    page = _dashboard_html()

    for helper in (
        "sourceLabel",
        "tierLabel",
        "sensitivityLabel",
        "matchSourceLabel",
        "selectionReasonLabel",
        "stageLabel",
        "scopeLabel",
        "eventRoleLabel",
        "eventTypeLabel",
        "topicLabel",
        "memorySubtypeLabel",
        "memorySummaryLabel",
    ):
        assert f"function {helper}" in page

    assert "esc(row.summary || row.memory_id)" not in page
    assert "esc(row.match_source)" not in page
    assert "esc(row.tier)" not in page
    assert "esc(item.role)" not in page
    assert "esc(item.event_type)" not in page
    assert "esc(proof.stage)" not in page
    assert "selected_by_hybrid_graph_or_text_search" in page
    assert "与问题内容和关联信息匹配" in page
    assert "memorySummaryLabel(item)" in page
    assert "topicLabel(item.topic)" in page
    assert "scopeLabel(item.scope" in page
    assert "evidencebench_v2:'系统验证'" in page
    assert "evidencebench_v2:'评测样例'" not in page
    assert "row.summary_for_user ||" in page
    assert "memorySubtypeLabel(memory.subtype" in page
    assert "暂无使用证据" in page


def test_dashboard_request_failures_are_local_to_each_operation():
    page = _dashboard_html()

    assert "let requestFailure" not in page
    assert "requestFailure =" not in page
    assert "function requestError" in page
    assert "throw requestError(message, data);" in page
    assert "throw requestError(message, payload);" in page
    assert "return {ok:false, error:String(err)};" not in page
    assert "loadSessionCapabilities().then(payload => refreshForSession(payload)).catch(handleInitialLoadFailure);" in page


def test_dashboard_labels_are_associated_with_form_controls():
    page = _dashboard_html()
    labels = re.findall(r"<label(?P<attrs>[^>]*)>", page)

    assert labels
    assert all('for="' in attrs for attrs in labels)
    for target in re.findall(r'<label[^>]*for="([^"]+)"', page):
        assert f'id="{target}"' in page


def test_dashboard_system_warnings_offer_direct_actions_without_fake_readiness_score():
    page = _dashboard_html()

    assert "renderDeliveryReadiness" not in page
    assert "readinessProgress" not in page
    assert "立即校验" in page
    assert "检查检索方式" in page


def test_dashboard_all_tab_entry_points_share_retryable_lazy_loading():
    page = _dashboard_html()

    assert "async function openTab(tab, options)" in page
    assert "await loadTabData(tab, null, {force:Boolean(settings.force)});" in page
    assert "async function loadTabData(tab, event, options)" in page
    assert "tabLoadedAt.set(tab, Date.now());" in page
    assert "const result = await loader(event);\n        if (result !== null)\n          tabLoadedAt.set(tab, Date.now());" in page
    assert "页面内容暂时没有加载出来" in page
    assert "openTab(btn.dataset.tab);\n      loadTabData(btn.dataset.tab, null);" not in page


def test_dashboard_refresh_renders_partial_results_and_preserves_mutation_success():
    page = _dashboard_html()

    assert "Promise.allSettled" in page
    assert "renderStatusPayload" in page
    assert "renderDiagnosticsPayload" in page
    assert "refreshAfterMutation" in page
    assert "result.updated ? '记忆已更新' : '记忆已保存'" in page
    assert "本次未新增记忆，请查看下方处理结果" in page
    assert "遗忘已经完成" in page
    assert "操作记录校验通过" in page
    assert "，但部分概览暂未更新。" in page


def test_dashboard_operation_feedback_is_sequenced_and_supports_noop_results():
    page = _dashboard_html()

    assert "let operationSequence = 0;" in page
    assert "const operationId = ++operationSequence;" in page
    assert "operationId === operationSequence" in page
    assert "function operationFeedback" in page
    assert "if (!id) {" in page
    assert "请先选择一条记忆。" in page
    assert page.index("if (!id) {") < page.index("return withBusy(event, async () => {", page.index("async function loadMemoryDetail"))


def test_dashboard_error_feedback_uses_one_visible_status_surface():
    page = _dashboard_html()

    status_body = page[page.index("function setOperationStatus") : page.index("function buttonText")]
    assert "if (state === 'bad') showError('');" in status_body


def test_dashboard_uses_authenticated_blob_download_for_diagnostics():
    page = _dashboard_html()

    export_body = page[page.index("async function exportDiagnostics") : page.index("openHashTab();")]
    assert "downloadJsonExport(event, '/api/export-diagnostics'" in export_body
    assert "async function downloadJsonExport" in export_body
    assert "fetchWithTimeout(url, {headers: authHeaders()}" in export_body
    assert "headers: authHeaders()" in export_body
    assert "await response.blob()" in export_body
    assert "URL.createObjectURL" in export_body
    assert "URL.revokeObjectURL" in export_body
    assert "Content-Disposition" in export_body
    assert "window.location.href = '/api/export-diagnostics'" not in page


def test_dashboard_applies_role_capabilities_before_exposing_privileged_actions():
    page = _dashboard_html()

    assert "async function loadSessionCapabilities" in page
    assert "function applySessionCapabilities" in page
    assert "data-min-role=\"operator\"" in page
    assert "data-min-role=\"admin\"" in page
    assert "currentRoleRank" in page
    assert "当前访问权限不足" in page
    assert "loadSessionCapabilities().then" in page
    assert '<button data-tab="evidence" data-min-role="admin">审计与证据</button>' in page
    assert '<button data-tab="diagnostics" data-min-role="admin">系统维护</button>' in page
    assert '<section id="audit" data-min-role="admin">' in page
    assert '<section id="diagnostics">' in page
    assert '<section id="acceptance" data-min-role="admin">' in page
    assert 'data-min-role="admin" onclick="openTab(\'diagnostics\')">系统维护</button>' in page
    assert 'data-min-role="admin" onclick="verifyAudit(event)">校验操作记录</button>' in page
    assert "requestedButton && requestedButton.hidden" in page
    assert "当前访问权限不足，已返回个人工作台。" in page
    assert "function privilegedActionMarkup" in page
    assert "此操作需要管理员处理" in page


def test_dashboard_routes_anonymous_users_to_the_credential_entry_without_protected_requests():
    page = _dashboard_html()

    session_body = page[page.index("async function refreshForSession") : page.index("function badge")]
    assert "result.role === 'anonymous'" in session_body
    assert "openTab('diagnostics')" in session_body
    assert "请先填写本机访问凭证" in session_body
    assert "setOperationStatus('', '');" in session_body
    assert "return refresh();" in session_body
    assert "loadSessionCapabilities().then(payload => refreshForSession(payload))" in page


def test_dashboard_overview_uses_real_product_state_without_internal_paths_or_fixed_demo_metrics():
    page = _dashboard_html()

    assert "当前记忆" in page
    assert "记忆空间" in page
    assert "仅保存在本机" in page
    assert "<span>存储目录</span>" not in page
    assert "width:82%" not in page
    assert "width:64%" not in page
    assert "width:38%" not in page
    assert "条形图表示流程阶段" not in page
    assert "正在连接记忆服务" in page
    assert "先预览影响，再确认执行" in page


def test_dashboard_requires_a_current_forget_preview_before_confirmation():
    page = _dashboard_html()

    assert 'id="forgetConfirmButton"' in page
    assert 'id="agentForgetConfirmButton"' in page
    assert "let forgetPreview = null;" in page
    assert "let agentForgetPreview = null;" in page
    assert "invalidateForgetPreview" in page
    assert "preview_token:confirmForget ? forgetPreview.previewToken : ''" in page
    assert "preview_token:confirmForget ? agentForgetPreview.previewToken : ''" in page
    assert "候选数量" in page
    assert "selected_memory_ids:confirmForget ? forgetPreview.selectedIds : []" in page
    assert "data-forget-memory-id" in page
    assert "全选候选" in page
    assert "清空选择" in page
    assert "未选择的候选将继续保留" in page
    assert "请先重新预览影响范围" in page
    assert "externalResidueMessage" in page
    assert "residue.user_explanation" in page
    assert "OpenHands 自身保存的会话副本" in page


def test_dashboard_serializes_mutations_and_renders_a_search_empty_state():
    page = _dashboard_html()

    assert "let activeMutation = null;" in page
    assert "async function withMutation" in page
    assert "当前已有一项修改正在处理" in page
    assert "return withMutation(event, '保存记忆'" in page
    assert "return withMutation(event, '精准遗忘'" in page
    assert "没有找到相关记忆" in page
    assert "调整关键词或先写入一条记忆" in page


def test_dashboard_memory_detail_uses_real_source_and_preference_scope_fields():
    page = _dashboard_html()

    assert "memory.preference_scope || memory.scope" in page
    assert "memory.source_kind || (memory.source || {}).kind" in page
    assert "default:'当前记忆空间'" in page


@pytest.mark.parametrize(
    ("method_name", "args"),
    [
        ("_send_json", ({"value": float("nan")},)),
        ("_send_json_attachment", ({"value": float("nan")}, "diagnostics.json")),
    ],
)
def test_dashboard_json_boundaries_emit_standard_json(method_name, args):
    handler = object.__new__(_DashboardHandler)
    handler.wfile = io.BytesIO()
    handler.send_response = lambda status: None
    handler.send_header = lambda name, value: None
    handler.end_headers = lambda: None

    assert "allow_nan=False" in inspect.getsource(getattr(_DashboardHandler, method_name))

    getattr(handler, method_name)(*args)

    raw = handler.wfile.getvalue()
    assert b"NaN" not in raw
    assert b"Infinity" not in raw
    assert json.loads(raw.decode("utf-8")) == {"value": None}


def test_dashboard_uses_consolidated_memory_security_navigation():
    page = _dashboard_html()

    expected = [
        'data-tab="overview">总览</button>',
        'data-tab="agent">Agent 工作台</button>',
        'data-tab="memory">记忆中心</button>',
        'data-tab="preferences">我的偏好</button>',
        'data-tab="sensitive">内容保护</button>',
        'data-tab="forget">精准遗忘</button>',
        'data-tab="evidence" data-min-role="admin">审计与证据</button>',
        'data-tab="diagnostics" data-min-role="admin">系统维护</button>',
        'data-tab="acceptance" data-min-role="admin">系统检查</button>',
    ]
    for marker in expected:
        assert marker in page

    assert '<button data-tab="ingest">记忆写入</button>' not in page
    assert '<button data-tab="search">记忆检索</button>' not in page
    assert '<button data-tab="context">上下文预览</button>' not in page
    assert '<button data-tab="memory-detail">记忆详情</button>' not in page


def test_dashboard_product_hubs_reuse_existing_memory_and_evidence_flows():
    page = _dashboard_html()

    assert '<section id="memory">' in page
    assert "添加记忆" in page
    assert "搜索记忆" in page
    assert "生成预览" in page
    assert "查看详情" in page
    assert '<section id="sensitive">' in page
    assert "敏感文本测试" in page
    assert "最近敏感阻断" in page
    assert "敏感内容不会默认进入长期记忆或 Agent 上下文" in page
    assert '<section id="evidence" data-min-role="admin">' in page
    assert "审计链状态" in page
    assert "导出证据包" in page
    assert "查看完整技术详情" in page


def test_ingest_feedback_distinguishes_updates_conflicts_and_applicable_actions():
    page = _dashboard_html()

    assert "result.updated ? '记忆已更新' : '记忆已保存'" in page
    assert "本次未新增记忆，请查看下方处理结果" in page
    assert "result.candidate_memory_ids || []" in page
    assert "核对原记忆" in page
    assert "item.preference_topic ?" in page
    assert "fact_slot:'事实属性索引'" in page


def test_dashboard_clears_contextual_operation_feedback_when_switching_pages():
    page = _dashboard_html()
    open_tab = page[page.index("async function openTab") : page.index("async function loadTabData")]

    assert "setOperationStatus('', '');" in open_tab
def test_dashboard_uses_offline_icons_and_safe_dynamic_action_delegation():
    page = _dashboard_html()

    assert "Paths are from Lucide" in page
    assert "const navIcons" in page
    assert "button[data-action]" in page
    assert 'data-action="open-memory-detail"' in page
    assert 'data-action="copy-memory-id"' in page
    assert 'data-action="notification-target"' in page


def test_forgetting_preview_hides_internal_step_names_and_refreshes_verification_after_commit():
    page = _dashboard_html()

    assert "main_record:'主记忆记录'" in page
    assert "确认后自动复检" in page
    assert "await loadRecentForgetting(null, true);" in page
    assert "遗忘已经完成，验证结果已更新" in page
    assert "button.dataset.preserveBusyState === 'true'" in page
    assert "forgetConfirmButton.dataset.preserveBusyState = 'true'" in page


def test_overview_status_renderer_only_targets_current_dom_ids():
    page = _dashboard_html()

    assert "document.getElementById('statMemories')" in page
    assert "document.getElementById('liveCount').textContent" not in page
    assert "document.getElementById('namespaceCount').textContent" not in page
    assert "document.getElementById('dataLocation').textContent" not in page
    assert "loadOverviewEvidence(null)" in page


def test_detail_erasure_discards_stale_content_and_checks_product_verification():
    page = _dashboard_html()
    action = page[page.index("async function forgetMemoryFromDetail") : page.index("async function agentForget")]
    assert "payload.ok === true && result.ok === true" in action
    assert "forgottenIds.includes(id)" in action
    assert "memoryDetailPreview = null;" in action
    assert "删除前的内容不再展示" in action
    assert "await loadMemoryDetail(null);" in action
    assert "return feedback;" in action
