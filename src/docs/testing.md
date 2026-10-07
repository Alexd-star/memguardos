# 测试与评测

本文说明 MemGuardOS 如何验证功能正确性、检索质量、精准遗忘、安全边界、真实 Agent 行为和 openKylin 原生交付。测试结果按证据来源分层报告，不把单元测试、自建 benchmark、确定性配对实验和真实模型运行混为同一结论。

## 1. 测试分层

| 层级 | 主要问题 | 入口 | 能证明什么 |
|---|---|---|---|
| 单元测试 | 单个解析器、存储器、排序器或策略函数是否符合契约 | `tests/test_*.py` | 已覆盖输入下的局部逻辑正确性 |
| 集成测试 | 写入、检索、遗忘、审计、Web、桌面与 API 能否串联 | `pytest`、smoke 脚本 | 组件之间的数据契约没有断裂 |
| 可复现 benchmark | 抽取、召回、敏感检测、遗忘和延迟是否达到门槛 | `memguard.eval.run_eval` | 固定数据集与协议下的量化结果 |
| 真实 Agent 测试 | OpenHands 是否真正接收上下文、调用工具并回写事件 | `run_openhands_behavior_suite.py` | 真实 Agent 运行链路成立 |
| openKylin 实机 | systemd、cgroup、SDK、Peony、UKUI、Polkit 和 Debian 包是否可用 | 原生采集脚本 | 指定源修订在目标系统上的安装运行状态 |
| 发布门禁 | 文档、核心文件、证据、SBOM、安全扫描和归档哈希是否完整 | `check_competition_ready.py`、提交包审计器 | 发布物的结构和证据未漂移 |

任何一层通过都不能替代其他层。例如，`pytest passed` 不证明开放表达下的语义泛化；真实 Agent 成功运行也不等于每个工具任务都由模型一次完成。

## 2. 快速验证

安装开发依赖后执行：

```bash
python -m compileall -q memguard scripts
python -m ruff check memguard ../tests scripts
python -m pytest -q
python scripts/check_docs_links.py
python scripts/check_metric_consistency.py
python scripts/web_dashboard_smoke.py
```

量化评测输出到未纳入版本控制的 `output/`，避免运行结果污染正式文档：

```bash
mkdir -p output
python -m memguard.eval.run_eval \
  --latency-size 1000 \
  --latency-queries 120 \
  --output output/eval_result.json \
  --markdown output/eval_report.md
```

发布清单与 ZIP 审计：

```bash
python scripts/package_submission.py --kind competition
python scripts/inspect_submission_package.py
python scripts/smoke_submission_archive.py
```

## 3. 通用功能 benchmark

通用评测由 217 条可复现种子和用例组成，覆盖 59 条种子记忆、54 条检索、31 条敏感检测、29 条精准遗忘、10 条冲突处理、19 条图谱检索、6 条数据质量和 9 条程序性记忆场景。

精选 openKylin 10k 结果位于 `docs/evidence/performance_latest/eval_result_openkylin_10k_optimized.json`：

| 指标 | 结果 | 含义 |
|---|---:|---|
| 类型识别准确率 | 1.0000 | 偏好、知识等顶层类型分类 |
| 子类型识别准确率 | 1.0000 | 工具偏好、回答格式等细分类别 |
| Recall@5 | 1.0000 | 每个查询的目标记忆是否出现在前 5 条 |
| 跨语言 Recall@5 | 1.0000 | 中文查询召回英文或中英混合记忆 |
| 纯文本基线 Recall@5 | 0.8426 | 仅使用 FTS 的对照结果 |
| 敏感阻断 F1 | 1.0000 | 高风险内容阻断的精确率与召回综合值 |
| 脱敏 F1 | 1.0000 | 可保留个人信息的脱敏效果 |
| 遗忘候选精确率 | 1.0000 | 被选中的候选中有多少确实应删除 |
| 遗忘候选召回率 | 1.0000 | 应删除的目标中有多少被找到 |
| 应保留记忆成功率 | 1.0000 | 精准遗忘未误删 hard negative |
| 冲突处理正确率 | 1.0000 | 新旧偏好覆盖与保留规则 |

这些 1.0000 来自固定自建数据集，说明实现对公开协议中的正例、负例和 hard negative 处理一致。它们不是开放世界的总体准确率，也不替代外部数据和人工盲测。

## 4. 检索性能与消融

openKylin 10k 负载使用 10,000 条记忆和 150 次查询。精选结果为：

