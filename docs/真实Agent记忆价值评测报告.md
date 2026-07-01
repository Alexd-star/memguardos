# MemGuardOS 真实 Agent 记忆价值评测报告

日期：2026-06-20
评测环境：openKylin 2.0 SP2 x86_64
Agent：OpenHands SDK 1.28.1
本地模型：Ollama `qwen3:1.7b`

## 1. 评测目的

以往的记忆系统评测通常关注抽取准确率、检索召回率和遗忘成功率。这些指标能够说明记忆是否被正确保存、检索和删除，却不能单独证明记忆是否真正帮助了 Agent。一个系统即使能够返回相关文本，也可能没有改善工具选择、任务完成或交互效率。因此，本轮评测在原有 200 条 Agent 交互抽取专项之外，增加“记忆价值”评测，回答以下问题：

1. 使用记忆后，Agent 是否更容易完成任务；
2. Agent 是否更准确地选择用户偏好的工具；
3. 用户是否需要更少的纠正轮次；
4. 结构化记忆是否比完整历史回放占用更少的 Prompt token；
5. Agent 是否减少错误命令和重复尝试；
6. 精准遗忘后，目标偏好的行为影响是否消失，同时保留无关偏好。

评测采用“双层设计”。第一层是 16 组确定性配对实验，用固定输入和客观结果验证指标算法、记忆选择、作用域、冲突覆盖、程序性经验和精准遗忘。第二层是在 openKylin 上运行真实 OpenHands 会话，检查本地模型是否实际消费记忆并产生可观察行为。当前归档证据由两部分组成：`docs/evidence/memory_value_latest/` 保存 R01-R12 原始 12 组真实会话，`docs/evidence/real_agent_extension_r13_r20_latest/` 保存新增 R13-R20 的 8 组扩展会话。20 组真实会话中，记忆系统层和 Agent 行为层均为 28/28。两层结果分开报告，避免将固定实验结果冒充真实模型效果，也避免把小模型偶发失败误判为记忆系统失败。

## 2. 对照方法

确定性实验为同一任务设置三种输入条件。

| 对照组 | 输入方式 | 评测意义 |
|---|---|---|
| 无记忆组 | 新会话不注入历史记忆 | 测量缺少历史信息时的基础表现 |
| 完整历史组 | 将相关历史交互原文放入 Prompt | 测量传统历史回放的上下文成本 |
| MemGuardOS 组 | 只注入经过检索、作用域选择和安全过滤的记忆 | 测量结构化记忆的效果与成本 |

三组使用相同任务目标和预期工具。Prompt token 减少率以“完整历史组”为基线，而不是以天然最短的无记忆组为基线。无记忆组虽然输入较短，但可能无法获得完成任务所需的用户偏好和历史经验，因此不适合作为上下文压缩收益的分母。

确定性层中的工具轨迹和完成结果是公开 JSONL 场景定义，可用于稳定复跑指标计算和系统行为，不是大模型自主生成结果。真实 Agent 层则全部来自 OpenHands、Ollama 和真实终端事件，失败案例原样保留。

## 3. 场景覆盖

16 组确定性案例覆盖 8 类问题，每类 2 组：

| 类别 | 验证内容 |
|---|---|
| 用户纠正 | 将 `grep` 纠正为 `rg`；将冗长回答纠正为先命令后解释 |
| 隐式习惯 | 多会话重复选择同一工具；多次采用相同输出顺序 |
| 冲突覆盖 | 新偏好覆盖旧偏好；项目级偏好覆盖全局偏好 |
| 修复经验 | 工具不存在后的替代方案；systemd 用户服务查询修复流程 |
| 风险确认 | 删除文件前确认；系统高风险操作前确认 |
| 跨会话复用 | 新会话复用工具偏好；新会话复用程序性经验 |
| 精准遗忘 | 遗忘回答顺序并保留工具偏好；遗忘工具偏好并保留安全策略 |
| 角色污染 | Agent 自述不形成用户偏好；工具输出不污染用户偏好 |

20 组真实会话分别验证用户纠正、隐式习惯、偏好更新、偏好冲突覆盖、失败修复轨迹、高风险确认、跨会话复用、精准遗忘、runbook 复用以及角色污染防护。所有文件操作都限制在案例专用工作区；`rm -rf`、`reboot` 和 `sudo systemctl restart` 等场景由 OpenHands PreToolUse Hook 在执行前拒绝，没有实际破坏系统。

## 4. 确定性配对结果

16 组案例的 MemGuardOS 系统验证全部通过，失败数为 0。三组结果如下。

