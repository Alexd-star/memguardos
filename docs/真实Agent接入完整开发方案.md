# MemGuardOS 真实 Agent 接入完整开发方案

版本：1.0

日期：2026-06-18

适用项目：MemGuardOS 0.1.x

适用赛题：面向 Linux 的智能体记忆提取与精准遗忘机制

目标环境：openKylin 2.0 SP2 x86_64、Python 3.12

## 1. 文档目的

本文给出 MemGuardOS 接入真实现有 Agent 的完整技术方案。方案不是增加一个仅用于演示的聊天窗口，也不是把人工准备好的 JSON 文件包装成 Agent 数据，而是让一个可实际对话、规划和调用工具的现有 Agent 在 openKylin 上运行，并由 MemGuardOS 自动完成交互事件采集、偏好提取、知识沉淀、上下文构建、跨会话复用、版本更新、敏感治理和精准遗忘。

老师最新答复明确了两个边界：

1. 参赛队伍不需要自行实现 OS Agent，安装并接入一个现有 Agent 即可。
2. 赛题中的“用户操作习惯”主要指用户与 Agent 的交互习惯，而不是用户与其他桌面应用无关的全量操作轨迹。

这意味着 MemGuardOS 当前的记忆内核、Unix socket API、工具结果接入、RAG 上下文和精准遗忘仍然可以直接复用，但 shell history 只能作为补充数据源，不能再作为“用户与 Agent 交互习惯自动提取”的主要证据。项目必须补齐真实 Agent 的自动采集与自动调用闭环。

## 2. 当前实现评估

### 2.1 可以直接复用的能力

| 能力 | 当前实现 | 在真实 Agent 闭环中的作用 |
|---|---|---|
| 文本和文件接入 | `MemGuardService.ingest_text()` | 接收用户消息、Agent 反馈和会话摘要 |
| 工具结果接入 | `ToolResult`、`ingest_tool_result()` | 保存 Agent 的工具动作、执行结果和程序性经验 |
| 偏好与知识抽取 | `HybridMemoryExtractor` | 对标准化交互事件执行可解释抽取 |
| 敏感信息治理 | `SensitiveDetector` | 在事件进入长期记忆前阻断或脱敏 |
| 版本管理 | `update_memory()`、`versions()`、`rollback()` | 处理偏好变化、纠正和回溯 |
| 统一检索 | `search()`、`batch_search()` | 为 Agent 规划、工具选择和回答生成检索记忆 |
| RAG 上下文 | `build_context()`、`build-prompt` | 构造脱敏、可解释、受预算约束的上下文 |
| 精准遗忘 | `forget()`、`compact()`、`verify_forget_probe()` | 删除指定偏好、会话经验或知识并验证泄漏 |
| 审计 | SQLite 哈希链、journald、auditd | 证明采集、调用、更新和遗忘过程可追溯 |
| OS 机制 | mmap、systemd/cgroup、namespace、BPF LSM | 满足赛题的系统级创新和资源治理要求 |

### 2.2 必须补齐的缺口

| 缺口 | 根本原因 | 影响 |
|---|---|---|
| 没有真实现有 Agent | 当前只有“上游可以调用”的 API | 不能证明方案真正应用于智能体 |
| 没有统一交互事件模型 | 数据入口以文本、文件和工具结果分别实现 | 无法稳定表达用户、Agent、工具、反馈和会话关系 |
| 用户交互习惯证据不足 | shell history 被当作用户行为主要来源 | 与老师最新解释不完全一致 |
| 缺少自动采集 | 工具 JSON 主要由命令人工导入 | “动态捕捉”证据不够强 |
| 缺少自动记忆注入 | `build_context` 需要上游主动调用 | 不能证明 Agent 规划、工具调用和生成真实使用记忆 |
| 缺少多轮习惯聚合 | 当前规则主要处理单条文本 | 隐式偏好容易误判或无法形成 |
| 缺少来源角色约束 | Assistant 文本也可能含有偏好词句 | 存在 Agent 自我污染记忆的风险 |
| 缺少真实闭环 benchmark | 当前 benchmark 主要评测记忆模块本身 | 不能量化 Agent 接入后的自动采集和复用效果 |
| 原始会话与精准遗忘边界未定义 | Agent 可能自行保存完整会话 | MemGuardOS 删除后原始会话仍可能留存目标内容 |