| 项目 | 结果 |
|---|---:|
| P50 | 9.097 ms |
| P95 | 10.105 ms |
| P99 | 75.255 ms |
| 段文件 | 31,096.541 KB |
| SQLite | 51,672.000 KB |

检索 v2 在 openKylin 上重复三轮，协议固定为 1,000 条记忆、120 次查询和 6 个 worker。`docs/evidence/performance_latest/retrieval-v2-openkylin.json` 中三轮 Kylin 持久化向量 Recall@5 均为 1.0000，质量与延迟门禁通过。

消融结果说明：

1. 纯 FTS 延迟最低，但 Recall@5 为 0.8426。
2. 混合检索在加入稳定 hash-vector、候选补足和图谱后恢复到 1.0000。
3. 热结果缓存显著降低重复查询延迟，但不能作为冷查询延迟替代值。
4. Kylin SDK 首次推理存在冷启动，因此采用预热、持久化候选向量、延迟预算和 fallback，而不是让桌面操作无限等待。

## 5. Agent 交互专项

`data/benchmarks/agent_interactions.jsonl` 包含 200 条交互专项场景：60 条匿名化真实风格样例和 140 条合成边界样例。类别包括显式偏好、隐式习惯、用户纠正、工具轨迹、跨会话复用、角色污染、敏感交互和精准遗忘。

原始汇总 `docs/evidence/agent/base-benchmark-metrics.json` 显示：

| 指标 | 结果 |
|---|---:|
| 场景数 | 200 |
| 角色污染率 | 0 |
| 敏感泄漏率 | 0 |
| 精准遗忘成功率 | 1.0000 |
| 跨会话偏好遵循率 | 1.0000 |
| P95 | 113.966 ms |

专项 benchmark 验证事件归一化、偏好提取、检索、作用域遗忘和指标计算。由于部分场景是合成数据，结果不能单独证明所有自然语言表达都具备同样泛化能力。

### 5.1 记忆到行动的执行前评测

`data/benchmarks/action_contract.jsonl` 单独评估记忆对工具行动的影响，避免把“检索到了偏好”和“Agent 真的按偏好行动”混成一个指标。样例覆盖正常允许、工具偏好改道、高风险确认、破坏性命令拒绝、测试入口改道和无关工具放行六类路径。评测只调用 provider-neutral 行动契约，不执行命令；每条结果同时输出期望决策、实际决策、策略类别和原因。

```bash
python -m memguard.eval.action_contract \
  --input data/benchmarks/action_contract.jsonl \
  --output output/action_contract_report.json
```

当前固定协议包含 8 条用例，四类决策均有覆盖，报告中的准确率为 1.0000。该结果证明的是行动契约对固定安全边界和偏好场景的确定性一致性；真实 Agent 是否采纳建议，还需结合 OpenHands Hook 的工具事件和任务完成结果进行验证。

### 5.2 会话记忆巩固回归

`tests/test_consolidation.py` 验证短期经验进入长期记忆前的审核边界：明确偏好可以生成 `promote` 候选，证据不足的知识保持在会话层，计划必须经过确认，用户只选择的编号才会晋级；候选版本变化后旧计划会被拒绝。测试同时通过 service、Unix socket API 和 CLI 的同一份计划契约，确保三类入口不会产生不同的治理语义。

`scripts/collect_consolidation_evidence.py` 使用隔离的临时存储生成可归档证据，不读取或修改操作者的正式记忆空间。openKylin 原生采集脚本会自动运行它，输出包含系统平台、Git 修订、候选分类、确认前后结果和旧计划 CAS 拒绝结果的 `consolidation_evidence.json`；该文件可与桌面入口、Agent 和系统服务证据放在同一份真机证据包中。

```bash
python -m pytest -q ../tests/test_consolidation.py
python -m memguard.cli.memctl --help | findstr consolidation
python scripts/collect_consolidation_evidence.py --output output/consolidation_evidence.json
```

真实使用时可以先保存计划，再人工审核：

```bash
memctl consolidation-plan --session-id session-001 --project-root "$PWD" > consolidation-plan.json
memctl consolidate --plan-file consolidation-plan.json
memctl consolidate --plan-file consolidation-plan.json --select mem_example --yes
```

计划只提升有证据支持且未命中敏感治理边界的记忆；它不会把“生成了计划”当作“已经写入长期记忆”，也不会把固定回归结果替代真实 Agent 的长期效果评估。

