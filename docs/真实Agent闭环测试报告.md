# MemGuardOS 真实 Agent 闭环测试报告

日期：2026-06-19

本文记录 MemGuardOS 接入现有 OpenHands Agent 后的真实运行证据。测试目标是验证用户与 Agent 的交互习惯能够自动采集、转化为记忆、跨会话复用，并能按交互范围精准遗忘。本文只陈述已经由代码、命令输出或证据文件支持的结果。

## 1. 测试环境

| 项目 | 环境 |
|---|---|
| 操作系统 | openKylin 2.0 SP2 x86_64 |
| Agent | OpenHands SDK 1.28.1 |
| 本地模型服务 | Ollama 0.30.10 |
| 实证模型 | `qwen2.5-coder:1.5b`、`qwen3:1.7b` |
| MemGuardOS namespace | `realagent-demo` |
| 服务方式 | 直接 runner 与 `memguard-agent@luo.service` |
| 证据目录 | `docs/evidence/agent_latest/` |

本地模型只用于完成无需付费 API 的真实 Agent 调用证明。小模型的复杂命令稳定性不是 MemGuardOS 的质量指标，因此测试分别检查上下文选择、事件采集、工具执行和最终任务结果，不把它们混成一个模糊的“成功率”。

## 2. 已验证闭环

### 2.1 真实工具调用

会话 `real-agent-session-a2` 接收用户要求：后续代码搜索使用 `rg`，并采用先命令后解释的回答顺序。OpenHands 随后实际调用终端：

```text
rg -n build_agent_context memguard
```

真实结果包含：

```text
memguard/service.py:1362
```

该证据位于：

- `session_a_events.json`
- `real-agent-session-a2_context.json`

它证明的内容是：OpenHands 真实会话、真实终端调用、真实工具结果和 MemGuardOS 事件回收链路均已工作。它不能单独证明任意模型在任意复杂任务上都能正确生成命令。

### 2.2 偏好自动提取

同一会话自动生成了两类偏好：

| 偏好 | 值 | 作用阶段 |
|---|---|---|
| 代码搜索工具 | `rg` | 工具选择 |
| 回答顺序 | 先命令后解释 | 结果生成 |

偏好来自用户与 Agent 的真实交互事件，不是预先写死到 benchmark 或配置文件。每条偏好保留来源 Agent、会话、轮次和证据事件，可用于回溯与定向遗忘。

### 2.3 跨会话上下文复用

后续会话 `real-agent-session-b5` 在没有重复写入偏好的情况下构建三阶段上下文。工具选择上下文命中了 `rg` 偏好，结果生成上下文命中了回答顺序偏好。证据位于：

- `session_b_events.json`
- `real-agent-session-b5_context.json`

本次会话证明“记忆被跨会话选中并交给 Agent”。由于测试使用的小参数本地模型与当时终端 `PATH` 条件有限，不能把该会话表述为所有命令均成功完成。正式答辩应把“上下文选择成功”和“模型执行能力边界”分开说明。

## 3. 交互精准遗忘

系统对“先命令后解释”偏好执行了 dry-run、确认删除和 compact。验证结果为：

| 检查项 | 结果 |
|---|---|
| 三阶段上下文均不可再检索 | `all_stages_absent=true` |
| 事件载荷明文已清理 | `event_payload_scrubbed=true` |
| 追加式段文件明文已清理 | `segment_plaintext_absent=true` |
| 无关的 `rg` 工具偏好 | 保留 |
| 审计链 | 通过 |

这说明精准遗忘可以只删除指定交互偏好，而不误删同一会话中的无关工具偏好。系统同时给出外部持久化风险提示：OpenHands 原始会话、shell history、远程模型提供方日志或系统备份若持有副本，需要调用各自删除机制。MemGuardOS 不宣称越过自身存储边界删除外部数据。

## 4. systemd Agent bridge

`memguard-agent@luo.service` 在 openKylin 上处于 active 状态，关键安全属性如下：

| 属性 | 实测值 |
|---|---|
| 运行用户 | `luo` |
| `NoNewPrivileges` | `yes` |
| `PrivateTmp` | `yes` |
| `PrivateDevices` | `yes` |
| `ProtectSystem` | `strict` |
| `MemoryMax` | `2G` |
| `CPUQuota` | `100%` |