## 3. Agent 选型

### 3.1 主方案：OpenHands SDK

选择 OpenHands SDK 作为首个真实适配器，理由如下：

1. OpenHands 是现有开源软件工程 Agent，不需要本项目重新实现 Agent。
2. SDK 提供 `Conversation`、Agent、工具、工作空间、事件及回调等明确边界。
3. 用户消息、Agent 消息、工具动作和工具结果可以被标准化为事件。
4. Python 包可以作为可选依赖安装，不污染 MemGuardOS 无依赖的核心路径。
5. openKylin 现有 Python 3.12.2 满足当前 SDK 的 Python 版本条件。
6. Agent 接入层可以在用户态运行，MemGuardOS 仍由 systemd、cgroup 和 Unix socket 管理。

OpenHands 只作为“真实 Agent 生产者和记忆消费者”。记忆所有权、敏感治理、版本化和遗忘仍由 MemGuardOS 负责，不能把 MemGuardOS 退化成 OpenHands 会话文件的旁路索引。

### 3.2 可选方案：Open Interpreter

Open Interpreter 适合做第二适配器或离线会话导入器。其优势是交互直观、能够执行本地代码和命令；不足是当前项目从早期 Python API 向新架构迁移，版本接口稳定性弱于通用事件层。因此首轮不把它作为唯一主路径。

### 3.3 不采用的方案

不自行实现一个“伪 Agent”。自制循环虽然容易控制，但不符合老师“安装现有 Agent 即可”的提示精神，也容易被评委质疑为为了证明记忆系统而临时搭建的演示外壳。

不直接侵入 OpenHands 核心源码。侵入式补丁会增加升级和复验风险，也难以证明 MemGuardOS 能适配其他 Agent。

不把完整聊天记录全部写入长期记忆。长期保存原始会话会制造隐私、冗余和精准遗忘边界问题。

## 4. 总体架构

```text
┌─────────────────────────────────────────────────────────────┐
│                    openKylin 用户会话                       │
└──────────────────────────┬──────────────────────────────────┘
                           │ 用户任务
                           ▼
┌─────────────────────────────────────────────────────────────┐
│                    OpenHands 现有 Agent                      │
│ Conversation / Agent / Tools / Workspace / Event callbacks  │
└───────────────┬──────────────────────────────┬──────────────┘
                │ 事件回调                      │ 每轮前检索
                ▼                              ▲
┌──────────────────────────┐      ┌───────────────────────────┐
│ Agent Event Adapter      │      │ Context Injector          │
│ 角色、会话、工具、结果   │      │ planning/tool/generation  │
└───────────────┬──────────┘      └─────────────┬─────────────┘
                │ 标准事件                       │ 上下文包
                ▼                               │
┌─────────────────────────────────────────────────────────────┐
│                    MemGuardOS 核心                           │
│ 质量校验 → 敏感治理 → 偏好/知识抽取 → 聚合 → 版本化存储     │
│ mmap + SQLite/FTS/图谱 → 混合检索 → 精准遗忘 → 审计         │
└─────────────────────────────────────────────────────────────┘
```

架构采用“核心协议稳定、Agent 适配器可替换”的设计。OpenHands 事件先转换为 MemGuardOS 自有事件，后续再增加 Open Interpreter、Claude Code、Codex CLI 或其他 Agent 时，不需要修改记忆内核。

## 5. 标准交互事件协议

新增 `memguard/integrations/events.py`，定义 `AgentInteractionEvent`：

```python
@dataclass(frozen=True)
class AgentInteractionEvent:
    schema_version: str
    event_id: str
    agent_name: str
    agent_version: str
    session_id: str
    turn_id: str
    sequence: int
    event_type: str
    role: str
    content: str = ""
    tool_name: str = ""
    tool_args: dict[str, object] = field(default_factory=dict)
    tool_result: str = ""
    exit_code: int | None = None
    state_before: str = ""
    state_after: str = ""
    user_feedback: str = ""
    timestamp: str = field(default_factory=utc_now)
    source_uri: str = ""
    provenance_hash: str = ""
```

`event_type` 只允许：

