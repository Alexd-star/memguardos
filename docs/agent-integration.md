# MemGuardOS 真实 Agent 接入使用说明

本文说明如何把现有 OpenHands Agent 接入 MemGuardOS，使用户与 Agent 的消息、反馈、工具调用和工具结果自动进入统一事件链，并让后续会话在任务规划、工具选择和结果生成三个阶段读取经过治理的记忆。该接入用于落实赛题老师对“用户操作习惯”的解释：重点采集用户与 Agent 的交互习惯，而不是无差别监控桌面应用。

## 1. 接入目标

接入完成后，系统形成以下闭环：

```text
用户消息
  -> OpenHands 执行任务
  -> MemGuardOS 规范化消息、工具调用和工具结果
  -> 提取显式偏好、隐式习惯、纠正反馈和程序性经验
  -> 写入会话事件链与偏好/知识记忆
  -> 新会话按规划、工具选择、结果生成阶段构建上下文
  -> OpenHands 使用上下文执行任务
  -> 用户可按 Agent、会话、轮次、主题或时间范围精准遗忘
```

MemGuardOS 不修改 OpenHands 核心源码。适配层位于 `memguard/integrations/`，通过公开的会话事件和 Hook 接口完成上下文注入与执行结果回收，因此 Agent 升级与记忆系统升级可以分别进行。

## 1.1 一键进入真实 Agent 记忆工作台

五包安装后，第一次使用真实 Agent 时运行：

```bash
memguard-agent-setup
```

以后可以从任意目录启动，并把需要 Agent 操作的目录作为参数传入：

```bash
memguard-agent-showcase "$HOME/要操作的项目"
```

启动器优先使用 `/opt/memguardos` 中经过安装包管理的程序，并从用户数据目录加载独立的 OpenHands 运行环境；源码开发目录只作为未安装产品时的后备。它会自动读取 `~/.config/memguardos/` 下权限为 `600` 的模型配置，关闭 OpenHands 调试横幅，并进入带 `/guide`、`/memory on|off`、`/run` 和自然语言遗忘的中文工作台。用户可以直接输入“忘掉 grep 的习惯”。只有一条候选时输入 `yes`/“确认”；出现多条候选时先输入编号（如 `1` 或 `1,3`）或“全部”，再确认执行；输入 `no`/“取消”则停止。误写为 `/run 忘掉……` 时也会被安全转入遗忘预览。用户不需要执行 `cd ~/memguardos`，模型凭据也不会写入项目目录、Git 或演示输出。需要直接录制两轮对照时，可运行 `memguard --model <模型> compare-live "<任务>"`。

工作台不会把任意一句话都默认为长期记忆。明确的“记住/保存/以后采用”进入记忆治理流程，普通任务使用 `/run`，语义不确定时界面同时给出两种可执行写法。该边界用于避免疑问句、代码说明和一次性操作被误判为长期偏好。自然语言保存、Web 保存和 Agent 事件接入最终共享同一套敏感检测、保留期限、来源治理和反馈结构；可选语义增强器只能补充分类，不能绕过本机敏感阻断和结构校验。

## 2. 主要组件

| 组件 | 作用 |
|---|---|
| `normalizer.py` | 把 OpenHands 原生事件转换为稳定的 MemGuardOS 事件协议 |
| `events.py` | 定义用户消息、Agent 消息、工具调用、工具结果、反馈和错误等事件结构 |
| `openhands_adapter.py` | 读取 OpenHands 会话事件并完成角色、轮次和工具字段映射 |
| `openhands_runner.py` | 创建会话、注入记忆上下文、运行 Agent 并回收真实事件 |
| `context_injector.py` | 分别构建任务规划、工具选择和结果生成上下文 |
| `interaction_extractor.py` | 从用户与 Agent 的交互中提取显式偏好、纠正和反馈 |
| `habit_aggregator.py` | 聚合跨轮次隐式习惯，避免把一次性动作误判为长期偏好 |
| `bridge.py` | 接收 systemd 队列任务并在受限进程中运行 OpenHands |
| `replay.py` | 重放标准事件，便于离线回归和故障复现 |

### 2.1 接入其他 Agent：通用 JSON/JSONL 适配器

OpenHands 是当前完成真实端到端验证的第一方适配器，但记忆内核不把事件格式绑定到某一家 Agent。没有专用 SDK 的本地 Agent 可以输出提供方无关的 JSON/JSONL，再由 `generic-json` 适配器统一映射。适配器只负责识别事件类型、角色、会话、轮次和工具字段；敏感检测、偏好提取、作用域隔离、版本管理和精准遗忘仍由 MemGuardOS 服务完成。

先查看能力目录：

```bash
memctl agent adapters
```

通用 JSONL 每行一个对象，最小示例为：

