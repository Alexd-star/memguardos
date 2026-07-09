# Web Acceptance Review Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 在本地 Web 控制台管理员分组中加入“人工复核验收”页面，让用户能按步骤运行可自动化的核验项、查看通过标准、记录人工截图项，并导出复核结果。

**Architecture:** 新增一个小型 `memguard.web.acceptance` 模块，集中定义白名单复核项和安全运行逻辑；`memguard.web.dashboard` 只负责 HTTP 路由和页面渲染。自动项调用现有 `MemGuardService` 方法，少量工程项通过固定白名单命令执行，不提供任意命令入口。

**Tech Stack:** Python 标准库 HTTP server、现有 MemGuardService、无前端框架的 HTML/CSS/JavaScript、pytest 回归测试。

---

### Task 1: 失败测试

**Files:**
- Modify: `tests/test_web_rbac.py`
- Modify: `tests/test_web_dashboard_product_ui.py`
- Modify: `scripts/web_dashboard_smoke.py`

- [ ] 写测试：`/api/acceptance-plan` 返回分组清单，viewer 可读。
- [ ] 写测试：`/api/acceptance-run` 需要 admin，运行 `A21_VERIFY_AUDIT` 返回通过结果。
- [ ] 写测试：页面包含管理员分组“复核中心”和复核进度 UI，但不出现任意命令输入框。
- [ ] 运行测试，确认因为接口和页面尚未实现而失败。

### Task 2: 后端白名单复核模块

**Files:**
- Create: `memguard/web/acceptance.py`
- Modify: `memguard/web/dashboard.py`

- [ ] 定义 `acceptance_plan()`，返回自动项与人工项。
- [ ] 定义 `run_acceptance_item(service, item_id)`，只允许固定 ID。
- [ ] 自动项覆盖 doctor/状态、写入检索、上下文、遗忘、审计、证据目录等关键能力。
- [ ] 增加 `/api/acceptance-plan`、`/api/acceptance-run`、`/api/acceptance-report`。

### Task 3: Web 页面

**Files:**
- Modify: `memguard/web/dashboard.py`

- [ ] 导航加入管理员分组下的“复核中心”。
- [ ] 页面展示总进度、下一步建议、自动复核项、人工截图项。
- [ ] 每个自动项提供运行按钮、通过标准、结果摘要、详细记录折叠区。
- [ ] 人工项提供“标记通过/待复验/不通过”的本地页面状态，不写入任意系统数据。

### Task 4: 验证

**Files:**
- Modify: `scripts/web_dashboard_smoke.py`

- [ ] smoke 检查页面入口、计划接口、单项运行接口。
- [ ] 运行 `python -m pytest tests/test_web_rbac.py tests/test_web_dashboard_product_ui.py -q`。
- [ ] 运行 `python scripts/web_dashboard_smoke.py`。
- [ ] 运行 `python -m pytest -q`。
