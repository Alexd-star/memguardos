# MemGuardOS 真实 Agent 闭环开发补齐规划

> **执行要求：** 实施本规划时，应使用测试驱动开发，逐任务完成测试、实现、openKylin 复验和文档同步。不得在闭环未验证前把赛题符合性状态改为“已满足”。

**目标：** 在不破坏 MemGuardOS 现有稳定主链路的前提下，接入 OpenHands 现有 Agent，完成用户—Agent 交互习惯自动提取、工具结果自动沉淀、三阶段记忆调用、跨会话复用和精准遗忘闭环。

**架构：** 使用 MemGuardOS 自有标准事件协议隔离 Agent 版本差异；OpenHands 适配器只负责事件转换和上下文注入；偏好提取、敏感治理、版本化存储、检索和遗忘继续由现有核心服务负责。

**技术栈：** Python 3.12、OpenHands SDK、dataclasses、SQLite、Unix domain socket、pytest、systemd、cgroup v2、journald、openKylin 2.0 SP2。

---

## 1. 实施原则

1. OpenHands SDK 必须作为可选依赖，`pip install -e .` 的核心安装仍保持轻量。
2. 所有交互事件先标准化，再进入记忆核心。
3. 只有用户角色或明确用户反馈可以直接形成偏好。
4. Assistant 消息默认不能形成用户偏好。
5. 工具输出先脱敏再持久化。
6. 所有写入必须幂等。
7. 所有闭环能力都要有无网络 replay 测试。
8. Windows 本地测试、openKylin 实测和文档指标必须分开记录。
9. 不修改或删除现有用户生成的证据和文档。
10. 每完成一个阶段就复跑核心测试，避免最后集中排错。

## 2. 文件结构规划

### 2.1 新增源码

```text
memguard/integrations/
├── __init__.py
├── events.py
├── normalizer.py
├── interaction_extractor.py
├── habit_aggregator.py
├── context_injector.py
├── openhands_adapter.py
├── open_interpreter_adapter.py
└── replay.py
```

### 2.2 修改源码

```text
memguard/models.py
memguard/service.py
memguard/store/sqlite_meta.py
memguard/api/server.py
memguard/cli/memctl.py
memguard/web/dashboard.py
memguard/eval/run_eval.py
pyproject.toml
```

### 2.3 新增测试和数据

```text
tests/test_agent_events.py
tests/test_agent_preferences.py
tests/test_agent_context.py
tests/test_openhands_adapter.py
tests/test_agent_closed_loop.py
data/benchmarks/agent_interactions.jsonl
data/demo/agent_sessions/
```

### 2.4 新增脚本和证据

```text
scripts/install_agent_extras_openkylin.sh
scripts/run_openhands_memguard_demo.py
scripts/replay_agent_session.py
scripts/evaluate_agent_interactions.py
scripts/collect_agent_evidence.sh
docs/真实Agent接入使用说明.md
docs/真实Agent闭环测试报告.md
```

## 3. 分阶段优先级

| 阶段 | 内容 | 优先级 | 完成后价值 |
|---|---|---:|---|
| A | 标准事件协议、幂等和存储 | P0 | 建立所有 Agent 共用底座 |
| B | 用户交互偏好提取与聚合 | P0 | 正面满足老师最新解释 |
| C | 三阶段上下文注入 | P0 | 证明记忆服务真实 Agent |
| D | OpenHands 适配器 | P0 | 形成真实现有 Agent 闭环 |
| E | 精准遗忘与外部会话边界 | P0 | 消除遗忘证据漏洞 |
| F | Benchmark、Web 和证据包 | P1 | 提升可信度与答辩观感 |
| G | Open Interpreter 兼容 | P2 | 展示可扩展性，不阻塞主线 |

## 4. 任务 1：建立标准交互事件协议

**文件：**

- 新建 `memguard/integrations/__init__.py`
- 新建 `memguard/integrations/events.py`
- 新建 `tests/test_agent_events.py`

### 步骤

- [ ] 编写事件创建、字段校验、JSON 序列化和 provenance hash 的失败测试。
- [ ] 验证无 `session_id`、非法角色、非法事件类型会被拒绝。
- [ ] 验证相同规范化事件产生相同哈希。
- [ ] 验证不同轮次或工具结果产生不同哈希。
- [ ] 实现不可变 `AgentInteractionEvent`。
- [ ] 运行：

