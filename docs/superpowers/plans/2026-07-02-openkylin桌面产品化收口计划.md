# openKylin 桌面产品化收口计划

本计划用于把 MemGuardOS 的 openKylin 原生能力从“可构建、可运行、可证明”进一步收口为“普通桌面用户和管理员可理解、可操作、可复验”的产品级体验。目标不是增加花哨界面，而是在 UKUI/Peony 的系统一致性、管理员授权、安全反馈和证据门禁上补齐最后一层可信闭环。

## 1. 设计原则

1. 使用 Qt/UKUI 原生控件、系统图标、系统字体和系统调色板，不使用自绘 Web 卡片、硬编码品牌皮肤或大面积装饰色。
2. 面向普通桌面用户时显示“状态、建议、结果、下一步”，面向管理员时保留可展开的技术详情。
3. 所有高权限操作都通过固定 action、Polkit policy 和白名单 helper 完成，不允许任意命令透传。
4. 所有桌面入口都必须有失败解释、超时处理和可截图证据。
5. 证据脚本必须记录真实安装状态和安全拒绝路径，避免只证明源码存在。

## 2. 本轮补强范围

### 2.1 UKUI 控制中心

控制中心在已有总览、服务与安全、诊断三个页签基础上补充：

- 操作结果摘要：最近一次刷新、授权操作、诊断导出或日志查看的结果用短句呈现。
- 操作历史：以列表记录最近操作、状态、耗时和说明，帮助用户理解刚刚发生了什么。
- 资源快照：显示 `memguardd.service` 的 cgroup、内存当前值、内存上限、CPU 配额和 CPU 使用计数。
- 授权说明：在服务启动、停止、重启、审计规则安装和 BPF 验证区域明确说明会触发管理员授权窗口。

### 2.2 Polkit 实机证据

原生证据采集脚本需要主动安装或复验：

- `/usr/share/polkit-1/actions/org.memguardos.policy`
- `/usr/lib/memguardos/memguardos-native-helper.sh`

采集输出必须包含：

- `policy_installed=true`
- `helper_installed=true`
- `unknown_action_rejected=true`
- action 列表

如果当前环境没有 sudo 权限，脚本要明确输出失败原因，并由 readiness 门禁报告为未满足，而不是静默通过。

### 2.3 readiness 门禁

`check_competition_ready.py` 继续保持正式模式 P0 严格检查。新增对 `polkit_assets.txt`、`tray_assets.txt`、`deb_lifecycle.txt` 和 Peony 批量 JSON 的内容级检查，避免只检查文件存在。

## 3. 验证计划

1. 先写测试，确认当前实现不能通过新增产品化红线。
2. 修改 UKUI 控制中心源码和证据采集脚本。
3. 扩展 readiness 内容级检查。
4. 跑 `tests/test_openkylin_native.py`、`tests/test_packaging.py`、ruff、全量 pytest。
5. 同步 openKylin VM，按当前 Git HEAD 重新采集原生证据。
6. 跑正式 readiness、提交包审计和 GitHub CI。

## 4. 完成标准

- UKUI 控制中心源码中存在操作历史、资源快照、授权说明和结果摘要，并保持原生 Qt 控件。
- Peony 仍保持预览、确认、敏感阻断、manifest 防篡改和审计闭环。
- Polkit policy/helper 在 openKylin 证据中显示已安装，未知 action 被拒绝。
- 五包 `.deb` 生命周期、托盘、桌面插件、cgroup、SDK、pytest 均进入原生证据包。
- readiness、pytest、提交包 smoke 和 GitHub CI 全部通过。