### 5.3 强化与可逆衰减回归

`tests/test_decay_policy.py` 验证长期记忆健康度策略：长期未使用且低分的知识进入 `demote_to_session`，明确偏好和有检索命中的记忆继续保留；降级前需要确认，降级后产生新的 session 版本，旧计划和版本变化会被拒绝。`scripts/collect_decay_evidence.py` 使用隔离存储生成结构化环境、计划、动作和 CAS 结果，openKylin 采集脚本会将其写入 `decay_evidence.json`。

```bash
python -m pytest -q ../tests/test_decay_policy.py
python scripts/collect_decay_evidence.py --output output/decay_evidence.json
memctl decay-plan --max-age-days 30 --score-threshold 0.35 > decay-plan.json
memctl decay --plan-file decay-plan.json --target-session-id maintenance-review
```

### 5.4 阶段感知上下文编排回归

`tests/test_context_compaction.py` 验证上下文表示层的三个边界：规划阶段的普通知识可以压缩为摘要，工具选择阶段的偏好保留可执行指令和来源，程序性流程在行动阶段保留完整步骤；上下文收据会记录表示映射、压缩原因和预计节省量。测试同时检查 `full` 策略仍可用于离线复核，且压缩不会改写记忆正文。

```bash
python -m pytest -q ../tests/test_context_compaction.py
python -m memguard.cli.memguard --workspace "$PWD" context \
  "查找项目构建流程" --stage planning --token-budget 500
```

openKylin 采集脚本会在隔离存储中运行 `scripts/collect_context_compaction_evidence.py`，输出不同阶段的上下文表示、预算变化和收据校验结果。该证据用于证明功能来自当前部署版本，不把单次模型回答当作上下文编排能力的替代指标。

### 5.5 行动结果反馈闭环回归

`tests/test_action_feedback.py` 将执行前行动契约与外部结果回写放在同一条测试链路中：代码搜索偏好先返回 `redirect`，实际改用 `rg` 后记录 `success`；实际仍使用 `grep` 时记录 `deviation`，不会伪装成成功强化；重复提交同一幂等键只保留一条；记忆版本变化后旧收据不能回写；精准遗忘会同步删除与该记忆关联的行动账本。`action_outcome_stats` 提供成功率和最近成功时间，衰减计划将真实成功证据作为保留信号，但显式偏好、敏感治理和版本状态仍优先。

```bash
python -m pytest -q ../tests/test_action_feedback.py
python scripts/collect_action_feedback_evidence.py --output output/action_feedback_evidence.json
memctl agent record-outcome --preflight-file preflight.json \
  --tool-input '{"command":"rg -n target ."}' --exit-code 0
memctl agent action-outcomes --memory-id mem_example
```

openKylin 采集脚本会在隔离存储中运行同一证据脚本，输出预检判定、成功遵循、偏离、重试去重、统计结果和旧收据拒绝结果。该测试证明的是记忆证据与行动结果之间的系统契约；它不把一次确定性工具调用等同于所有模型或所有 Agent 的长期泛化效果。

### 5.6 记忆驱动动作质量回归

`tests/test_action_impact.py` 在同一 Agent、项目和任务类型下构造成功与失败的行动反馈，检查报告是否区分实际执行与拦截/未知状态，是否计算 Wilson 成功下界，是否给出同类观察性差值，以及小样本是否保持“继续观察”而不是直接判定稳定。报告不保存命令正文，也不会自行修改记忆层级。

```bash
python -m pytest -q ../tests/test_action_impact.py
python scripts/collect_action_impact_evidence.py --output output/action_impact_evidence.json
memctl agent action-impact --agent-name openhands --task-type code_search
```

openKylin 采集脚本使用同一 service、同一反馈表和同一门控逻辑生成 `action_impact_evidence.json`。证据中的正向差值是同类观察结果，成功下界用于抑制小样本偶然性；它可以证明质量报告链路已工作，不能替代严格的随机对照实验。

### 5.7 价值感知记忆预算回归

`tests/test_memory_budget.py` 验证长期存储预算的完整生命周期：候选同时输出价值、风险、估算字节数和单位字节价值；明确偏好保持在长期层；普通记忆按单位成本的净价值进入可逆降级计划；执行前必须确认；确认后追加 session 版本；计划所依赖的版本或正文大小发生变化时，旧计划返回 `stale_memory_budget_plan`。API 契约和独立证据脚本使用同一 service 实现，避免测试只验证展示层排序。