```bash
python -m pytest tests/test_agent_events.py -q
```

**通过标准：** 事件可以稳定序列化和反序列化；无效角色和类型被拒绝；哈希可复现。

## 5. 任务 2：事件标准化与幂等

**文件：**

- 新建 `memguard/integrations/normalizer.py`
- 修改 `memguard/store/sqlite_meta.py`
- 修改 `memguard/service.py`
- 扩展 `tests/test_agent_events.py`

### 数据库迁移

新增 `agent_events`：

```sql
CREATE TABLE IF NOT EXISTS agent_events (
    event_id TEXT PRIMARY KEY,
    provenance_hash TEXT NOT NULL UNIQUE,
    schema_version TEXT NOT NULL,
    agent_name TEXT NOT NULL,
    agent_version TEXT NOT NULL,
    session_id TEXT NOT NULL,
    turn_id TEXT NOT NULL,
    sequence INTEGER NOT NULL,
    event_type TEXT NOT NULL,
    role TEXT NOT NULL,
    content TEXT NOT NULL,
    tool_name TEXT NOT NULL,
    tool_args_json TEXT NOT NULL,
    tool_result TEXT NOT NULL,
    exit_code INTEGER,
    timestamp TEXT NOT NULL,
    source_uri TEXT NOT NULL,
    created_at TEXT NOT NULL
);
```

### 步骤

- [ ] 测试同一事件提交两次只保存一次。
- [ ] 测试批量事件乱序时按 `sequence` 标准化。
- [ ] 测试工具参数中的环境变量、Token 和密码在存储前被过滤。
- [ ] 实现 `normalize_event()` 和 `normalize_tool_args()`。
- [ ] 实现 SQLite migration，不删除旧表。
- [ ] 新增 `MemGuardService.ingest_interaction()`。
- [ ] 审计事件写入 `interaction_ingest` 和 `interaction_duplicate`。

**通过标准：** 事件重放不会生成重复事件或重复记忆；原有数据库可以原位升级。

## 6. 任务 3：建立记忆与事件的可追溯关联

**文件：**

- 修改 `memguard/store/sqlite_meta.py`
- 修改 `memguard/service.py`
- 修改 `memguard/models.py`
- 扩展 `tests/test_agent_events.py`

新增表：

```sql
CREATE TABLE IF NOT EXISTS memory_event_links (
    memory_id TEXT NOT NULL,
    event_id TEXT NOT NULL,
    relation TEXT NOT NULL,
    PRIMARY KEY (memory_id, event_id, relation)
);
```

### 步骤

- [ ] 测试一条显式偏好能够回溯到原始用户事件。
- [ ] 测试一个聚合偏好可以关联多个事件。
- [ ] 测试遗忘记忆后保留脱敏的审计关系，但不返回已删除原文。
- [ ] 为 service 增加 `preference_evidence(memory_id)`。
- [ ] API 和 CLI 输出来源 Agent、会话、轮次和事件 ID。

**通过标准：** 评委可以从偏好记录查到形成依据，又不会看到被遗忘或未脱敏的原文。

## 7. 任务 4：显式交互偏好提取

**文件：**

- 新建 `memguard/integrations/interaction_extractor.py`
- 修改 `memguard/extraction/semantic.py`
- 新建 `tests/test_agent_preferences.py`

### 抽取结构

```python
@dataclass
class PreferenceObservation:
    topic: str
    value: str
    polarity: str
    scope: str
    explicit: bool
    confidence: float
    evidence_event_id: str
```

### 必测样例

- “以后搜索代码优先使用 rg。”
- “回答先给命令，再解释。”
- “删除文件前必须询问我。”
- “这次用 grep，下次还是用 rg。”
- “不要记住这条消息。”
- Assistant：“我以后会优先用 rg。”应产生零条用户偏好。
- Tool 输出包含“prefer rg”应产生零条用户偏好。

### 步骤

- [ ] 先写角色隔离和显式偏好拆分测试。
- [ ] 实现主题标准化：工具选择、输出顺序、语言、详细程度、安全确认。
- [ ] 实现局部例外识别。
- [ ] 一条消息包含多个偏好时拆分为多个 observation。
- [ ] 将 observation 转为独立 MemoryRecord。

**通过标准：** 用户显式偏好可自动生成，Assistant 和工具文本不会污染偏好。

