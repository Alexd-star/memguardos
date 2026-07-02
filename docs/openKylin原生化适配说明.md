# openKylin 原生化适配说明

本文说明 MemGuardOS 针对 openKylin 做了哪些系统级和桌面级适配，以及这些适配如何验证、如何使用、边界在哪里。本文面向评审、部署人员和答辩准备使用，避免把“能在 Linux 上运行”与“针对 openKylin 形成原生体验”混为一谈。

## 1. 适配目标

MemGuardOS 的核心目标是为 OS Agent 提供本地记忆提取、检索增强、精准遗忘和审计治理能力。面向 openKylin 时，项目进一步追求三个目标：

1. **像系统组件一样运行**：通过 systemd、cgroup、auditd、journald 和 `.deb` 安装包接入系统运行环境。
2. **像桌面应用一样可操作**：通过 UKUI 控制中心、Peony 文件管理器右键菜单和桌面通知降低用户操作门槛。
3. **像本机 AI 能力一样可扩展**：探测并可选使用 Kylin embedding SDK 与 vector SDK，同时保留稳定 fallback，保证没有 GPU、没有云 API 时仍可运行。

## 2. 当前已实现的 openKylin 专项能力

| 能力 | 已实现内容 | 核验入口 |
|---|---|---|
| 本机状态总览 | `memctl openkylin-status` 聚合系统、服务、SDK、桌面、审计、资源状态，并输出红黄绿摘要和修复建议 | `python -m memguard.cli.memctl openkylin-status` |
| systemd/cgroup | `memguardd.service`、`memguardd@.service`、`memguard-agent@.service`，包含资源限制和安全运行选项 | `systemctl status memguardd`、`memctl status` |
| auditd/journald | 真实 auditd 日志解析、敏感路径阻断、journald 审计写入 | `memctl auditd-rules`、`scripts/auditd_to_memguard.sh` |
| Kylin AI SDK | embedding/vector SDK 探测，embedding 可选真实推理探针，vector SDK C++ 探针 | `memctl sdk-status --probe-embedding`、`scripts/run_kylin_vector_probe.sh` |
| Kylin SDK 预热 | 登录后可通过 systemd user service 或脚本预热端侧 embedding，降低首次推理尾延迟影响 | `scripts/warmup_kylin_sdk.sh` |
| Peony 右键闭环 | 右键选中文件后调用 `memctl desktop scan-file`，生成扫描报告；安全文本可导入，敏感文本阻断，目录/大文件只审计 | `memctl desktop scan-file --file <path> --ingest` |
| UKUI 控制中心 | 原生插件入口“MemGuardOS 记忆安全”，提供本机状态、审计、SDK、诊断、本地控制台操作 | `native/ukui/ukcc-memguard/` |
| Polkit 授权资产 | 提供 `org.memguardos.policy` 和受控 helper，支持服务安装、auditd 规则、BPF LSM 测试等高权限动作的桌面授权路径 | `packaging/polkit/org.memguardos.policy` |
| .deb 安装 | `build_deb.sh` 打包核心代码、systemd 服务、user warmup service、Polkit policy、helper 和可选 UKUI/Peony 插件 | `bash scripts/build_deb.sh` |
| 原生化证据采集 | 一键采集 openKylin 本机状态、Peony 扫描、SDK 预热、systemd、Polkit、桌面插件证据 | `bash scripts/collect_openkylin_native_evidence.sh` |

## 3. 本机状态总览

`openkylin-status` 是新增的统一诊断入口，目的是避免评审或用户分别查看十几个命令。它会输出如下结构：

```bash
python -m memguard.cli.memctl openkylin-status
```

关键字段包括：

- `system`：系统版本、内核、架构、是否识别为 openKylin 类环境。
- `services`：systemd、`memguardd.service`、`kytensor.service`、`kylin-ai-vector-engine.service` 等状态。
- `sdk`：Kylin embedding SDK 与 vector SDK 的库、头文件、服务、socket、模型和 fallback 状态。
- `desktop`：Peony、UKUI 控制中心、通知工具和插件安装路径。
- `security`：auditd、BPF LSM 源码、Polkit policy 状态。
- `resources`：cgroup v2、内存、CPU 限制状态。
- `summary`：整体 `green/yellow/red` 状态和 readiness score。
- `recommendations`：面向用户的修复建议。

如果需要真实探测 embedding 推理，可执行：

```bash
python -m memguard.cli.memctl openkylin-status --probe-sdk --probe-timeout 90
```

该命令可能触发 Kylin AI runtime 冷启动，因此默认状态总览不强制探测真实推理，以免影响控制中心打开速度。

## 4. Peony 文件右键扫描闭环

