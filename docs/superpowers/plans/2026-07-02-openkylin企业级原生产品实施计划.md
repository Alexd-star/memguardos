# MemGuardOS openKylin 企业级原生产品实施计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 将 Peony、UKUI、Kylin SDK、Polkit、Debian 分包、托盘和证据链补齐为可安装、可操作、可降级、可审计的 openKylin 产品闭环。

**Architecture:** 采用薄 Qt 原生客户端与稳定 JSON 契约，核心治理逻辑保留在 Python 服务层；特权操作通过最小权限 Polkit helper；Debian 包按核心、桌面、Agent 和文档职责拆分。所有任务以失败测试开始，并以 openKylin 实机证据结束。

**Tech Stack:** Python 3.10+、pytest、Qt5 Widgets、Peony/UKCC 插件接口、systemd、cgroup v2、Polkit、Kylin AI SDK、Debian `dpkg-deb`。

---

## Task 1：冻结桌面 JSON 契约与修正 cgroup 探针

**Files:**
- Modify: `memguard/os_guard/openkylin_native.py`
- Create: `memguard/os_guard/service_control.py`
- Modify: `tests/test_openkylin_native.py`

- [ ] 增加失败测试：模拟 `systemctl show memguardd.service`，要求状态读取服务 `ControlGroup`、`MemoryMax` 和 `CPUQuotaPerSecUSec`。
- [ ] 增加失败测试：状态 schema 包含稳定 `actions`、`code`、`level`、`summary` 和用户可读 `advice`。
- [ ] 实现服务级 cgroup 探针和结构化操作模型，保留旧字段兼容性。
- [ ] 运行 `pytest tests/test_openkylin_native.py -q`，确认通过后提交。

## Task 2：实现 Peony 多文件预览与确认后执行

**Files:**
- Modify: `memguard/desktop/peony_scan.py`
- Modify: `memguard/cli/memctl.py`
- Create: `memguard/desktop/scan_manifest.py`
- Modify: `tests/test_openkylin_native.py`
- Modify: `native/ukui/peony-memguard/memguard_peony_plugin.cpp`
- Modify: `native/ukui/peony-memguard/memguard_peony_plugin.h`

- [ ] 增加失败测试：多文件扫描保留输入顺序，限制数量和总字节数，敏感项默认不可选择。
- [ ] 增加失败测试：apply manifest 拒绝路径篡改、过期清单、重复执行和未扫描文件。
- [ ] 实现 `desktop scan-files` 与 `desktop apply-scan`，使用版本化 JSON 和一次性 nonce。
- [ ] 把 Peony 菜单改为“检查记忆内容”，先运行 dry-run，再显示 Qt 预览表格、复选框和确认按钮。
- [ ] 显示批处理结果对话框与审计编号，单项失败不丢失其他结果。
- [ ] 构建插件并运行 Python 测试和 Peony smoke 后提交。

## Task 3：产品化 UKUI 记忆安全中心

**Files:**
- Modify: `native/ukui/ukcc-memguard/memguard_ukcc_plugin.cpp`
- Modify: `native/ukui/ukcc-memguard/memguard_ukcc_plugin.h`
- Create: `native/ukui/common/memguard_json_process.h`
- Create: `native/ukui/common/memguard_json_process.cpp`
- Modify: `scripts/build_ukui_plugins.sh`
- Modify: `tests/test_openkylin_native.py`

- [ ] 增加源码契约测试：必须存在结构化状态区、刷新、服务控制、日志、诊断导出和技术详情折叠区。
- [ ] 抽取带超时、取消、JSON 解析和错误分类的 Qt 进程执行器。
- [ ] 用状态卡和表格呈现服务、审计、SDK、资源与桌面组件，不把原始 JSON 作为默认视图。
- [ ] 全部样式从 `QPalette`、系统字体、主题图标和 UKUI/Kylin Qt Widgets 获取，不硬编码颜色、圆角、阴影或字体。
- [ ] 为状态控件、按钮和技术详情设置可访问名称、键盘焦点顺序与中文换行策略。
- [ ] 增加启动、停止、重启、日志和诊断操作；重复点击期间禁用对应操作。
- [ ] 在 openKylin 构建并通过 `ldd`、插件发现和人工可见性 smoke 后提交。
- [ ] 在默认主题、深色主题和 150% 缩放下核验文字裁切、控件重叠、焦点顺序和颜色可读性。

## Task 4：完成 Polkit 桌面授权闭环

**Files:**
- Modify: `packaging/polkit/org.memguardos.policy`
- Modify: `scripts/memguardos-native-helper.sh`
- Create: `memguard/os_guard/privileged_actions.py`
- Modify: `memguard/cli/memctl.py`
- Modify: `tests/test_openkylin_native.py`