## 8. 任务 5：多轮交互习惯聚合

**文件：**

- 新建 `memguard/integrations/habit_aggregator.py`
- 修改 `memguard/store/sqlite_meta.py`
- 修改 `memguard/service.py`
- 扩展 `tests/test_agent_preferences.py`

新增 `preference_observations` 表，包含主题、取值、极性、Agent、项目、会话和证据等级。

### 聚合规则

```python
stable = (
    evidence_count >= 3
    and distinct_sessions >= 2
    and positive_ratio >= 0.8
    and contradiction_count == 0
)
```

### 步骤

- [ ] 测试一次偶然行为不会形成长期偏好。
- [ ] 测试三次相同反馈、两个会话后形成长期偏好。
- [ ] 测试出现相反反馈时保持候选，不自动升级。
- [ ] 测试用户明确确认可以直接提升候选。
- [ ] 实现候选、稳定、冲突三种状态。
- [ ] 提供 `interaction_status()` 查看聚合过程。

**通过标准：** 隐式习惯形成保守、可解释，不因一次行为过拟合。

## 9. 任务 6：偏好作用域和冲突规则

**文件：**

- 修改 `memguard/models.py`
- 修改 `memguard/retrieval/conflict.py`
- 修改 `memguard/store/sqlite_meta.py`
- 扩展 `tests/test_agent_preferences.py`

### 步骤

- [ ] 增加 `preference_scope`、`agent_name`、`project_root`、`task_type` 元数据。
- [ ] 测试本轮要求覆盖项目偏好。
- [ ] 测试项目偏好覆盖全局偏好。
- [ ] 测试显式偏好覆盖隐式偏好。
- [ ] 测试“这次例外”不会关闭长期版本。
- [ ] 更新冲突解释，返回选择原因和未选原因。

**通过标准：** 跨场景适配不是简单返回全部偏好，而是按作用域选择正确版本。

## 10. 任务 7：三阶段 Agent 上下文

**文件：**

- 新建 `memguard/integrations/context_injector.py`
- 修改 `memguard/service.py`
- 修改 `memguard/api/server.py`
- 新建 `tests/test_agent_context.py`

### 接口

```python
def build_agent_context(
    task: str,
    stage: str,
    agent_name: str,
    session_id: str,
    project_root: str = "",
    token_budget: int = 800,
) -> dict[str, object]:
    ...
```

允许阶段：

- `planning`
- `tool_selection`
- `response_generation`

### 步骤

- [ ] 测试 planning 不注入无关输出格式。
- [ ] 测试 tool_selection 优先返回工具偏好和程序性记忆。
- [ ] 测试 response_generation 返回语言和输出风格。
- [ ] 测试已删除、过期、敏感或跨 namespace 记忆不会进入上下文。
- [ ] 测试每条选择都有理由和来源事件。
- [ ] 实现一次批量检索后按阶段重排，控制额外延迟。

**通过标准：** 三个阶段得到不同、正确且可解释的记忆集合。

## 11. 任务 8：API 和 CLI

**文件：**

- 修改 `memguard/api/server.py`
- 修改 `memguard/cli/memctl.py`
- 修改 `tests/test_core.py`
- 扩展 `tests/test_agent_context.py`

### 新命令

```text
memctl agent ingest-event --file EVENT.json
memctl agent ingest-session --file SESSION.jsonl
memctl agent context --stage planning --task "..."
memctl agent status
memctl agent evidence MEMORY_ID
memctl agent replay --file SESSION.jsonl
```

### 步骤

- [ ] 为 API 增加严格大小、数量、类型和长度限制。
- [ ] 批量入口最多接收 500 个事件。
- [ ] 返回 `stored`、`duplicate`、`blocked`、`memory_ids`。
- [ ] 内部异常使用现有 error ID 和审计机制，不向 Agent 泄露堆栈。
- [ ] CLI 所有输出保持 JSON 可复验。

**通过标准：** 不安装 OpenHands 时，事件 replay 和所有核心 Agent API 仍可使用。

## 12. 任务 9：OpenHands 可选依赖和环境探针

**文件：**

- 修改 `pyproject.toml`
- 新建 `scripts/install_agent_extras_openkylin.sh`
- 新建 `tests/test_openhands_adapter.py`