- `user_message`
- `assistant_message`
- `tool_call`
- `tool_result`
- `user_feedback`
- `task_complete`
- `task_error`
- `session_start`
- `session_end`

`role` 只允许 `user`、`assistant`、`tool`、`system`。适配器不得把 Assistant 输出伪装成用户输入。

`provenance_hash` 由 Agent、会话、轮次、序号、事件类型和规范化载荷计算，用于幂等去重。Agent 重试、事件回放或证据复验不得产生重复记忆。

## 6. 数据处理链路

### 6.1 用户消息

用户消息进入以下处理流程：

1. 标准化编码、空白和长度。
2. 识别凭据、PII 和自定义敏感模式。
3. 提取显式偏好、安全策略、纠正信号和任务知识。
4. 写入事件审计。
5. 高置信显式偏好进入长期层。
6. 普通任务内容默认只进入 working/session 层，不自动长期保存。

例如：

> “以后在项目里搜索代码先用 rg，回答时先给命令，再解释原因。”

应产生两个独立偏好：

- `tool_preference/code_search_tool`：优先使用 `rg`。
- `output_style/command_first`：先给命令，再解释。

不能把整句话作为一个不可拆分的大文本偏好，否则后续只遗忘输出风格时会误删工具偏好。

### 6.2 Assistant 消息

Assistant 消息默认不进入偏好抽取，只可用于：

- 记录任务结果摘要；
- 判断 Agent 是否遵守已注入偏好；
- 计算交互效果指标；
- 在用户明确确认后形成知识案例。

禁止从 Assistant 的“我以后会使用 rg”自动生成用户偏好。这是防止自我强化和记忆污染的关键规则。

### 6.3 工具调用

工具调用事件保留：

- 工具名称；
- 脱敏后的参数；
- 工作目录；
- 风险等级；
- 调用前状态；
- 对应用户任务；
- 使用了哪些偏好或历史经验。

工具参数中发现 Token、密码、私钥或高风险路径时，先执行敏感治理，再决定是否记录。默认不保存完整环境变量。

### 6.4 工具结果

工具结果转换为现有 `ToolResult`，并增强以下字段：

- `session_id`
- `turn_id`
- `tool_call_id`
- `task_goal`
- `preference_memory_ids`
- `retrieved_memory_ids`
- `result_class`

成功结果可以形成 workflow、case 或 procedural trace；失败结果进入 working 层，只有在修复成功或多次出现时才提升到长期知识。

### 6.5 用户反馈

用户反馈是交互习惯和偏好版本化的高价值来源：

| 反馈 | 动作 |
|---|---|
| “对，就按这个格式” | 增加相应偏好证据计数 |
| “以后不要这样写” | 创建相反偏好或关闭旧版本 |
| “这次用 grep，下次还是 rg” | 记录局部例外，不覆盖全局偏好 |
| “这条不要记住” | 当前事件不进入记忆，并写入审计 |
| “忘记我刚才的偏好” | 进入精准遗忘 dry-run |

## 7. 偏好提取与交互习惯聚合

### 7.1 显式偏好

显式偏好可以立即保存，但必须满足：

- 说话角色是用户；
- 文本有明确指令或选择；
- 抽取结果有具体主题和值；
- 不包含未处理的高风险敏感信息；
- 能给出原始事件 ID 和证据片段。

### 7.2 隐式习惯

“用户交互习惯”不能由单次偶然行为推断。推荐采用保守聚合：

```text
形成候选：
  相同规范化主题至少出现 2 次

形成稳定隐式偏好：
  evidence_count >= 3
  distinct_sessions >= 2
  positive_ratio >= 0.8
  contradiction_count == 0
```

隐式候选在达到稳定阈值前保存在 session 层，不注入高风险工具决策。达到阈值后提升到 long_term，并记录聚合证据、会话数量和置信度。

### 7.3 偏好作用域

每条偏好增加作用域：

- `global`：所有 Agent 和项目。
- `agent`：只对指定 Agent。
- `project`：只对项目目录。
- `task_type`：只对代码搜索、部署、文档等任务类型。
- `session`：只在当前会话有效。

冲突处理顺序：

```text
用户本轮明确要求
> session
> project/task_type
> agent
> global
> 推断型隐式偏好
```

同一作用域内，显式纠正优先于旧偏好，随后比较有效时间、来源等级和置信度。

