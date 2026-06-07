# 《MemGuardOS 中文操作手册》

| 文档项 | 内容 |
|---|---|
| 产品名称 | MemGuardOS |
| 文档名称 | 中文操作手册 / 软件说明书 |
| 适用版本 | MemGuardOS 0.1.0 及当前 `main` 分支交付版本 |
| 文档类型 | 本地部署软件系统操作文档 |
| 目标用户 | 竞赛评委、系统管理员、运维人员、OS Agent 开发者、项目答辩人员 |
| 部署形态 | 源码运行、本地 Python 环境、openKylin systemd 服务、Debian 安装包 |
| 主要入口 | CLI `memctl`、本地 Web 控制台、Unix socket API、systemd 服务 |
| 生成日期 | 2026-06-07 |

## 文档版本记录

| 版本 | 日期 | 说明 |
|---|---|---|
| V1.0 | 2026-06-07 | 形成 MemGuardOS 中文操作手册初版，覆盖安装、操作、验证、维护和提交包检查 |

本文档面向竞赛提交、项目交付和后续维护三个场景编写。对于评委或验收人员，建议先阅读第 1 章至第 3 章，了解系统定位、运行环境和整体能力，再阅读 openKylin 证据、评测和提交包章节核查作品是否具备可复验性。对于实际操作人员，建议从安装启动章节开始，按功能操作章节逐步完成写入、检索、上下文构建和精准遗忘。对于系统管理员和维护人员，应重点阅读本地 API、系统机制、安全审计、配置维护和故障处理相关内容。文档中的命令以源码运行方式为主，如果已经通过 `.deb` 安装，可将 `python -m memguard.cli.memctl` 替换为系统命令 `memctl`。

## 目录

第 1 章 引言  
第 2 章 软件概述  
第 3 章 运行环境  
第 4 章 安装与启动  
第 5 章 功能操作说明  
第 6 章 系统界面说明  
第 7 章 Web 控制台操作  
第 8 章 本地 Agent API  
第 9 章 openKylin 系统机制验证  
第 10 章 评测、证据和提交包  
第 11 章 配置参数与运维维护  
第 12 章 数据管理、安全与审计  
第 13 章 常见问题与处理方法  
第 14 章 使用注意事项和交付建议  
第 15 章 场景化使用说明  
第 16 章 管理员运维说明  
第 17 章 数据安全治理说明  
第 18 章 验收与答辩操作说明  
第 19 章 长期维护与二次开发说明  
第20章 实际操作流程详解  
第21章 质量验收与指标解释  
第22章 教学、课程设计与项目展示说明  
第23章 维护交接与风险控制说明  
附录 A 常用命令速查  
附录 B 文档质量自检

## 第1章 引言

### 1.1 编写目的

本文档用于说明 MemGuardOS 的安装、启动、配置、功能操作、验证、维护和常见问题处理方法。阅读者可以按照本文完成从源码环境准备、基础功能验证、记忆写入与检索、RAG 上下文构建、精准遗忘、审计验证，到 openKylin 系统机制复验和正式提交包生成的完整操作。

MemGuardOS 面向“Linux 智能体记忆提取与精准遗忘”场景，不是普通聊天记录管理工具。系统需要处理来自用户文本、文件、结构化配置、shell 行为、工具执行结果、OS 事件和 auditd 审计日志的多源输入，并将其中具有长期价值的信息沉淀为偏好记忆、知识记忆或安全策略记忆。上游 OS Agent 可以通过命令行或本地 API 查询这些记忆，构建已脱敏、可解释、可追溯的 RAG 上下文；当用户要求遗忘某类信息时，系统可以生成候选计划、确认删除、清理索引并通过审计链证明操作发生过。

### 1.2 项目背景

Linux 桌面和开发环境中的智能体会长期接触命令、配置、日志、文件路径、工具调用结果、错误修复轨迹和用户个人偏好。如果这些信息只被保存在普通对话历史中，系统很难判断哪些内容值得长期保留，哪些内容包含敏感信息，哪些旧偏好已经失效，以及用户要求“忘掉某个项目代号”时到底应该删除哪些记录。MemGuardOS 的目标是在 Linux/openKylin 环境中提供一个可运行、可测试、可审计的端侧记忆基础设施，使智能体能够更可靠地积累经验，也能在需要时精准遗忘。

### 1.3 适用对象

本文适用于以下人员：

| 角色 | 阅读重点 |
|---|---|
| 竞赛评委 | 了解系统核心能力、操作路径、openKylin 证据和赛题相关验证方式 |
| 答辩人员 | 按照快速演示流程完成现场展示，并准确说明系统边界 |
| 系统管理员 | 完成安装、systemd 服务、cgroup、auditd、SDK 和桌面插件验证 |
| OS Agent 开发者 | 使用 CLI 或 Unix socket API 写入、检索、构建上下文和执行遗忘 |
| 运维人员 | 查看日志、诊断报告、审计链、资源限制和故障处理步骤 |

### 1.4 文档范围

本文覆盖 MemGuardOS 的本地源码运行、openKylin 部署、核心功能操作、Web 控制台、Unix socket API、系统机制验证、数据管理、安全审计、评测和故障排查。本文不包含外部 LLM API 购买说明，不提供公网 SaaS 部署方案，不承诺多租户企业平台能力，也不把 BPF LSM 测试路径描述为完整生产级访问控制系统。

### 1.5 术语说明

| 术语 | 说明 |
|---|---|
| 记忆 | 系统从输入中沉淀出的结构化信息，包括偏好、知识和安全策略 |
| 偏好记忆 | 用户对回答风格、工具选择、输出格式、工作习惯等方面的偏好 |
| 知识记忆 | 可复用的技术事实、故障案例、配置经验、工具轨迹或 OS 事件 |
| 精准遗忘 | 根据自然语言请求定位候选记忆，确认后删除索引并压缩物理存储 |
| RAG 上下文 | 在调用上游模型前构建的已脱敏记忆上下文包 |
| namespace | 多会话隔离标识，不同 namespace 使用独立存储和敏感规则 |
| auditd | Linux 审计框架，可监控敏感路径访问事件 |
| Kylin SDK | openKylin 提供的端侧 AI embedding 和向量数据库相关 SDK |
| Peony/UKUI | openKylin 桌面环境中的文件管理器和控制中心 |

## 第2章 软件概述

### 2.1 软件简介

MemGuardOS 是一个面向 Linux/openKylin 的智能体记忆系统。它以 Python 核心服务为主体，结合 mmap 追加式段文件、SQLite 元数据和 FTS 索引、图谱关系、审计哈希链、systemd、cgroup、auditd、journald、BPF LSM 测试路径、Peony 右键菜单和 UKUI 控制中心入口，形成从记忆接入到精准遗忘的闭环。

系统默认不依赖 GPU、外部 LLM API 或重型向量数据库。基础检索链路由 SQLite FTS、稳定 hash-vector、图谱关联和冲突处理组成；在 openKylin 环境中检测到 Kylin embedding/vector SDK 且真实推理可用时，可以作为可选增强路径使用。这样的设计使系统既能在普通开发机上运行，也能在 openKylin 虚拟机中复验。

### 2.2 核心价值

MemGuardOS 的核心价值体现在四个方面。第一，它把多源输入整理为结构化记忆，使 OS Agent 能够积累用户偏好、工具经验和系统事件经验。第二，它在写入和生成前处理敏感信息，避免凭据、私钥、系统高风险路径等内容被直接沉淀为长期上下文。第三，它提供自然语言级精准遗忘流程，不只是删除数据库行，还包含候选解释、确认执行、索引清理、段文件压缩和审计验证。第四，它接入 openKylin 的系统机制，使作品不止是应用层文本处理，而是具备操作系统赛题应有的 systemd/cgroup/auditd/桌面集成证据。

### 2.3 主要功能模块

| 模块 | 功能说明 | 常用入口 |
|---|---|---|
| 多源接入 | 写入文本、文件、配置、shell 历史、工具结果、OS 事件和 auditd 事件 | `memctl ingest`、`ingest-config`、`ingest-tool-result`、`ingest-history`、`ingest-os-event` |
| 抽取解释 | 展示质量校验、敏感检测、记忆类型、子类型和语义信号 | `memctl explain-extraction` |
| 记忆检索 | 支持全文、hash-vector、图谱、跨语言 token 和冲突处理 | `memctl search`、`batch-search` |
| RAG 上下文 | 输出已脱敏上下文、来源分布、选择轨迹和预算信息 | `memctl build-context`、`build-prompt` |
| 层次图谱 | 按摘要层、实体关系层、原文 chunk 层检索图谱记忆 | `memctl hierarchical-graph-search` |
| 精准遗忘 | 生成遗忘候选、确认删除、compact 和不可逆验证 | `memctl forget`、`verify-forget`、`compact` |
| 审计链 | 记录关键操作并校验哈希链完整性 | `memctl audit`、`verify-audit` |
| 系统诊断 | 检查运行环境、检索后端、Kylin SDK 和 namespace 状态 | `memctl doctor`、`diagnostics`、`sdk-status` |
| Web 控制台 | 本地可视化操作和评审演示 | `memctl web` |
| OS 集成 | systemd、cgroup、auditd、BPF LSM、Peony、UKUI | `scripts/` 和 `native/` 目录脚本 |

### 2.4 总体运行流程

MemGuardOS 的典型流程如下：用户或 OS Agent 写入一段文本、配置、工具结果或系统事件；系统先进行输入质量校验和敏感检测，再将有效信息抽取为偏好、知识或安全策略记忆；记忆被追加到 mmap 段文件，并在 SQLite 中建立元数据、FTS、图谱和审计记录；当 Agent 发起查询时，系统通过混合检索返回相关记忆，并构建已脱敏上下文；当用户要求遗忘时，系统先返回候选计划，确认后执行删除、索引清理、compact 和审计校验。

【流程图占位符：MemGuardOS 从多源输入到记忆检索、上下文构建、精准遗忘和审计验证的总体流程】

### 2.5 用户角色说明

MemGuardOS 当前定位为本地系统组件，不提供公网用户账号体系。默认使用当前操作系统用户身份运行，数据保存在当前用户目录下；在 systemd 服务模式下，具体权限取决于安装脚本、服务用户和 socket 权限设置。若启用 `--auth-token-file`，本地 Unix socket API 和 Web 控制台请求需要携带令牌。多人或高安全环境中，应将 socket 权限从演示友好的 `0666` 调整为专用用户组 `0660`，并使用本地令牌文件。

## 第3章 运行环境

### 3.1 硬件环境

MemGuardOS 默认不需要 GPU。普通 x86_64 开发机或 openKylin 虚拟机即可运行核心功能。为了保证评测和 openKylin 复验体验，建议为虚拟机分配至少 2 个 CPU 核心、4 GB 内存和 40 GB 以上磁盘空间；若需要 Kylin embedding SDK 真实推理、UKUI 桌面插件和 10k 规模评测，建议提高到 4 个 CPU 核心和 6 GB 以上内存。上述配置是经验建议，不是系统硬性要求。

### 3.2 软件环境

| 环境项 | 要求或建议 |
|---|---|
| 操作系统 | Windows 本机开发、Linux、openKylin；正式复验建议 openKylin |
| Python | Python 3.9 或以上 |
| Python 包 | 通过 `pip install -e ".[dev]"` 安装开发依赖 |
| systemd | openKylin/Linux 服务部署需要 |
| auditd | 敏感路径审计验证需要 |
| LibreOffice/Pandoc | 仅文档导出需要，不是运行系统的必要组件 |
| Kylin SDK | 可选增强，核心功能有 fallback |
| Peony/UKUI 开发包 | 构建桌面插件时需要 |

### 3.3 网络环境

核心功能默认离线可运行，不需要访问外部网络。安装 Python 依赖、克隆仓库或推送 GitHub 时需要网络。若 Windows 上 pip 访问 PyPI 受限，可使用本机代理 `http://127.0.0.1:7897`。openKylin 虚拟机是否能访问该代理取决于虚拟机网络模式和宿主机代理监听地址；无法联网时，应优先使用已准备好的离线依赖或本地缓存。

### 3.4 数据和存储路径

默认 namespace 为 `default`，通常使用当前用户目录下的 MemGuardOS 存储路径。显式指定 namespace 后，系统会为该 namespace 使用独立的段文件、SQLite 元数据库、自定义敏感规则和审计链。用户可以通过以下命令查看实际路径：

```bash
python -m memguard.cli.memctl namespace-status
python -m memguard.cli.memctl --namespace demo-a namespace-status
```

操作前应确认当前 namespace，避免把测试数据写入正式演示库。执行精准遗忘、compact 或清理会话记忆前，建议先导出审计或备份当前存储目录。

## 第4章 安装与启动

### 4.1 源码方式安装

源码方式适合开发、测试和比赛答辩准备。进入项目根目录后执行：

```powershell
cd <项目目录>\memguardos
python -m pip install -e ".[dev]"
python -m pytest -q
```

在 Linux/openKylin 中可使用：

```bash
cd ~/memguardos
python3 -m venv .venv
source .venv/bin/activate
pip install -U pip
pip install -e ".[dev]"
python -m pytest -q
```

预期结果是测试全部通过。若依赖安装失败，先确认 Python 版本、网络和 pip 代理配置；若测试失败，应保存完整输出并运行 `python -m memguard.cli.memctl doctor` 查看环境状态。

### 4.2 基础启动和退出

MemGuardOS 的 CLI 不需要常驻进程即可执行写入和检索。最小验证流程如下：

```bash
python -m memguard.cli.memctl doctor
python -m memguard.cli.memctl ingest --text "以后回答 Linux 命令时直接给命令和风险提示。"
python -m memguard.cli.memctl search "Linux 命令回答偏好"
python -m memguard.cli.memctl verify-audit
```

如果 `doctor` 输出环境检查信息，`ingest` 返回记忆写入结果，`search` 能检索到相关记录，`verify-audit` 返回 `ok: true`，说明基础链路可用。

### 4.3 systemd 服务安装

在 openKylin 中可以安装 systemd 服务：

```bash
cd ~/memguardos
sudo bash scripts/install_openkylin.sh "$(pwd)"
sudo systemctl start memguardd
systemctl status memguardd --no-pager
```

验证资源限制：

```bash
systemctl show memguardd -p MemoryMax -p CPUQuotaPerSecUSec --no-pager
cat /sys/fs/cgroup/system.slice/memguardd.service/memory.max
cat /sys/fs/cgroup/system.slice/memguardd.service/cpu.max
```

模板服务可为不同 namespace 启动独立实例：

```bash
sudo systemctl start memguardd@demo-a memguardd@demo-b
systemctl status memguardd@demo-a memguardd@demo-b --no-pager
systemctl show memguardd@demo-a memguardd@demo-b -p ControlGroup -p MemoryMax -p CPUQuotaPerSecUSec --no-pager
```

### 4.4 Debian 安装包方式

MemGuardOS 提供 `.deb` 打包脚本，用于在 openKylin/Debian 系系统中进行系统包安装验证：