```json
{"type":"message","role":"user","content":"以后搜索代码优先使用 rg"}
{"type":"action","id":"call-1","tool":"terminal","arguments":{"command":"rg build_agent_context"}}
{"type":"observation","id":"result-1","tool":"terminal","output":"memguard/service.py:4201","exit_code":0}
```

接入时显式指定 Agent、版本、会话和项目范围：

```bash
memctl agent ingest-adapted-session \
  --file agent-events.jsonl \
  --adapter generic-json \
  --agent-name local-agent \
  --agent-version 0.4 \
  --session-id session-001 \
  --project-root "$PWD" \
  --task-type code_search
```

系统会先完整转换整份 JSONL，再一次性进入统一事件写入流程；事件类型不支持、缺少会话编号或工具参数不是对象时，整批失败并不留下部分会话。重复事件依据事件内容哈希幂等处理。工具参数中的密码、Token 和授权头仍会在持久化前递归脱敏，工具输出中的“以后”“优先”等文字也不会被当作用户意愿。适配器不能绕过这些治理边界。

事件写入后的记忆还要经过统一治理状态机。安全且可解释的内容进入 `active`，可疑外部材料进入 `quarantined`；旧偏好、撤回来源和用户遗忘分别进入 `superseded`、`source_invalidated` 和 `forgotten`。适配器只负责把外部事件翻译成规范事件，不直接修改治理状态，也不能绕过敏感阻断、版本校验或遗忘验证。需要查看状态语义时，可使用 `memctl governance states`。

若 Agent 使用自己的 SDK，则直接实现 `convert(raw_event) -> AgentInteractionEvent` 并注册到 `AgentAdapterRegistry`，再调用 `MemGuardService.ingest_interaction`。上下文仍通过 `build_agent_context` 按规划、工具选择和回答生成阶段请求，遗忘仍调用统一的 `forget` 或 `forget_agent_interactions` 接口；不建议在适配器中自行拼接长期记忆或直接操作 SQLite。

## 3. 安装可选 Agent 环境

核心 MemGuardOS 不依赖 OpenHands。只有需要运行真实 Agent 闭环时才安装可选环境。产品安装用户执行：

```bash
memguard-agent-setup
```

该命令使用 Python 3.12 或更高版本，在 `~/.local/share/memguardos/agent-venv` 创建用户级运行环境，不修改系统 Python。源码开发者也可以显式传入项目目录，继续使用仓库内虚拟环境：

```bash
cd ~/memguardos
bash scripts/install_agent_extras_openkylin.sh "$(pwd)"
```

脚本会创建 `.venv-agent`，安装项目本体与 OpenHands SDK。若 openKylin 虚拟机访问 PyPI 或 GitHub 超时，可显式传入代理地址。代理仅用于安装依赖，不是产品运行依赖：

```bash
bash scripts/install_agent_extras_openkylin.sh "$(pwd)" http://127.0.0.1:17897
```

当 Windows VPN 只监听宿主机 `127.0.0.1:7897` 时，虚拟机无法直接访问该回环地址。可在临时 SSH 会话中建立反向端口转发：

```powershell
ssh -N -R 17897:127.0.0.1:7897 luo@<openKylin虚拟机IP>
```

该命令建立的是安装期网络通道，关闭 SSH 会话后即失效。正式演示不需要保持代理。

## 4. 配置本地模型

OpenHands 可以连接兼容的本地或远程模型。项目的 openKylin 实证使用本机 Ollama，不需要购买 LLM API。示例配置如下：

```bash
export OPENHANDS_MODEL=ollama/qwen2.5-coder:1.5b
export OPENHANDS_OLLAMA_BASE_URL=http://127.0.0.1:11434
```

小参数本地模型适合证明真实 Agent 能运行、能调用终端并能被记忆系统治理，但其复杂命令生成稳定性不等同于大型云端模型。比赛主张应聚焦“记忆适配器和治理闭环正确工作”，不能把小模型偶发的命令语法错误包装成记忆系统错误，也不能宣称本地模型在所有任务上稳定完成。

## 4.1 使用远程模型 API 进行流畅演示

当 openKylin 虚拟机 CPU 和内存有限时，推荐把大模型推理放到远程 OpenAI 兼容 API，把虚拟机留给 OpenHands、终端工具和 MemGuardOS 记忆治理。这样更接近真实 Agent 产品的部署方式：Agent 在本机执行受控工具，模型只负责规划和生成文本，记忆的采集、检索、遗忘和审计仍在 MemGuardOS 本机完成。

远程 API 演示应使用环境变量传入密钥，不要把密钥写入命令、文档或配置仓库：