## 8. 三阶段上下文注入

### 8.1 规划阶段

查询内容由用户任务、项目、历史失败和安全约束构成。注入：

- 相关 workflow；
- 历史成功/失败案例；
- 项目知识；
- 全局和项目级安全策略；
- 当前任务已知限制。

### 8.2 工具选择阶段

注入：

- 工具选择偏好；
- 高风险操作确认策略；
- 相似状态下的程序性记忆；
- 最近失败修复轨迹；
- 工具禁用和替代规则。

### 8.3 结果生成阶段

注入：

- 输出语言；
- 详细程度；
- 文档格式；
- 是否先给结论；
- 证据边界；
- 禁止泄露的敏感策略。

三阶段分别执行检索和审计，但可以共享一次批量检索以降低延迟。输出应包含 `selected`、`filtered`、`reason`、`memory_id`、`source_event_ids` 和 `estimated_tokens`。

## 9. OpenHands 适配器

新增 `memguard/integrations/openhands_adapter.py`。适配器职责：

1. 检测 OpenHands SDK 是否安装。
2. 创建或接收 `Conversation`。
3. 注册事件回调。
4. 将 OpenHands 事件转换为 `AgentInteractionEvent`。
5. 在每轮开始前调用上下文注入器。
6. 在每轮结束后批量提交事件。
7. 记录适配器状态、版本和错误。

适配器不得承担偏好判断、敏感识别或数据库写入，这些必须由核心服务完成。

建议提供两种运行模式：

- `memctl agent run --adapter openhands`：启动真实交互会话。
- `memctl agent replay --adapter openhands --file events.jsonl`：离线重放真实事件，用于测试和证据复验。

如果 OpenHands API 版本变化，只修改适配器，不修改事件协议和核心服务。

## 10. 原始会话与精准遗忘

真实 Agent 接入后，精准遗忘必须覆盖四个层面：

1. MemGuardOS 当前有效记忆。
2. SQLite FTS、图谱边和向量索引。
3. mmap 段文件中的旧明文，经过 compact 清理。
4. Agent 自身可能保存的原始会话。

默认策略：

- 关闭 OpenHands 原始内容的永久保存，或把会话工作目录放入临时 namespace。
- MemGuardOS 只保存筛选后的结构化记忆。
- 如果用户明确选择保存 Agent 会话，精准遗忘结果必须返回 `external_residue_warning`，说明外部会话存储不受 MemGuardOS 单独控制。
- 最终演示使用“无外部长期会话副本”模式，保证遗忘边界清晰。

遗忘后的真实 Agent 验证不能只运行 `verify-forget`，还必须重新开启一个会话，证明被遗忘偏好没有进入 planning、tool_selection 或 response_generation 上下文。

## 11. API 扩展

Unix socket 增加以下命令：

| 命令 | 作用 |
|---|---|
| `ingest_interaction` | 接收一个标准 Agent 事件 |
| `ingest_interactions` | 原子化接收一批事件 |
| `build_agent_context` | 按阶段构建 Agent 上下文 |
| `interaction_status` | 查看会话、事件、聚合和去重状态 |
| `preference_evidence` | 查看偏好的来源事件和聚合证据 |
| `forget_interaction` | 按 Agent、会话、轮次或主题规划遗忘 |

批量事件提交需要事务边界。部分事件失败时返回逐项结果，但不得重复写入已经成功的事件。

## 12. 存储模型扩展

建议增加以下 SQLite 表：

### 12.1 `agent_events`

保存脱敏后的事件元数据、规范化摘要和哈希，不保存不必要的原始大文本。

### 12.2 `memory_event_links`

建立记忆与来源事件的多对多关系，支持回答“这条偏好是根据哪些交互形成的”。

### 12.3 `preference_observations`

保存规范化主题、取值、极性、会话、证据等级和置信度，用于多轮聚合。

### 12.4 `context_usage`

记录某条记忆在哪个 Agent、会话、阶段被注入，以及 Agent 是否遵守，用于评测复用效果。

这些表必须纳入 namespace 隔离和审计链。数据库升级通过显式 schema migration 完成，不能依靠删除旧数据库。

## 13. Web 控制台增强

新增“Agent 交互”视图：