```bash
cd ~/memguardos
bash scripts/build_deb.sh
sudo apt install ./dist/memguardos_0.1.0_amd64.deb
memctl doctor
sudo systemctl start memguardd
systemctl status memguardd --no-pager
```

安装后，`memctl` 和 `memguardd` 会作为系统命令可用。核心代码、脚本、样例和正式文档安装到 `/opt/memguardos`，systemd 服务安装到 `/lib/systemd/system/`。若构建机器缺少 UKUI/Peony 开发依赖，默认 `WITH_UKUI=auto` 会跳过桌面插件并生成核心安装包；如要求插件必须打入包，可使用 `WITH_UKUI=yes bash scripts/build_deb.sh`。

卸载命令：

```bash
sudo systemctl stop memguardd 2>/dev/null || true
sudo apt remove memguardos
```

### 4.5 Web 控制台启动

Web 控制台用于本地操作和评审演示，默认建议只绑定本机地址：

```bash
python -m memguard.cli.memctl web --host 127.0.0.1 --port 8765
```

打开浏览器访问：

```text
http://127.0.0.1:8765
```

【截图占位符：MemGuardOS Web 控制台首页与诊断视图】

如需令牌保护，可先生成本地令牌文件：

```bash
install -m 600 /dev/null ~/.memguardos-api.token
python3 - <<'PY' > ~/.memguardos-api.token
import secrets
print(secrets.token_urlsafe(32))
PY
chmod 600 ~/.memguardos-api.token
python -m memguard.cli.memctl web --host 127.0.0.1 --port 8765 --auth-token-file ~/.memguardos-api.token
```

### 4.6 启动后的初始化检查

无论采用源码方式、systemd 服务方式还是 `.deb` 安装方式，首次启动后都应进行一次初始化检查。该检查的目的不是追求输出内容完全一致，而是确认当前环境具备必要的运行能力，存储路径可写，审计链可以创建，检索后端能够正常初始化。建议按以下顺序执行：

```bash
python -m memguard.cli.memctl doctor
python -m memguard.cli.memctl namespace-status
python -m memguard.cli.memctl retrieval-status
python -m memguard.cli.memctl verify-audit
```

如果是空库环境，`verify-audit` 可能只检查到较少事件，这是正常现象。若 `doctor` 提示 Kylin SDK 不可用，不代表核心系统不可用；只有在准备展示 openKylin 端侧向量能力时，才需要进一步运行 `sdk-status --probe-embedding` 和相关修复脚本。初始化检查完成后，建议写入一条无敏感内容的测试记忆并立即检索，以确认写入、索引和审计链都能正常工作。

## 第5章 功能操作说明

### 5.1 写入文本记忆

文本写入用于保存用户偏好、知识经验、安全策略或临时工作信息。执行前应确认当前 namespace，必要时先运行 `namespace-status`。

```bash
python -m memguard.cli.memctl ingest --text "以后回答 Linux 命令时直接给命令和风险提示。"
```

预期结果：命令返回 JSON 结构，包含记忆 ID、类型、子类型、层级、敏感检测结果和审计信息。若文本为空、质量过低或包含高风险敏感内容，系统会拒绝写入或进行脱敏处理，并写入审计事件。

如需写入指定层级：

```bash
python -m memguard.cli.memctl ingest --text "本轮调试先观察 memguardd 日志。" --tier working
```

常用层级含义如下：

| 层级 | 适用场景 |
|---|---|
| `working` | 当前任务临时信息，适合短期调试 |
| `session` | 当前会话或项目阶段信息 |
| `long_term` | 长期偏好、稳定知识和可复用经验 |

文本写入完成后，建议立即使用与输入语义接近但不完全相同的查询进行搜索。例如写入“回答 Linux 命令时直接给命令和风险提示”后，可以搜索“Linux 命令回答偏好”。如果能够命中，说明全文索引、向量相似度和语义 token 处理均已参与工作；如果不能命中，应先检查输入是否被敏感策略阻断，再查看 `audit` 和 `quality-report` 中的记录。

### 5.2 写入文件内容

文件写入适合把说明文档、日志摘要或技术笔记导入记忆库：

```bash
python -m memguard.cli.memctl ingest --file data/demo/linux_workflow.md
```

执行后，系统会读取文件内容，进行质量校验和敏感检测，再生成结构化记忆。若文件中包含密钥、私钥、账号凭据或高风险路径，写入结果可能被阻断或脱敏。操作人员应避免直接导入未检查的生产敏感日志。

### 5.3 写入结构化配置

结构化配置导入用于把用户偏好、安全策略和技术配置以 JSON 或简单 YAML 形式导入：

```bash
python -m memguard.cli.memctl ingest-config --file data/demo/user_config.json
```

系统会解析配置叶子节点，将有意义的配置项转为记忆，并跳过空值或无效结构。导入完成后可运行：

```bash
python -m memguard.cli.memctl quality-report --limit 20
```

该命令用于查看最近的输入质量校验结果，帮助判断哪些配置被接受、哪些被跳过。

结构化配置导入适合用于演示“系统不是只处理自然语言文本”。例如用户偏好、审计策略、常用工具选择和输出格式要求，都可以通过配置文件导入。导入时应注意字段含义清晰，避免把整个大对象作为一个含糊字段写入。系统更倾向于处理叶子节点明确、值具有业务意义的配置项；空值、纯分组节点或结构不完整的字段会被跳过，并在质量报告中留下解释。

### 5.4 写入 shell 历史

shell 历史导入用于把有价值的工具使用习惯、调试路径和命令偏好转为用户行为记忆：

```bash
python -m memguard.cli.memctl ingest-history --file ~/.bash_history --limit 200
```

系统会过滤 `ls`、`cd`、`history` 等低价值噪声，保留 `pytest`、`rg`、`git`、`systemctl`、`journalctl`、`ssh`、`scp`、`pip`、`make` 等更能体现智能体操作习惯的命令。导入 shell 历史前，应确认其中不含明文密码、token 或生产服务器敏感地址。

### 5.5 写入工具执行结果和程序性记忆

工具执行结果导入适合记录“状态、动作、结果、经验”。示例 JSON：

```json
{
  "command": "systemctl daemon-reload && systemctl restart memguardd && systemctl is-active memguardd",
  "cwd": "/opt/memguardos",
  "exit_code": 0,
  "stdout": "active",
  "state_before": "memguardd unit changed and service inactive",
  "state_after": "memguardd active after daemon reload and restart",
  "lesson": "after editing memguardd.service, reload, restart, then verify active state"
}
```

导入命令：

```bash
python -m memguard.cli.memctl ingest-tool-result --file data/demo/tool_result_success.json
```

当输入包含 `state_before`、`state_after` 和 `lesson` 字段时，系统会把工具轨迹建模为状态-动作-结果程序性记忆。这类记忆适合被 OS Agent 在后续自动调试、服务恢复、测试复跑和证据生成时复用。

### 5.6 写入 OS 事件和 auditd 事件

OS 事件可以通过 JSON、auditd 或 fanotify 风格输入导入：

```bash
python -m memguard.cli.memctl ingest-os-event --file data/demo/os_event_sensitive_path.json
```

auditd 规则预览：

```bash
python -m memguard.cli.memctl auditd-rules --format auditctl
```

安装 auditd 规则并采集事件：

```bash
sudo bash scripts/install_auditd_rules.sh "$(pwd)"
sudo auditctl -l | grep memguard_sensitive
bash scripts/auditd_to_memguard.sh "$(pwd)" memguard_sensitive 200 --summary
```

预期结果：敏感路径访问事件会被转为 OS 事件记忆或安全审计记录。若目标系统未安装 auditd，需要先安装并启用 auditd 服务。

OS 事件和 auditd 事件是 MemGuardOS 区别于普通应用层记忆库的重要输入来源。它们用于记录系统层面的事实，例如敏感路径访问、服务状态变化、审计规则触发和运行时异常。导入这类事件时，应尽量保留事件时间、进程、路径、动作、结果和来源字段。字段越完整，后续检索、审计解释和答辩展示越有说服力。若原始日志包含真实用户名、主机名或生产路径，建议先在测试环境中脱敏后再作为演示样例使用。

### 5.7 查看抽取解释

抽取解释命令不会写入记忆，只用于查看系统将如何理解一段输入：

```bash
python -m memguard.cli.memctl explain-extraction --text "真实案例：systemd service failed with exit_code=1，修复后运行 pytest。"
```

输出会包含质量校验、敏感检测、记忆类型、子类型、摘要、置信度和命中的语义信号。答辩或调试时，建议用该命令解释系统为什么把某条输入识别为工作流、工具轨迹、OS 事件或用户偏好。

### 5.8 查看和检索记忆

查看当前记忆：

```bash
python -m memguard.cli.memctl list
python -m memguard.cli.memctl list --tier long_term
```

搜索记忆：

```bash
python -m memguard.cli.memctl search "openKylin 服务内存限制"
```

批量检索：

```bash
python -m memguard.cli.memctl batch-search \
  "任务规划 systemd cgroup" \
  "工具调用 pytest journalctl" \
  "结果生成 运维报告 证据" \
  --top-k 3 \
  --workers 3
```

批量检索用于 OS Agent 同时为任务规划、工具调用和结果生成准备记忆证据。系统为每个子查询返回独立结果和延迟指标，并只写入一次 `batch_search` 审计事件，避免并发读路径审计放大。

检索结果并不是简单按照插入时间返回。系统会综合全文匹配、稳定 hash-vector 相似度、图谱关联、跨语言技术词扩展和冲突处理结果进行排序。对于面向答辩或验收的演示，建议准备几类不同查询：一类查询用户偏好，一类查询系统知识，一类查询工具轨迹，一类查询 OS 事件。这样可以展示系统对不同记忆类型的覆盖，而不是只展示单一关键词命中。

### 5.9 构建 RAG 上下文和 Prompt

构建上下文：

```bash
python -m memguard.cli.memctl build-context "openKylin 服务内存限制" --top-k 5 --token-budget 1200
```

构建 provider-neutral prompt：

```bash
python -m memguard.cli.memctl build-prompt "openKylin 服务内存限制" --top-k 5 --token-budget 1200
```

`build-context` 输出中包含 `explainability` 字段，用于说明来源分布、类型分布、层级分布、敏感等级分布和每条候选的选择轨迹。排查“为什么这条记忆进入生成上下文”时，应优先查看 `selection_trace`。如果用户不关心 token 成本，可不传 `--token-budget`；如果需要控制上游模型输入长度，可以显式设置预算。

生成上下文后，上游 Agent 可以把结果交给本地模型、远程模型或人工答辩演示使用。MemGuardOS 本身不会默认调用外部 LLM，因此不会产生外部 API 费用。若使用 `generate-answer --provider dry-run`，系统只会输出 prompt 和调用计划；只有显式选择 `local-command` 或 `openai-compatible` 并配置命令、端点和密钥时，才会真正调用外部或本地模型。比赛演示中如果不需要展示生成模型，可只展示 `build-context` 和 `build-prompt`，这样更能突出本项目的记忆系统职责。

### 5.10 图谱检索和层次检索

普通图谱检索：

```bash
python -m memguard.cli.memctl graph-search systemd --limit 5
python -m memguard.cli.memctl related systemd
```

层次图谱检索：

```bash
python -m memguard.cli.memctl hierarchical-graph-search systemd --limit 5
```

层次图谱检索会按摘要层、实体关系层、原文 chunk 层返回结果，并输出检索路径。这适合展示系统不是只做关键词匹配，而是能从不同信息粒度组织记忆证据。

### 5.11 精准遗忘

精准遗忘建议分两步执行。第一步先生成 dry-run 计划：

```bash
python -m memguard.cli.memctl forget "忘掉 temporary project codename"
```

系统会返回候选记忆、匹配关键词、区别性关键词和候选分数。确认候选无误后执行：

```bash
python -m memguard.cli.memctl forget "忘掉 temporary project codename" --yes --compact
python -m memguard.cli.memctl verify-forget "temporary project codename"
python -m memguard.cli.memctl verify-audit
```

`--compact` 会压缩追加式段文件并清理已遗忘明文。高风险操作必须先查看 dry-run 结果；如果候选包含不应删除的记忆，不要使用 `--yes`，应调整遗忘请求，使关键词更具体。

精准遗忘的核心不是“把所有包含某个词的内容都删掉”，而是在自然语言请求和已有记忆之间建立可解释的候选关系。系统会根据请求关键词、区别性关键词、摘要和相关记忆得分生成候选。操作人员需要判断候选是否真正符合遗忘意图，例如“忘掉 temporary project codename”应当命中临时代号相关记忆，而不应误删普通项目经验。确认删除后，系统会把记忆标记为删除、关闭有效时间窗口、清理 FTS 和图谱索引，并在 compact 后验证段文件中是否仍残留明文。

### 5.12 版本、回滚和层级维护

查看版本：

```bash
python -m memguard.cli.memctl versions <memory_id>
```

追加新版本：

```bash
python -m memguard.cli.memctl update <memory_id> --text "新的记忆内容"
```

回滚：

```bash
python -m memguard.cli.memctl rollback <memory_id> <version_id>
```

提升或降低层级：

```bash
python -m memguard.cli.memctl promote <memory_id> long_term
python -m memguard.cli.memctl evict-tier session --yes
```

时间有效性状态：

```bash
python -m memguard.cli.memctl temporal-status --limit 10
```

这些命令用于处理偏好变化、旧知识失效、短期任务清理和历史追溯。回滚和清理操作会写入审计链。

### 5.13 自定义敏感规则

查看规则：

```bash
python -m memguard.cli.memctl patterns
```

添加规则时，应优先使用受限、明确、可解释的正则表达式，避免过宽匹配。规则添加后建议用 `explain-extraction` 或测试输入验证效果。不要把过宽规则直接用于正式演示库，否则可能导致大量正常记忆被误判为敏感。

### 5.14 诊断与状态查看

常用诊断命令：

```bash
python -m memguard.cli.memctl doctor
python -m memguard.cli.memctl status
python -m memguard.cli.memctl namespace-status
python -m memguard.cli.memctl retrieval-status
python -m memguard.cli.memctl memory-policy
python -m memguard.cli.memctl diagnostics --output docs/evidence/latest/diagnostics.json
```

`doctor` 适合检查本地运行环境和 OS 能力；`retrieval-status` 适合查看当前检索后端、Kylin SDK 可选增强和 fallback 策略；`memory-policy` 适合查看分层记忆数量、存储占用、可淘汰候选和 cgroup 状态；`diagnostics` 适合导出现场排查证据。

诊断报告适合在提交前、答辩前和出现异常时生成。报告中不应包含用户私人凭据或生产敏感信息。如果需要把诊断报告放入证据包，应先确认其中没有本机绝对路径、真实服务器地址、账号信息或未脱敏日志。正式提交包审计脚本会检查部分本机路径和禁入材料，但操作人员仍应对证据内容进行人工复核。

## 第6章 系统界面说明

### 6.1 命令行界面

MemGuardOS 的主要操作界面是命令行工具 `memctl`。命令行适合开发者、评审人员和运维人员，因为它能够输出完整 JSON 结果，便于保存证据、复现实验和定位问题。源码运行时使用 `python -m memguard.cli.memctl`，系统包安装后可以直接使用 `memctl`。两种方式调用的是同一套核心能力，区别只在入口路径。

