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
python -m ruff check memguard tests scripts
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
