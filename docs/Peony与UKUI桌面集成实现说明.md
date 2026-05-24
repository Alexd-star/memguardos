# Peony 与 UKUI 桌面集成实现说明

本文说明 MemGuardOS 如何实现 openKylin 桌面 UI 集成，重点覆盖 Peony 文件管理器右键菜单、UKUI 控制中心插件原型、只读 `/usr` 环境下的安装处理、插件不显示问题的定位过程，以及最终可见性验证证据。

## 1. 设计目标

MemGuardOS 的默认主链路是 CLI、daemon、Unix socket API 和本地 Web 控制台。为了进一步体现 openKylin 桌面生态适配，项目补充了 UKUI 原生插件能力：

1. 在 Peony 文件管理器右键菜单中增加“发送到 MemGuardOS 记忆安全扫描”动作。
2. 用户右键选中文件后，可以把文件路径转换为统一 OS event，写入 MemGuardOS 记忆系统。
3. OS event 进入既有敏感检测、审计链和检索链路，复用核心安全能力。
4. 提供 UKUI 控制中心插件入口，用于展示本机运行状态、审计完整性、端侧向量能力、诊断报告和本地控制台入口。
5. 在 openKylin 只读 `/usr` 的 ostree/overlay 环境中，提供可复现的演示安装方法。

这个设计避免把桌面联动做成单纯的 `notify-send` 脚本，而是接入 openKylin 的真实插件接口。

## 2. 文件结构

相关源码和脚本如下：

| 路径 | 作用 |
|---|---|
| `native/ukui/peony-memguard/` | Peony 文件管理器右键菜单插件源码 |
| `native/ukui/peony-memguard/memguard_peony_plugin.cpp` | 右键菜单动作、事件转换和命令触发实现 |
| `native/ukui/peony-memguard/memguard_peony_plugin.h` | `Peony::MenuPluginInterface` 插件类声明 |
| `native/ukui/peony-memguard/common.json` | Peony Qt 插件元数据，包含 `version: 3.2.2` |
| `native/ukui/ukcc-memguard/` | UKUI 控制中心插件原型源码 |
| `native/ukui/ukcc-memguard/ukcc-memguard.json` | UKCC 插件元数据，包含 `version: 3.2.2` |
| `scripts/build_ukui_plugins.sh` | 构建 Peony/UKCC 两个 `.so` |
| `scripts/install_ukui_plugins.sh` | 安装插件，支持只读 `/usr` 下的 bind mount 演示安装 |
| `scripts/build_deb.sh` | 生成 MemGuardOS Debian 安装包，并在依赖存在时把 Peony/UKUI 插件打入系统插件目录 |
| `scripts/probe_ukui_deep_plugins.sh` | 探测开发包、插件目录和系统环境 |
| `scripts/verify_ukui_plugins.sh` | 检查构建产物、安装产物、动态依赖、关键字符串和人工可见性步骤 |

## 3. Peony 右键菜单插件实现

Peony 文件管理器的菜单扩展通过 `Peony::MenuPluginInterface` 实现。MemGuardOS 插件类继承 `QObject` 和 `Peony::MenuPluginInterface`，并通过 Qt 插件宏声明元数据：

```cpp
class MemGuardPeonyPlugin : public QObject, public Peony::MenuPluginInterface
{
    Q_OBJECT
    Q_PLUGIN_METADATA(IID MenuPluginInterface_iid FILE "common.json")
    Q_INTERFACES(Peony::MenuPluginInterface)
};
```

插件类型返回 `Peony::PluginInterface::MenuPlugin`，表示这是菜单扩展：

```cpp
Peony::PluginInterface::PluginType MemGuardPeonyPlugin::pluginType()
{
    return Peony::PluginInterface::MenuPlugin;
}
```

核心逻辑位于 `menuActions(...)`。Peony 在构建右键菜单时会传入菜单类型、当前 URI 和选中文件 URI 列表，插件根据这些信息返回一个 `QAction`：

```cpp
QList<QAction *> MemGuardPeonyPlugin::menuActions(
    Peony::MenuPluginInterface::Types types,
    const QString &uri,
    const QStringList &selectionUris)
```