命令行输出通常包含结构化字段，例如记忆 ID、类型、子类型、敏感级别、检索得分、审计状态和错误原因。对于演示或验收，建议保留命令和输出截图，特别是 `doctor`、`sdk-status --probe-embedding`、`build-context`、`forget --yes --compact`、`verify-forget` 和 `verify-audit` 的输出。这些输出能够说明系统不仅能执行功能，还能解释执行依据。

### 6.2 Web 控制台界面

Web 控制台是本地可视化入口，适合展示系统能力和降低操作门槛。控制台没有复杂登录页，默认依赖本机访问边界；如需鉴权，应通过 `--auth-token-file` 启用本地令牌。控制台主要由状态、写入、抽取解释、检索、RAG 上下文、精准遗忘、审计和诊断等区域组成。

状态区域用于确认系统是否正常运行，写入区域用于提交文本记忆，抽取解释区域用于观察系统如何判断输入类型，检索区域用于查看命中结果，RAG 上下文区域用于展示脱敏和选择轨迹，精准遗忘区域会在执行前要求确认，审计区域用于检查关键操作，诊断区域用于导出运行环境信息。由于控制台用于本地演示和运维，不建议绑定 `0.0.0.0` 或公网地址。

【截图占位符：Web 控制台主界面功能布局】

### 6.3 openKylin 桌面入口

在 openKylin 桌面环境中，MemGuardOS 提供 Peony 文件右键菜单和 UKUI 控制中心入口。Peony 入口适合从文件管理器中触发“用 MemGuardOS 扫描并导入记忆”；该入口会把选中文件路径写成 OS event，并对小于 1 MB 的文本类文件执行内容导入。UKUI 控制中心入口适合集中查看运行环境检查、审计链校验、端侧向量能力检查、诊断报告导出和本地控制台打开操作。

这两个入口用于展示 MemGuardOS 与 openKylin 桌面环境的集成方向。它们不是独立账号系统，也不是完整企业控制台。若插件在当前桌面会话中不可见，应先确认插件版本、安装路径、只读 `/usr` 状态和桌面缓存，再重启 Peony 或注销重进桌面会话。

【截图占位符：Peony 文件右键菜单中的 MemGuardOS 操作项】

【截图占位符：UKUI 控制中心中的 MemGuardOS 记忆安全页面】

## 第7章 Web 控制台操作

### 7.1 控制台用途

Web 控制台是本地操作界面，适合演示和人工检查。它覆盖运行状态、写入、抽取解释、检索、RAG 上下文、精准遗忘、审计和系统诊断。控制台默认绑定 `127.0.0.1`，不建议绑定公网地址。

### 7.2 启动和访问

执行：

```bash
python -m memguard.cli.memctl web --host 127.0.0.1 --port 8765
```

浏览器访问 `http://127.0.0.1:8765`。如果端口被占用，可改用其他端口：

```bash
python -m memguard.cli.memctl web --host 127.0.0.1 --port 8766
```

### 7.3 典型操作路径

打开控制台后，可以按照以下顺序完成一次演示：

1. 进入状态或诊断页面，确认审计链、检索后端和记忆策略正常。
2. 在写入页面输入一条偏好或知识文本，点击写入。
3. 在抽取解释页面输入同一类文本，查看类型、子类型、质量和敏感信息判断。
4. 在检索页面输入关键词，确认返回记忆命中。
5. 在 RAG 上下文页面输入任务问题，查看已脱敏上下文和解释性字段。
6. 在精准遗忘页面先执行预览，确认候选后再执行删除。
7. 回到审计页面，查看写入、检索、遗忘和 compact 相关审计事件。

【截图占位符：写入记忆页面】

【截图占位符：RAG 上下文解释性输出页面】

【截图占位符：精准遗忘二次确认页面】

## 第8章 本地 Agent API

### 8.1 API 启用方式

systemd 服务启动后，默认 Unix socket 路径为：

```text
/run/memguardos/memguard.sock
```

请求格式为一行 JSON。示例：

```bash
python3 - <<'PY'
import json, socket
with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as client:
    client.connect("/run/memguardos/memguard.sock")
    client.sendall(json.dumps({"command": "health"}).encode("utf-8") + b"\n")
    print(client.recv(4096).decode("utf-8"))
PY
```

### 8.2 常用命令

| 命令 | 用途 |
|---|---|
| `health` | 查看 API 健康状态 |
| `ingest` | 写入记忆 |
| `search` | 检索记忆 |
| `batch_search` | 多查询并发检索 |
| `build_context` | 构建 RAG 上下文 |
| `hierarchical_graph_search` | 层次图谱检索 |
| `forget` | 精准遗忘预览或执行 |
| `audit` | 查看审计事件 |
| `verify_audit` | 校验审计哈希链 |
| `namespace_status` | 查看当前 namespace 存储隔离 |
| `memory_policy` | 查看分层策略和资源状态 |
| `retrieval_status` | 查看检索后端和 SDK 状态 |

启用令牌后，请求必须包含 `token` 字段：

```json
{"command": "health", "token": "本地令牌内容"}
```

令牌只用于本机 socket 调用鉴权，不等同于公网 OAuth、LDAP 或企业 SSO。

## 第9章 openKylin 系统机制验证

### 9.1 一键验证

在 openKylin 虚拟机中运行：

```bash
cd ~/memguardos
source .venv/bin/activate
bash scripts/openkylin_verify.sh
```

完整证据采集：

```bash
bash scripts/collect_openkylin_evidence.sh --with-10k
```

证据目录通常位于 `docs/evidence/latest/`。采集完成后，应查看 `manifest.json`、`SHA256SUMS.txt`、`pytest.txt`、`eval_10k.txt`、`sdk_embedding_probe.txt`、`verify_audit.txt` 等文件是否存在且非空。

### 9.2 Kylin SDK 验证

查看 SDK 状态：

```bash
python -m memguard.cli.memctl sdk-status
python -m memguard.cli.memctl sdk-status --probe-embedding --probe-timeout 90
```

如果文本 embedding 探针未返回向量，先执行：

```bash
bash scripts/fix_kylin_embedding_runtime.sh --probe-timeout 180
bash scripts/diagnose_kylin_embedding.sh --no-probe
bash scripts/diagnose_kylin_embedding.sh --restart-runtime --probe-timeout 180
```

验证 Kylin embedding 作为实际可选检索重排后端：

```bash
MEMGUARD_EMBEDDING_BACKEND=auto MEMGUARD_KYLIN_EMBEDDING_TIMEOUT=90 \
  python -m memguard.cli.memctl search "openKylin 服务内存限制" --top-k 2
```

若输出中包含 `vector_backend: "kylin_sdk"`，说明 SDK 重排路径可用。若 SDK 不可用，系统会使用 FTS/hash/graph fallback，核心功能仍可复现。

### 9.3 BPF LSM 策略验证

生成 denylist header：

```bash
python3 scripts/generate_bpf_lsm_policy.py --policy data/demo/bpf_lsm_policy.json --output /tmp/memguard_lsm_policy.generated.h
```

验证安全测试文件强制拒绝：

```bash
bash scripts/run_bpf_lsm_enforcer.sh docs/evidence/latest
```

注意：当前 BPF LSM 路径用于安全测试文件强制拒绝验证，不应表述为默认拦截系统登录链路或生产级全局访问控制。

### 9.4 Peony 和 UKUI 桌面集成验证

构建和安装：

```bash
bash scripts/build_ukui_plugins.sh
bash scripts/install_ukui_plugins.sh --no-build
bash scripts/verify_ukui_plugins.sh
```

安装后重启 Peony：

```bash
export DISPLAY=:0
export DBUS_SESSION_BUS_ADDRESS=unix:path=/run/user/1000/bus
pkill -x peony 2>/dev/null || true
pkill -x peony-qt-desktop 2>/dev/null || true
nohup peony-qt-desktop -w -d >/tmp/peony_desktop_memguardos.log 2>&1 &
nohup peony "$(xdg-user-dir DESKTOP)" >/tmp/peony_window_memguardos.log 2>&1 &
```

预期结果：

1. Peony 文件右键菜单中出现【用 MemGuardOS 扫描并导入记忆】。
2. UKUI 设置/控制中心左侧安全分类下出现【MemGuardOS 记忆安全】。
3. 控制中心页面可见【运行环境检查】、【校验审计链】、【检查端侧向量能力】、【导出诊断报告】和【打开本地控制台】。

如需自动复验 Peony 右键动作对应的导入链路，可运行：

```bash
bash scripts/peony_file_scan_smoke.sh "$(pwd)"
```

该脚本会创建文本样例文件，模拟 Peony 右键 OS event，执行文件内容导入，随后检索样例内容并校验审计链。预期输出中 `ok`、`file_ingest_stored` 和 `audit_ok` 均为 `true`，且 `search_hits` 大于 0。

若 openKylin 使用只读 `/usr`，安装脚本可能通过 bind mount 进行演示安装。该方式适合评审现场验证 UI 可见性，不等同于正式 `.deb` 包安装。

## 第10章 评测、证据和提交包

### 10.1 本地评测

运行本地测试和评测：

```bash
python -m pytest -q
python -m memguard.eval.run_eval --latency-size 1000 --latency-queries 120 --output docs/eval_result.json --markdown docs/eval_report.md
python scripts/demo_smoke.py
python scripts/web_dashboard_smoke.py
```

### 10.2 openKylin 10k 评测

```bash
python -m memguard.eval.run_eval --latency-size 10000 --latency-queries 150 --output docs/eval_result_openkylin_10k.json --markdown docs/eval_report_openkylin_10k.md
```

当前正式证据中，openKylin 10k 负载 P95 为 19.115 ms，P99 为 118.875 ms，RSS 为 129176 KB；openKylin 单元测试证据为 60 passed；本地单元测试为 63 passed。提交前如果重新运行评测并得到新结果，应同步更新 README、测试报告、平台摘要和答辩材料中的指标。

### 10.3 竞赛就绪自检

```bash
python scripts/check_competition_ready.py
python scripts/check_docs_links.py
```

`check_competition_ready.py` 会检查中文文档、核心代码、量化指标、openKylin 证据、SDK 真实推理、层次图谱、上下文解释性、证据清单和工程卫生。

### 10.4 生成正式提交包

```bash
python scripts/package_submission.py
python scripts/inspect_submission_package.py --package dist/memguardos_competition_submission_<时间戳>.zip --manifest dist/memguardos_competition_submission_<时间戳>_manifest.json
python scripts/smoke_submission_archive.py --package dist/memguardos_competition_submission_<时间戳>.zip
```

正式竞赛提交包只包含作品主体、正式中文文档和证据包，不应包含本地连接记录、过程研究资料、临时缓存或个人路径。完整开源仓库包可以包含工程化支撑材料，但不应替代正式提交包。

## 第11章 配置参数与运维维护

### 11.1 常用配置项

MemGuardOS 的大多数操作可以通过命令行参数完成。对于需要长期运行的环境，可以通过环境变量、systemd unit、服务启动参数或本地令牌文件固定配置。配置时应遵循“默认可运行、显式增强、失败回退”的原则：核心功能应当在没有外部模型、没有 GPU、没有 Kylin SDK 的情况下仍能完成；只有在验证环境具备相应能力时，才启用 SDK 重排、外部生成器或桌面插件增强。

| 配置项或参数 | 作用 | 默认行为 | 注意事项 |
|---|---|---|---|
| `--namespace` | 指定记忆命名空间 | 使用 `MEMGUARD_NAMESPACE` 或 `default` | 用于多项目、多会话隔离 |
| `MEMGUARD_NAMESPACE` | 默认 namespace 环境变量 | 未设置时为 `default` | systemd 模板服务可为不同 namespace 创建独立实例 |
| `--auth-token-file` | 指定本地 API 或 Web 控制台令牌文件 | 默认不启用令牌 | 多人环境建议启用 |
| `MEMGUARD_EMBEDDING_BACKEND` | 指定 embedding 后端 | 默认稳定 fallback | 设为 `auto` 时可尝试 Kylin SDK |
| `MEMGUARD_KYLIN_EMBEDDING_TIMEOUT` | Kylin embedding 探针或调用超时 | 按命令默认值执行 | VM 资源不足时可适当增大 |
| `--token-budget` | 构建上下文时的 token 预算 | 不传时不按预算强裁剪 | 用于控制上游 LLM 输入长度 |
| `--socket` | daemon Unix socket 路径 | 安装脚本使用 `/run/memguardos/memguard.sock` | 路径目录需可写 |
| `--socket-mode` | socket 文件权限 | 演示时可为 `666` | 正式环境建议 `660` 并配合用户组 |

配置变更后，应重新运行 `doctor`、`namespace-status`、`retrieval-status` 和 `verify-audit`。如果变更涉及 systemd 服务文件，还应执行 `sudo systemctl daemon-reload`，再重启对应服务。对于比赛答辩环境，不建议临场大幅修改配置；应提前准备一套已验证的默认配置，并在录制视频或现场演示前复跑验证脚本。

### 11.2 日志查看

源码方式运行时，命令输出本身就是最直接的日志来源。systemd 服务方式运行时，应使用 journald 查看后台服务日志：

```bash
journalctl -u memguardd -n 100 --no-pager
journalctl -t memguardd -n 50 --no-pager
```

如果启动了 namespace 模板服务，可以查看指定实例：

```bash
journalctl -u memguardd@demo-a -n 100 --no-pager
```

日志排查时，应先看服务是否启动成功，再看 socket 是否创建，最后看具体命令是否返回错误。不要只依据 Web 控制台页面是否打开判断系统状态，因为控制台异常可能来自端口占用、浏览器缓存或鉴权配置，而核心 CLI 和 daemon 仍可能正常。

### 11.3 备份、恢复和清理

MemGuardOS 的数据与 namespace 绑定。备份时应先确认当前 namespace 的实际存储路径，再备份对应目录。建议在以下场景前执行备份：大规模导入、执行精准遗忘、执行 compact、升级代码、重新采集证据、修改自定义敏感规则。

恢复时，应停止正在写入的 daemon，恢复完整目录后再启动服务，并运行：

```bash
python -m memguard.cli.memctl namespace-status
python -m memguard.cli.memctl verify-audit
python -m memguard.cli.memctl search "用于验证恢复的关键词"
```

如果恢复后审计链失败，通常说明数据库、段文件或审计记录没有一起恢复，或者恢复过程中混用了不同时间点的数据。此时不应继续采集正式证据，应回退到完整备份重新恢复。

### 11.4 升级与回滚建议

当前项目以源码仓库和 `.deb` 包两种方式交付。源码方式升级前，应先提交或备份当前工作区，确认没有未保存的运行数据混入仓库。`.deb` 方式升级前，应记录当前包版本、systemd 服务状态、数据目录位置和证据目录。升级后应至少运行 `doctor`、`pytest`、`demo_smoke.py`、`web_dashboard_smoke.py` 和 `verify-audit`。

