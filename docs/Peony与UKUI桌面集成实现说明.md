# Peony 与 UKUI 桌面集成实现说明

本文说明 MemGuardOS 如何在 openKylin 桌面环境中形成可见、可用、可验证的原生集成。重点覆盖 Peony 文件管理器右键菜单、UKUI 控制中心、UKUI 托盘、Polkit 授权、只读 `/usr` 环境处理和最终证据采集。本文只描述当前代码已经实现的能力，不把可选增强夸大为 openKylin 官方内置组件。

## 1. 设计目标

MemGuardOS 的核心能力是 Agent 记忆提取、检索增强、精准遗忘和审计治理。桌面集成不是为了替代核心 CLI、daemon 或 Unix socket API，而是让 openKylin 用户可以用熟悉的桌面方式完成常见操作。

桌面侧有三个目标：

1. **降低入口门槛**：用户可以从 Peony 右键菜单选择文件，也可以从 UKUI 控制中心查看服务、SDK、审计和资源状态。
2. **避免误导和误操作**：文件导入必须先预览、再确认；敏感文件默认阻断；服务启停和 auditd/BPF 操作必须走受控授权。
3. **保持 openKylin 原生风格**：插件使用 Qt Widgets、系统字体、系统调色板和主题图标，不使用网页式卡片、渐变、硬编码颜色或自定义浮窗。

这套设计与赛题的最新 Q&A 一致：系统不需要自己实现一个完整 OS Agent，但需要接入现有 Agent，并提取用户与 Agent 交互中的偏好和习惯。因此 Peony/UKUI 入口服务的是用户主动选择的 Agent 相关资料、日志、任务说明和系统状态治理，而不是后台采集所有桌面行为。

## 2. 文件结构

| 路径 | 作用 |
|---|---|
| `native/ukui/peony-memguard/` | Peony 文件管理器右键插件源码 |
| `native/ukui/peony-memguard/memguard_peony_plugin.cpp` | 菜单动作、多文件 URI 解析、预览对话框、确认导入和结果提示 |
| `native/ukui/peony-memguard/common.json` | Peony Qt 插件元数据，包含与系统插件接口匹配的 `version` |
| `native/ukui/ukcc-memguard/` | UKUI 控制中心“MemGuardOS 记忆安全”插件源码 |
| `native/ukui/tray/` | UKUI 系统托盘源码和 `.pro` 构建文件 |
| `packaging/desktop/memguardos-tray.desktop` | 托盘自动启动入口，仅在 UKUI 会话启用 |
| `packaging/polkit/org.memguardos.policy` | Polkit 授权策略 |
| `scripts/memguardos-native-helper.sh` | 受控高权限 helper，只允许白名单动作 |
| `memguard/desktop/peony_scan.py` | 单文件扫描、敏感检测、导入、审计、批量 manifest 生成与确认执行 |
| `memguard/desktop/scan_manifest.py` | 批量扫描 manifest 的签名、过期、文件指纹、nonce 和防重放校验 |
| `memguard/os_guard/openkylin_native.py` | openKylin 状态总览，供 CLI、UKUI 控制中心和证据采集复用 |
| `memguard/os_guard/service_control.py` | systemd 服务级 cgroup 状态读取 |
| `memguard/os_guard/privileged_actions.py` | Polkit/native action 白名单定义和执行入口 |
| `scripts/build_ukui_plugins.sh` | 构建 Peony、UKUI 控制中心和托盘 |
| `scripts/install_ukui_plugins.sh` | 在可写系统目录或只读 `/usr` 演示路径安装插件 |
| `scripts/build_deb_packages.py` | 构建五个职责独立的 Debian 包 |
| `scripts/verify_deb_lifecycle.sh` | 验证分包离线安装、移除、重装和用户数据保护 |
| `scripts/collect_openkylin_native_evidence.sh` | 采集 openKylin 原生化证据 |

## 3. Peony 右键菜单：从“触发按钮”到“确认闭环”

早期桌面入口只做到了右键菜单可见并调用单文件 `scan-file --ingest`。当前实现已经升级为更接近产品的两阶段闭环。

第一阶段是预览。用户在 Peony 中选择一个或多个文件，右键点击 MemGuardOS 菜单后，插件会调用：

```bash
memctl desktop scan-files --file <path1> --file <path2>
```