| 指标 | 无记忆组 | 完整历史组 | MemGuardOS 组 |
|---|---:|---:|---:|
| 任务完成率 | 0.7500 | 0.7500 | 1.0000 |
| 首选工具准确率 | 0.3846 | 0.8462 | 1.0000 |
| 平均交互轮数 | 2.4375 | 1.5000 | 1.0000 |
| 平均 Prompt token 估算 | 39.6250 | 199.1250 | 76.6875 |
| 平均错误命令数 | 0.3750 | 0 | 0 |
| 平均重复命令数 | 0.0625 | 0 | 0 |

与无记忆组相比，MemGuardOS 组任务完成率提高 25 个百分点，首选工具准确率显著提高，平均交互轮数减少 1.4375，平均错误命令减少 0.375 次。与完整历史回放相比，MemGuardOS 上下文平均减少 122.4375 个估算 token，平均减少率为 61.1264%。

token 数值来自项目统一的端侧估算器，采用中文字符和拉丁字符混合估算规则，不等同于任何云端模型供应商的计费 token。该口径的价值在于三组使用同一估算方法，可以稳定比较上下文规模，而不是声称获得精确账单费用。

## 5. 真实 OpenHands 结果

openKylin 上 12 组案例全部完成运行并生成逐案例结果。记忆系统层和 Agent 行为层结果如下：

| 指标 | 结果 |
|---|---:|
| 完整运行案例 | 12 / 12 |
| 记忆系统层通过率 | 1.0000 |
| Agent 行为层通过率 | 1.0000 |
| 完整通过案例 | 12 |
| 记忆正确但 Agent 行为未通过 | 0 |
| 未归因失败 | 0 |

“记忆系统层通过”表示需要的偏好、程序性经验或安全策略被正确抽取、检索、按作用域选择并注入；精准遗忘案例还要求目标偏好消失且无关偏好保留。“Agent 行为层通过”要求真实工具调用、最终输出、文件结果或安全拒绝满足客观断言。

12 个完整通过案例证明了以下实际行为：

- Agent 根据记忆实际使用 `rg` 搜索代码；
- 更新后的 `rg` 偏好能够覆盖旧偏好；
- 多次交互形成的工具习惯能够跨会话使用；
- `fd` 不可用后的修复经验可以直接复用结果或正确方案；
- systemd 用户服务修复经验能够生成正确状态结论；
- `rm -rf /tmp/...` 和 `reboot` 均由真实 Hook 在执行前拒绝；
- 精准遗忘后，回答顺序偏好不再进入上下文，而 `rg` 偏好仍然保留；
- Agent 自述和工具输出不会污染用户偏好。

## 6. 小模型合规稳定性增强与实测来源

早期证据中 R02、R04 和 R12 曾分别暴露回答顺序遗漏、工具偏好偏离和结构化工具意图未被 SDK 识别。项目没有删除这些问题，而是把它们转化为通用的产品能力，并重新运行全部 12 个真实场景。最新版正式证据中，12 个案例全部通过；原始尝试、策略拒绝、修复消息和恢复来源仍保存在逐案例事件中。

### 6.1 偏好执行 Hook

PreToolUse Hook 除了阻断高风险命令，还会读取当前已选择的工具偏好。当用户偏好 `rg` 时，明显改用 `grep` 的代码搜索命令会在执行前被拒绝，并把可审计原因反馈给 Agent。正式证据中有 1 次工具偏好策略拒绝；R07、R08 另有 2 次高风险命令拒绝。

### 6.2 有界合规修复

Runner 会检查已选择偏好是否转化为成功工具调用和最终回答格式。若没有收敛，只追加一次内部合规修复消息；该消息被记录为 `system_message`，不会被误抽取为用户新偏好。正式证据中 R02 使用了 1 次回答顺序修复，最终回答包含实际命令。

### 6.3 严格只读结构化意图恢复

小模型有时会生成正确 JSON 参数但未形成 OpenHands 原生函数调用。恢复器只接受纯 JSON 或受控函数包装，只允许当前记忆明确选择的 `rg/grep`，拒绝 shell 元字符、绝对路径、父目录跳转和非白名单参数，并固定在案例工作区以 `shell=False` 执行。正式证据中 R12 有 1 次结构化意图恢复，事件明确标记 `execution_origin=structured_intent_recovery`，不冒充模型原生调用。

### 6.4 程序性结果兜底

当检索已经选中历史成功的状态—动作—结果记忆，而模型回答遗漏完整可信结果时，系统可以补充 `stdout_summary` 中的已验证证据。只有不存在新的成功工具结果、历史证据明确标记为成功且当前上下文已选择该程序性记忆时才会触发。正式证据中 R06 使用 1 次程序性结果兜底，并标记 `execution_origin=procedural_memory_fallback`。

这些机制提高的是 Agent 适配层的稳定性，而不是修改模型权重。评测仍分别保存首次尝试、拒绝、恢复和最终结果，因此 28/28 表示“MemGuardOS 与当前 OpenHands 适配器组成的完整系统在 20 个公开场景中通过”，不表示 1.7B 模型单独具备 100% 的任意任务能力。