如升级后出现异常，源码方式可以回退到上一个 Git 提交，`.deb` 方式可以卸载当前包并重新安装上一版包。无论哪种方式，数据目录都不应在未备份情况下直接删除。若问题与 Kylin SDK、Peony/UKUI 或 BPF LSM 有关，应先禁用对应增强路径，确认核心 CLI、存储、检索和遗忘链路仍能正常工作，再逐步恢复增强功能。

## 第12章 数据管理、安全与审计

### 12.1 数据对象

MemGuardOS 的核心数据对象包括记忆记录、版本记录、图谱边、敏感规则、审计事件和追加式段文件内容。记忆记录保存类型、子类型、层级、namespace、摘要、实体、关系、敏感标签、版本窗口和存储偏移；审计事件保存操作、对象、详情、前序哈希和当前哈希。

### 12.2 备份和恢复

备份时应备份当前 namespace 对应的存储目录。执行精准遗忘、compact、大量导入或评测前，建议先备份。恢复时应停止正在写入的 daemon，恢复存储目录后再启动服务并运行：

```bash
python -m memguard.cli.memctl verify-audit
python -m memguard.cli.memctl namespace-status
```

如果审计链校验失败，说明审计数据库可能被外部修改或恢复不完整，应保留现场文件并重新从备份恢复。

### 12.3 高风险操作

以下操作具有破坏性或安全影响：

| 操作 | 风险 | 建议 |
|---|---|---|
| `forget --yes --compact` | 删除候选记忆并压缩段文件 | 先 dry-run，确认候选无误后执行 |
| `evict-tier --yes` | 清理指定层级记忆 | 只对 working/session 等可淘汰层级使用 |
| 自定义敏感规则 | 过宽规则可能误杀正常记忆 | 小样本验证后再用于正式库 |
| 修改 socket 权限 | 过宽权限可能允许本机其他用户调用 API | 多人环境使用 `0660` 和令牌 |
| 导入 shell/history 日志 | 可能包含凭据或生产地址 | 导入前检查并限制来源 |

### 12.4 审计链验证

查看审计：

```bash
python -m memguard.cli.memctl audit --limit 20
```

校验哈希链：

```bash
python -m memguard.cli.memctl verify-audit
```

正常情况下返回 `ok: true`。如果返回 hash mismatch，应检查是否手工改动过 SQLite 数据库、是否从不完整备份恢复、是否存在并发写入异常。提交证据前必须重新运行审计链验证。

## 第13章 常见问题与处理方法

### 13.1 `pip install` 失败

先检查 Python 版本和网络。如果 Windows 本机访问 PyPI 失败，可使用：

```powershell
python -m pip install -e ".[dev]" --proxy http://127.0.0.1:7897
```

openKylin 虚拟机无法访问宿主机代理时，不要反复重试耗时下载，应优先使用离线 wheelhouse 或已同步的依赖缓存。

### 13.2 `memctl` 命令找不到

源码方式运行时使用：

```bash
python -m memguard.cli.memctl doctor
```

只有在 `.deb` 安装后，系统命令 `memctl` 才会直接可用。若安装后仍找不到，检查 `/usr/bin/memctl` 是否存在，并确认当前 shell 的 PATH。

### 13.3 systemd 服务启动失败

执行：

```bash
systemctl status memguardd --no-pager
journalctl -u memguardd -n 100 --no-pager
python -m memguard.cli.memctl doctor
```

常见原因包括安装路径不正确、Python 环境不可用、权限不足、socket 目录不存在或 unit 文件修改后未执行 `systemctl daemon-reload`。

### 13.4 Web 控制台打不开

先确认进程是否启动、端口是否占用：

```bash
python -m memguard.cli.memctl web --host 127.0.0.1 --port 8765
```

如果提示端口占用，换一个端口。若在虚拟机中访问，应确认是在虚拟机浏览器访问 `127.0.0.1`，还是从宿主机访问虚拟机 IP。默认绑定 `127.0.0.1` 只允许本机访问。

### 13.5 Kylin embedding SDK 探针超时

先运行：

```bash
python -m memguard.cli.memctl sdk-status --probe-embedding --probe-timeout 90
```

若 metadata 可达但推理超时，重点检查 `kytensor.service`、`kylin-ai-runtime.service`、模型仓库、`core-textembedding.sock`、ondevice 插件和虚拟机资源。可执行：

```bash
bash scripts/fix_kylin_embedding_runtime.sh --probe-timeout 180
bash scripts/diagnose_kylin_embedding.sh --restart-runtime --probe-timeout 180
```

SDK 不可用不会导致核心功能失败，系统会回退到 FTS/hash/graph 检索路径；但如果要展示“SDK 真实推理”，必须以 `sdk_embedding_probe.txt` 中 `inference_ready=true` 为准。

### 13.6 Peony 或 UKUI 入口不可见

先确认插件已构建和安装：

```bash
bash scripts/build_ukui_plugins.sh
bash scripts/install_ukui_plugins.sh --no-build
bash scripts/verify_ukui_plugins.sh
```

然后重启 Peony 和控制中心。若 `/usr` 只读，普通复制无法生效，应使用维护模式、`.deb` 安装方式或项目脚本提供的 bind mount 演示安装路径。设置里的控制中心入口一般可从系统设置或控制中心应用进入；如果界面缓存未刷新，注销或重启桌面会话通常能解决。

### 13.7 审计链校验失败

如果 `verify-audit` 返回失败，不要直接覆盖证据。先保存当前 `verify-audit` 输出、数据库文件、最近操作记录和相关日志。常见原因是手工修改数据库、复制恢复不完整、旧证据和新数据库混用或并发写入异常。处理顺序是：停止 daemon，确认当前 namespace，恢复完整备份，重新运行 `verify-audit`，再采集证据。

## 第14章 使用注意事项和交付建议

### 14.1 准确表述系统能力

对外介绍 MemGuardOS 时应准确描述能力边界。可以说明系统已实现多源记忆提取、敏感治理、轻量存储、混合检索、RAG 上下文、精准遗忘、审计链、namespace 隔离、systemd/cgroup、auditd、BPF LSM 安全测试路径、Peony/UKUI 可见入口、Kylin SDK 探测和可选增强。不要夸大为完整企业多租户平台、默认全局内核拦截、完整 UKUI 控制中心产品、云端 SaaS 或内置通用 LLM 生成器。

### 14.2 答辩演示顺序建议

建议使用 5 到 8 分钟演示路径：

1. 展示 README 当前状态和 openKylin 证据目录。
2. 运行 `memctl doctor` 和 `verify-audit`。
3. 写入一条偏好记忆和一条工具轨迹记忆。
4. 运行 `explain-extraction` 解释抽取结果。
5. 运行 `search`、`batch-search` 和 `build-context` 展示检索和上下文解释性。
6. 执行精准遗忘 dry-run，再执行确认删除和 `verify-forget`。
7. 展示 systemd/cgroup、Kylin SDK、auditd、Peony/UKUI 或 Web 控制台证据。
8. 展示 `check_competition_ready.py` 和正式提交包审计结果。

### 14.3 提交前检查清单

提交前建议逐项检查：

- `python -m pytest -q` 是否通过。
- `python -m ruff check memguard scripts tests` 是否通过。
- `python scripts/check_competition_ready.py` 是否通过。
- `python scripts/check_docs_links.py` 是否通过。
- openKylin 证据是否为最新且包含 SDK、10k、auditd、UKUI、BPF、审计链。
- `scripts/package_submission.py` 是否生成正式提交包。
- `scripts/inspect_submission_package.py` 是否显示 `ok: true` 且 `evidence_content_failures: []`。
- 正式包内是否没有本机私有路径、过程研究资料和临时文件。
- 演示视频、平台摘要、答辩稿和 GitHub 仓库是否与最终指标一致。

## 第15章 场景化使用说明

### 15.1 面向 OS Agent 的日常记忆积累场景

MemGuardOS 最常见的使用方式，是作为 OS Agent 的本地记忆底座长期运行。这里的“智能体”并不是指系统内部一定要包含一个完整大模型，而是指上游自动化程序、命令行助手、桌面助手或开发辅助工具可以把执行过程中的关键信息交给 MemGuardOS 管理。智能体在处理 Linux 任务时，经常会经历“理解任务、选择工具、执行命令、读取日志、修复错误、总结结果”的过程。这个过程里有大量值得保留的信息，例如用户喜欢怎样的回答格式、某个服务失败后通常先看哪个日志、某个 openKylin 环境里 Kylin SDK 需要先检查哪个 socket、某类测试失败后应该怎样复跑。MemGuardOS 的作用，就是把这些信息从一次性上下文中提取出来，变成后续任务可以复用的记忆。

在日常使用中，用户不需要每次都手工整理复杂 JSON。最简单的方式是把明确的偏好或经验用一句自然语言写入，例如“以后排查 systemd 服务时先给出 journalctl 和 systemctl status 命令”。系统会根据文本中的工具名、动作词和技术对象判断这更接近工具偏好或工作流知识，并把它存入对应层级。下一次 Agent 查询“systemd 服务启动失败如何排查”时，这条记忆就可能出现在 RAG 上下文中。这样做的好处是，记忆积累过程不会打断用户的正常工作，也不要求用户理解底层存储结构。

当任务来自自动化工具时，更推荐使用工具执行结果导入。工具执行结果比普通文本更有结构，因为它包含命令、工作目录、退出码、标准输出、标准错误、执行前状态、执行后状态和经验总结。系统可以把它建模为程序性记忆，也就是“在什么状态下做了什么动作，最后得到什么结果”。这类记忆对 OS Agent 很重要，因为操作系统任务往往不是单个知识点，而是一串可复用的处理流程。比如修改 service 文件后需要 daemon-reload，重启服务后需要检查 active 状态，最后还要查看 cgroup 配置是否生效。这些经验一旦沉淀下来，后续智能体就不必每次从零摸索。

在长期积累过程中，用户应注意区分不同层级。临时任务中使用的路径、一次性实验参数和短期排查线索可以放在 `working` 或 `session` 层；稳定的回答偏好、常用工具顺序、经过验证的 openKylin 修复流程更适合放在 `long_term` 层。层级不是为了增加操作复杂度，而是为了避免短期信息污染长期记忆。一个成熟的智能体记忆系统，不能把所有内容都永久保存，也不能把所有内容都当作临时上下文。MemGuardOS 通过层级、时间有效性和淘汰策略，让不同价值的信息有不同生命周期。

### 15.2 面向开发调试的工具轨迹场景

开发调试场景非常适合展示 MemGuardOS 的价值。开发人员在修复问题时，通常会不断运行测试、查看日志、搜索代码、修改配置、重启服务。传统做法是这些过程只存在终端历史和人的脑子里，一旦换人或隔几天再看，就很难复现当时的判断过程。MemGuardOS 可以把这些工具轨迹转为结构化记忆，使系统知道某个问题是如何被定位、如何被修复、修复后如何验证的。

例如开发者遇到 Web 控制台无法打开的问题，先运行 `memctl web`，发现端口被占用；再换端口启动，浏览器访问成功；随后运行 `web_dashboard_smoke.py`，确认页面、诊断、写入、检索和审计均正常。这个过程如果只写成一句“Web 控制台已修复”，价值很低。更好的方式是把命令、错误现象、修复动作和验证结果放入工具执行结果 JSON 中。MemGuardOS 会把它识别为工具轨迹或工作流知识，后续遇到“控制台打不开”时，可以把这条经验作为候选上下文返回。

对于调试类记忆，建议用户保留足够的上下文，但不要导入大量无意义输出。标准输出和错误输出如果很长，应先裁剪到关键部分，例如错误码、异常类型、失败文件、服务状态和最终验证结果。这样既能减少存储噪声，也能提高检索质量。MemGuardOS 的质量校验会对低价值输入给出提示，但系统无法完全替代人的判断。优秀的记忆输入通常具有三个特点：问题明确、动作明确、结果明确。只要这三个要素清楚，后续智能体就能更容易把它转化为可执行建议。

在多人协作或比赛准备中，工具轨迹还可以作为“证据链补充”。比如 openKylin 虚拟机中 Kylin embedding SDK 从超时到可用，中间可能经历模型仓库检查、runtime socket 检查、服务重启、探针复跑等步骤。把这些步骤写成程序性记忆后，答辩人员不仅可以说“我们解决了 SDK 推理问题”，还可以解释“我们怎样定位运行时、模型、socket 和服务状态之间的关系”。这种表达比单纯展示最终结果更有说服力，也更符合操作系统类赛题对系统理解能力的期待。

### 15.3 面向 RAG 上下文构建的生成前准备场景

MemGuardOS 并不把自己定位为完整问答机器人。它更像一个生成前的记忆整理器：当上游 Agent 准备调用本地或远程大模型时，先向 MemGuardOS 查询相关偏好、知识、工具经验和安全策略；MemGuardOS 根据查询构建一个已脱敏、可解释、带来源分布和选择轨迹的上下文包；上游 Agent 再把上下文包放入 prompt 中，让模型生成更符合用户习惯、系统状态和安全要求的回答。

这个流程的关键是“生成前控制”。如果把所有历史记录都直接塞给模型，既浪费 token，也容易把过时、冲突或敏感信息带入回答。MemGuardOS 的 `build-context` 会根据检索结果、记忆类型、层级、敏感级别和预算进行组织。输出中的 `explainability` 字段说明每条候选为什么被选择，哪些内容因为敏感或预算原因被过滤。这样，用户可以看到上下文不是黑箱产生的，而是有明确依据的。

在实际使用时，建议为不同任务设计不同查询。任务规划阶段可以查询“当前项目部署策略、用户偏好、限制条件”；工具调用阶段可以查询“历史命令、失败修复轨迹、日志查看方式”；结果生成阶段可以查询“输出风格、答辩口径、证据路径”。这种多阶段查询比一次性问一个大而泛的问题更稳定。MemGuardOS 提供 `batch-search`，正是为了让 Agent 同时准备多个子问题的记忆证据，而不是反复串行调用。

如果用户关心 token 成本，可以使用 `--token-budget` 控制上下文长度。需要注意的是，token 预算不是越小越好。预算太小会导致有价值的证据被裁掉，回答可能变得空泛；预算太大又可能让上游模型处理无关内容。比赛场景中，除非明确考察成本，不建议为了节省少量 token 牺牲展示效果。更合理的做法是保留足够上下文，同时展示系统具备预算控制能力。这样既能体现工程完整性，也不会影响核心功能表现。

### 15.4 面向精准遗忘的安全治理场景

精准遗忘是 MemGuardOS 的核心功能之一，也是区别普通检索系统的重要能力。普通系统删除数据时，往往只删除数据库中的一条记录，或者只把记录隐藏起来。MemGuardOS 的目标更进一步：用户用自然语言表达遗忘意图后，系统先找到可能相关的候选记忆，并解释匹配原因；用户确认后，系统执行逻辑删除、索引清理、时间有效性关闭、段文件 compact 和明文探针验证。这个流程更接近真实安全治理，而不是简单“删掉一行”。

