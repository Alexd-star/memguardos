# UKUI 轻量联动说明

本文记录 MemGuardOS 当前对 openKylin/UKUI 桌面环境的低风险联动方式。

## 1. 设计定位

赛题鼓励与 openKylin UKUI 桌面环境联动。当前项目不宣称已经实现 Peony 文件管理器插件、控制中心插件或完整 UKUI 深度插件，而是补充一个稳定、可演示、可复现的轻量联动能力：

- 通过 freedesktop 桌面通知接口发送 MemGuardOS 安全提示；
- 在敏感记忆阻断、精准遗忘或审计异常场景中，可用于提醒用户查看审计链；
- 不影响主功能、测试和性能指标；
- 在 SSH 环境下也可通过 `/run/user/<uid>/bus` 指定会话 DBus。

## 2. 使用命令

```bash
bash scripts/ukui_notify_smoke.sh \
  "MemGuardOS 敏感记忆阻断" \
  "检测到高风险凭据，内容未写入长期记忆。"
```

成功时输出：

```text
ukui notification sent via notify-send
```

## 3. openKylin 验证命令

```bash
DBUS_SESSION_BUS_ADDRESS=unix:path=/run/user/1000/bus \
DISPLAY=:0 \
notify-send -a MemGuardOS \
  "MemGuardOS UKUI联动测试" \
  "敏感记忆阻断通知 smoke test"
```

当前 openKylin 虚拟机验证结果：

```text
notify_ok
```

## 4. 诚实边界

当前能力属于“UKUI 轻量联动”，不能夸大为“UKUI 深度联动”或“桌面环境插件”。更强的后续方向包括：

1. Peony 文件管理器右键菜单：对选中文件执行“加入/遗忘记忆”动作。
2. UKUI 控制中心面板：展示记忆数量、敏感阻断次数和审计状态。
3. 常驻托盘入口：显示 MemGuardOS daemon 状态和最近安全事件。