```bash
export OPENHANDS_API_KEY="在当前终端临时填写"
export OPENHANDS_SUPPRESS_BANNER=1
export OPENHANDS_LITELLM_STORE=true
export OPENHANDS_MODEL_CANONICAL_NAME=gpt-4o-mini

.venv-agent/bin/python scripts/run_openhands_memguard_demo.py \
  --namespace realagent-api-demo \
  --session-id api-demo-01 \
  --task "以后搜索代码优先使用 rg。请查找 build_agent_context 的定义，并先给命令再解释。" \
  --model openai/gpt-5.4-mini \
  --base-url https://ai.pite.chat/v1 \
  --workspace "$(pwd)" \
  --max-iterations 8
```

其中 `OPENHANDS_LITELLM_STORE=true` 用于兼容部分 OpenAI 兼容服务的 Responses API 会话持久化行为；`OPENHANDS_MODEL_CANONICAL_NAME` 只用于 OpenHands 能力识别，不改变实际调用的模型名称。若虚拟机无法直连外网，但宿主机 VPN 正常，可在同一个 SSH 会话中使用反向端口转发，使虚拟机通过宿主机代理访问 API：

```powershell
ssh -R 17897:127.0.0.1:7897 luo@<openKylin虚拟机IP> `
  "cd ~/memguardos && HTTPS_PROXY=http://127.0.0.1:17897 HTTP_PROXY=http://127.0.0.1:17897 <运行命令>"
```

远程模型 API 能显著提高演示流畅度，但它不是 MemGuardOS 的硬依赖。正式说明时应明确：远程模型验证的是“现有 OpenHands Agent + 远程模型 + MemGuardOS 适配器”的完整闭环；不是证明模型本身被本项目训练，也不是把 API 服务能力算作记忆系统能力。

## 5. 运行前检查

先执行不产生实际 Agent 任务的初始化检查：

```bash
.venv-agent/bin/python scripts/run_openhands_memguard_demo.py \
  --namespace realagent-demo \
  --session-id dry-init \
  --task "检查 OpenHands 运行环境" \
  --dry-init
```

通过标准包括：

1. OpenHands SDK 能导入；
2. Agent 会话对象能够创建；
3. 终端工具配置有效；
4. MemGuardOS namespace 和上下文构建器可用；
5. 不出现 API Key、数据库或段文件路径泄漏。

## 6. 运行真实闭环

```bash
.venv-agent/bin/python scripts/run_openhands_memguard_demo.py \
  --namespace realagent-demo \
  --session-id real-agent-session-a \
  --task "以后搜索代码统一使用 rg，并先给命令再解释。请用 rg 查找 build_agent_context 的定义。" \
  --max-iterations 8
```

执行过程分为四步：

1. MemGuardOS 为当前任务构建三阶段上下文；
2. runner 把上下文注入 OpenHands 会话；
3. OpenHands 调用真实终端工具；
4. adapter 回收用户消息、Agent 消息、工具调用和工具结果，并写入事件链。

查询交互状态：

```bash
python -m memguard.cli.memctl --namespace realagent-demo agent status
```

构建下一会话的三个阶段上下文：

```bash
python -m memguard.cli.memctl --namespace realagent-demo agent context \
  --agent-name openhands \
  --session-id real-agent-session-b \
  --stage tool_selection \
  --task "查找记忆上下文构建代码"
```

预期结果是工具选择阶段包含 `rg` 偏好，结果生成阶段包含“先命令后解释”的偏好，并附带来源记忆、选择理由和过滤理由。

## 7. systemd 队列桥

`scripts/memguard-agent@.service` 用于把 Agent 任务放入独立 systemd 单元。通过 `memguardos-agent-bridge` 软件包安装后，可直接按 Linux 用户启动：

```bash
sudo systemctl daemon-reload
sudo systemctl enable --now memguard-agent@luo.service
```

早期版本曾指导用户把模板手工复制到 `/etc/systemd/system/memguard-agent@.service`。该路径优先级高于软件包安装到 `/lib/systemd/system` 的新单元。升级安装时，`memguardos-agent-bridge` 只会识别包含旧版固定解释器 `ExecStart` 的已知模板，将其备份为 `memguard-agent@.service.memguardos-legacy` 后移除；管理员自行修改过、未匹配旧签名的单元不会被覆盖。升级后可用 `systemctl cat memguard-agent@luo.service` 确认当前加载的是软件包单元。

服务通过 `/usr/lib/memguardos/memguardos-agent-launcher.sh` 选择 Python 解释器，顺序如下：

1. 管理员在 `/etc/memguardos/agent-luo.env` 中设置的 `MEMGUARD_AGENT_PYTHON`；
2. `/opt/memguardos/.venv-agent/bin/python`；
3. 当前用户主目录下 `memguardos/.venv-agent/bin/python`；
4. 系统 `/usr/bin/python3`。

如果管理员显式配置的解释器不存在或不可执行，服务会停止并在 journald 中给出明确原因，不会静默换用其他环境。未安装 OpenHands 扩展时，系统 Python 仍可启动队列桥和返回结构化失败结果；要执行真实 Agent 任务，应先运行 `scripts/install_agent_extras_openkylin.sh`，或把 `MEMGUARD_AGENT_PYTHON` 指向已安装 `openhands-sdk` 的受控虚拟环境。

任务以 JSON 文件写入用户队列目录。桥接进程只读取允许字段，不保存 OpenHands 原始任务归档：

```json
{
  "namespace": "realagent-demo",
  "session_id": "queued-session-01",
  "task": "使用 rg 查找 build_agent_context",
  "max_iterations": 8
}
```

服务单元使用普通用户运行，并启用 `NoNewPrivileges`、`PrivateTmp`、`PrivateDevices`、`ProtectSystem=strict`、`MemoryMax=2G` 和 `CPUQuota=100%`。`StateDirectory=memguardos-agent/%i` 会由 systemd 自动创建权限为 `0700` 的用户状态目录，避免首次启动依赖人工创建 `/var/lib` 路径。这些限制用于降低 Agent 工具执行的系统影响，不等同于容器或虚拟机级安全隔离。

## 8. 精准遗忘 Agent 交互

遗忘前先 dry-run：

```bash
python -m memguard.cli.memctl --namespace realagent-demo agent forget \
  --agent-name openhands \
  --session-id real-agent-session-a \
  --preference-topic "先给命令再解释"