在使用精准遗忘时，用户应先理解 dry-run 的意义。dry-run 不是多余步骤，而是防止误删的保护机制。自然语言请求可能存在歧义，例如“忘掉项目 A”可能指项目代号、项目路径、项目中的某个敏感配置，也可能指所有与项目 A 相关的经验。如果系统在没有确认的情况下直接删除，就会带来很高风险。因此，MemGuardOS 默认先返回候选列表，用户需要查看摘要、关键词、区别性关键词和得分，再决定是否确认执行。

精准遗忘执行后，验证同样重要。`verify-forget` 用于确认特定明文是否仍能被检索或在存储中发现；`verify-audit` 用于确认遗忘操作被审计链记录，并且审计链没有被破坏。对于正式证据，建议把遗忘前候选、遗忘执行结果、compact 后验证和审计链校验一起保存。这样可以形成完整闭环：系统知道要忘什么，用户确认了要忘什么，系统执行了遗忘，遗忘后明文不可再检索，审计链能证明这个过程。

用户还应避免把精准遗忘当作普通清理命令滥用。如果只是想清理短期工作记忆，可以使用层级淘汰；如果只是想更新旧偏好，可以使用版本更新；只有当用户明确要求某类信息不再被保存或使用时，才应执行精准遗忘。这样的使用边界有助于系统保持稳定，也便于答辩时说明“遗忘”与“更新”“淘汰”“回滚”之间的区别。

### 15.5 面向 openKylin 评审演示的完整链路场景

在 openKylin 评审演示中，推荐使用“先核心功能、再 OS 机制、最后证据包”的顺序。先展示 MemGuardOS 能够写入、检索、构建上下文和精准遗忘，让评委理解系统解决的核心问题；再展示 systemd、cgroup、auditd、Kylin SDK、Peony/UKUI 和 BPF LSM 测试路径，让评委看到作品与操作系统环境的结合；最后展示测试、评测、审计链和提交包审计，让评委确认结果可复验。

演示时不建议一开始就打开大量脚本或长日志。更自然的做法是先用一句话说明当前演示目标，例如“下面展示一条工具轨迹如何被写入为程序性记忆，并在 RAG 上下文中被选中”。随后运行写入命令、检索命令和 `build-context`，让输出自己证明系统能力。对于 OS 机制，可以选择两三个最有代表性的点展示，例如 `systemctl status memguardd`、`systemctl show memguardd -p MemoryMax`、`sdk-status --probe-embedding` 和 UKUI 控制中心入口。过多命令堆叠会让观众失去主线。

如果现场时间较短，可以把完整证据提前采集到 `docs/evidence/latest/`，现场只运行关键 smoke。这样既节省时间，也降低虚拟机临时波动带来的风险。正式答辩中应主动说明证据目录的来源、采集时间和关键文件含义。比如 `pytest.txt` 证明 openKylin 单元测试结果，`eval_result_openkylin_10k.json` 证明 10k 负载指标，`sdk_embedding_probe.txt` 证明 Kylin embedding 真实推理，`verify_audit.txt` 证明审计链校验。评委若追问，答辩人员可以直接打开对应文件，而不是临时翻找。

## 第16章 管理员运维说明

### 16.1 管理员的工作边界

MemGuardOS 当前更像本地系统组件，而不是完整企业后台平台。因此管理员的主要职责不是创建大量用户账号或配置复杂组织权限，而是保证本地运行环境稳定、服务能启动、存储路径安全、审计链完整、证据可复验。对于比赛项目来说，这种边界是合理的：赛题关注 Linux 智能体记忆提取与精准遗忘机制，系统需要证明端侧运行、OS 机制适配和安全治理能力，而不是强行包装成公网多租户 SaaS。

管理员首先要明确当前部署方式。如果是源码方式，重点在 Python 环境、项目路径和依赖完整性；如果是 systemd 服务方式，重点在 unit 文件、socket 权限、服务用户和 cgroup 限制；如果是 `.deb` 安装方式，重点在系统包内容、安装路径、服务注册和卸载恢复。不同部署方式的操作入口不同，但底层数据和功能逻辑是一致的。管理员在排查问题时，不应把入口问题误判为核心功能问题。

其次，管理员应建立固定的验证顺序。每次重启虚拟机、更新代码、安装新包或采集证据前，都建议先运行 `doctor`，再运行 `namespace-status`，然后运行 `verify-audit` 和一条简单搜索。这个顺序可以快速判断环境、存储、审计和检索是否正常。若一开始就运行复杂评测，失败时很难判断问题来自依赖、服务、数据还是评测脚本。

### 16.2 服务运行和资源控制

systemd 服务是 MemGuardOS 在 openKylin 中展示 OS 机制的重要入口。管理员安装服务后，应确认服务处于 active 状态，并查看 cgroup 中的资源限制是否生效。`MemoryMax` 和 `CPUQuota` 的意义不仅是限制资源，更是说明系统在端侧部署时考虑了资源边界。对于智能体记忆系统来说，不能无限占用内存和 CPU；即使当前项目规模不大，也应展示这种工程意识。

在长期运行中，管理员应关注三类问题。第一是服务是否正常启动，常见原因包括 Python 路径不正确、项目路径移动、socket 目录权限不足、unit 文件修改后未执行 daemon-reload。第二是资源限制是否过紧，如果评测或 SDK 推理需要更多内存，过低的 MemoryMax 可能导致进程异常退出。第三是日志是否出现重复错误，如果 journald 中持续出现相同异常，应先停止演示操作，保留日志，再定位问题。

模板服务 `memguardd@.service` 适合展示不同 namespace 的隔离能力。管理员可以启动 `memguardd@demo-a` 和 `memguardd@demo-b`，再分别查看它们的 ControlGroup、MemoryMax、socket 路径和数据路径。这样可以说明系统不仅在应用层用一个参数区分会话，也在 systemd/cgroup 层面为不同实例建立了可观察的运行边界。

### 16.3 存储目录和权限管理

MemGuardOS 的默认数据路径与当前用户和 namespace 有关。管理员在备份、迁移和排错前，必须先通过 `namespace-status` 确认真实路径，而不是凭经验猜测。很多审计链错误或数据找不到的问题，本质上是因为用户在一个 namespace 写入，却在另一个 namespace 检索；或者源码方式和 systemd 服务方式使用了不同用户，导致数据目录不同。

权限管理方面，演示环境为了方便普通用户验证，socket 权限可以较宽；但在多人环境中，不建议长期使用 `0666`。更稳妥的做法是建立专用用户组，把 socket 权限设为 `0660`，并启用本地 token 文件。这样只有被授权的本机用户才能调用 API。需要强调的是，MemGuardOS 的 token 是本地调用保护，不是企业级 SSO，也不是公网认证体系。文档和答辩中应保持这种准确表述。

备份时，管理员应同时考虑段文件、SQLite 元数据、自定义敏感规则和审计链。只备份其中一部分，恢复后就可能出现索引找不到记录、审计链不一致或遗忘验证异常。建议在执行大规模导入、精准遗忘、compact、版本升级和证据采集前都做一次备份。备份名称最好包含日期、namespace 和用途，例如 `memguardos-demo-a-before-compact-20260607`，便于后续追踪。

### 16.4 日志、诊断和证据归档

运维人员应把日志和证据分开管理。日志用于排查运行过程中的问题，证据用于证明系统能力和评测结果。日志可能包含较多临时信息和路径，不一定适合直接放入正式提交包；证据包则应经过筛选、脱敏、清单登记和哈希校验。MemGuardOS 的 `collect_openkylin_evidence.sh` 会把关键证据收集到 `docs/evidence/latest/`，并生成 manifest 和 SHA256SUMS，管理员仍然需要人工确认文件内容没有泄露隐私。

诊断命令 `diagnostics` 适合在现场出现异常时使用。它能够导出当前运行环境、检索后端、记忆策略和部分系统状态。遇到问题时，建议先保存诊断结果，再做修复操作。这样即使修复过程改变了现场状态，也能保留问题发生时的证据。对于比赛项目，这种习惯会显得很专业，因为它说明团队不是只追求“跑通一次”，而是具备可复盘的工程流程。

证据归档时，应区分最新证据和历史证据。正式提交包通常只需要最新证据目录，历史时间戳目录可以保留在完整仓库中供追溯，但不应全部塞进正式包。过多重复证据会增加评委阅读负担，也可能造成指标口径不一致。当前项目已经通过提交包审计脚本检查正式包边界，管理员在重新打包前仍应检查 README、平台摘要、答辩稿和测试报告中的指标是否与最新证据一致。

### 16.5 升级前后的操作建议

升级代码或安装包前，管理员应先确认当前工作树状态和数据备份。如果项目使用 Git 管理，应先查看 `git status`，避免把未提交的文档、证据或本地配置覆盖掉。升级后应先运行轻量 smoke，再运行完整测试。轻量 smoke 的目的在于快速确认系统入口可用，例如 `doctor`、一次写入、一次搜索和 `verify-audit`；完整测试则包括单元测试、评测、Web 控制台 smoke 和提交包审计。

如果升级后出现问题，不要急于同时修改多个地方。应先判断问题属于依赖、配置、服务、数据还是代码。依赖问题通常表现为模块导入失败；配置问题通常表现为路径、权限或环境变量异常；服务问题通常出现在 systemd status 和 journald 中；数据问题通常表现为审计链失败、检索不到旧记录或 namespace 不一致；代码问题则可能导致测试失败或功能异常。按类别定位可以避免越修越乱。

回滚时同样要谨慎。源码回滚只改变代码，不会自动回滚数据；数据恢复只改变存储，不会自动匹配代码版本。若升级涉及存储结构变化，回滚前应确认旧代码能否读取新数据。当前 MemGuardOS 的数据结构相对轻量，但在正式演示前仍建议保持代码、文档、证据和数据处于同一验证周期，不要临时混用多个版本。

## 第17章 数据安全治理说明

### 17.1 记忆系统中的敏感信息风险

智能体记忆系统天然容易接触敏感信息。用户可能在命令中粘贴 token，在日志中包含用户名和路径，在配置文件中保留密钥，在 shell 历史中留下服务器地址。普通聊天系统往往只关注回答是否正确，而记忆系统必须额外关注“哪些内容不应该被长期保存”。MemGuardOS 的敏感治理设计正是围绕这个问题展开：在写入前识别高风险信息，在上下文构建时进行脱敏，在遗忘时验证明文是否仍然残留。

敏感信息风险可以分为几类。第一类是直接凭据，例如 API Key、password、secret、private key 和 token，这类内容通常不应写入长期记忆。第二类是个人信息，例如手机号、邮箱、身份证号、中文姓名和地址，这类内容需要根据场景决定阻断或脱敏。第三类是系统高风险路径，例如 SSH 私钥、系统账户文件和敏感配置路径，这些内容即使不是凭据，也可能暴露系统结构。第四类是上下文敏感信息，例如项目代号、内部服务器名、未公开版本号，它们不一定能被通用正则识别，但用户可能明确要求遗忘。

安全治理不能只依赖一个正则列表。正则可以处理常见格式，但无法理解所有业务含义；自然语言规则可以更灵活，但也容易过宽。MemGuardOS 将内置规则、自定义规则、自然语言敏感规则和人工确认结合起来。写入时，系统尽量阻断明显高风险内容；构建上下文时，系统尽量减少敏感内容进入 prompt；执行遗忘时，系统要求先 dry-run 再确认。这样的设计不是追求绝对完美，而是在端侧系统中建立可解释、可审计、可复验的安全流程。

### 17.2 写入前的质量校验和敏感判断

输入质量校验是安全治理的第一道门。低质量输入不仅影响检索效果，也可能掩盖敏感内容。例如一段巨大日志中只有少数几行真正有价值，如果全部写入，既浪费存储，也增加泄露风险。MemGuardOS 会对空输入、控制字符、低信号文本、结构化字段缺失等情况给出拒绝或警告。用户看到质量警告后，不应简单忽略，而应判断输入是否需要裁剪、脱敏或改写。

敏感判断发生在记忆写入前。对于明显高风险内容，系统会拒绝写入，并记录敏感阻断审计。对于可脱敏内容，系统会保存脱敏后的版本，并在元数据中标注敏感标签。这样做有两个好处：一是避免原始敏感内容进入长期记忆；二是让后续检索和上下文构建知道这条记忆的敏感等级。上游 Agent 在使用上下文时，可以根据敏感等级决定是否进一步过滤。

用户在导入外部文件、shell 历史和 auditd 日志时，尤其需要注意写入前检查。外部文件通常不是为了记忆系统专门准备的，里面可能包含大量无关信息；shell 历史可能包含临时 token 或连接命令；auditd 日志可能包含真实系统路径。即使 MemGuardOS 有敏感检测，也建议用户先在测试环境中使用样例数据验证，再导入更大范围的数据。安全系统最可靠的使用方式，永远是自动检测和人工确认配合。

### 17.3 上下文构建中的脱敏和过滤

RAG 上下文是敏感信息最容易外传的环节。写入系统内部的记忆，如果只在本地存储中存在，风险相对可控；但一旦进入 prompt，就可能被发送给本地模型、远程模型或其他上游组件。因此，MemGuardOS 在 `build-context` 中把脱敏、过滤和解释性作为重点。输出不仅包含记忆片段，还包含敏感等级分布和选择轨迹，方便用户判断上下文是否适合交给模型。

脱敏并不意味着简单把所有内容替换成星号。过度脱敏会让上下文失去可用性，不脱敏又会带来风险。MemGuardOS 的目标是在可用性和安全性之间取得平衡。例如，对于命令排查经验，保留工具名、错误类型和处理步骤通常很有价值；对于真实 token、私钥或个人身份信息，则应阻断或替换。用户如果发现上下文中出现不应展示的内容，应先更新敏感规则，再重新构建上下文，而不是在 prompt 里临时手工删除。

如果上游模型是外部 API，风险控制应更严格。比赛项目默认不依赖外部 LLM，这让演示链路更稳定，也减少了费用和隐私风险。但项目提供了可选生成器接口，方便用户在明确配置后接入本地或兼容端点。使用外部端点前，用户应确认输入数据是否允许发送、API key 是否安全保存、上下文是否脱敏、日志是否会记录 prompt。文档中不提供默认外部 API 密钥，也不建议把密钥写入命令行历史。

### 17.4 遗忘后的安全验证

遗忘后的验证是精准遗忘可信度的关键。很多系统声称删除了数据，但实际上只是把记录标记为不可见，原始内容仍可能留在索引、缓存、日志或追加式存储文件中。MemGuardOS 的 compact 和 verify-forget 设计，就是为了把这个问题暴露出来。执行遗忘后，系统应确认元数据中不再返回该记忆，FTS 和图谱索引中不再召回该记忆，追加式段文件在 compact 后不再保留对应明文。

用户需要理解，append-only 段文件在未 compact 前可能仍有旧内容，这是追加式存储的正常特征。精准遗忘流程中使用 `--compact`，就是为了把逻辑删除推进到物理清理。验证输出通常会区分 metadata 是否泄漏、segment 是否泄漏和 irreversible 是否满足。答辩时可以借此解释系统为什么不是简单删除数据库记录，而是考虑了存储层残留。