Peony 插件现在调用统一命令：

```bash
python -m memguard.cli.memctl desktop scan-file --file <path> --ingest --notify
```

该命令会先生成扫描报告，再按策略处理：

1. 文本文件小于 1 MB 且无高风险敏感内容时，写入 `peony_file` 记忆，并记录 OS event。
2. `.env`、密钥、令牌、私钥等高风险内容会被识别为 `block_sensitive`，不进入长期记忆。
3. 目录、大文件、二进制文件和特殊文件不会直接读取内容，只记录用户主动选择该文件的 OS event。
4. 所有 `--ingest` 操作会验证审计链，报告中返回 `verification.audit_chain_ok`。

示例：

```bash
python -m memguard.cli.memctl desktop scan-file --file ~/桌面/note.md --ingest
```

返回报告的核心字段：

```json
{
  "schema": "memguardos-peony-scan-report-v1",
  "recommended_action": "import_text",
  "sensitivity": {"level": "none", "labels": []},
  "ingest": {"attempted": true, "stored": true},
  "audit_event": {"attempted": true, "stored": true},
  "verification": {"audit_chain_ok": true}
}
```

这个设计让 Peony 右键菜单不只是“可见按钮”，而是能产生可审计的扫描、导入、阻断和遗忘前置证据。

## 5. Kylin SDK 预热策略

Kylin embedding SDK 的真实推理在首次调用时可能存在冷启动尾延迟。MemGuardOS 因此采用两层策略：

1. 默认检索链路使用稳定的 SQLite FTS、hash vector 和图谱检索，不把 Kylin SDK 作为唯一硬依赖。
2. 在 openKylin 环境中，如果 Kylin SDK 可用，可以通过预热脚本提前触发 runtime 和模型加载。

预热命令：

```bash
bash scripts/warmup_kylin_sdk.sh
```

安装 `.deb` 后，也会提供 systemd user service：

```bash
systemctl --user enable --now memguard-kylin-warmup.service
```

预热成功时，脚本输出 `inference_ready: true`；如果 SDK 元数据可达但推理未就绪，脚本会明确建议继续使用 fallback，并提示检查 `kylin-ai-runtime`、`kytensor.service`、模型仓库和 socket。

## 6. Polkit 与高权限操作边界

项目提供 `packaging/polkit/org.memguardos.policy` 和 `scripts/memguardos-native-helper.sh`，目的是让桌面环境中的高权限操作具备受控授权路径。helper 只允许以下白名单动作：

- `install-services`
- `install-auditd-rules`
- `run-bpf-lsm-test`
- `collect-native-evidence`

它不会接受任意 shell 命令，也不会把 UI 入口变成隐式 root shell。是否出现图形授权弹窗取决于目标 openKylin 的 Polkit agent 是否运行；在命令行或维护模式下，也可以继续使用 `sudo` 方式复验。

## 7. 一键原生化证据采集

最终提交前建议在 openKylin 上执行：

```bash
cd ~/memguardos
source .venv/bin/activate
bash scripts/collect_openkylin_native_evidence.sh
```

默认输出目录：

```text
docs/evidence/openkylin_native_latest/
```

该目录包含：

- `openkylin_status.txt`
- `openkylin_status_probe_sdk.txt`
- `kylin_warmup.txt`
- `peony_scan_preview.txt`
- `peony_scan_ingest.txt`
- `peony_scan_sensitive.txt`
- `systemd_units.txt`
- `desktop_plugins.txt`
- `polkit_assets.txt`
- `manifest.json`
- `SHA256SUMS.txt`

这组证据用于证明项目已经形成 openKylin 原生化操作闭环，但不能替代人工桌面截图。Peony 菜单和 UKUI 控制中心可见性仍建议在最终演示视频中展示。

## 8. 准确边界

可以准确表述：

- 项目已经具备 openKylin 专项诊断、Kylin SDK 探测、Peony 文件右键扫描、UKUI 控制中心入口、systemd/cgroup、auditd/journald、Polkit 授权资产和 `.deb` 打包能力。
- Peony 右键只处理用户主动选择的文件，不做后台全盘扫描。
- 高风险敏感文件不会默认写入长期记忆。
- Kylin SDK 是可选增强；默认链路保留稳定 fallback。
- BPF LSM 是可选强制拦截验证路径，不是通用内核态 DLP。

不能夸大表述：

- 不能说已经进入 openKylin 官方软件源。
- 不能说 Kylin SDK 是唯一默认检索路径。
- 不能说 Polkit 弹窗在所有环境中都一定出现，因为这取决于系统 Polkit agent。
- 不能说精准遗忘覆盖 SSD 物理残留、VM 快照、OpenHands 外部日志和备份。
