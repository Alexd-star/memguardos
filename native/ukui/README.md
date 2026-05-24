# MemGuardOS UKUI 深度联动原型

该目录提供两个可选 UKUI 原生插件原型：

- `peony-memguard`：Peony 文件管理器右键菜单插件，把选中文件路径作为 `peony_context_menu` OS 事件发送给 `memctl ingest-os-event`，触发 MemGuardOS 的敏感路径识别与审计。
- `ukcc-memguard`：UKUI 控制中心插件入口，提供运行环境检查、审计链校验、端侧向量能力检查、诊断报告和本地控制台入口。

这两个插件需要 `libpeony-dev`、`libukcc-dev`、Qt5 开发包和 openKylin 插件安装能力。当前默认主路径仍是 CLI、daemon、Unix socket API 和本地 Web 控制台；插件属于面向 openKylin 桌面生态的原生增强。

Peony 会检查 Qt 插件元数据中的 `version` 字段。openKylin 2.0 SP2 当前 `pkg-config --modversion peony` 返回 `3.2.2`，因此 `common.json` 中显式写入 `version: 3.2.2`，避免插件能被 `QPluginLoader` 单独加载、但被 Peony 插件管理器过滤。

## 构建

```bash
bash scripts/build_ukui_plugins.sh
```

构建产物：

- `native/ukui/build/peony-memguard/libpeony-menu-plugin-memguardos.so`
- `native/ukui/build/ukcc-memguard/libmemguardos-ukcc-plugin.so`

## 安装位置

```bash
sudo install -m 755 native/ukui/build/peony-memguard/libpeony-menu-plugin-memguardos.so /usr/lib/x86_64-linux-gnu/peony-extensions/
sudo install -m 755 native/ukui/build/ukcc-memguard/libmemguardos-ukcc-plugin.so /usr/lib/x86_64-linux-gnu/ukui-control-center/
```

安装后重启 Peony 或 UKUI 控制中心再验证。插件接口来自 openKylin 系统中的 `libpeony-dev` 和 `libukcc-dev` 头文件，属于真实桌面插件路径，不是单纯通知脚本。

openKylin ostree/overlay 环境可能把 `/usr` 挂载为只读。`scripts/install_ukui_plugins.sh` 会优先尝试直接安装；如果目标目录不可写，会把系统插件目录复制到 `/var/tmp/memguardos-ukui-visible/` 并使用 bind mount 做演示安装。该方式适合评审现场验证 UI 可见性；如需重启后自动恢复，可配置 systemd oneshot 服务重新执行安装脚本。

## 可见性结果

当前 openKylin 桌面已完成人工可见性验证：

- Peony 文件右键菜单可见【发送到 MemGuardOS 记忆安全扫描】。
- UKUI 设置/控制中心可见【MemGuardOS 记忆安全】入口。
- 控制中心页面可见【运行环境检查】、【校验审计链】、【检查端侧向量能力】、【导出诊断报告】和【打开本地控制台】操作。

详细实现、根因分析和证据索引见 `docs/Peony与UKUI桌面集成实现说明.md`。
