# openKylin 原生化适配说明

本文说明 MemGuardOS 针对 openKylin 进行的系统级、桌面级和交付级适配。它面向评审、部署人员、答辩人员和后续维护者，重点回答三个问题：项目不是“普通 Linux 程序换到 openKylin 上运行”吗？桌面入口是否真的可用？最终证据如何证明当前代码版本与 openKylin 运行结果一致？

MemGuardOS 的定位是面向 OS Agent 的端侧记忆治理系统。面向 openKylin 时，项目不依赖云端大模型或高级 GPU，而是把 Agent 交互记忆、系统审计、Kylin AI SDK、UKUI 桌面入口、Peony 文件操作、systemd 服务、cgroup 资源控制和 Debian 包安装纳入同一条可复验链路。这样做的价值是：评委和用户既能看到算法指标，也能看到系统组件在 openKylin 上如何安装、启动、授权、诊断、降级和导出证据。

## 1. 适配目标

MemGuardOS 面向 openKylin 的适配目标分为四层。

第一层是系统服务化。项目提供 `memguardd.service`、namespace 模板服务和 Agent bridge 服务，并通过 cgroup v2 读取真实服务级资源状态。以前仅检查当前命令进程 cgroup 容易造成误判，现在 `openkylin-status` 会读取 systemd 中 `memguardd.service` 的 `ControlGroup`，再从 `/sys/fs/cgroup` 读取 `memory.current`、`memory.max`、`cpu.max` 和 `cpu.stat`，从而证明服务自身是否受控，而不是只证明诊断命令能运行。

第二层是桌面可操作性。项目提供 Peony 右键插件、UKUI 控制中心插件和 UKUI 托盘。Peony 不再是“点一下就直接写入”的演示按钮，而是先生成批量扫描预览清单，展示每个文件的风险、建议动作和是否可导入，再由用户确认后执行。UKUI 控制中心不再只是几颗测试按钮，而是按系统状态、SDK、审计、安全操作和诊断导出组织成原生运维页面。托盘用于低干扰状态提醒，不替代核心服务。

第三层是 openKylin AI 能力协同。项目会检测 Kylin embedding SDK、Kylin vector engine、`kylin-ai-runtime`、`kytensor.service`、socket、模型仓库和插件目录。在 openKylin 环境下，检索后端默认采用 `auto` 策略：SDK 可用并推理就绪时优先尝试 Kylin embedding；失败时进入有冷却时间的 fallback，不让一次冷启动或临时失败永久禁用 SDK，也不让 SDK 异常破坏核心检索能力。

第四层是可交付安装。项目已经从单一 `.deb` 演进为五个职责独立的包：`memguardos-core`、`memguardos-ukui`、`memguardos-agent-bridge`、`memguardos-doc` 和 `memguardos` 元包。这样桌面插件、Agent 适配和文档可以按需安装，卸载桌面包不会删除用户记忆，后续企业部署也更容易做最小化安装。

## 2. 当前已实现能力

| 能力 | 当前实现 | 核验入口 |
|---|---|---|
| openKylin 状态总览 | 聚合系统版本、systemd、服务级 cgroup、Kylin SDK、Peony、UKUI、Polkit、BPF、auditd、诊断建议 | `python -m memguard.cli.memctl openkylin-status` |
| 服务级资源治理 | 读取 `memguardd.service` 的真实 ControlGroup，再读取 cgroup v2 资源文件 | `systemctl show memguardd.service`、`memctl openkylin-status` |
| Kylin SDK 自适应检索 | openKylin 默认 `auto`，支持 SDK 成功路径、失败冷却、LRU 缓存、延迟预算和稳定 fallback | `memctl sdk-status --probe-embedding`、`memctl openkylin-status --probe-sdk` |
| Peony 文件右键闭环 | 支持多文件扫描、敏感阻断、预览清单、一次性签名 manifest、确认后导入、本次导入撤销和审计验证 | `memctl desktop scan-files`、`memctl desktop apply-scan`、`memctl desktop forget-import` |
| UKUI 控制中心 | 原生 Qt Widgets 页面，按系统状态、SDK、审计、安全操作、诊断导出组织，不使用网页式硬编码 UI | `native/ukui/ukcc-memguard/` |
| UKUI 托盘 | 提供健康状态轮询、状态变化通知、打开控制中心、SDK/审计定向页、本地控制台审计页和重新检查状态 | `native/ukui/tray/` |
| Polkit 授权 | 仅允许白名单动作，固定 PATH，拒绝任意命令和环境变量注入 | `memctl native-action`、`scripts/memguardos-native-helper.sh` |
| Debian 分包 | 生成核心包、桌面包、Agent 适配包、文档包和元包，并检查文件冲突和卸载边界 | `bash scripts/build_deb.sh`、`bash scripts/verify_deb_lifecycle.sh` |
| 原生化证据采集 | 当前 Git HEAD 门禁、Peony 批量证据、SDK 探针、桌面插件构建、Polkit、cgroup、分包、pytest | `bash scripts/collect_openkylin_native_evidence.sh` |

