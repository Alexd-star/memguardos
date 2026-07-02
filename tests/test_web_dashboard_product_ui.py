from memguard.web.dashboard import _dashboard_html


def test_dashboard_uses_role_oriented_product_navigation_without_contest_copy():
    page = _dashboard_html()

    assert "个人工作台" in page
    assert "普通用户" in page
    assert "系统维护" in page
    assert "管理员视图" in page
    assert "高级信息" in page
    assert "MemGuardOS 控制台" not in page
    assert "一键验收路径" not in page
    assert "演示" not in page
    assert "验收" not in page
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
    assert "原始响应" in page
    assert "<pre id=\"overviewOut\"" in page
    assert "<pre id=\"agentOut\"" in page


def test_dashboard_has_visual_product_affordances_not_only_text_blocks():
    page = _dashboard_html()

    assert "quick-actions" in page
    assert "health-matrix" in page
    assert "lifecycle" in page
    assert "status-dot" in page
    assert "memory-funnel" in page
    assert "服务健康" in page
    assert "采集" in page and "抽取" in page and "上下文" in page and "遗忘" in page