遗忘验证也应纳入审计。一次完整的遗忘证据，最好包含 dry-run 候选、确认执行结果、compact 前后探针、verify-forget 和 verify-audit。这样可以证明系统既有用户确认，也有执行结果，还有存储清理和审计记录。如果只展示最后一句“删除成功”，评委很难判断精准遗忘是否真正可靠。

### 17.5 自定义敏感规则的管理原则

自定义敏感规则能够扩展系统识别能力，但也可能造成误判。一个过宽的规则，例如匹配所有包含数字的字符串，会让大量正常命令、版本号和配置值都被误判为敏感。一个过窄的规则，则可能漏掉变体格式。管理员添加规则时，应先明确规则目的，再用少量正例和反例测试。正例是应该被识别的敏感内容，反例是相似但不应被识别的正常内容。

自然语言敏感规则更适合表达业务意图，例如“把某个项目代号视为敏感”。系统会尝试把这种意图转为受限规则，但用户仍需要检查效果。对于比赛演示，建议使用清晰、范围小、容易解释的规则，避免临场添加复杂规则导致不可预期结果。规则变更后，应查看审计记录，确认系统记录了规则添加行为。

规则管理还应考虑 namespace。不同项目或会话可能有不同敏感词，如果所有规则都放在默认 namespace，可能造成互相影响。显式 namespace 的独立规则文件可以降低这种风险。比如 demo-a 用于展示 openKylin 服务内存限制，demo-b 用于展示 auditd shadow access，两者的敏感规则可以独立维护，互不干扰。

## 第18章 验收与答辩操作说明

### 18.1 验收人员应如何阅读本项目

验收人员阅读 MemGuardOS 时，建议把它看作“智能体记忆系统 + Linux/openKylin 系统机制适配”的组合，而不是只看成一个命令行小工具。项目的核心不在于某个单独命令，而在于多源输入、结构化抽取、轻量存储、混合检索、上下文构建、精准遗忘、审计验证和 OS 机制之间能否形成闭环。评审时如果只看写入和搜索，会低估项目；如果只看桌面插件，又会忽略核心算法和安全治理。

最直接的验收路径是从 README 当前状态开始。README 中列出了本地测试、openKylin 测试、10k 负载、Recall、敏感检测、精准遗忘和冲突处理等指标。随后查看 `docs/evidence/latest/`，确认这些指标有对应证据文件。最后运行少量 smoke 命令，确认当前环境能复现关键能力。这样的验收方式兼顾文档、证据和现场运行，不会完全依赖作者口头说明。

对于代码审查，建议重点查看 `memguard/service.py`、`memguard/store/`、`memguard/retrieval/`、`memguard/sensitivity/`、`memguard/forgetting/`、`memguard/api/server.py` 和 `scripts/`。这些目录分别对应核心服务、存储、检索、敏感治理、遗忘、API 和系统验证。评委如果追问“这个功能在哪里实现”，答辩人员可以快速定位，而不是只展示文档。

### 18.2 答辩讲解的推荐叙述方式

答辩讲解应避免一开始堆指标。更自然的叙述方式是先讲问题，再讲设计，再讲证据。可以这样展开：Linux OS Agent 在长期工作中会积累命令、日志、配置、工具调用和用户偏好，但普通聊天历史无法判断哪些内容应该记住、哪些内容应该脱敏、哪些内容应该遗忘。MemGuardOS 围绕这个问题提供端侧记忆管理，包括多源提取、敏感治理、轻量存储、混合检索、RAG 上下文和精准遗忘，并通过 openKylin 的 systemd、cgroup、auditd、Kylin SDK 和桌面插件证明系统不是纯应用层概念演示。

讲设计时，可以强调“端侧稳定优先”。项目没有把外部 LLM API 作为默认依赖，是为了保证 openKylin 环境中可复现。规则型和结构化抽取不是因为系统不能接入模型，而是为了让核心评测路径稳定、可解释、离线可运行。Kylin SDK 作为可选增强存在，当 SDK 真实推理可用时可以参与检索重排；不可用时系统回退到 FTS/hash/graph，不影响核心功能。这种表述既诚实，也能体现工程取舍。

讲证据时，应选择最有代表性的输出。比如 openKylin 单元测试证明代码能跑，10k 负载证明性能，sdk_embedding_probe 证明 SDK 真实推理，build_context_explainability 证明上下文解释性，hierarchical_graph_search 证明层次图谱，verify_audit 证明审计链，ukui 可见性证据证明桌面入口。每个证据只需要讲清楚“证明什么”，不必逐行解释全部内容。

### 18.3 现场演示的节奏控制

现场演示最容易出现的问题，是命令太多、节奏太快、观众看不出主线。建议把演示拆成四段。第一段展示核心记忆能力，用一条偏好和一条工具轨迹完成写入、检索和上下文构建。第二段展示精准遗忘，用 dry-run、确认删除、verify-forget 和 verify-audit 形成闭环。第三段展示 OS 机制，用 systemd/cgroup、auditd、Kylin SDK 或 UKUI 入口展示与 openKylin 的结合。第四段展示证据包和提交包审计，说明作品可复验、可提交、边界清晰。

每段演示前都应先说一句目标。例如“这一段展示系统如何把工具执行结果变成程序性记忆”。然后运行命令，解释输出中的关键字段。不要让观众自己从大段 JSON 中猜重点。演示后再用一句话收束，例如“这说明系统保存的不只是文本，而是状态、动作、结果和经验”。这种节奏更符合中文答辩习惯，也能减少技术细节过载。

如果现场虚拟机速度较慢，不要强行运行所有耗时评测。可以提前采集 10k 证据，现场只运行轻量 smoke 和打开证据文件。答辩中应说明“完整 10k 评测证据已在 openKylin 中采集，现场为节省时间只复跑 smoke”。这种说明是合理的，不会削弱可信度，反而体现对现场环境风险的控制。

### 18.4 对常见追问的解释口径

如果评委问“为什么抽取不是大模型做的”，可以回答：核心路径采用可解释、离线、可复现的规则与结构化抽取，是为了满足端侧系统和 openKylin 复验要求；项目保留 `MEMGUARD_EXTRACTOR_COMMAND` 和生成器 adapter，可在明确配置后接入本地模型或外部模型，但不把不可控外部 API 作为默认依赖。

如果评委问“Kylin SDK 是否真正使用”，应根据证据准确回答。当前证据中应以 `sdk_embedding_probe.txt` 和 `retrieval-status` 为准。如果 `inference_ready=true` 且检索结果显示 `vector_backend: "kylin_sdk"`，可以说明 SDK 已作为可选重排路径验证；如果某环境中 SDK 不可用，则说明系统有稳定 fallback。不要把“可检测”夸大成“所有检索都强制依赖 SDK”。

如果评委问“BPF LSM 是否生产级拦截”，应说明当前 BPF LSM 是安全测试路径，对指定测试文件完成强制拒绝验证，并支持 JSON denylist 策略生成。项目没有宣称默认拦截 `/etc/shadow` 或系统登录链路，因为那会带来较高风险，也不适合比赛原型环境。这样的回答比夸大能力更可信。

如果评委问“没有完整企业 UI 是否扣分”，可以说明本项目定位是 OS Agent 本地记忆基础设施，主要入口是 CLI、daemon、Unix socket API 和本地 Web 控制台，同时提供 Peony/UKUI 原生入口验证桌面集成方向。项目重点不是做一个漂亮后台，而是把记忆提取、精准遗忘、安全审计和 OS 机制做成可运行闭环。

### 18.5 验收资料的组织建议

正式提交资料应保持清晰。推荐把平台要求的作品压缩包、演示视频、平台摘要、答辩稿和 GitHub 链接分开准备。作品压缩包使用 `scripts/package_submission.py` 生成的 `memguardos_competition_submission_*.zip`，不要直接上传完整开发目录。完整仓库包可以作为开源补充，但正式包应更干净，避免把研究资料、临时缓存、本地连接记录和过程性文档混进去。

文档材料应以正式中文文档为主。研究笔记可以保留在仓库的研究目录中，但不应作为评委第一阅读入口。评委最需要的是项目介绍、部署手册、用户手册、测试报告、赛题符合性说明、证据包索引、答辩稿和操作手册。材料太多且重复，会让评委难以抓住重点；材料少但有清晰证据，反而更容易建立信任。

提交前应检查所有指标口径是否一致。比如本地测试是 63 passed，openKylin 证据是 60 passed，就不要在文档中混写。不同环境的测试数量不同是可以解释的，但如果文档互相矛盾，会让评委怀疑证据是否过期。每次重新采集证据后，都应同步更新 README、平台摘要、测试报告和答辩稿中的关键数字。

## 第19章 长期维护与二次开发说明

### 19.1 代码结构理解

MemGuardOS 的代码结构围绕核心服务展开。`memguard/service.py` 是多数功能的协调入口，负责调用质量校验、敏感检测、抽取、存储、检索、遗忘和审计。`memguard/store/` 负责 mmap 段文件、SQLite 元数据、FTS、图谱边和审计链。`memguard/retrieval/` 负责混合检索、Kylin SDK 适配、冲突处理和图谱召回。`memguard/sensitivity/` 负责敏感规则和自定义模式。`memguard/forgetting/` 负责遗忘候选规划。`memguard/api/server.py` 提供本地 Unix socket API。理解这些边界后，二次开发会更稳。

新增功能时，应优先沿用现有服务边界。比如新增一种输入源，不应直接绕过质量校验和敏感检测写数据库，而应接入统一 ingest 流程；新增一种检索信号，不应破坏原有 FTS/hash/graph fallback，而应作为可选评分因素；新增一种安全策略，不应只在 Web 控制台显示，而应进入敏感治理和审计链。这样可以保证新功能不会成为孤立 demo，也不会破坏已有测试。

测试同样要跟着边界走。核心服务变化应增加单元测试；检索排序变化应更新 benchmark 或相关断言；遗忘逻辑变化必须测试 dry-run、确认删除、compact 和 verify-forget；API 变化应测试 socket 请求和错误响应；文档和提交包变化应运行包审计。项目当前已经有提交包审计脚本，后续新增正式材料时，应确认它们不会把过程资料或本机路径带入正式包。

### 19.2 新增输入源的开发建议

如果后续要接入新的输入源，例如 IDE 操作日志、桌面通知、浏览器下载记录或任务管理器事件，建议先定义最小结构。一个好的输入源至少应包含来源、时间、主体、动作、对象、结果和可信度。没有这些字段也可以写入普通文本，但结构化程度越高，后续检索和解释越好。不要急于把所有原始日志都导入，应该先筛选出对智能体记忆有价值的事件。

新增输入源还应考虑敏感信息。IDE 日志可能包含项目路径和代码片段，浏览器记录可能包含 URL 和账号信息，桌面通知可能包含个人消息。接入前应先设计过滤策略，再写入系统。MemGuardOS 的价值不是“收集越多越好”，而是“把值得记的、允许记的、后续能用的信息记好”。这句话也适合作为后续开发的原则。

开发完成后，应提供一个 demo 数据文件和一条 CLI 或脚本入口。这样评审和维护人员可以独立复现输入源效果，而不必搭建复杂真实环境。比如 auditd 接入有规则预览、安装脚本和日志导入脚本；工具结果接入有 demo JSON；结构化配置接入有 demo 配置。新增输入源也应保持这种可复验风格。

### 19.3 新增检索后端的开发建议

检索后端扩展应遵循可选增强原则。现有系统默认使用稳定 fallback，是为了保证任何环境都能运行。如果接入新的向量模型、图数据库或检索服务，不能让核心功能变成强依赖。更合理的方式是检测可用性、设置超时、失败回退，并在 `retrieval-status` 中明确说明当前使用的后端。这样既能展示先进能力，也不会牺牲稳定性。

新增检索后端还应关注可解释性。检索结果不能只返回一个分数，最好说明分数来源，例如全文命中、向量相似、图谱关联、时间有效性、层级权重和敏感过滤。MemGuardOS 当前强调 `explainability` 和 `selection_trace`，后续后端也应把解释信息接入这个结构。否则新增模型虽然可能提高召回，却会削弱答辩时的可解释优势。

性能测试也很重要。新增后端可能在小样例上表现很好，但在 10k 或更大规模下延迟变高。开发者应至少运行本地 1k 和 openKylin 10k 评测，观察 P95、P99 和 RSS。若后端需要 GPU 或大内存，应明确标为可选增强，不应影响默认端侧部署路径。

### 19.4 新增 UI 或桌面能力的开发建议

如果后续要进一步增强 UI，建议先明确 UI 的目标。MemGuardOS 的 UI 不应变成泛泛的管理后台，而应服务于记忆写入、检索解释、精准遗忘、安全审计和系统诊断。一个好的 UI 应让用户更容易理解记忆系统，而不是只是把 CLI 命令包装成按钮。比如在精准遗忘页面，应突出候选解释、风险提示和二次确认；在 RAG 上下文页面，应突出来源分布、敏感过滤和选择轨迹；在诊断页面，应突出 SDK、审计链、namespace 和服务状态。

桌面插件方面，Peony 和 UKUI 的价值在于让记忆安全能力进入 openKylin 桌面工作流。右键菜单适合处理文件扫描和导入，控制中心适合做状态检查和诊断导出。后续如果继续开发，应优先补充真实文件扫描结果展示、扫描后写入策略选择、敏感命中解释和打开本地控制台的联动。不要只增加更多按钮，否则 UI 看起来热闹，但对用户任务帮助有限。

UI 开发还要注意权限和安全。控制台默认绑定本机地址是合理选择；如果未来支持远程访问，就必须增加更完整的认证、授权、CSRF 防护和日志审计。当前项目不应为了展示效果轻易把控制台绑定公网。比赛场景下，本地可见、可操作、可诊断已经足够说明产品化方向。

### 19.5 文档和证据的长期维护

随着项目继续开发，文档最容易出现的问题是“功能已经变了，说明没更新”。MemGuardOS 文档中包含大量指标、命令和证据路径，任何一次测试数量变化、证据更新时间变化、SDK 状态变化，都可能造成文档口径不一致。因此，后续维护应把文档更新作为开发完成的一部分，而不是最后临时补。

建议维护一套固定检查流程。开发功能后运行测试和评测，更新对应文档；采集 openKylin 证据后更新测试报告和证据索引；生成正式包后运行包审计；提交前全文搜索旧指标和本机路径。这个流程看起来繁琐，但对比赛交付非常重要。评委看到文档、代码和证据互相一致，会更容易相信项目成熟度。

同时，不是所有过程材料都适合进入正式文档。研究论文总结、调试记录、虚拟机连接方式、个人 GitHub 配置方法等内容可以帮助开发，但不应混入正式提交包。正式文档应面向评委和用户，讲清楚系统是什么、怎么用、如何验证、边界在哪里。过程资料可以留在开发目录或本地 notes 中，必要时作为团队内部参考。

## 第20章 实际操作流程详解

### 20.1 从空环境开始完成一次基础验证

对于第一次接触 MemGuardOS 的用户，最容易困惑的地方不是某一条命令本身，而是不知道应该按照什么顺序验证系统。建议从空环境开始时，不要马上导入大量数据，也不要直接运行完整评测。更稳妥的方式是先确认环境，再写入一条简单记忆，随后检索、构建上下文、执行审计校验。这个顺序能够帮助用户快速判断系统最核心的链路是否可用，也能避免在环境尚未稳定时引入大量干扰数据。