- 当前 Agent 和会话状态；
- 最近事件时间线；
- 自动提取的显式偏好；
- 尚未达到阈值的隐式习惯候选；
- 偏好来源证据；
- 三阶段上下文选择轨迹；
- 工具调用与结果；
- 精准遗忘候选；
- Agent SDK、MemGuardOS daemon、审计链红黄绿状态。

页面不能展示未脱敏的 Token、密码或私钥。高风险遗忘仍需二次确认。

## 14. 测试与评测体系

### 14.1 单元测试

- 事件 schema 校验。
- provenance hash 稳定性。
- 重复事件幂等。
- 用户与 Assistant 角色隔离。
- 显式偏好拆分。
- 隐式习惯阈值。
- 相反反馈和版本更新。
- 工具结果映射。
- 三阶段上下文过滤。
- 外部会话残留警告。

### 14.2 集成测试

- OpenHands callback → 标准事件 → 记忆。
- 用户偏好 → 新会话上下文注入。
- 工具失败 → 修复 → 程序性记忆 → 相似任务召回。
- 精准遗忘 → 新会话不再注入。
- 敏感用户消息和工具输出不进入明文长期存储。
- daemon 和 Unix socket 重启后继续工作。

### 14.3 Benchmark 数据

新增 `data/benchmarks/agent_interactions.jsonl`，至少覆盖：

- 中文显式输出风格；
- 中文工具偏好；
- 安全确认策略；
- 英文偏好；
- 中英混合偏好；
- 多轮隐式习惯；
- 用户纠正；
- 局部例外；
- Assistant 诱导负例；
- 工具失败和修复；
- 敏感参数；
- 精准遗忘；
- 跨会话调用；
- 跨项目作用域；
- 事件重放。

真实样例应来自实际 OpenHands 会话，完成脱敏后纳入数据集，并保留生成方式和人工标注说明。

### 14.4 量化指标

| 指标 | 目标 |
|---|---:|
| 事件解析成功率 | ≥99% |
| 显式偏好 Precision | ≥0.95 |
| 显式偏好 Recall | ≥0.95 |
| 隐式习惯 Precision | ≥0.90 |
| Assistant 误抽取为用户偏好 | 0 |
| 工具结果自动接入率 | ≥98% |
| 跨会话偏好复用成功率 | ≥95% |
| 偏好遵守率 | ≥90% |
| 事件重复写入率 | 0 |
| 适配器附加 P95 延迟 | ≤50ms |
| 完整记忆检索 P95 | ≤500ms |
| 遗忘后上下文泄漏率 | 0 |
| 审计链验证 | 100% 通过 |

“偏好遵守率”必须通过可自动检查任务计算，例如是否实际选择 `rg`、回答是否以命令开头，而不是仅由人工主观判断。

## 15. openKylin 验证方案

最终证据必须来自 openKylin 2.0 SP2：

1. 安装 OpenHands SDK 可选依赖。
2. 输出 Agent 版本和 Python 版本。
3. 启动 MemGuardOS daemon。
4. 启动真实 Agent 会话。
5. 自动提取两个用户偏好。
6. 新会话复用偏好。
7. 真实执行一个工具并自动导入结果。
8. 复用工具经验。
9. 精准遗忘一个偏好。
10. 新会话证明不再注入。
11. 验证审计链、journald、cgroup 和 namespace。
12. 采集事件 JSONL、上下文选择轨迹、测试结果和 SHA256 清单。

证据目录建议新增：

```text
docs/evidence/latest/
├── agent_environment.txt
├── agent_install.txt
├── agent_session_explicit_preference.jsonl
├── agent_session_cross_session_reuse.jsonl
├── agent_tool_result_auto_ingest.jsonl
├── agent_context_planning.json
├── agent_context_tool_selection.json
├── agent_context_response_generation.json
├── agent_forget_before.json
├── agent_forget_after.json
├── agent_interaction_eval.json
└── agent_closed_loop_summary.md
```

## 16. 评分点映射