## 3. 本机状态总览

`openkylin-status` 是 openKylin 适配的统一诊断入口。它输出 JSON，既可供终端查看，也可被 UKUI 控制中心和证据脚本消费。

```bash
python -m memguard.cli.memctl openkylin-status
```

控制中心、托盘和日常状态刷新默认使用快速状态。快速状态只检查 SDK 文件、头文件、模型仓库、socket、systemd 服务和插件文件，不执行完整 embedding 推理。这样可以避免用户打开【设置】>【安全】时被端侧模型冷启动拖慢。快速状态下，如果 Kylin embedding SDK、模型和运行路径已经可检测，系统会显示“已安装，当前未预热”，并给出“首次使用可能稍慢，可点击预热”的下一步建议；这不是故障，也不会影响默认 FTS/hash/graph 稳定检索路径。

Kylin vector engine 在不同 openKylin 安装形态中可能表现为 systemd user service，也可能只暴露用户态 socket。快速状态不会只因为 `kylin-ai-vector-engine.service` 未登记就把整机状态判为异常；只要 `/tmp/kylin-ai-vector-engine-<uid>.sock` 可连接，就显示为“向量服务接口可连接”。这更符合桌面用户的实际感知：用户关心本机智能检索是否可用，管理员才需要进一步查看服务单元是否完整登记。

需要证明 Kylin embedding 真实推理可用时，应主动运行完整探针：

```bash
python -m memguard.cli.memctl openkylin-status --probe-sdk --probe-timeout 90
```

完整探针会调用 SDK 返回真实 768 维向量，适合证据采集、答辩和安装后复验；日常 UI 不默认执行它。

该命令检查的内容包括：

- `system`：系统版本、内核、架构、是否识别为 openKylin 类环境。
- `services`：`memguardd.service`、Kylin AI runtime、Kylin vector engine、kytensor 等服务状态。
- `resources`：服务级 cgroup 资源文件是否可读、内存与 CPU 限制是否存在。
- `sdk`：Kylin embedding/vector SDK 的库、头文件、socket、模型仓库、插件和 fallback 状态。
- `desktop`：Peony、UKUI 控制中心、托盘、通知工具和插件安装状态。
- `security`：auditd、Polkit policy、受控 helper、BPF LSM 安全验证资产。
- `summary`：整体状态、readiness score 和面向用户的修复建议。

如需真实触发 Kylin embedding 推理探针，可执行：

```bash
python -m memguard.cli.memctl openkylin-status --probe-sdk --probe-timeout 90
```

真实推理可能触发 Kylin AI runtime 冷启动，因此控制中心默认先显示元数据和服务状态，再把推理探针放在用户主动点击的“检查本机智能检索”操作中。这样既符合端侧稳定性，也避免打开控制中心时出现无意义等待。

## 4. Peony 右键文件闭环

Peony 集成的目标不是“把文件管理器里看到的所有内容都自动记住”，而是服务用户主动选择的 Agent 相关文件。老师最新 Q&A 已明确“用户操作习惯”主要指用户与 Agent 的交互习惯，因此 Peony 文件入口定位为辅助导入 Agent 上下文、命令记录、任务说明和用户主动选择的材料，而不是后台全盘采集桌面行为。

当前 Peony 插件采用两阶段流程。

第一阶段生成预览清单：

```bash
python -m memguard.cli.memctl desktop scan-files --file note.md --file history.txt
```