具体来说，用户进入项目目录后，首先应运行 `doctor`。这个命令不是业务功能，而是环境体检。它会帮助用户理解当前系统是否具备基本运行能力，例如 Python 环境是否正常、存储路径是否可写、可选系统能力是否能检测到。随后运行 `namespace-status`，确认当前数据会写入哪个 namespace。很多初学者在使用记忆系统时，会把数据写入默认 namespace，却在另一个 namespace 中搜索，最后误以为系统没有写入成功。先看 namespace，可以减少这类低级误会。

完成环境确认后，可以写入一条没有敏感内容的偏好记忆。建议使用明确、短小、容易检索的内容，例如“以后回答 Linux 命令时直接给命令和风险提示”。写入后，不要只查看命令是否返回成功，还应立即用相近语义进行检索，例如搜索“Linux 命令回答偏好”。如果检索能命中，说明写入、抽取、索引和检索链路已经贯通。最后运行 `verify-audit`，确认写入行为已经进入审计链。这一组操作虽然简单，却覆盖了系统最重要的基础能力。

当基础验证通过后，用户才适合继续导入配置、工具结果、shell 历史或 OS 事件。这样做的原因是，一旦后续复杂输入出现异常，用户可以确认问题不是基础环境造成的，而是特定输入格式、敏感规则或数据质量导致的。成熟的软件操作习惯往往不是一次运行所有功能，而是按层次逐步扩大验证范围。MemGuardOS 的命令设计也适合这种渐进式验证方式。

### 20.2 从开发任务中沉淀可复用记忆

在真实开发任务中，用户往往不会专门停下来写“记忆”。他们更多是在解决问题，例如修复测试失败、配置 systemd 服务、排查 SDK 推理超时、整理提交材料。MemGuardOS 的价值在于把这些过程中的关键经验沉淀下来。用户可以在任务结束后，把成功的命令、失败原因、修复动作和验证结果整理成工具执行结果，也可以直接把经验总结写成自然语言记忆。两种方式都可以，但结构化工具轨迹更适合后续 Agent 自动复用。

例如一次服务修复过程可能包括修改 unit 文件、执行 daemon-reload、重启服务、查看 active 状态、检查 cgroup 参数和复跑测试。如果用户只写入“服务修好了”，系统以后很难给出具体建议。如果用户写入“修改 memguardd.service 后，需要 systemctl daemon-reload，再 restart 服务，并用 systemctl is-active 和 systemctl show 检查状态”，这条记忆就会更有价值。它不仅说明结果，还说明了操作顺序和验证方式。

这种记忆积累方式适合长期使用。一个项目做得越久，团队积累的工具轨迹越多，后续 Agent 就越能给出符合本项目习惯的建议。比如本项目中，遇到 Python 依赖问题时会先检查代理和离线 wheelhouse；遇到 openKylin SDK 问题时会先看 kytensor、runtime socket 和模型仓库；遇到正式包问题时会运行包审计脚本。这些经验如果只存在对话记录里，后续很难稳定复用；如果进入 MemGuardOS，就可以成为 Agent 的本地工作记忆。

需要注意的是，开发轨迹不应无选择地全部导入。大量重复的测试输出、无意义的 `ls`、临时失败命令和完整长日志会降低记忆质量。用户应优先保存“有判断价值”的信息，例如错误现象、定位依据、修复动作和最终验证。MemGuardOS 有质量校验和敏感检测，但它不能完全替代人对经验价值的判断。写入质量越高，检索和上下文构建效果越稳定。

### 20.3 从敏感输入到安全输出的完整路径

安全场景下，用户应关注信息从输入到输出的全过程。MemGuardOS 的敏感治理不是单一命令，而是一条链路。首先，输入进入系统时会经过质量校验和敏感检测；其次，记忆写入时会根据敏感等级决定阻断、脱敏或保存；再次，检索和上下文构建时会根据敏感标签过滤或脱敏；最后，如果用户要求遗忘，系统会执行候选定位、确认删除、索引清理、段文件压缩和验证。只有把这些环节连起来看，才能理解系统为什么适合智能体记忆场景。

举例来说，如果用户导入一段包含 API Key 的日志，系统可能会直接阻断写入，并在审计中记录敏感阻断事件。此时用户不应绕过系统强行写入，而应把日志裁剪成不包含密钥的故障摘要。如果用户导入一段包含普通邮箱或路径的说明，系统可能会脱敏后保存，后续上下文中也只显示脱敏版本。这样的行为体现了安全优先原则：记忆系统不是尽可能保存所有内容，而是在可用性和安全性之间做控制。

当系统生成 RAG 上下文时，用户应查看 `explainability` 字段。这个字段不仅说明哪些记忆被选中，也能帮助用户观察敏感等级分布和过滤策略。如果上下文中出现不应该出现的信息，正确处理方式不是在最终 prompt 中手工删除，而是回到敏感规则、记忆内容或遗忘流程中进行治理。这样才能保证问题不只是这一次被遮住，而是在系统层面得到修复。

安全输出还包括对外表述的准确性。比如 Kylin SDK 真实推理可用时，可以说明系统完成了 SDK 探针和可选重排验证；如果某个环境只是能检测到库和头文件，却不能完成推理，就只能说 SDK metadata 可达，不能夸大为主检索路径已经完全由 SDK 驱动。类似地，BPF LSM 当前是安全测试路径，不能说成默认生产级全系统拦截。准确表达本身也是一种安全治理，因为它避免了评审和用户基于错误预期使用系统。

### 20.4 从证据采集到正式提交的操作路径

正式提交前，用户需要把“项目能跑”转化为“项目能被别人复验”。这一步经常被忽略。很多项目在开发者电脑上可以运行，但提交给评委后，评委看不到清晰证据，也不知道从哪里开始验证。MemGuardOS 为此准备了测试报告、openKylin 证据目录、竞赛就绪自检、提交包生成和提交包审计。操作人员应把这些工具串成固定流程，而不是临时手工整理。

建议先在本地运行单元测试和文档检查，确认代码和文档没有明显问题。然后在 openKylin 环境运行验证脚本和证据采集脚本，生成最新 `docs/evidence/latest/`。证据采集完成后，不要马上打包，而应先打开关键文件查看内容，例如 `pytest.txt`、`eval_result_openkylin_10k.json`、`sdk_embedding_probe.txt`、`verify_audit.txt`、`ukui_plugin_visible_final.txt`。确认这些文件内容清晰、指标合理、没有本机敏感路径后，再运行提交包脚本。

提交包生成后，必须运行 `inspect_submission_package.py`。这个脚本不仅检查文件是否存在，也检查正式包中是否混入研究资料、过程脚本、本机路径和弱证据。对于当前项目来说，包审计还会检查 SDK 真实推理、10k 指标、层次图谱证据和上下文解释性证据。换句话说，提交包不是简单压缩目录，而是经过质量门禁筛选的正式作品材料。

最后，用户还应运行 `smoke_submission_archive.py`。这个脚本会在解压后的正式包中执行核心编译和 smoke，证明包不是只在开发目录中可用。这样的提交流程比手工压缩更可靠，也更容易向评委解释。如果评委问“如何保证提交包没有漏文件或混入过程资料”，答辩人员可以展示包审计结果，而不是只凭口头承诺。

### 20.5 从用户问题到维护结论的排错路径

当用户反馈“系统不能用”时，维护人员不要急于修改代码。更好的做法是把问题拆成环境、入口、数据、功能和证据五个层面。环境层面看 Python、依赖、openKylin 服务、权限和路径；入口层面看 CLI、Web 控制台、Unix socket 或 systemd 哪个入口失败；数据层面看 namespace、存储路径和审计链；功能层面看写入、检索、上下文或遗忘哪一步异常；证据层面看输出是否能被保存和复验。

例如用户说 Web 控制台打不开，维护人员应先确认 CLI 是否正常。如果 CLI 正常，只是控制台打不开，问题可能是端口占用、绑定地址、浏览器访问位置或 token 配置；如果 CLI 也失败，问题可能在环境或依赖。再例如用户说检索不到记忆，应先运行 `namespace-status` 确认是否查错 namespace，再运行 `list` 查看记忆是否存在，再查看质量报告和敏感阻断审计。只有这些基础检查都通过后，才需要怀疑检索算法。

这种排错路径看似朴素，但非常实用。复杂系统的问题往往不是某个“高级算法”突然坏了，而是路径、权限、环境变量、数据隔离或证据口径不一致。MemGuardOS 的诊断命令和审计链就是为了帮助维护人员按层次定位问题。维护结论也应写清楚：问题现象是什么，影响范围是什么，定位依据是什么，修复动作是什么，验证结果是什么。这样的记录后续也可以作为程序性记忆写入系统。

## 第21章 质量验收与指标解释

### 21.1 功能完整性的验收思路

验收 MemGuardOS 时，不能只看某个单独功能是否有输出，而要看功能之间是否形成闭环。多源接入如果不能进入统一记忆模型，就只是导入脚本；检索如果不能构建上下文，就难以服务 Agent；精准遗忘如果没有审计和 compact，就缺少可信度；OS 机制如果没有证据，就容易变成口头描述。因此，功能完整性的验收应围绕“输入、建模、存储、检索、输出、遗忘、审计、复验”八个环节展开。

输入环节需要验证文本、文件、配置、工具结果、shell 历史、OS 事件和 auditd 事件。建模环节需要验证偏好、知识、安全策略、工具轨迹和 OS 事件能被区分。存储环节需要验证 mmap 段文件、SQLite 元数据、FTS、图谱和审计链。检索环节需要验证普通搜索、批量搜索、图谱搜索和层次图谱搜索。输出环节需要验证 `build-context` 和 `build-prompt`。遗忘环节需要验证 dry-run、确认删除、compact 和明文探针。审计环节需要验证 `verify-audit`。复验环节需要验证 openKylin 证据包和正式提交包。

如果这些环节都能被命令和证据支撑，就可以说明系统已经具备完整作品形态。反之，如果某个环节只能靠口头解释，就应继续补证据或补实现。比如只说“支持 Kylin SDK”不够，必须有 `sdk-status --probe-embedding` 或 C API 探针证据；只说“支持 UKUI”不够，必须有构建、安装和可见性证据；只说“精准遗忘可靠”不够，必须有遗忘前后和审计链证据。

### 21.2 指标数字的正确理解

当前文档中包含本地测试、openKylin 测试、10k 负载、Recall@5、敏感检测 F1、精准遗忘精确率和召回率等指标。用户阅读这些数字时，应理解它们的含义和边界。本地测试说明代码在 Windows 开发环境中通过当前测试集；openKylin 测试说明代码在目标系统中通过对应测试；10k 负载说明系统在指定规模样例和查询下的检索延迟；Recall@5 说明目标相关记忆是否能进入前五个结果；敏感检测 F1 说明当前样例集中的识别效果；精准遗忘指标说明候选定位在当前 benchmark 上的表现。

这些指标很重要，但不能被误解为无限泛化保证。任何自建 benchmark 都有覆盖边界。当前项目通过扩充真实日志、工具轨迹、干扰样例、跨语言样例和边界样例提高可信度，但仍应在答辩中承认评测集是项目构建的可复现实验集，不是覆盖所有真实世界输入的工业级大规模数据集。这样的表述不会削弱项目，反而说明团队理解评测边界。

指标还应注意环境差异。本地 P95 和 openKylin P95 不一定相同，因为硬件、虚拟机、磁盘、CPU 分配、系统负载和 Python 环境都会影响结果。文档中分别列出本地和 openKylin 指标，就是为了避免混淆。提交前如果重新运行评测，可能得到略有波动的结果。只要仍在合理范围内，就属于正常现象；如果波动很大，应检查虚拟机资源、后台服务、磁盘状态和是否混入异常数据。

### 21.3 文档质量的验收标准

正式文档的质量不只是字数多少，而是读者能否照着完成操作。一个合格的操作手册应说明软件是什么、适合谁、解决什么问题、需要什么环境、如何安装、如何启动、如何执行核心功能、如何处理异常、如何验证结果、如何理解边界。MemGuardOS 的中文操作手册围绕这些目标组织章节，并尽量用段落解释操作逻辑，而不是只放命令。

文档中使用表格的地方，主要是角色、环境、参数、风险和命令用途。这些信息适合结构化展示。对于背景、场景、原理、验收和维护建议，则使用自然段说明。这样的排版更符合中文软件说明书习惯，也便于转换为 Word 和 PDF。用户如果后续继续维护文档，应保持这种风格：表格用于对照，段落用于解释，步骤用于操作，不要把所有内容都写成短句清单。

文档验收还应检查准确性。不能为了显得强大而写不存在的账号体系、云端服务、企业 RBAC、默认 LLM 生成器或生产级内核拦截。当前文档对这些边界都做了说明。准确表述对比赛非常重要，因为评委往往会追问细节。一个诚实但证据充分的作品，比一个夸大但经不起追问的作品更有竞争力。

### 21.4 代码质量的验收标准

代码质量应从可运行、可测试、可维护和可扩展四个角度看。可运行意味着源码方式、openKylin 方式和提交包 smoke 都能执行核心功能。可测试意味着关键能力有单元测试、评测脚本和自检脚本。可维护意味着模块边界清楚，功能入口可定位，文档和代码一致。可扩展意味着新增输入源、检索后端、UI 或安全规则时，不需要推翻现有架构。

MemGuardOS 当前代码按功能分目录组织，核心服务、存储、检索、敏感治理、遗忘、API、Web 控制台和系统脚本各自有明确职责。维护人员在阅读代码时，应先从服务层理解业务流程，再进入具体模块。不要一开始就深入某个算法细节，否则容易失去整体感。比如检索问题可能涉及抽取、存储、索引、查询扩展、图谱和排序，不应只看某一个函数。

代码验收还应关注失败路径。一个成熟系统不仅要在正常输入下工作，也要在空输入、低质量输入、敏感输入、SDK 超时、服务不可用、审计链异常、namespace 不一致时给出合理反馈。MemGuardOS 的多个诊断和 fallback 机制正是为这些情况准备的。后续开发中，如果新增功能只处理成功路径，不处理失败路径，就会降低整体交付质量。

### 21.5 证据质量的验收标准

证据质量决定项目能否被复验。好的证据应具备三个特点：第一，来源明确，能够说明是在本地还是 openKylin 中生成；第二，内容具体，能够看到命令、指标或状态；第三，边界清楚，不混入无关过程材料或个人私密信息。MemGuardOS 的 `docs/evidence/latest/` 目录和提交包审计脚本，就是为了保证证据清晰。

证据文件不需要每个都很长。某些证据只要证明状态即可，例如 systemd active、cgroup 参数、SDK 推理维度、UKUI 可见性确认。某些证据则需要更完整，例如评测 JSON、审计链验证、提交包审计。用户应根据证据用途决定保留内容。过短会缺乏说服力，过长会增加阅读负担。正式提交时，最好通过证据索引告诉评委每个文件证明什么。