```bash
python -m pytest -q ../tests/test_memory_budget.py
python scripts/collect_memory_budget_evidence.py --output output/memory_budget_evidence.json
memctl budget-plan --target-bytes 1048576 > budget-plan.json
memctl budget --plan-file budget-plan.json --target-session-id budget-review
memctl budget --plan-file budget-plan.json --target-session-id budget-review --yes
```

预算中的字节数是可重复的持久化规模估算，用于候选比较、目标规划和提交前一致性校验；它不冒充底层文件系统精确计费。计划不会自动处理显式偏好、高敏感内容或治理状态异常记录，目标不可达时会如实返回 `target_reached: false`，而不是为了达到数字越过保护边界。

### 5.8 记忆准入与动作计划回归

`tests/test_memory_admission.py` 验证准入层的五个边界：人工明确偏好可以获得长期层建议，工具结果和行为轨迹在证据不足时保持会话层，高风险敏感内容直接阻断，预览接口不会创建记忆记录，开启 `enforce_admission` 后自动来源会实际写入会话层。测试同时调用 `agent_action_plan`，检查已有工具偏好能够转化为 `replace_then_execute` 闸门，并在计划中携带记忆版本和结果反馈要求。

```bash
python -m pytest -q ../tests/test_memory_admission.py
python scripts/collect_memory_admission_evidence.py --output output/memory_admission_evidence.json
memctl admission-preview --text "工具结果：本次搜索使用 rg 成功" --source-kind tool_result
memctl agent action-plan --task "查找 build_agent_context 的定义" --tool-name terminal --tool-input '{"command":"grep -n build_agent_context ."}'
```

该专项证明的是写入和执行两端的确定性治理链路，不将“准入建议”冒充为模型自主学习，也不将动作计划的策略判定冒充为外部工具已经执行；真实 Agent 的最终效果仍由事件、工具结果和行动质量报告共同核验。

## 6. 记忆价值实验

记忆价值实验分为两种口径。

### 6.1 确定性配对实验

`docs/evidence/agent/memory-value-deterministic.json` 包含 16 组固定场景，用同一任务比较无记忆、检索记忆和策略增强三种条件。它用于验证任务完成率、工具选择、交互轮数、Prompt token 和错误尝试等指标的计算链路，适合稳定回归；它不运行随机 LLM，因此不能冒充模型因果实验。

### 6.2 真实 OpenHands 运行

真实运行分为基础 12 次、扩展 8 次和留出 8 次，共 28 次。对应汇总为：

- `memory-value-real-agent.json`
- `extension-r13-r20.json`
- `holdout.json`

每次运行检查 OpenHands 版本、任务输入、三阶段上下文、工具调用、事件回写、记忆系统结果和 Agent 行为结果。适配器保留模型原始尝试；当执行偏离用户偏好时，可以进行有界修复并记录来源，因此“最终遵循”可能包含适配器治理贡献，不应全部归因于模型原生能力。

## 7. 精准遗忘验证

遗忘测试覆盖以下表面：

1. 自然语言解析出的主题、类型、时间、来源和 namespace 过滤条件。
2. dry-run 候选及应保留项。
3. 确认后的 tombstone 和元数据删除状态。
4. FTS、图谱、向量缓存和结果缓存不可继续召回。
5. `compact()` 后追加式段文件不再包含目标明文。
6. 审计链包含请求、执行和验证结果。

示例：

```bash
memguard remember "项目 Alpha 中优先使用 rg 搜索代码。"
memguard forget "忘掉项目 Alpha 的搜索工具偏好"
memctl verify-audit
```

精准遗忘只覆盖 MemGuardOS 管理的数据。外部 Agent 会话、模型服务日志、备份和虚拟机快照必须由其所属系统单独清理。

## 8. openKylin 原生验收

最终实机采集命令：

```bash
bash scripts/collect_openkylin_native_evidence.sh
python scripts/check_competition_ready.py \
  --eval docs/evidence/performance_latest/eval_result_openkylin_10k_optimized.json
```

`docs/evidence/openkylin/` 应至少包含：