```

确认候选后执行删除和 compact：

```bash
python -m memguard.cli.memctl --namespace realagent-demo agent forget \
  --agent-name openhands \
  --session-id real-agent-session-a \
  --preference-topic "先给命令再解释" \
  --confirm
```

系统同时处理三类对象：

1. 交互事件中的相关明文字段；
2. 由这些事件生成的偏好或知识记忆；
3. FTS、图谱和段文件中的可检索副本。

验证结果应包含 `all_stages_absent=true`、`event_payload_scrubbed=true` 和 `segment_plaintext_absent=true`。若 OpenHands、终端历史、远程模型服务或外部日志仍保留副本，结果会提示外部持久化风险。MemGuardOS 只能保证自己管理的存储边界，不能替代外部系统的数据删除接口。

已经构建的 OpenHands 上下文携带作用域、所选版本和适用偏好签名。工具前检查点会重新读取当前状态；记忆被遗忘、更新、移出当前范围，或新加入适用的覆盖偏好时，当前上下文失效，任务停止并提示重新开始。重新开始后仍有效的其他记忆继续参与任务。此机制防止旧上下文在后续检查点继续发挥作用，不中断已经开始执行的工具，也不删除外部模型的内部缓存。接入其他 Agent 时须在其上下文与工具调用边界实现对应检查。

## 9. 证据采集

```bash
bash scripts/collect_agent_evidence.sh
```

精选结果位于 `docs/evidence/agent/`，包括 200 条交互专项 benchmark、16 组确定性记忆价值实验、真实 OpenHands 基础运行、扩展运行和留出运行汇总。逐案例原始事件、上下文和校验清单作为 Release 附件发布，源代码树只保留可直接理解的代表性结果。

## 10. 常见问题

### 10.1 GitHub 或依赖下载超时

先确认虚拟机是否能直连目标站点。若宿主机 VPN 正常而虚拟机直连失败，原因通常是 VPN 只代理宿主机应用或只监听宿主机回环地址。使用前文的 SSH 反向端口转发即可，不要把代理地址写入正式服务配置。

### 10.2 OpenHands 能创建会话但工具无法执行

检查 `rg`、`git` 和 shell 是否在 Agent 进程的 `PATH` 中：

```bash
command -v rg
command -v git
systemctl show memguard-agent@luo.service -p Environment
```

openKylin 实证中曾使用临时 bind mount 暴露用户态 `rg`，该操作不是正式产品安装方式。正式部署应通过系统包、`.deb` 依赖或管理员批准的 `/usr/local/bin` 安装提供工具。

### 10.3 本地模型生成错误命令

首先检查上下文中是否已经正确选择工具，再区分“记忆选择错误”和“模型执行错误”。若上下文明确包含 `rg`，但小模型拼接了错误参数，应降低任务复杂度、换用更强模型或由工具策略层限制命令模板。证据中必须如实记录，不应把一次成功泛化为所有任务均成功。

### 10.4 为什么不默认使用云端模型

赛题核心是记忆提取、复用、精准遗忘和 OS 机制，而不是比较 LLM 能力。默认离线设计更便于 openKylin 复验、控制成本、保护隐私和保持指标可重复。远程模型可以作为可选演示增强，不进入核心评分依赖。