证据还需要与文档同步。比如文档写 openKylin 10k P95 为 19.115 ms，就应能在对应 JSON 或报告中找到这个值。如果文档和证据不一致，评委可能认为数据过期或不可信。每次重新采集证据后，都应更新指标口径，并运行全文搜索检查旧数字残留。这个步骤虽然细碎，但对正式交付很关键。

## 第22章 教学、课程设计与项目展示说明

### 22.1 作为课程设计项目的阅读方式

如果把 MemGuardOS 作为课程设计或研究生项目展示，阅读重点应放在问题定义、系统设计、实现机制和验证方法上。问题定义部分说明为什么 OS Agent 需要记忆提取与精准遗忘；系统设计部分说明多源接入、结构化建模、轻量存储、混合检索、安全治理和 OS 集成如何组合；实现机制部分说明各模块如何落地；验证方法部分说明测试、评测、openKylin 证据和提交包审计如何支撑结论。

课程设计文档不应只介绍界面，也不应只贴代码。教师或评审更关心学生是否理解系统结构、是否能解释设计取舍、是否能证明功能真实运行。MemGuardOS 的优势在于材料比较完整：既有命令行和 Web 控制台，也有系统服务、审计链、SDK 探针、桌面插件和性能评测。展示时应把这些内容串成故事，而不是逐个文件介绍。

如果需要写课程设计报告，可以把本操作手册作为使用说明和验收依据，再结合概要设计、详细设计、安全设计和测试报告形成完整材料。操作手册解决“怎么用”，设计文档解决“为什么这样做”，测试报告解决“效果如何”。三类文档职责不同，不应互相替代。

### 22.2 面向非开发读者的解释方式

非开发读者可能不熟悉 mmap、FTS、BPF LSM、auditd 或 RAG 等术语。向他们介绍时，应先用生活化但准确的比喻解释。可以说 MemGuardOS 像一个本地工作笔记管理员，它会从用户和系统的操作中挑出值得保留的经验；它不像普通笔记本那样什么都记，而是会先检查内容是否敏感；当用户要忘掉某个信息时，它不是只从列表里隐藏，而是会清理索引和存储残留，并留下审计证明。

解释 RAG 上下文时，可以说大模型回答问题前需要参考材料，MemGuardOS 负责从本地记忆中挑选合适材料，并把敏感信息处理好。这样大模型不必看到所有历史记录，也不必依赖用户每次重新描述偏好。解释精准遗忘时，可以说用户有权要求系统不再使用某些记忆，系统需要先找出相关记忆，再经用户确认后删除，并证明删除后不能再被检索。

对于操作系统机制，可以说项目不仅在应用层保存文字，还把 Linux 的服务管理、资源限制、审计日志和桌面入口结合进来。systemd 让系统能作为后台服务运行，cgroup 让服务受到资源约束，auditd 让系统能接收真实审计事件，Peony/UKUI 让能力进入桌面环境。这种解释既不牺牲准确性，也更容易让非开发读者理解。

### 22.3 面向技术评审的解释方式

技术评审更关注实现细节和边界。面对这类读者，应主动说明系统为什么选择 mmap + SQLite，而不是直接使用大型向量数据库；为什么默认使用可解释抽取，而不是强依赖 LLM；为什么 Kylin SDK 是可选增强，而不是唯一后端；为什么 BPF LSM 只作为安全测试路径，而不是默认生产拦截。主动说明取舍，可以减少评审误解。

技术评审还可能关注性能。此时应解释评测条件、样例规模、查询次数、P95/P99、RSS 和 openKylin 环境。不要只说“性能很好”，而要说明“在当前 openKylin 10k 负载证据中，P95、P99 和 RSS 分别是多少，低于项目自检阈值”。如果评委问更大规模，可以说明当前设计适合端侧轻量场景，未来可通过更强向量后端、分片索引或缓存策略扩展，但当前比赛交付以稳定可复验为主。

技术评审也会关注安全。此时应强调写入前敏感检测、上下文脱敏、精准遗忘、compact、审计链和本地 API token。对于未实现的企业级 RBAC、公网认证或生产级内核策略，不要回避，而应说明当前定位是本地 OS Agent 记忆模块，安全重点是端侧敏感治理和可审计遗忘。这样的回答比泛泛说“系统很安全”更专业。

### 22.4 展示材料的组织方式

项目展示材料应少而清晰。首页可以放项目名称、赛题名称、核心问题和一句定位。随后展示系统架构图，说明输入、处理、存储、检索、遗忘和 OS 机制。再展示核心功能截图或命令输出，最后展示指标和证据包。不要在一页幻灯片上放太多命令，也不要把所有指标堆在一起。每一页最好回答一个问题，例如“系统解决什么问题”“如何提取记忆”“如何精准遗忘”“如何接入 openKylin”“效果如何验证”。

演示视频也应采用类似结构。前三十秒说明项目定位，一分钟展示写入和检索，一分钟展示 RAG 上下文和解释性，一分钟展示精准遗忘和审计，一分钟展示 openKylin OS 机制和证据，最后三十秒总结指标和边界。视频不需要把所有命令都跑完，但必须让观众看清楚核心闭环。长时间等待测试运行的片段可以提前采集证据，现场只展示结果和关键 smoke。

文档材料则应放在项目的正式文档目录中，文件名使用中文，便于评委直接打开。当前操作手册已经提供 Markdown、DOCX 和 PDF 三种格式。Markdown 适合在 GitHub 阅读，DOCX 适合学校或企业提交，PDF 适合固定版式归档。三种格式内容应保持一致，不应出现 PDF 是旧版、Markdown 是新版的情况。

### 22.5 学习者应掌握的知识点

通过 MemGuardOS 项目，学习者可以掌握多个层面的知识。第一是 Python 工程组织，包括 CLI、服务层、模块边界、测试和打包。第二是本地存储，包括追加式文件、SQLite 元数据、FTS、图谱关系和审计链。第三是检索增强，包括全文检索、向量相似、图谱召回、上下文构建和 token 预算。第四是安全治理，包括敏感检测、脱敏、精准遗忘和审计验证。第五是 Linux/openKylin 机制，包括 systemd、cgroup、auditd、journald、BPF LSM 和桌面插件。

这些知识点之间不是孤立的。比如精准遗忘同时涉及自然语言请求解析、候选检索、数据库标记、索引清理、段文件 compact 和审计链；openKylin 证据同时涉及系统服务、资源限制、SDK 运行时和桌面环境。学习这个项目的价值，正是在于看到一个系统如何把算法、工程、安全和操作系统机制结合起来。

如果学习者要复现项目，建议先跑基础 CLI，再理解存储结构，然后看检索和遗忘，最后看 OS 集成。不要一开始就研究 BPF 或 UKUI 插件，因为那会增加环境复杂度。先掌握核心记忆链路，再逐步理解系统增强，学习效率会更高。

## 第23章 维护交接与风险控制说明

### 23.1 交接时应说明的内容

项目交接时，维护人员应向接手者说明五类信息。第一是代码仓库位置和当前分支，确保接手者知道从哪里获取最新代码。第二是运行环境，包括 Python 版本、openKylin 虚拟机状态、依赖安装方式和是否使用代理。第三是数据和证据位置，包括 namespace 存储路径、`docs/evidence/latest/`、正式提交包和历史包。第四是常用命令，包括测试、评测、证据采集、打包、包审计和 Git 推送。第五是已知边界，包括 SDK 可选增强、BPF LSM 测试路径、UKUI 插件安装方式和 Web 控制台本地绑定。

交接文档不应只写“项目在某目录”。接手者真正需要的是能够继续维护项目的上下文。例如，为什么 openKylin 证据中测试数量和本地测试数量不同；为什么正式包不包含研究资料；为什么 SDK 状态要区分 metadata 可达和 inference_ready；为什么 UI 入口不能夸大成完整企业控制台。这些信息如果不交接，后续维护人员很容易在文档中写错口径。

建议交接时现场运行一次轻量验证。维护者可以演示 `doctor`、`pytest`、`verify-audit`、`build-context`、`forget` dry-run 和包审计。接手者看到命令和输出后，会比只阅读文档更容易理解系统。对于 openKylin 虚拟机，还应说明登录方式、项目路径、维护模式、共享剪贴板和桌面插件可见性验证方法，但这些涉及个人环境的信息不应放入正式提交包。

### 23.2 常见维护风险

第一个风险是文档和代码不同步。项目开发过程中，功能经常更新，如果文档没有同步，评委或用户就会按照旧命令操作，导致失败。解决办法是把文档更新纳入开发完成标准。每次新增命令、修改参数、改变证据路径或更新指标，都应同步更新操作手册、README 和相关提交材料。

第二个风险是证据过期。证据包看起来很完整，但如果它来自旧版本代码，就不能证明当前版本。解决办法是在提交前复跑关键证据，并记录最新时间。正式包审计可以检查证据文件是否存在和部分指标是否达标，但它不能替代人工判断证据是否与当前演示状态一致。

第三个风险是本地私密信息混入。开发过程中会产生虚拟机连接记录、GitHub 配置方法、临时诊断、个人路径和代理设置。这些材料对开发有帮助，但不适合正式提交。解决办法是区分正式文档、研究资料、本地 notes 和证据包。正式包打包脚本和审计脚本已经做了部分限制，但维护人员仍应人工复核。

第四个风险是临场修改环境。答辩前临时升级 openKylin、重装 SDK、修改 service、调整权限或更新依赖，都可能破坏已验证状态。除非必须修复严重问题，否则演示前应冻结环境，只做轻量复跑。想继续优化，应另开开发环境，不要直接动正式演示环境。

### 23.3 风险问题的记录方式

遇到风险问题时，记录方式应清晰。建议使用“现象、影响、定位、处理、验证、后续建议”六段式。现象说明用户看到什么错误；影响说明哪些功能受影响；定位说明通过哪些命令判断原因；处理说明做了哪些修改；验证说明哪些命令通过；后续建议说明是否需要补测试、补文档或补证据。

例如 Kylin SDK 推理超时问题，不能只记录“SDK 修好了”。更好的记录是：现象为 `sdk-status --probe-embedding` 超时；影响为 Kylin embedding 不能作为可选重排证据；定位检查了 runtime socket、kytensor 服务、模型仓库和插件路径；处理执行了 runtime 修复脚本并重启相关服务；验证看到 `inference_ready=true` 和 dim=768；后续建议是提交前复跑 SDK 探针并保存证据。这种记录可以直接转化为程序性记忆。

风险记录也应区分内部和外部版本。内部记录可以包含更多过程细节，帮助团队复盘；外部提交材料则应只呈现结论、证据和准确边界。比如内部可以记录虚拟机登录方式，正式文档不应包含密码；内部可以记录某次失败尝试，正式手册不需要描述所有弯路。文档面向谁，决定了应该保留哪些内容。

### 23.4 交付后的持续改进方向

交付后，如果继续打磨项目，建议优先提升真实数据覆盖和用户体验。真实数据覆盖方面，可以继续扩充 shell history、工具调用、openKylin 日志、SDK 故障、UKUI 操作和误删边界样例，让 benchmark 更有说服力。用户体验方面，可以优化 Web 控制台的信息层级，让写入、检索、上下文和遗忘更容易被非开发用户理解。

算法方面，可以继续探索更强的端侧抽取和检索增强，但仍应保持默认可复现。比如接入本地小模型做实体识别或摘要时，应提供超时、失败回退和开关；接入更强向量模型时，应记录模型来源、维度、资源占用和指标变化；接入外部 LLM 时，应明确费用、隐私和配置边界。不要为了追求“高级”而破坏当前系统最大的优势：稳定、可解释、可审计、可在 openKylin 复验。

OS 集成方面，可以继续增强 Peony 文件扫描结果展示、UKUI 控制中心页面信息密度、systemd 模板服务管理和 BPF LSM 策略生成说明。需要注意的是，越靠近系统底层，风险越高。任何内核或系统级增强都应先在测试文件和隔离环境中验证，不应直接作用于真实敏感路径。比赛原型可以展示方向，但必须对安全边界保持克制。

### 23.5 项目成熟度的判断标准

判断 MemGuardOS 是否成熟，不能只看功能数量。更重要的是看每个功能是否有清晰入口、清晰文档、清晰证据和清晰边界。一个功能如果只能在开发者口头解释下运行，就还不够成熟；一个功能如果有命令、有测试、有文档、有证据、有失败处理，就更接近可交付。当前项目已经围绕核心赛题形成了较完整闭环，后续维护应继续沿着这个方向提升，而不是盲目增加分散功能。

成熟项目还需要“可被别人接手”。如果只有原作者知道怎么运行，项目就不算真正交付。操作手册、部署手册、测试报告、证据索引、答辩稿和提交包审计，都是为了降低接手成本。后续任何优化都应问一个问题：这会不会让评委、用户或维护人员更容易理解和复验？如果答案是否定的，就应谨慎加入。

最后，项目成熟度也体现在诚实表达上。MemGuardOS 可以突出自己的亮点：多源记忆、程序性记忆、上下文解释性、层次图谱、精准遗忘、审计链、openKylin 系统机制和证据闭环。同时也应保留准确边界：默认不依赖外部 LLM，不宣称完整企业多租户平台，不把 BPF LSM 测试路径夸大为生产拦截，不把桌面入口夸大为完整控制中心产品。这样的表达更稳，也更经得起严格评审。

## 附录A 常用命令速查

```bash
# 环境检查
python -m memguard.cli.memctl doctor

# 写入文本
python -m memguard.cli.memctl ingest --text "以后回答 Linux 命令时直接给命令和风险提示。"

# 抽取解释
python -m memguard.cli.memctl explain-extraction --text "systemd service failed with exit_code=1，修复后运行 pytest。"

# 检索
python -m memguard.cli.memctl search "openKylin 服务内存限制"

# 构建上下文
python -m memguard.cli.memctl build-context "openKylin 服务内存限制" --top-k 5 --token-budget 1200

# 精准遗忘
python -m memguard.cli.memctl forget "忘掉 temporary project codename"
python -m memguard.cli.memctl forget "忘掉 temporary project codename" --yes --compact
python -m memguard.cli.memctl verify-forget "temporary project codename"

# 审计
python -m memguard.cli.memctl audit --limit 20
python -m memguard.cli.memctl verify-audit

# Web 控制台
python -m memguard.cli.memctl web --host 127.0.0.1 --port 8765

# openKylin 证据
bash scripts/openkylin_verify.sh
bash scripts/collect_openkylin_evidence.sh --with-10k
```

## 附录B 文档质量自检

本文已按以下标准进行自检：

- 已说明产品名称、版本、目标用户、部署形态和文档范围。
- 已覆盖软件概述、运行环境、安装启动、CLI、Web 控制台、API、openKylin 验证、评测证据、安全审计和故障处理。
- 操作步骤均给出命令、适用场景和预期结果。
- 高风险操作包含风险提示。
- 未编造账号、密码、公网地址、外部 API 购买要求或未确认企业能力。
- 对 BPF LSM、Kylin SDK、Peony/UKUI、Web 控制台等能力均保持准确边界。
- Markdown 源文档可转换为 Word、PDF 或在线帮助中心。