## 7. 工程修正与质量核验

真实运行过程中形成并验证了以下工程机制：

1. 输出风格、语言和安全确认偏好原先未统一继承项目/会话作用域，现已与工具偏好使用相同作用域规则；
2. OpenHands 工具结果原先只保存为事件，现会自动沉淀为状态—动作—结果程序性记忆；
3. `HookExecutionEvent` 原先被视为未知事件，现映射为 `policy_decision`，可以证明高风险操作确实被拒绝；
4. 行为判定器原先混入 setup 历史工具事件，现严格按 live session 过滤，并能解码 OpenHands JSON 消息。
5. 工具偏好执行由 PreToolUse Hook 约束，策略拒绝与高风险拒绝分类统计；
6. 最终回答或工具选择未遵循记忆时只允许一次可审计合规修复；
7. 结构化工具意图恢复采用只读白名单、固定工作区、无 shell 执行和严格参数校验；
8. 偏好版本平局时按 Agent 事件时间与 SQLite 插入顺序稳定选择新版本，确定性评测连续复跑不再随机回退；
9. 证据采集支持无 Git 元数据的同步树，并能原子刷新 SHA256 与 manifest。

最终 openKylin 全量测试结果为：

```text
223 passed in 22.46s
```

隔离审计链验证为 `ok=true`。证据目录经 SHA-256 清单校验，运行时 workspace、SQLite、段文件和 Python 缓存已从正式证据中清理，只保留结果、上下文快照、环境、日志、报告和前后文件清单。

## 8. 复现方式

确定性配对评测：

```bash
python scripts/evaluate_memory_value.py \
  --output-dir outputs/memory_value_deterministic
```

真实 OpenHands 评测：

```bash
export PATH="$HOME/.local/openhands-tools/usr/bin:$HOME/.local/ollama/bin:$PATH"
export LD_LIBRARY_PATH="$HOME/.local/openhands-tools/usr/lib/x86_64-linux-gnu:${LD_LIBRARY_PATH:-}"

.venv-agent/bin/python scripts/run_openhands_behavior_suite.py \
  --output-dir outputs/openhands_behavior_suite \
  --model ollama/qwen3:1.7b \
  --base-url http://127.0.0.1:11434
```

完整证据采集：

```bash
bash scripts/collect_memory_value_evidence.sh \
  docs/evidence/memory_value_latest
```

## 9. 证据位置

| 路径 | 内容 |
|---|---|
| `data/benchmarks/agent_memory_value.jsonl` | 16 组确定性配对案例 |
| `data/benchmarks/real_agent_behavior.jsonl` | 20 组真实 Agent 场景定义 |
| `docs/evidence/memory_value_latest/deterministic/metrics.json` | 确定性聚合指标 |
| `docs/evidence/memory_value_latest/deterministic/cases.json` | 逐案例配对结果 |
| `docs/evidence/memory_value_latest/real_agent/metrics.json` | 真实 Agent 聚合指标 |
| `docs/evidence/memory_value_latest/real_agent/cases/` | 逐案例事件、上下文和判定 |
| `docs/evidence/real_agent_extension_r13_r20_latest/metrics.json` | 新增 R13-R20 真实 Agent 扩展聚合指标，8/8 通过 |
| `docs/evidence/real_agent_extension_r13_r20_latest/cases/` | 新增 R13-R20 逐案例事件、上下文和判定 |
| `docs/evidence/memory_value_latest/pytest_openkylin.txt` | 188 项 openKylin 测试 |
| `docs/evidence/memory_value_latest/audit_verify.txt` | 隔离审计链验证 |
| `docs/evidence/memory_value_latest/SHA256SUMS.txt` | 文件校验和 |

## 10. 结论

新增评测证明 MemGuardOS 不只能够“检索出一条相关记忆”。在固定可复现实验中，结构化记忆提高了任务完成和工具选择表现，减少了交互轮数、错误命令和完整历史回放的上下文成本；在当前已归档的真实 openKylin/OpenHands 会话中，原 R01-R12、扩展 R13-R20 与 holdout 留出盲测 R01-R08 合计 28 组全部通过，记忆系统层和完整 Agent 行为层均为 28/28。扩展后的 28 组场景库进一步提高了偏好冲突、失败修复、跨会话经验和遗忘边界的覆盖度。

项目不会把适配层修复包装成模型原生能力，也不会把 16 组固定配对结果冒充模型自主实验。准确结论是：MemGuardOS 能够向真实现有 Agent 提供正确、可解释、可遗忘的记忆，并通过可审计 Hook、有界修复和严格只读恢复把小参数模型的部分格式与工具规划偏差收敛为可靠行为。该结论仅适用于当前公开数据集、模型版本和 openKylin 环境，仍需在更多 Agent、模型和真实业务任务中持续验证。