最初实现只判断 `DirectoryView`，但桌面文件右键在当前 openKylin/Peony 环境中可能以 `DesktopWindow` 类型传入，因此最终实现支持三类条件：

1. `DirectoryView`：文件管理器目录视图右键。
2. `DesktopWindow`：桌面文件或桌面区域右键。
3. `selectionUris` 非空：只要存在选中文件，即可返回动作。

最终返回的菜单文本为：

```text
发送到 MemGuardOS 记忆安全扫描
```

该动作的 `objectName` 为：

```text
memguardos-peony-memory-scan
```

## 4. 点击菜单后的事件链路

用户点击菜单后，插件执行 `sendSelectionToMemGuard()`。

该函数首先选择目标路径：

1. 如果存在 `selectionUris`，使用第一个选中文件。
2. 否则使用当前目录或菜单 URI。
3. 如果是 `file://` 本地文件 URI，则转换为本地路径。

之后构造统一 OS event：

```json
{
  "event_type": "peony_context_menu",
  "path": "/home/<用户>/桌面/memguardos-ui-test.txt",
  "process": "peony",
  "action": "inspect"
}
```

插件通过本地命令调用 MemGuardOS：

```bash
python3 -m memguard.cli.memctl ingest-os-event --json '<event-json>'
```

因此 Peony 插件不是只显示 UI，而是接入项目已有 OS 事件处理链路：

```text
Peony 右键菜单
  -> peony_context_menu OS event
  -> memctl ingest-os-event
  -> OS 路径解析
  -> 敏感检测
  -> 记忆写入/阻断
  -> SQLite 审计哈希链
  -> 后续检索、RAG 上下文和精准遗忘
```

插件还会调用：

```bash
notify-send MemGuardOS 已将 Peony 选中文件发送到记忆安全扫描
```

用于桌面提示。

## 5. 元数据 version 的关键适配

Peony 使用 Qt 插件元数据识别和管理插件。调试过程中发现，系统自带 Peony 菜单插件的元数据都包含：

```json
"MetaData": {
  "version": "3.2.2"
}
```

而最初的 `common.json` 只有 `Keys`、`Name`、`Description`，单独用 `QPluginLoader` 可以加载，但 Peony 插件管理器可能因为版本字段缺失而过滤该插件。通过在 openKylin 虚拟机中执行：

```bash
pkg-config --modversion peony
```

得到当前 Peony 插件接口版本：

```text
3.2.2
```

因此最终 `native/ukui/peony-memguard/common.json` 写为：

```json
{
  "version": "3.2.2",
  "Keys": [
    "memguardos-peony-menu-plugin"
  ],
  "Name": "MemGuardOS Peony Menu Plugin",
  "Description": "Send selected files to MemGuardOS as OS-agent memory events."
}
```

UKUI 控制中心插件元数据也同步加入 `version: 3.2.2`，保持与系统插件风格一致。

这个问题的定位结论是：

1. `.so` 文件本身可被 `QPluginLoader` 加载。
2. `menuActions(...)` 单独调用能返回菜单项。
3. Peony UI 不显示时，根因更可能在 Peony 插件管理器注册阶段。
4. 补齐 `version` 元数据后，Peony 右键菜单可见。

## 6. openKylin 只读 `/usr` 的安装处理

当前 openKylin 虚拟机使用 ostree/overlay 布局，`/usr` 挂载为只读：

```text
/dev/sda5 on /usr type ext4 (ro,relatime)
overlay on /usr type overlay (ro,relatime,...)
```

因此直接执行：

```bash
sudo install -m 755 libpeony-menu-plugin-memguardos.so /usr/lib/x86_64-linux-gnu/peony-extensions/
```

会返回：

```text
只读文件系统
```

为兼顾系统安全和比赛演示，`scripts/install_ukui_plugins.sh` 实现了两级安装策略：

1. 如果目标插件目录可写，直接安装到系统目录。
2. 如果目标插件目录不可写，复制原插件目录到 `/var/tmp/memguardos-ukui-visible/`，把 MemGuardOS 插件放入该目录，再使用 bind mount 挂载回系统插件目录。

关键逻辑如下：