- `source_revision.json`：40 位 Git 修订和干净工作树状态。
- `pytest_openkylin.txt`：实机测试摘要。
- `debian_packages.txt` 与 `deb_lifecycle.txt`：五包安装、拆卸、重装和数据保护。
- `deb_apt_install.txt`：当前五包通过 APT 实际重装并完成服务、桌面资产和审计复核。
- `systemd_cgroup.txt`：服务状态与资源限制。
- `openkylin_status_probe_sdk.txt`：显式 SDK 推理探针。
- `peony_batch_preview.json`、`peony_batch_apply.json`：文件扫描预览与确认导入。
- `manifest.json` 与 `SHA256SUMS.txt`：目录级完整性。

发布门禁要求源修订与发布 HEAD 一致。证据提交自身只追加证据文件时允许通过；任何核心代码、测试、打包或采集脚本在证据之后变化，都必须重新采集。

正式决赛发布前使用严格模式，禁止将历史实机证据作为当前版本的验收依据：

```bash
python scripts/check_competition_ready.py --require-current-evidence
```

严格模式要求 `docs/evidence/openkylin/source_revision.json` 中的修订号与当前
`HEAD` 完全一致；即使当前提交只新增了文档或证据清单，也必须重新在当前代码上采集
openKylin 实机证据。开发阶段如需检查其余交付项并暂时保留历史证据，可继续使用
`--allow-missing-openkylin`，但该模式不适合作为最终提交前的通过依据。

## 9. 安全与供应链

```bash
python scripts/run_security_scan.py
python scripts/generate_sbom.py
```

安全门禁要求 Bandit 高危和中危计数为 0、pip-audit 已知漏洞计数为 0、扫描工具无缺失。`docs/sbom.json` 记录运行时、开发、OpenHands 和安全依赖以及关键源文件 SHA-256。

## 10. 结果解释原则

验证后的流程经验可独立运行以下验收。脚本调用正式产品 API，在临时工作区创建和检查真实文件，覆盖未完成时拒绝学习、新会话参数复用、重复反馈去重、完整约束进入上下文、来源更新后失效、撤回派生记录及保留独立记忆。文件操作由脚本完成，不计为 Agent 自主行动成绩；临时目录自动移除，正式记忆不参与。

```bash
python scripts/verify_procedure_showcase.py --output output/procedure_showcase.json
```

安装态验证同样需在源码目录之外设置 `PYTHONPATH=/opt/memguardos` 并添加 `--installed`。输出包含实际导入模块的位置、SHA-256 和逐项结果；未通过时退出码非零并保留已执行检查与错误，不使用成功模板覆盖失败。流程检查只支持指定工作区内的安全路径，对越界、符号链接、系统路径、非文本或过大文件采用拒绝或未知状态。

新增的日常使用链路可以在临时目录中独立复跑：

```bash
python scripts/verify_finals_scenarios.py --output output/finals_scenarios.json
```

脚本通过产品 API 写入和纠正自然语言要求，再调用真实上下文构建、事实核对、作用域检索、遗忘与运行态快照检查。测试包括同句两个偏好、新会话复用、纠正后的旧上下文拒用、矛盾事实确认、临时记忆隔离、短密码拒存、单项遗忘后保留无关偏好，以及服务重开后不复活。数据只在自动创建的临时目录中形成，不使用正式记忆，也不替换产品算法。资源压力判断使用显式模拟读数检查调度分支，实际 cgroup/PSI 读数另行记录。该脚本验证服务和保护机制，不能用其结果代替真实模型的工具行动效果。

五包安装后，应从源码目录之外核对实际安装模块：

```bash
cd /tmp
PYTHONPATH=/opt/memguardos python3 /路径/scripts/verify_finals_scenarios.py \
  --installed --output /路径/installed_scenarios.json
```

`--installed` 要求模块来自 `/opt/memguardos`，否则直接失败。输出记录模块路径与 SHA-256，避免源码验证掩盖安装版本滞后。尚未提交的开发版本使用文件级快照和包版本绑定证据，不能将 Git HEAD 当作工作树当前代码的唯一标识。

- `PASS`：指定检查项达到门槛。
- `passed`：pytest 已执行的测试通过；不代表未覆盖路径绝对没有缺陷。
- `Warning`：当前不阻断发布，但必须说明影响和处理方法。
- `P95`：95% 样本不超过该时长，仍需同时观察 P99 与最大值。
- `Recall@5`：目标是否进入前五，不评价最终自然语言回答质量。
- `F1`：精确率与召回率的调和平均，适合同时关注误报和漏报。

发布前应同时检查自动测试、真实用户流程、openKylin 原生安装、证据源修订和提交包哈希。任何单一漂亮指标都不能替代完整链路。