截至 2026 年 6 月 18 日，PyPI 发布的 `openhands-sdk` 最新版本为 `1.28.1`，要求 Python `>=3.12`。openKylin 当前证据环境为 Python 3.12.2，因此将 `1.28.1` 作为首选验证版本：

```toml
[project.optional-dependencies]
agent-openhands = ["openhands-sdk==1.28.1"]
```

实施时仍需在 openKylin 隔离虚拟环境中记录 `pip index versions openhands-sdk`、依赖解析结果和 smoke test。如果 `1.28.1` 在目标环境发生上游依赖冲突，只允许回退到经 openKylin 实测通过的明确版本，并同步修改 `pyproject.toml`、安装记录、测试报告和证据清单，不能使用无上限的浮动依赖。

### 步骤

- [ ] 测试未安装 SDK 时返回清晰的可选依赖提示。
- [ ] 安装 SDK 后输出版本和核心类可用状态。
- [ ] 安装脚本支持 `--proxy http://127.0.0.1:7897`。
- [ ] 下载 wheel 到 `dist/wheels/agent-openhands/` 作为复验缓存，但正式源码包是否包含大体积 wheel 由提交限制决定。
- [ ] 不把任何 API Key 写入仓库、日志或证据。

**通过标准：** openKylin 可重复安装，核心 MemGuardOS 不因未安装 Agent 依赖而失败。

## 13. 任务 10：OpenHands 事件适配器

**文件：**

- 新建 `memguard/integrations/openhands_adapter.py`
- 扩展 `tests/test_openhands_adapter.py`

### 步骤

- [ ] 使用测试替身模拟 OpenHands 用户消息、Agent 消息、工具调用和工具结果。
- [ ] 验证每类事件映射到正确角色和类型。
- [ ] 验证未知事件记录为 `adapter_unknown_event`，但不导致会话崩溃。
- [ ] 验证 callback 重试不会重复写入。
- [ ] 实现版本兼容层，只在一个模块中导入 OpenHands 类型。
- [ ] 把 OpenHands 原始事件 ID 写入 `source_uri`。

**通过标准：** 适配器不包含偏好规则和数据库逻辑，OpenHands 类型变化只影响该模块。

## 14. 任务 11：真实 Agent 运行器

**文件：**

- 新建 `scripts/run_openhands_memguard_demo.py`
- 修改 `memguard/cli/memctl.py`
- 新建 `tests/test_agent_closed_loop.py`

### 运行流程

1. 获取用户任务。
2. 调用 `build_agent_context(stage="planning")`。
3. 把结构化记忆作为附加上下文交给 Agent。
4. Agent 规划后，在工具调用前调用 `tool_selection` 上下文。
5. 工具结果由 callback 自动写入。
6. 在最终回答前调用 `response_generation` 上下文。
7. 会话结束时写入任务结果和审计摘要。

### 步骤

- [ ] 使用 fake Agent 完成全闭环自动测试。
- [ ] 再使用真实 OpenHands 运行最小任务。
- [ ] 每轮输出 `context_trace_id`，便于 Web 和证据关联。
- [ ] 默认工作区指向专用演示目录，不允许操作用户主目录。
- [ ] 高风险工具调用遵循 MemGuardOS 安全策略。

**通过标准：** 用户不需要手工运行 `memctl ingest` 或准备工具 JSON。

## 15. 任务 12：精准遗忘闭环

**文件：**

- 修改 `memguard/service.py`
- 修改 `memguard/forgetting/planner.py`
- 修改 `memguard/api/server.py`
- 扩展 `tests/test_agent_closed_loop.py`

### 步骤

- [ ] 支持按 Agent、会话、轮次、偏好主题和时间窗口生成遗忘候选。
- [ ] 返回受影响的事件、记忆、版本、上下文使用记录和外部残留风险。
- [ ] 执行后清理 FTS、图谱、活动偏好和聚合 observation。
- [ ] compact 后检查 mmap 明文。
- [ ] 新建会话重新构建三个阶段上下文。
- [ ] 断言目标偏好在三个阶段均不存在。
- [ ] 断言 Agent 实际行为不再受该偏好影响。

**通过标准：** 不能只证明数据库记录被删，必须证明真实 Agent 不再获得和使用目标偏好。

## 16. 任务 13：交互 Benchmark

**文件：**