该命令不会立刻写入长期记忆，而是生成预览 manifest。manifest 包含文件路径、大小、类型、内容摘要、敏感标签、建议动作、是否可勾选、文件指纹、过期时间和签名。目录、大文件、二进制文件和敏感文件默认不可勾选或只进入审计事件，不会被直接读入长期记忆。

第二阶段是确认。Peony 插件用 Qt 原生对话框显示预览表格，用户明确勾选后才调用：

```bash
memctl desktop apply-scan --manifest <manifest_path> --select <path>
```

`apply-scan` 会校验 manifest 的 HMAC 签名、nonce、过期时间、文件指纹和是否已经使用。只有预览过且用户确认的安全文件才会进入导入流程。导入结果会写入审计链，返回成功、跳过、阻断和失败统计。

这个设计解决了三个风险：

- 防止右键误点后直接把敏感文件写入长期记忆。
- 防止预览和执行之间文件被替换。
- 防止旧 manifest 被重复利用或篡改。

## 4. Peony 插件源码实现要点

Peony 菜单扩展通过 `Peony::MenuPluginInterface` 实现。MemGuardOS 插件继承 `QObject` 和 `Peony::MenuPluginInterface`，并通过 Qt 插件宏加载 `common.json`：

```cpp
class MemGuardPeonyPlugin : public QObject, public Peony::MenuPluginInterface
{
    Q_OBJECT
    Q_PLUGIN_METADATA(IID MenuPluginInterface_iid FILE "common.json")
    Q_INTERFACES(Peony::MenuPluginInterface)
};
```

插件在 `DirectoryView`、`DesktopWindow` 或存在选中文件 URI 时返回菜单项。这样既支持普通文件管理器窗口，也支持桌面文件右键。菜单文本使用自然中文，不包含“赛题演示”或“测试按钮”等不适合产品交付的字样。

点击菜单后，插件会把 `file://` URI 转为本地路径，批量传给 `memctl desktop scan-files`。预览结果通过 `QDialog`、`QTreeWidget`、`QDialogButtonBox` 等原生组件展示，按钮使用系统标准按钮语义。界面不使用硬编码背景色和网页式卡片，确保在 openKylin 默认主题、深色主题和高缩放环境中尽量保持一致。

## 5. UKUI 控制中心：记忆安全中心

UKUI 控制中心插件的定位是“MemGuardOS 记忆安全中心”。它不重复实现后端逻辑，而是消费 `memctl openkylin-status`、`memctl sdk-status`、`memctl verify-audit` 和 `memctl native-action` 等稳定命令。

页面包括：

- **日常使用**：面向普通桌面用户，展示服务状态、文件扫描入口、本地控制台入口、遗忘入口、资源快照和处理建议。
- **管理员**：启动、停止、重启 `memguardd.service`，查看服务日志，检查 systemd/cgroup 状态、Kylin embedding/vector SDK、审计链、Polkit 和 BPF LSM 可选验证。
- **端侧智能**：管理员页可检查 Kylin embedding/vector SDK，执行 SDK 预热，显示当前是否使用 fallback；日常页只显示用户能理解的可用、未预热或需处理状态。
- **安全审计**：校验审计链，安装 auditd 规则，运行 BPF LSM 安全验证。
- **诊断**：导出诊断报告，打开本地 Web 控制台，查看技术详情。

控制中心使用 `QTabWidget`、`QTreeWidget`、`QGroupBox`、`QPushButton`、`QTextEdit` 等系统控件，遵循 openKylin/UKUI 的视觉习惯。状态呈现以红黄绿语义为主，但颜色由系统调色板和主题控制，不把自定义 UI 风格强行覆盖到系统设置里。

## 6. UKUI 托盘

托盘程序位于 `native/ukui/tray/`。它的作用是提供低干扰状态提醒，而不是做一个悬浮控制台。托盘启动后会周期性调用 `memctl openkylin-status`，判断服务、SDK 和审计是否处于正常状态。首次启动保持安静，只有状态变化时才发送通知，避免持续弹窗影响用户。

托盘菜单包含：

- 【打开记忆安全中心】
- 【打开本地控制台】
- 【重新检查状态】
- 【退出托盘】

退出托盘只关闭托盘进程，不停止 `memguardd.service`。托盘图标优先使用 freedesktop/UKUI 主题图标，缺失时使用 Qt 标准图标降级。

