# openKylin 原生化产品补强实施计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 将 MemGuardOS 从“已能在 openKylin 上运行和验证”进一步补强为更接近 openKylin 原生系统组件的交付形态，覆盖诊断面板、Peony 文件扫描闭环、Kylin SDK 预热、Polkit 授权资产、通知联动和最终证据采集入口。

**Architecture:** 不推翻现有 CLI、daemon、UKUI/Peony、Kylin SDK 和 `.deb` 架构，而是在现有边界内增加可测试的本机体验层。核心 Python 层输出结构化诊断和扫描报告；UKUI/Peony 插件消费这些 CLI 能力；脚本层提供预热、授权和证据采集；文档层只描述已实现能力和准确边界。

**Tech Stack:** Python 3.9+、argparse、systemd、cgroup v2、auditd/journald、Kylin AI SDK 探测、Qt5/UKUI/Peony 插件、freedesktop notification、Polkit policy、Debian packaging。

---

## 1. 实施边界

本轮只做能稳定提升 openKylin 适配度和用户好感度的内容：

- 提供 `memctl openkylin-status`，输出红黄绿结构化状态，供 UKUI 控制中心和终端验收共用。
- 提供 `memctl desktop scan-file`，对 Peony 右键选中文件生成“是否可导入、敏感风险、预期动作、导入结果、审计结果”的报告。
- 提供 `scripts/warmup_kylin_sdk.sh` 和 systemd user service，用于登录后预热 Kylin embedding runtime，并明确 fallback 状态。
- 提供 Polkit policy 和受控 helper 脚本资产，让高权限操作具备原生授权路径。
- 提供 `scripts/collect_openkylin_native_evidence.sh`，一键采集 openKylin 原生化证据。
- 更新 UKUI/Peony 文档与 README，避免把原型夸大为官方内置组件。

本轮不做以下高风险或需要人工签名发布的内容：

- 不把 BPF LSM 扩展成通用 DLP。
- 不把 Kylin SDK 改成唯一默认检索路径。
- 不声明进入 openKylin 官方软件源。
- 不伪造 UI 截图或人工可见性证据。

## 2. 文件结构

- 新增 `memguard/os_guard/openkylin_native.py`：聚合 openKylin 本机状态，生成 UI 可消费的红黄绿状态。
- 新增 `memguard/desktop/peony_scan.py`：实现 Peony 文件扫描预览和可选导入报告。
- 修改 `memguard/cli/memctl.py`：新增 `openkylin-status` 与 `desktop scan-file` 子命令。
- 新增 `scripts/warmup_kylin_sdk.sh`：预热 Kylin embedding SDK 并输出 JSON 结果。
- 新增 `scripts/memguard-kylin-warmup.service`：systemd user 预热单元。
- 新增 `scripts/memguardos-native-helper.sh`：Polkit 受控 helper，只允许白名单动作。
- 新增 `packaging/polkit/org.memguardos.policy`：Polkit 授权策略。
- 修改 `scripts/build_deb.sh`：打包 Polkit、warmup service、helper 脚本和可选 UKUI 插件。
- 新增 `scripts/collect_openkylin_native_evidence.sh`：采集 native status、Peony scan、SDK warmup、policy、服务状态。
- 新增 `tests/test_openkylin_native.py`：测试状态聚合、扫描报告、CLI 输出、打包资产存在。
- 修改 `native/ukui/peony-memguard/memguard_peony_plugin.cpp`：右键动作改为调用 `memctl desktop scan-file --ingest --notify`，由 CLI 生成更准确的扫描结果。
- 修改 `native/ukui/ukcc-memguard/memguard_ukcc_plugin.cpp`：增加“本机状态总览”和“预热 Kylin SDK”操作。
- 更新 `docs/openKylin原生化适配说明.md`、`README.md`、`docs/Peony与UKUI桌面集成实现说明.md`。

## 3. 开发任务

### Task 1: openKylin 本机状态聚合

- [ ] 编写测试：`openkylin_status()` 在非 openKylin 平台也返回稳定 schema。
- [ ] 实现 `memguard/os_guard/openkylin_native.py`。
- [ ] 新增 `memctl openkylin-status`。
- [ ] 验证 JSON 至少包含 system、services、sdk、desktop、security、resources、summary、recommendations。

### Task 2: Peony 文件扫描闭环

- [ ] 编写测试：文本文件、敏感文件、大文件、目录分别得到正确 action。
- [ ] 实现 `memguard/desktop/peony_scan.py`，默认 dry-run，`--ingest` 时执行 OS event 和文本导入。
- [ ] 新增 `memctl desktop scan-file --file PATH [--ingest] [--notify]`。
- [ ] 修改 Peony 插件调用新命令。

### Task 3: Kylin SDK 预热与通知

- [ ] 编写测试：warmup 脚本和 service 被打包资产识别。
- [ ] 新增 `scripts/warmup_kylin_sdk.sh`，调用 `memctl sdk-status --probe-embedding` 并输出 `ok/inference_ready/fallback/recommendation`。
- [ ] 新增 `scripts/memguard-kylin-warmup.service`，作为 user service，不阻塞桌面登录。
- [ ] UKUI 控制中心增加“预热 Kylin SDK”按钮。

### Task 4: Polkit 与受控 helper

- [ ] 新增 policy，声明安装服务、审计规则、BPF LSM 三类授权动作。
- [ ] 新增 helper，强制白名单动作，不接受任意 shell 命令。
- [ ] 打包脚本安装 policy 和 helper。
- [ ] 文档说明该能力是原生授权资产，是否出现桌面授权弹窗取决于目标系统 Polkit 环境。

### Task 5: openKylin 原生化证据采集

- [ ] 新增 `scripts/collect_openkylin_native_evidence.sh`。
- [ ] 采集 `openkylin-status`、`desktop scan-file`、`warmup_kylin_sdk.sh`、systemd、policy 文件、UKUI 插件探针。
- [ ] 输出 manifest，便于最终提交复验。

### Task 6: 文档与验收

- [ ] 新增中文说明文档，写清楚已实现、边界和人工截图建议。
- [ ] 更新 README openKylin 复验入口。
- [ ] 运行 `pytest`、`ruff`、`compileall`、打包和 readiness 检查。
- [ ] 如 openKylin VM 可连接，同步并运行 native evidence smoke。

## 4. 验收标准

- `python -m pytest tests/test_openkylin_native.py -q` 通过。
- `python -m pytest -q` 通过。
- `python -m ruff check memguard tests scripts` 通过。
- `python -m compileall -q memguard scripts` 通过。
- `bash scripts/build_deb.sh` 在 Linux/openKylin 环境可把新增资产纳入包结构。
- `memctl openkylin-status` 和 `memctl desktop scan-file` 输出 JSON，不依赖人工截图也可审计。
- 所有中文文档准确说明边界，不把可选增强夸大成默认生产能力。