## 11. 阅读真实 Agent 运行记录

真实 Agent 套件的逐案例 `result.json` 可以导出为本地 HTML。报告把任务、各阶段的记忆选择、原始对话、工具命令、工具输出和判定放在同一页面，双臂结果按案例并列。它只读取已有结果，不调用模型或执行命令；在 Windows 或 openKylin 中打开 HTML 即可查看。

```bash
python scripts/render_agent_trace.py \
  --input output/real_agent/cases/R09/memory_on/result.json \
  --output output/real_agent/R09.html

python scripts/render_agent_trace.py \
  --input output/real_agent \
  --case-id R09 \
  --output output/real_agent/R09_compare.html
```

默认保留已有 HTML，更新同一文件时需显式添加 `--overwrite`。输入中的失败、缺失输出与未完成状态会保留；适配器纠正、接管和模型行动分别标记。报告不重新计算评分，也不将“记忆被选择”改成“模型已经遵循”。对照侧是否真正关闭记忆，还需核对原始 `run.memory_enabled`，不能只依据目录名或 `memory_mode` 判断。

报告使用已有敏感识别器脱敏，并移除凭据字段；公开展示前仍需核对原始任务和路径。JSON 文件的 SHA-256 显示在报告中，以便对应原始记录。对话按输入中的顺序呈现，长内容可展开；截图属于该次运行的离线报告，不能标成实时 Agent 界面。

## 12. 最新能力的真实模型闭环

`verify_real_agent_showcase.py` 在隔离的临时记忆和项目中调用真实 OpenHands Agent。七次会话依次检查：同一查找任务的记忆开启与关闭对照、工具偏好的自然语言纠正、只撤回工具偏好并保留回答偏好、核对矛盾事实并使用新版本，以及复核文件后学习流程、在新文件上复用流程、来源变更后停止使用旧流程。原始任务、实际工具命令及输出、各阶段上下文、文件修改前后内容、产品操作与失败信息分别保存，不依赖预设成功回复。

```bash
python scripts/verify_real_agent_showcase.py \
  --model "$OPENHANDS_MODEL" --base-url "$OPENHANDS_BASE_URL" \
  --output-dir output/real_showcase

python scripts/render_agent_trace.py --input output/real_showcase \
  --case-id A01 --output output/real_showcase/A01_compare.html
```

密钥通过 `OPENHANDS_API_KEY` 或 `--api-key-env` 指定的环境变量传入，不放入命令参数或报告。输出目录必须尚不存在，避免覆盖此前尝试。安装态运行时在源码目录之外设置 `PYTHONPATH=/opt/memguardos` 并添加 `--installed`；报告逐个记录产品模块位置和哈希。默认调用真实模型，单元测试注入替身时明确标成 `in_process_test_factory`，两者不能混计。

流程规范由客户端通过正式 API 提交，且仅在 Agent 实际修改文件、独立后置条件检查通过后才能学习。该实验检验的是已验证流程的参数化复用和来源治理，不把客户端提供的流程步骤称为模型自动抽取。敏感写入拒绝和会话隔离作为独立产品检查记录，也不计成模型任务。七次会话是功能展示案例，不能当作大规模基准的泛化成绩；SDK 每次会话可能包含多次模型请求及纠正行动。

每次会话默认最多 10 次 SDK 迭代，可用 `--max-iterations` 调整。达到上限仍未形成最终回答时保留未完成结果，即使文件已改变也不计作完整任务成功。工具结果优先用 SDK 调用编号关联，缺少编号的旧数据按记录顺序处理；复合命令只记录整个命令组的退出码，不能据此推断每个子命令单独成功。Hook、偏好遵循检查和用户反馈共用引号感知的 shell 词法解析，避免把输出文本或正则表达式中的工具名误判成调用。

来源隔离改变了旧抽取数据集的计分结果。原 EvidenceBench v2 的 40 条抽取样本中，10 条把来自 `tool_result` 的指令文字标为用户偏好；当前生产逻辑将这些文字归为工具知识，类型和子类型准确率按原标注均为 0.75，主题准确率仍为 1.0。原数据、标注和评分程序保持不变，10 条不一致逐条进入失败清单，不通过重标注恢复满分。该项历史 1.0 成绩不能用于新版本。新来源边界另由工具文本污染回归、跨会话已验证工具经验及真实 Agent 遗忘链路检验：经验仍可沉淀复用，但工具输出不能充当用户授权。
