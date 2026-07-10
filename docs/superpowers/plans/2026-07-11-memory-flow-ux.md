# Memory Flow UX Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 让 MemGuardOS 的记忆写入、检索、查看和遗忘形成清晰操作流，并治理重复结果和交互尾延迟。

**Architecture:** 数据层对精确重复写入做幂等处理，检索层对历史重复记录做非破坏折叠并生成用户可读命中原因，Web 将记忆中心重构为统一列表工作区，UKUI 收敛为系统状态与任务入口。Kylin SDK 继续作为可选增强，但在 `auto` 模式中严格服从剩余延迟预算。

**Tech Stack:** Python 3、SQLite/FTS、原生 HTML/CSS/JavaScript、Qt/UKUI、pytest、Playwright。

---

### Task 1: 重复记忆与检索契约

**Files:**
- Modify: `tests/test_core.py`
- Modify: `memguard/store/sqlite_meta.py`
- Modify: `memguard/service.py`
- Modify: `memguard/feedback.py`

- [ ] 写入失败测试：重复写入返回同一 `memory_id`，活动记忆数量保持 1。
- [ ] 写入失败测试：已有重复记录在搜索中折叠，并返回 `duplicate_count` 与 `duplicate_memory_ids`。
- [ ] 实现活动记忆精确匹配和幂等写入审计。
- [ ] 实现搜索结果折叠、顺序编号和中文命中原因。
- [ ] 运行 `python -m pytest tests/test_core.py tests/test_feedback_contracts.py -q`。

### Task 2: Kylin SDK 交互延迟预算

**Files:**
- Modify: `tests/test_core.py`
- Modify: `memguard/retrieval/hybrid.py`

- [ ] 写入失败测试：`auto` 模式把剩余预算换算为 SDK 子进程超时。
- [ ] 为 `KylinEmbeddingProvider.vector()` 增加单次超时覆盖值。
- [ ] 在查询向量和候选向量阶段传递剩余预算；预算不足立即回退。
- [ ] 运行 Kylin 检索相关测试。

### Task 3: Web 记忆中心与总览重构

**Files:**
- Modify: `tests/test_web_dashboard_lifecycle.py`
- Modify: `scripts/web_dashboard_smoke.py`
- Modify: `memguard/web/dashboard.py`

- [ ] 写入失败测试：页面包含记忆短编号、命中原因、结果折叠和清晰的空/加载状态。
- [ ] 将总览收敛为状态横幅、三个关键指标、主要动作和两列近期信息。
- [ ] 将记忆中心改为统一工具栏与列表，显示编号、类型、来源、范围、状态和操作。
- [ ] 默认显示前三条搜索结果，其余放入“更多结果”；不默认展示原始 JSON。
- [ ] 增加局部骨架、成功/失败反馈、重试入口和一致的按钮状态。
- [ ] 运行 Web smoke 和生命周期测试。

### Task 4: UKUI 入口收敛与视觉层级

**Files:**
- Modify: `tests/test_openkylin_native.py`
- Modify: `native/ukui/ukcc-memguard/memguard_ukcc_plugin.cpp`

- [ ] 写入失败测试：普通页包含查看记忆、搜索记忆和精准遗忘三个主入口，技术维护仅在管理员页。
- [ ] 精简普通页说明，强化状态摘要、最近记忆行为和任务按钮层级。
- [ ] 使用系统主题图标、原生控件和一致间距，不添加自绘品牌皮肤。
- [ ] 运行原生源码检查与 UKUI 构建脚本。

### Task 5: 产品与性能验证

**Files:**
- Modify: `docs/测试报告.md`
- Modify: `docs/用户手册.md`

- [ ] 运行定向 pytest、Ruff 和 Web smoke。
- [ ] 用 Playwright 检查 1440×1000 与 760×900，确认无重叠、无控制台错误。
- [ ] 运行小规模重复写入与冷热搜索基准，记录缓存前后结果。
- [ ] 在 openKylin 同步并编译 UKUI，复核运行页面与检索操作。
- [ ] 更新文档中的用户流程、重复治理和 `P95 <= 500 ms` 指标解释。