桥接服务读取受控 JSON 任务，调用 OpenHands runner，并把规范化结果交给 MemGuardOS。服务不保存 OpenHands 原始任务归档。该设计降低了 Agent 常驻运行对系统的影响，但不等同于强对抗容器沙箱。

## 5. 专项 benchmark

Agent 交互记忆专项 benchmark 共 200 条，组成如下：

| 场景 | 数量 |
|---|---:|
| 显式偏好 | 40 |
| Agent 角色污染负例 | 30 |
| 隐式习惯聚合 | 20 |
| 用户纠正 | 20 |
| 工具轨迹 | 30 |
| 敏感交互 | 20 |
| 精准遗忘 | 20 |
| 跨会话复用 | 20 |

当前证据中的提取精确率、召回率、F1、遗忘成功率和跨会话遵循率均为 `1.0000`，角色污染率与敏感泄漏率均为 `0`，P95 为 `113.97 ms`。该 P95 包含每个案例创建隔离数据库的测试开销，不等同于 10k 主检索 benchmark 的查询 P95。专项数据集用于防止角色污染、误抽取和误删回归，不能替代公开真实用户数据集；由于交互数据涉及隐私，项目采用 60 条匿名化真实风格样例与 140 条合成难例的双轨数据。

## 6. 自动化测试

新增记忆价值配对评测、真实行为判定、偏好合规守卫和程序性记忆自动沉淀后，本地工作树全量测试为 `341 passed, 3 skipped`；openKylin 目录现保存当前 HEAD 快照 `344 passed`，source revision 与当前 Git HEAD 一致：

```text
344 passed（对应可执行代码基线实机复验；准确版本见原生证据目录 `source_revision.json`）
```

其中新增测试覆盖：

- 事件协议和幂等写入；
- OpenHands 角色映射；
- 显式偏好与隐式习惯；
- 三阶段上下文注入；
- runner 错误传播；
- 按 Agent、会话、轮次、主题和时间范围遗忘；
- event-memory 关联；
- systemd 队列桥；
- Agent Web 治理接口；
- 200 条专项 benchmark。

openKylin 已完成真实 runner、systemd bridge、专项 benchmark、精准遗忘、审计链和完整 pytest 证据采集。新增的 16 组确定性配对实验、20 个公开真实 Agent 场景与 8 个 holdout 留出盲测见 `docs/真实Agent记忆价值评测报告.md`；合计 28 次真实运行的记忆系统层与 Agent 行为层均为 28/28，原始尝试、策略拒绝与恢复来源均保留。

## 7. 证据文件说明

| 文件 | 内容 |
|---|---|
| `openhands_runtime.txt` | OpenHands SDK 和运行时信息 |
| `pytest_openkylin.txt` | openKylin 全量 pytest 输出 |
| `local_models.txt` | 本地 Ollama 模型清单 |
| `session_a_events.json` | 成功真实工具调用会话事件 |
| `session_b_events.json` | 跨会话上下文使用事件 |
| `real-agent-session-a2_context.json` | 首会话三阶段上下文 |
| `real-agent-session-b5_context.json` | 后续会话三阶段上下文 |
| `interaction_status.json` | 交互事件、偏好与遗忘状态 |
| `agent_bridge_systemd.txt` | systemd 状态 |
| `agent_bridge_security.txt` | 服务隔离和资源属性 |
| `agent_bridge_journal.txt` | 服务日志 |
| `agent_dashboard_snapshot.json` | Web Agent 治理接口快照 |
| `benchmark/metrics.json` | 200 条专项指标 |
| `benchmark/report.md` | 专项 benchmark 中文报告 |
| `audit_verify.json` | 审计链校验 |
| `SHA256SUMS.txt` | 证据文件校验和 |

## 8. 结论与边界

本轮实证已经补齐老师问答指出的关键缺口：项目不再只提供抽象 Agent API，而是接入了现有 OpenHands Agent；“用户操作习惯”不再依赖 shell history 推断，而是从用户消息、纠正反馈、工具调用和工具结果中自动提取；记忆也已经在后续会话的规划、工具选择和结果生成上下文中被实际调用。

仍需准确说明两个边界。第一，本地小模型能够完成真实 Agent 与终端调用证明，但复杂命令成功率受模型能力和工具环境影响。第二，MemGuardOS 的精准遗忘保证其管理的事件、记忆、索引、图谱和段文件边界，不保证自动清除 OpenHands、终端、模型服务或备份系统中的外部副本。