- 新建 `data/benchmarks/agent_interactions.jsonl`
- 新建 `scripts/evaluate_agent_interactions.py`
- 修改 `memguard/eval/run_eval.py`
- 新建 `docs/真实Agent闭环测试报告.md`

### 数据规模

首轮最低目标：

| 类型 | 数量 |
|---|---:|
| 显式偏好正例 | 40 |
| 角色污染负例 | 30 |
| 多轮隐式习惯 | 20 组 |
| 偏好纠正和例外 | 20 组 |
| 工具结果轨迹 | 30 组 |
| 敏感交互 | 20 组 |
| 精准遗忘 | 20 组 |
| 跨会话复用 | 20 组 |

### 步骤

- [ ] 数据包含人工标注字段和标注说明。
- [ ] 至少两名标注者裁决冲突类样例。
- [ ] 区分合成样例、真实脱敏会话和公开可复用样例。
- [ ] 输出 Precision、Recall、F1、遵守率、接入率、泄漏率和 P95。
- [ ] 报告列出失败样例和误差归因，不能只展示 1.0000。

**通过标准：** benchmark 能回答“不是只对自己写的几句话有效吗”的评委质疑。

## 17. 任务 14：Web 控制台产品化

**文件：**

- 修改 `memguard/web/dashboard.py`
- 扩展 Web API 测试

### 页面

- Agent 状态总览。
- 交互事件时间线。
- 显式偏好和隐式候选。
- 偏好证据抽屉。
- 三阶段上下文对比。
- 工具调用和程序性记忆。
- 精准遗忘二次确认。
- 一键导出 Agent 诊断包。

### 步骤

- [ ] 后端先提供结构化 API。
- [ ] 页面按时间线呈现，不把所有信息堆成指标卡。
- [ ] 敏感内容始终使用脱敏预览。
- [ ] 已删除事件只显示删除事实和哈希，不显示原文。
- [ ] 在 1366×768 和 1920×1080 下检查无重叠。

**通过标准：** 评委可以直接看懂“用户说了什么、提取了什么、Agent 用了什么、忘记后发生了什么”。

## 18. 任务 15：openKylin 服务化

**文件：**

- 修改 `scripts/install_openkylin.sh`
- 新增或修改 systemd unit
- 新建 `scripts/collect_agent_evidence.sh`

建议服务边界：

- `memguardd.service`：稳定核心 daemon。
- `memguard-agent@.service`：可选 Agent 会话桥。
- 每个实例单独 namespace 和 cgroup。

### 步骤

- [ ] Agent bridge 使用普通用户权限运行。
- [ ] 配置 `NoNewPrivileges`、`PrivateTmp` 和合理的文件访问范围。
- [ ] 设置 CPU 和内存限制。
- [ ] journald 记录启动、会话、错误和上下文 trace，不记录敏感原文。
- [ ] 服务失败不影响核心 daemon。

**通过标准：** Agent 适配器异常时，MemGuardOS 核心检索、遗忘和审计仍可用。

## 19. 任务 16：真实 openKylin 闭环证据

### 演示数据

使用无敏感信息的专用代码目录，设计两轮会话：

**会话 A：**

```text
以后搜索项目代码优先使用 rg。回答时先给命令，再解释原因。
请找出项目中定义 build_context 的位置。
```

**会话 B：**

```text
请再次查找精准遗忘的实现位置。
```

预期会话 B 自动使用 `rg`，回答先给命令，并在证据中标明使用的偏好 ID。

随后执行：

```text
忘记我关于“回答时先给命令”的偏好。
```

会话 C 证明工具偏好仍存在，但输出顺序偏好已经消失。这同时验证偏好拆分和精准遗忘粒度。

### 证据采集

- [ ] Agent 安装和版本。
- [ ] daemon 与 bridge 状态。
- [ ] 会话事件 JSONL。
- [ ] 自动提取记录。
- [ ] 三阶段上下文。
- [ ] 工具实际调用。
- [ ] 跨会话复用。
- [ ] 遗忘 dry-run。
- [ ] 遗忘确认和 compact。
- [ ] 新会话不再使用。
- [ ] 审计链。
- [ ] systemd/cgroup。
- [ ] SHA256 清单。

**通过标准：** 任一评委仅根据证据目录和命令即可复跑闭环。

## 20. 任务 17：赛题文档与口径更新

**文件：**