```bash
BIND_BASE="${MEMGUARDOS_UKUI_BIND_BASE:-/var/tmp/memguardos-ukui-visible}"
sudo cp -a "${dest_dir}/." "${bind_dir}/" 2>/dev/null || true
sudo install -m 755 "${plugin}" "${bind_dir}/${dest_name}"
sudo mount --bind "${bind_dir}" "${dest_dir}"
sudo install -m 755 "${plugin}" "${dest_dir}/${dest_name}"
```

这种方式不会修改只读系统镜像，适合评审现场演示。为了重启后自动恢复挂载，虚拟机中还增加了 `memguardos-ukui-bind.service`：

```text
/etc/systemd/system/memguardos-ukui-bind.service
```

该服务开机后执行：

```bash
/bin/bash /home/<用户>/memguardos/scripts/install_ukui_plugins.sh --no-build
```

当前服务状态：

```text
enabled
active
```

## 7. 构建与安装步骤

在 openKylin 中执行：

```bash
cd ~/memguardos
source .venv/bin/activate
bash scripts/probe_ukui_deep_plugins.sh docs/evidence/latest
bash scripts/build_ukui_plugins.sh
bash scripts/install_ukui_plugins.sh --no-build
```

构建结果：

```text
native/ukui/build/peony-memguard/libpeony-menu-plugin-memguardos.so
native/ukui/build/ukcc-memguard/libmemguardos-ukcc-plugin.so
```

安装后目标路径：

```text
/usr/lib/x86_64-linux-gnu/peony-extensions/libpeony-menu-plugin-memguardos.so
/usr/lib/x86_64-linux-gnu/ukui-control-center/libmemguardos-ukcc-plugin.so
```

重启 Peony：

```bash
export DISPLAY=:0
export DBUS_SESSION_BUS_ADDRESS=unix:path=/run/user/1000/bus
pkill -x peony 2>/dev/null || true
pkill -x peony-qt-desktop 2>/dev/null || true
nohup peony-qt-desktop -w -d >/tmp/peony_desktop_memguardos.log 2>&1 &
nohup peony "$(xdg-user-dir DESKTOP)" >/tmp/peony_window_memguardos.log 2>&1 &
```

## 8. 验证方法

### 8.1 插件产物验证

执行：

```bash
bash scripts/verify_ukui_plugins.sh
```

检查内容包括：

1. 构建产物是否存在。
2. 系统安装产物是否存在。
3. `file` 是否识别为 ELF shared object。
4. `ldd` 是否存在 `not found`。
5. `strings` 是否包含 `MemGuardOS`、`memguardos`、`发送到`、`记忆安全` 等关键字符串。

### 8.2 Qt 插件元数据验证

调试时使用 `QPluginLoader` 输出元数据，确认安装后的 Peony 插件包含：

```json
{
  "IID": "org.ukui.peony-qt.plugin-iface.MenuPluginInterface",
  "MetaData": {
    "Description": "Send selected files to MemGuardOS as OS-agent memory events.",
    "Keys": ["memguardos-peony-menu-plugin"],
    "Name": "MemGuardOS Peony Menu Plugin",
    "version": "3.2.2"
  },
  "className": "MemGuardPeonyPlugin"
}
```

### 8.3 菜单函数验证

通过小型 Qt/Peony 探针直接实例化插件并调用 `menuActions(...)`，验证：

```text
DirectoryView actions 1 "发送到 MemGuardOS 记忆安全扫描"
DesktopWindow actions 1 "发送到 MemGuardOS 记忆安全扫描"
```

这说明插件逻辑对文件管理器目录视图和桌面文件右键都能返回菜单项。

### 8.4 桌面 UI 可见验证

人工验证步骤：

1. 打开 openKylin 桌面。
2. 打开 Peony 文件管理器或直接使用桌面文件。
3. 右键文件 `memguardos-ui-test.txt`。
4. 确认菜单中出现：

```text
发送到 MemGuardOS 记忆安全扫描
```

当前已经由人工截图确认该菜单项在 openKylin 桌面中可见，证据记录为：

```text
docs/evidence/latest/ukui_peony_visible_manual_confirmation.txt
docs/evidence/latest/ukui_plugin_visible_final.txt
```

其中确认文件记录：

```text
menu_text=发送到 MemGuardOS 记忆安全扫描
status=visible_in_user_screenshot
```

### 8.5 UKUI 控制中心可见验证