- [ ] 增加失败测试：helper 拒绝未知动作、额外参数、危险环境变量和非绝对项目根目录。
- [ ] 为服务启动、停止、重启和审计规则安装定义独立 action，固定 helper 路径与参数。
- [ ] 实现 `memctl native-action`，仅输出可审计结构化结果；桌面层使用 `pkexec` 调用。
- [ ] 在维护模式安装 policy/helper，验证授权成功、取消授权和未知动作拒绝。
- [ ] 采集 journal/审计证据后提交。

## Task 5：Kylin SDK 自动能力协商与缓存

**Files:**
- Modify: `memguard/retrieval/hybrid.py`
- Modify: `memguard/retrieval/kylin_sdk.py`
- Modify: `memguard/service.py`
- Modify: `scripts/memguard-kylin-warmup.service`
- Modify: `tests/test_core.py`

- [ ] 增加失败测试：openKylin 默认为 `auto`，其他平台默认为 `hash`，显式环境配置始终优先。
- [ ] 增加失败测试：能力缓存有 TTL；推理失败时单次降级并记录 code、时间和原因；恢复后可重新启用。
- [ ] 实现后端选择策略和向量缓存，缓存键包含模型、维度和规范化文本摘要。
- [ ] 状态接口区分“已安装、推理就绪、当前使用、已降级”，控制中心消费真实状态。
- [ ] 在 VM 分别验证 Kylin 成功路径和人为不可用时的 fallback 路径后提交。

## Task 6：拆分 Debian 包并验证生命周期

**Files:**
- Create: `packaging/debian/packages.json`
- Create: `scripts/build_deb_packages.py`
- Modify: `scripts/build_deb.sh`
- Create: `scripts/verify_deb_lifecycle.sh`
- Modify: `tests/test_packaging.py`

- [ ] 增加失败测试：构建清单必须生成 core、ukui、agent-bridge、doc 和兼容元包，文件所有权不能冲突。
- [ ] 实现声明式分包构建，桌面包与 Agent 包严格依赖同版本 core，doc 使用 `Architecture: all`。
- [ ] 为旧单包升级加入正确的 `Breaks/Replaces`，维护脚本保持幂等。
- [ ] 验证最小 core 安装、完整安装、旧包升级、删除桌面包、重新安装和 purge 行为。
- [ ] 将五包及 SHA256 清单纳入竞赛交付包后提交。

## Task 7：实现 UKUI 托盘与事件通知

**Files:**
- Create: `native/ukui/tray/memguard_tray.cpp`
- Create: `native/ukui/tray/memguard-tray.pro`
- Create: `packaging/desktop/memguardos-tray.desktop`
- Modify: `scripts/build_ukui_plugins.sh`
- Modify: `tests/test_openkylin_native.py`

- [ ] 增加源码和打包失败测试：托盘必须具有健康、降级、异常状态，自动启动文件必须使用绝对安装命令。
- [ ] 实现低频状态轮询、状态变化去重通知和菜单操作；退出托盘不得停止服务。
- [ ] 图标只使用 UKUI/freedesktop 主题语义图标，跟随深浅主题和系统缩放，不创建自定义浮窗。
- [ ] 对不可用托盘、无通知服务和服务断开提供安静降级。
- [ ] 在 UKUI 会话验证托盘图标、菜单、一次状态通知和重连后提交。

## Task 8：最终 HEAD 实机证据与交付门禁

**Files:**
- Modify: `scripts/collect_openkylin_native_evidence.sh`
- Modify: `scripts/check_competition_ready.py`
- Modify: `scripts/package_submission.py`
- Modify: `docs/openKylin原生化适配说明.md`
- Modify: `docs/openKylin验证记录.md`
- Modify: `README.md`

- [ ] 增加失败测试：证据版本为 `unknown` 或 `synchronized_tree_without_git_metadata` 时 readiness 必须失败。
- [ ] 要求采集器接受经校验的 `MEMGUARD_SOURCE_REVISION`，记录 dirty 状态、包清单和插件哈希。
- [ ] 在当前 HEAD 同步 VM，安装五包并运行完整 Python/Qt/桌面/SDK/Polkit/cgroup 流程。
- [ ] 刷新 manifest 与 SHA256SUMS，复制回仓库并校验每个文件。
- [ ] 更新中文文档，只描述有证据的能力和边界。
- [ ] 运行 `compileall`、`ruff`、全量 `pytest`、文档链接、readiness、Debian 生命周期、提交包审计和 archive smoke。
- [ ] 提交、推送并确认 GitHub Actions 3 个 Python 版本通过。

## 完成定义

八个任务的测试、构建、实机证据和文档必须同时完成。只完成 Python 后端、只构建 `.so`、只出现菜单入口或只通过 readiness 均不构成产品级完成。
