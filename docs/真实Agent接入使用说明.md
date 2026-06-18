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

## 3. 安装可选 Agent 环境

核心 MemGuardOS 不依赖 OpenHands。只有需要运行真实 Agent 闭环时才安装可选环境：

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

`scripts/memguard-agent@.service` 用于把 Agent 任务放入独立 systemd 单元。安装后可按 Linux 用户启动：

```bash
sudo cp scripts/memguard-agent@.service /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now memguard-agent@luo.service
```

任务以 JSON 文件写入用户队列目录。桥接进程只读取允许字段，不保存 OpenHands 原始任务归档：

```json
{
  "namespace": "realagent-demo",
  "session_id": "queued-session-01",
  "task": "使用 rg 查找 build_agent_context",
  "max_iterations": 8
}
```

服务单元使用普通用户运行，并启用 `NoNewPrivileges`、`PrivateTmp`、`PrivateDevices`、`ProtectSystem=strict`、`MemoryMax=2G` 和 `CPUQuota=100%`。这些限制用于降低 Agent 工具执行的系统影响，不等同于容器或虚拟机级安全隔离。

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

## 9. 证据采集

```bash
bash scripts/collect_agent_evidence.sh
```

正式证据位于 `docs/evidence/agent_latest/`，包括真实会话事件、三阶段上下文、systemd 安全属性、运行时版本、模型清单、精准遗忘状态、审计校验和 200 条专项 benchmark 报告。`SHA256SUMS.txt` 用于检查证据文件是否被意外修改。

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