- 修改 `README.md`
- 修改 `docs/赛题要求逐句符合性核查.md`
- 修改 `docs/概要设计说明书.md`
- 修改 `docs/详细设计说明书.md`
- 修改 `docs/测试报告.md`
- 修改 `docs/演示视频脚本.md`
- 修改 `docs/最终提交说明.md`
- 修改 `PROJECT_INDEX.md`

### 更新规则

- 在 openKylin 闭环完成前，R7、R10、R12、R19 标记为“部分满足”或“基本满足”。
- 完成并取得证据后再更新为“已满足”。
- shell history 表述为补充行为数据，不再代表主要用户交互习惯。
- 明确 OpenHands 是现有 Agent，MemGuardOS 没有自研 Agent。
- 明确模型费用和 Agent 原始会话保存边界。
- 指标只引用实际生成的 JSON 和证据文件。

**通过标准：** README、测试报告、符合性核查和视频稿不存在相互矛盾的完成度表述。

## 21. 任务 18：提交前质量门禁

### 自动门禁

```bash
python -m pytest -q
python -m ruff check memguard tests scripts
python scripts/evaluate_agent_interactions.py
python scripts/check_competition_ready.py
python scripts/package_submission.py
python scripts/inspect_submission_package.py
python scripts/smoke_submission_archive.py
```

### openKylin 门禁

```bash
bash scripts/openkylin_verify.sh
bash scripts/collect_agent_evidence.sh
bash scripts/collect_openkylin_evidence.sh --with-10k
```

### 阻断条件

出现以下任一情况不得宣称完成：

- OpenHands 真实会话未运行。
- 工具结果仍需人工导入。
- Assistant 文本会被抽成用户偏好。
- 跨会话没有自动注入。
- 遗忘后目标偏好仍在任一阶段上下文中。
- 原始 Agent 会话残留未说明。
- 审计链失败。
- 指标没有来源文件。
- openKylin 测试未通过。

## 22. 推荐实施顺序与预计时间

| 工作 | 预计时间 |
|---|---:|
| 事件协议、存储迁移和幂等 | 1.5 天 |
| 显式偏好、习惯聚合和作用域 | 2 天 |
| 三阶段上下文和 API | 1.5 天 |
| OpenHands 安装、适配和运行器 | 2 天 |
| 精准遗忘闭环 | 1 天 |
| benchmark 和误差修正 | 2 天 |
| Web 控制台增强 | 1.5 天 |
| openKylin 服务化和证据 | 1.5 天 |
| 文档、视频脚本和提交门禁 | 1 天 |
| 风险缓冲 | 2 天 |

合计约 16 天。若 AI 编程工具持续自动运行测试和修复，核心 P0 闭环可压缩到 7 至 10 天，但不能省略真实 openKylin 复验和人工观察 Agent 行为。

## 23. 阶段验收点

### 里程碑 M1：事件底座

- 标准事件、幂等、事件存储和来源关联全部通过。

### 里程碑 M2：交互偏好

- 显式偏好、隐式习惯、纠正、作用域和角色隔离通过。

### 里程碑 M3：记忆调用

- 三阶段上下文和跨会话 replay 闭环通过。

### 里程碑 M4：真实 Agent

- OpenHands 在 openKylin 完成真实工具任务并自动写入。

### 里程碑 M5：精准遗忘

- 新会话不再获得和使用被遗忘偏好。

### 里程碑 M6：竞赛交付

- benchmark、Web、证据、文档、提交包和视频脚本全部同步。

## 24. 开发完成后的严格评委判断

完成上述 P0 和 P1 任务后，MemGuardOS 将不再只是“具备供 Agent 调用的记忆模块”，而是有证据证明：

1. 一个真实现有 Agent 在 openKylin 上运行。
2. 用户与 Agent 的真实交互会自动形成偏好。
3. Agent 的工具结果会自动沉淀为知识和程序性记忆。
4. 新会话会在规划、工具选择和生成时调用不同记忆。
5. 系统能证明 Agent 是否真正遵守偏好。
6. 偏好变化可版本化、回溯和按粒度遗忘。
7. 遗忘后真实 Agent 不再使用目标记忆。
8. 全流程具备 OS 机制、性能指标、审计和可复验证据。

这将直接补上当前最容易被严格评委追问的缺口，并把项目从“高完成度记忆组件”提升为“真实 Agent 记忆治理闭环”。