| 评分点 | 新增证据价值 |
|---|---|
| 多源数据整合 | 真实用户消息、Agent 消息、工具调用、工具结果和反馈统一接入 |
| 偏好动态捕捉 | 显式提取、多轮聚合、纠正、版本和跨会话复用 |
| 知识结构化 | 工具轨迹自动形成 workflow、case 和 procedural memory |
| 评测机制 | 增加真实交互数据集、偏好遵守率和闭环指标 |
| 精准遗忘 | 证明遗忘后真实 Agent 不再使用目标记忆 |
| OS 深度集成 | Agent 和记忆服务在 openKylin 的 systemd/cgroup/namespace 中运行 |
| 性能优化 | 测量适配器附加延迟和三阶段上下文延迟 |
| 代码规范 | 通用事件协议与具体 Agent 解耦 |
| 文档质量 | 给出可复跑的真实 Agent 安装、使用和验收流程 |

## 17. 竞争性亮点

### 17.1 记忆来源可证明

每条偏好都能回到具体 Agent、会话、轮次和用户证据，而不是只给出一个抽取结果。

### 17.2 防自我污染

严格区分用户、Assistant、工具和系统角色，避免 Agent 自己生成的文本被误认为用户偏好。这是许多简单记忆演示忽略的问题。

### 17.3 多阶段记忆

不是把所有记忆拼接到一个提示词，而是分别服务规划、工具选择和结果生成，更贴合赛题原文。

### 17.4 行为效果闭环

系统不仅证明“检索到了偏好”，还验证 Agent 是否真正选择了指定工具、遵守了输出格式和安全策略。

### 17.5 精准遗忘闭环

从事件、结构化记忆、索引、段文件、上下文到下一次 Agent 行为逐层验证遗忘结果。

### 17.6 Agent 可替换

OpenHands 是首个适配器，但事件协议允许后续接入 Open Interpreter、Claude Code、Codex CLI 或其他 OS Agent，体现平台化能力。

## 18. 风险与控制措施

| 风险 | 控制措施 |
|---|---|
| OpenHands SDK 版本变化 | 锁定已验证版本，适配器独立，提供 replay 测试 |
| 模型 API 费用 | 默认支持 OpenAI 兼容端点；闭环测试可使用低成本模型或用户已有订阅认证 |
| Agent 执行危险命令 | 使用隔离工作区、只读演示项目和高风险确认策略 |
| 原始会话残留 | 默认不长期保存原始会话，或明确返回外部残留警告 |
| 事件量过大 | 批量写入、摘要、长度限制、working/session 淘汰 |
| Assistant 污染偏好 | 角色白名单，只有用户消息和明确用户反馈可直接形成偏好 |
| 隐式偏好误判 | 跨会话阈值、反例计数、人工确认入口 |
| 敏感工具输出 | 写入前脱敏，禁止保存环境变量和完整凭据 |
| 演示依赖网络 | 提前安装依赖并保留离线 wheel；提供事件 replay 作为应急证据 |

## 19. 参考资料

1. OpenHands SDK 文档：https://docs.openhands.dev/sdk
2. OpenHands 事件架构：https://docs.openhands.dev/sdk/arch/events
3. OpenHands SDK 源码：https://github.com/OpenHands/software-agent-sdk
4. Open Interpreter：https://github.com/OpenInterpreter/open-interpreter
5. Mem0: Building Production-Ready AI Agents with Scalable Long-Term Memory：https://arxiv.org/abs/2504.19413
6. A-MEM: Agentic Memory for LLM Agents：https://arxiv.org/abs/2502.12110
7. Zep: A Temporal Knowledge Graph Architecture for Agent Memory：https://arxiv.org/abs/2501.13956
8. General Agentic Memory Via Deep Research：https://arxiv.org/abs/2511.18423

## 20. 最终完成判据

只有同时满足以下条件，才可以把 R7、R10、R12 和 R19 更新为“已满足”：

1. openKylin 上真实安装并运行一个现有 Agent。
2. 用户与 Agent 的交互事件由适配器自动采集。
3. 工具调用和工具结果无需人工准备 JSON 即可进入记忆。
4. 显式偏好能够自动提取。
5. 多轮隐式习惯能够聚合，并有保守阈值。
6. 新 Agent 会话能够自动调用历史记忆。
7. Agent 的实际工具选择或输出行为能够证明偏好被使用。
8. 精准遗忘后，新会话不再收到和使用目标偏好。
9. 全流程有审计、测试、量化指标和 openKylin 证据。
10. 文档明确说明原始 Agent 会话的保存和遗忘边界。
