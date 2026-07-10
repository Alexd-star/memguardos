# 用户反馈链与可视化验证 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 把 MemGuardOS 的记忆产生、生效、风险、遗忘和验证做成跨 CLI、Web、UKUI、托盘、Peony 的统一产品反馈链。

**Architecture:** 在 `memguard/feedback.py` 建立只读投影层和四种统一结构，Service 负责从真实记录生成结构；Web/CLI/原生桌面入口只消费这些结构。Web 继续使用标准库 HTTP 和内嵌 HTML，原始 JSON 仅作折叠高级信息，原生组件通过 CLI 复用 Python 业务。

**Tech Stack:** Python 3.10/3.11、dataclasses、标准库 HTTP server、Qt/UKUI C++ 插件、pytest、ruff。

---

### Task 1: 统一反馈结构与服务投影

**Files:**
- Create: `memguard/feedback.py`
- Modify: `memguard/service.py`
- Modify: `memguard/api/server.py`
- Test: `tests/test_feedback_contracts.py`

- [ ] 写四种结构的序列化、敏感边界、空结果和状态测试。
- [ ] 实现可读摘要、下一步动作、验证步骤和通知投影。
- [ ] 在 `ingest_text`、`ingest_interaction`、`forget`、`forget_agent_interactions`、`preference_evidence` 上提供反馈结构。
- [ ] 为 API 增加 feedback summary、preference proof、forget verification、notifications、memory detail 接口。
- [ ] 运行目标测试并修正兼容性。

### Task 2: Web 工作台和可验证反馈

**Files:**
- Modify: `memguard/web/dashboard.py`
- Modify: `scripts/web_dashboard_smoke.py`
- Test: `tests/test_web_feedback_api.py`

- [ ] 先增加 endpoint 和 HTML 契约测试。
- [ ] 增加工作台、最近变化、偏好证明、记忆详情、遗忘验证和通知中心。
- [ ] 增加 demo/replay 入口，明确标记执行模式。
- [ ] 统一按钮反馈、错误处理、导航目标和可折叠高级信息。
- [ ] 运行 dashboard smoke 和浏览器级基础操作检查。

### Task 3: 用户 CLI 和演示闭环

**Files:**
- Modify: `memguard/cli/memguard.py`
- Modify: `scripts/run_judge_journey.py`
- Create/Modify: `tests/test_memguard_cli_feedback.py`

- [ ] 增加 `--json` 和交互式 `/preferences`、`/model`、`/workspace`、`/audit`、`/demo` 命令。
- [ ] 把写入、阻断、上下文、偏好证明、遗忘验证改为可读输出。
- [ ] demo 支持 replay/dry-run/真实 OpenHands 三种明确模式，不伪造真实 Agent 结果。
- [ ] 增加 CLI smoke 和 judge journey dry-run 测试。

### Task 4: UKUI、托盘和 Peony 反馈链

**Files:**
- Modify: `native/ukui/ukcc-memguard/memguard_ukcc_plugin.cpp`
- Modify: `native/ukui/tray/memguard_tray.cpp`
- Modify: `native/ukui/peony-memguard/memguard_peony_plugin.cpp`
- Modify: `native/ukui/README.md`
- Test: `tests/test_openkylin_native.py`

- [ ] 增加统一反馈结构的用户解释和最近行为入口。
- [ ] 托盘增加最近记忆、偏好、任务、遗忘、通知暂停和 Agent 工作台入口，并按事件级别控制通知。
- [ ] Peony 导入与撤销结果显示新增记忆、阻断和逐项遗忘验证，保持多文件和取消行为。
- [ ] UKUI 日常页增加行为摘要，管理员/维护页增加结构化状态说明。
- [ ] 在无 openKylin SDK/头文件环境下保持构建和契约测试可复验。

### Task 5: 文档、全量验证和交付同步

**Files:**
- Modify: `docs/用户使用指南.md`
- Modify: `docs/人工演示与录屏脚本.md`
- Modify: `docs/Web控制台与Agent工作台说明.md`
- Modify: `docs/UKUI与托盘反馈链说明.md`
- Modify: `docs/验证链设计说明.md`
- Modify: `docs/赛题评分点对应矩阵.md`

- [ ] 更新用户操作和证据口径，明确真实 Agent、replay 和 dry-run 边界。
- [ ] 运行 `pytest`、`ruff`、文档链接、dashboard smoke 和 competition readiness。
- [ ] 提交并推送源码与文档。
- [ ] VM 可达时在 openKylin 重新构建、安装、走一遍 UI 闭环并采集当前 HEAD 证据；不可达时只报告未完成的实机项。