打开 UKUI 设置/控制中心后，左侧“安全”分类下已经出现：

```text
MemGuardOS ...
```

右侧页面标题和说明为：

```text
MemGuardOS 记忆安全
管理本机 Agent 记忆服务的运行状态、审计完整性、端侧向量能力和诊断信息。
```

页面提供面向本机运维的常用操作：

```text
运行环境检查
校验审计链
检查端侧向量能力
导出诊断报告
打开本地控制台
```

当前已经由人工截图确认 UKUI 控制中心入口可见；最新版本已将原始命令按钮升级为本机运维面板。历史可见性证据记录为：

```text
docs/evidence/latest/ukui_control_center_visible_manual_confirmation.txt
docs/evidence/latest/ukui_plugin_visible_final.txt
```

其中确认文件记录：

```text
entry_text=MemGuardOS 记忆安全
buttons=运行 doctor,验证审计链,检查 Kylin SDK
status=visible_in_user_screenshot
```

新版本的按钮文本更接近企业交付语义：用户看到的是“运行环境检查”和“端侧向量能力”，而不是底层命令名；“导出诊断报告”会调用 `memctl diagnostics --probe-sdk` 汇总 doctor、namespace、检索状态、记忆策略、Kylin SDK 和审计链结果；“打开本地控制台”会启动并打开 `http://127.0.0.1:8765`，用于本机管理员查看记忆、检索、RAG 上下文、精准遗忘、审计记录和系统诊断。

## 9. 点击后的后端验证

为了确保“菜单可见”不是空按钮，还验证了系统 Python 可直接导入 MemGuardOS：

```bash
python3 -c "import memguard; print(memguard.__file__)"
```

在系统 Python 不知道项目路径时，通过用户 site-packages 中的 `.pth` 文件把项目根目录加入模块搜索路径：

```text
~/.local/lib/python3.12/site-packages/memguardos_editable.pth
```

内容：

```text
/home/<用户>/memguardos
```

之后执行：

```bash
python3 -m memguard.cli.memctl ingest-os-event --json '{"event_type":"peony_context_menu_probe","path":"/home/<用户>/桌面/memguardos-ui-test.txt","process":"peony","action":"inspect"}'
```

返回：

```json
{
  "stored": true,
  "record": {
    "type": "knowledge",
    "subtype": "os_event"
  }
}
```

这说明 Peony 菜单动作背后的 OS event 写入链路是可用的。

## 10. 当前边界

当前可以准确宣称：

1. Peony 右键菜单插件源码已实现。
2. Peony 插件已在 openKylin 上构建为 `.so`。
3. Peony 插件安装产物已出现在系统插件目录。
4. Peony 插件元数据包含与系统一致的 `version: 3.2.2`。
5. Peony 菜单函数对 `DirectoryView` 和 `DesktopWindow` 均返回菜单项。
6. Peony 右键菜单项已在 openKylin 桌面可见验证。
7. UKUI 控制中心入口已在 openKylin 设置界面可见验证；当前源码提供本机运维面板，覆盖运行环境检查、审计链校验、端侧向量能力、诊断报告和本地控制台入口。
8. 菜单点击后的 OS event 写入链路已验证。

仍需准确表述的边界：

1. bind mount 安装方式用于 openKylin 只读 `/usr` 环境下的评审演示和本地验证，不等同于正式 `.deb` 包安装。
2. 项目已经提供 `scripts/build_deb.sh` 用于生成 `.deb` 安装包；该包会把插件安装到系统插件目录，适合在允许系统包写入的维护模式或等效环境中验证。
3. 后续若进入长期产品化交付，还可以进一步制作 openKylin 官方扩展包或拆分 `memguardos` 与 `memguardos-ukui` 两个二进制包。

## 11. 对赛题的意义

该桌面集成增强了作品的 OS 属性和可展示性：

1. 证明系统能接入 openKylin 原生桌面组件，而不仅是命令行工具。
2. 文件右键菜单把用户桌面行为转换为统一 OS event，补强“用户行为数据接入”。
3. 选中文件路径进入敏感检测和审计链，体现 OS 上下文安全治理。
4. 与 auditd、BPF LSM、systemd/cgroup、mmap 存储形成互补，构成更完整的 Linux/openKylin 智能体记忆基础设施。
