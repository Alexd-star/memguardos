# MemGuardOS UKUI 深度联动原型

该目录提供两个可选 UKUI 原生插件原型：

- `peony-memguard`：Peony 文件管理器右键菜单插件，把选中文件路径作为 `peony_context_menu` OS 事件发送给 `memctl ingest-os-event`，触发 MemGuardOS 的敏感路径识别与审计。
- `ukcc-memguard`：UKUI 控制中心插件入口，展示 MemGuardOS 本地状态、审计链和 Kylin SDK 检查命令输出。

这两个插件需要 `libpeony-dev`、`libukcc-dev`、Qt5 开发包和 openKylin 维护模式安装能力。当前默认主路径仍是 CLI、daemon、Unix socket API 和 UKUI 通知 smoke；插件属于冲击 openKylin 生态加分项的可选增强。

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