输出包含 `manifest_path`、文件指纹、风险等级、建议动作、是否可勾选、过期时间和签名。敏感文件、目录、大文件或二进制文件不会被默认写入长期记忆。

第二阶段确认后执行：

```bash
python -m memguard.cli.memctl desktop apply-scan --manifest /tmp/memguardos-peony-xxx.json --select note.md
```

执行阶段会验证 manifest 的 HMAC 签名、文件指纹、nonce、过期时间和是否已使用。这样可以防止“预览一个文件、导入另一个文件”或重复使用旧清单。安全文本会进入 `peony_file` 来源，敏感内容会被阻断，所有结果进入审计链。

如果用户发现本次导入不合适，Peony 结果对话框里的【撤销导入 / 立即遗忘】会把本批次返回的 `memory_id` 直接传给：

```bash
python -m memguard.cli.memctl desktop forget-import --memory-id <memory_id> --compact
```

该命令只处理本次导入的记忆 ID，不要求用户再去控制台手动搜索；执行后会写入遗忘审计、压缩受管段文件，并返回不可恢复验证结果。这样右键文件形成了“预览、确认、导入、撤销、审计”的完整闭环。

这个流程对用户更友好：右键后先看结果，再决定是否导入；对评审更可信：每一步都有 JSON 证据、审计链和可复跑命令。

## 5. UKUI 控制中心和托盘

UKUI 控制中心插件遵循 openKylin/UKUI 原生桌面风格：使用系统字体、系统调色板、主题图标和 Qt Widgets 组件，不使用网页式卡片、渐变背景或硬编码深浅色。页面按普通用户和管理员分层，普通用户页只保留服务是否正常、文件是否安全、敏感是否阻断和如何遗忘；管理员页再展示 systemd、cgroup、auditd、BPF、Polkit、Kylin SDK 和诊断导出。页面主要包含：

- 状态页：服务状态、SDK 状态、审计状态、资源状态和修复建议。
- SDK 页：Kylin embedding/vector 的库、模型、socket、推理就绪和 fallback 说明。
- 安全页：审计链校验、auditd 规则安装、BPF 安全验证、Polkit 授权入口。
- 诊断页：导出诊断包、打开本地 Web 控制台、查看服务日志。

托盘程序使用 `QSystemTrayIcon`，只在 UKUI 会话中自动启动。它低频轮询 `memctl openkylin-status`，只在状态变化时发送通知，避免持续打扰用户。托盘菜单提供【打开记忆安全中心】、【查看本机智能检索】、【查看审计状态】、【查看最近一次阻断记录】、【打开本地控制台】和【重新检查状态】。SDK 和审计入口会在打开控制中心前写入轻量目标文件，使控制中心直接定位到对应管理员工具；敏感阻断入口会打开本地控制台审计页，便于用户查看最近阻断记录。托盘退出不会停止核心服务，符合系统组件的最小惊扰原则。

## 6. Polkit 与高权限边界

涉及服务启停、刷新服务、安装 auditd 规则和运行 BPF LSM 安全验证的操作需要系统权限。MemGuardOS 提供 Polkit policy 和受控 helper，不把 UKUI 按钮直接变成任意 root shell。

白名单动作包括：

- `start-service`
- `stop-service`
- `restart-service`
- `refresh-services`
- `install-auditd-rules`
- `run-bpf-lsm-test`

helper 会固定 PATH、清理高风险环境变量、拒绝未知动作，并通过 `/opt/memguardos` 作为固定项目根路径。是否出现图形授权弹窗取决于目标 openKylin 是否运行 Polkit agent；在维护模式或终端环境中，也可以用 `sudo` 复验同一条白名单路径。

## 7. Debian 分包交付

构建命令：

```bash
bash scripts/build_deb.sh
```

默认生成：

```text
dist/memguardos-core_0.1.0-2_amd64.deb
dist/memguardos-ukui_0.1.0-2_amd64.deb
dist/memguardos-agent-bridge_0.1.0-2_amd64.deb
dist/memguardos-doc_0.1.0-2_all.deb
dist/memguardos_0.1.0-2_all.deb
```