## 7. Polkit 授权边界

UKUI 控制中心里的部分操作需要管理员权限，例如启动/停止系统服务、刷新 systemd、安装 auditd 规则和运行 BPF 安全验证。项目提供 `packaging/polkit/org.memguardos.policy` 和 `scripts/memguardos-native-helper.sh`。

helper 的安全约束包括：

1. 只接受一个动作参数。
2. 只允许白名单动作，不接受任意 shell 命令。
3. 固定 PATH 为系统安全路径。
4. 清理 `PYTHONPATH`、`LD_PRELOAD`、`LD_LIBRARY_PATH` 等高风险环境变量。
5. 固定项目根为 `/opt/memguardos`。
6. 未知动作必须返回失败。

当前白名单动作为 `start-service`、`stop-service`、`restart-service`、`refresh-services`、`install-auditd-rules` 和 `run-bpf-lsm-test`。是否出现图形授权弹窗取决于 openKylin 桌面会话中的 Polkit agent；命令行和维护模式下可以通过 `sudo` 复验同一条路径。

## 8. 构建、安装与验证

构建桌面组件：

```bash
bash scripts/build_ukui_plugins.sh
```

构建成功后应看到 Peony 插件、UKUI 控制中心插件和托盘可执行文件：

```text
native/ukui/build/peony-memguard/libpeony-menu-plugin-memguardos.so
native/ukui/build/ukcc-memguard/libmemguardos-ukcc-plugin.so
native/ukui/build/tray/memguardos-tray
```

如果 openKylin 的 `/usr` 可写，可以通过安装脚本放入系统插件目录。如果 `/usr` 是只读 ostree/overlay 布局，脚本会使用 `/var/tmp/memguardos-ukui-visible/` 和 bind mount 做演示安装。该方式适合现场可见性验证；正式安装建议使用维护模式或系统允许写入软件包的环境。

分包构建：

```bash
bash scripts/build_deb.sh
```

分包离线生命周期验证：

```bash
bash scripts/verify_deb_lifecycle.sh
```

该验证会检查五包数量、依赖、文件冲突、离线解包、移除桌面包后核心能力保留、重装桌面包和用户数据保护。

## 9. 自动证据与人工截图

自动证据采集：

```bash
bash scripts/collect_openkylin_native_evidence.sh
```

该脚本会采集：

- 当前 Git HEAD 或显式 `MEMGUARD_SOURCE_REVISION`。
- `openkylin-status` 和 SDK 真实探针。
- Peony 批量预览、确认导入和敏感阻断 JSON。
- Peony、UKUI 控制中心、托盘构建产物和 `ldd` 依赖。
- Polkit policy 解析和未知动作拒绝证据。
- `memguardd.service` 的服务级 cgroup 状态。
- 五个 Debian 包构建和生命周期验证。
- openKylin 上的 pytest 结果。
- `manifest.json` 和 `SHA256SUMS.txt`。

人工截图仍然有价值。最终视频或截图建议覆盖：

1. Peony 右键菜单出现 MemGuardOS 入口。
2. Peony 预览对话框显示可导入文件与敏感阻断文件。
3. UKUI 控制中心“MemGuardOS 记忆安全”页面。
4. 托盘菜单和状态变化通知。
5. `openkylin-status` 输出中 service、SDK、desktop、security 状态。
6. 分包 `apt install` 或离线生命周期验证结果。

## 10. 准确表述边界

可以说：

- MemGuardOS 已经具备 Peony 多文件预览确认闭环、UKUI 控制中心原生运维页、UKUI 托盘、Polkit 授权资产和 Debian 分包交付。
- Peony 文件入口会阻断敏感内容，并通过 manifest 防篡改和防重放。
- UKUI 控制中心和托盘复用同一套 CLI/JSON 契约，不是孤立 demo。
- 桌面 UI 遵循 openKylin/UKUI 原生控件和主题，不采用网页仪表盘式风格。

不能说：

- 不能说已经进入 openKylin 官方控制中心或软件源。
- 不能说 Peony 会自动采集用户所有桌面行为。
- 不能说 Polkit 弹窗在无桌面 agent 或维护模式中必然出现。
- 不能说分包已经替代所有历史单包证据；新版五包的完整 `apt install` 证据仍需在最终维护模式下按当前 HEAD 复跑。