其中 `memguardos-core` 提供 CLI、daemon、systemd、Polkit、BPF 安全验证资产和核心 Python 模块；`memguardos-ukui` 提供 Peony、UKUI 控制中心、托盘和桌面入口；`memguardos-agent-bridge` 提供 OpenHands/Agent 适配；`memguardos-doc` 提供中文文档；`memguardos` 是完整安装元包。桌面包安装后会刷新桌面菜单、应用图标缓存，重启 Peony，并关闭已打开的 UKUI 控制中心实例，让用户重新打开设置时看到新插件；卸载桌面包不会删除核心服务和用户记忆数据。

离线生命周期验证命令：

```bash
bash scripts/verify_deb_lifecycle.sh
```

它会检查五包是否齐全、包间依赖是否正确、文件所有权是否冲突、离线解包安装是否成功、移除桌面包是否保留核心能力、重装桌面包是否可恢复，以及维护脚本是否不会删除用户记忆目录。

需要准确说明的是：新版五包已经完成构建、离线生命周期验证和 openKylin 维护模式完整 `apt install --reinstall` 复验。安装后继续验证了服务/cgroup、Peony 文件预览与敏感阻断、针对本次导入的遗忘、安装版 Web 鉴权、Polkit 白名单、托盘无面板存活和审计链。最终提交应读取 `docs/evidence/openkylin_native_latest/source_revision.json`，确认这些证据与提交 Git HEAD 一致。

为处理早期单包或手工安装残留，`memguardos-core` 的维护脚本会逐项检查项目已知的旧程序文件，仅在文件不属于任何当前 Debian 包时删除。该逻辑不使用递归删除或目录通配符，不会删除未知自定义文件，也不会触碰用户记忆、数据库、审计链、配置、备份和日志。

## 8. 最终 HEAD 证据门禁

为了避免“代码已经变了，但证据还是旧版本”的问题，`collect_openkylin_native_evidence.sh` 增加了来源版本门禁：

- 在 Git 工作树中采集时，工作树必须干净，证据自动记录 `git rev-parse HEAD`。
- 在 `git archive` 或同步目录中采集时，必须传入 40 位 `MEMGUARD_SOURCE_REVISION`。
- 证据目录必须包含 `source_revision.json`，`check_competition_ready.py` 会校验该 revision 是否等于当前 Git HEAD。

推荐最终采集方式：

```bash
MEMGUARD_SOURCE_REVISION=<当前Git完整提交号> \
MEMGUARD_SOURCE_MODE=git_archive \
bash scripts/collect_openkylin_native_evidence.sh
```

证据目录默认位于：

```text
docs/evidence/openkylin_native_latest/
```

关键文件包括 `source_revision.json`、`openkylin_status.txt`、`openkylin_status_probe_sdk.txt`、`peony_batch_preview.json`、`peony_batch_apply.json`、`peony_sensitive_preview.json`、`desktop_build.txt`、`tray_assets.txt`、`polkit_assets.txt`、`systemd_cgroup.txt`、`debian_packages.txt`、`deb_lifecycle.txt`、`pytest_openkylin.txt`、`manifest.json` 和 `SHA256SUMS.txt`。

## 9. 准确边界

可以准确表述：

- MemGuardOS 已具备 openKylin 专项诊断、Kylin SDK 探测与自适应检索、Peony 多文件预览确认闭环、UKUI 控制中心运维页、UKUI 托盘、systemd/cgroup、auditd/journald、Polkit 授权资产、BPF LSM 安全验证和五包 Debian 交付能力。
- Peony 只处理用户主动选择的文件，不做后台全盘扫描。
- 高风险敏感文件不会默认写入长期记忆。
- Kylin SDK 是 openKylin 上的优先增强后端，但系统仍保留稳定 fallback，确保无 GPU、无云 API、SDK 暂不可用时也能完成核心功能。
- 精准遗忘覆盖 MemGuardOS 管理的数据、索引、图谱和审计可见性，不宣称擦除 SSD 物理残留、VM 快照、OpenHands 外部日志或外部备份。

不能夸大表述：

- 不能说项目已经进入 openKylin 官方软件源或取得官方产品认证。
- 不能说 Kylin SDK 在所有机器上都是唯一默认检索路径。
- 不能说 Polkit 图形弹窗在无桌面 agent 或维护模式中一定出现。
- 不能说 BPF LSM 当前已经实现任意文件内容的内核态 PII 识别；它是安全测试路径和可审查 denylist 强制拦截能力。
