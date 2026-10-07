# 09 接口文档

本文档说明 MemGuardOS 的主要对外接口，包括 CLI、Unix socket API、Web 控制台和可选 LLM 生成接口。具体命令以 `python -m memguard.cli.memctl --help` 输出为准。

## 1. CLI 接口

CLI 是最常用入口，适合人工演示、脚本复验和调试。

| 命令 | 功能 | 输入 | 输出 |
|---|---|---|---|
| `ingest` | 写入文本或文件记忆 | `--text` 或 `--file` | 写入结果、记忆 ID、审计信息 |
| `ingest-config` | 导入结构化配置 | 配置文件路径 | 记忆写入结果和质量报告 |
| `ingest-history` | 导入 shell history | history 文件 | 行为记忆写入结果 |
| `ingest-tool-result` | 导入工具执行结果 | JSON 文件 | 程序性记忆和审计记录 |
| `ingest-os-event` | 导入 OS 事件 | JSON/文本事件 | OS 事件记忆 |
| `admission-preview` | 评估记忆准入 | 文本、来源、层级和作用域 | 不写入的长期/会话/工作层建议、证据门槛和阻断原因 |
| `search` | 检索记忆 | 查询文本、top-k | 排序结果 |
| `batch-search` | 批量检索 | 多个查询 | 每个查询的结果 |
| `build-context` | 构建 RAG 上下文 | 查询、top-k、token 预算 | context package |
| `build-prompt` | 渲染生成提示词 | 查询、top-k、token 预算 | prompt 文本 |
| `forget` | 精准遗忘 | 自然语言遗忘请求 | 候选或删除结果 |
| `verify-forget` | 验证遗忘 | 探针文本 | 验证结果 |
| `verify-audit` | 验证审计链 | 无 | 哈希链状态 |
| `fact-conflicts` | 查看待核对事实 | 可选状态与数量 | 事实属性、候选和版本快照 |
| `memguard resolve-conflict` | 处理一条事实核对 | 核对编号、决定、候选版本 | 核对结果或未执行原因 |
| `lifecycle` | 查看记忆形成与使用过程 | 记忆编号或 `--overview` | 记忆、来源、情节与操作视图 |
| `memctl governance states` | 查看治理状态机 | 无 | 状态、终态和合法流转 |
| `memctl agent adapters` | 查看 Agent 适配能力 | 无 | 已注册适配器、事件类型、上下文阶段和检查点能力 |
| `memctl agent ingest-adapted-session` | 接入通用 Agent JSONL 会话 | JSONL 文件、适配器与会话范围 | 规范化事件、提取结果、去重和审计摘要 |
| `memctl agent preflight-action` | 在 Agent 工具执行前检查记忆约束 | 任务、工具名称、工具参数和作用域 | `allow`、`redirect`、`confirm` 或 `deny`，以及版本化记忆证据 |
| `memctl agent action-plan` | 生成一次可执行的记忆驱动动作计划 | 任务、工具名称、工具参数和作用域 | 执行闸门、策略原因、版本证据和结果反馈要求 |
| `memctl agent action-impact` | 汇总真实工具结果并评估记忆驱动动作质量 | 可选记忆、Agent、项目和任务类型 | 成功下界、遵循率、失败/偏离风险、同类观察性差值与质量门控 |
| `memctl consolidation-plan` | 生成会话记忆巩固计划 | 会话编号、项目范围、数量上限 | 晋级、保留或清理复核候选，以及版本快照 |
| `memctl consolidate` | 按审核计划晋级长期记忆 | 计划 JSON、可选记忆编号、确认标记 | 实际晋级、失败项和仍保留的会话记忆 |
| `memctl decay-plan` | 生成长期记忆健康度与衰减计划 | 观察时间、年龄门槛、健康度门槛 | 强化保留、保护复核和可逆降级候选 |
| `memctl decay` | 按审核计划把冷记忆可逆降级到会话层 | 计划 JSON、目标会话、可选记忆编号、确认标记 | 降级结果、版本编号和旧计划拒绝原因 |
| `memctl budget-plan` | 按价值、风险和字节成本生成长期记忆预算计划 | 目标字节数、时间、数量上限 | 价值分、风险分、保护项和可逆降级候选 |
| `memctl budget` | 按预算计划把选定普通记忆可逆降级到会话层 | 计划 JSON、目标会话、可选记忆编号、确认标记 | 回收估算、版本编号和旧计划拒绝原因 |
| `doctor` | 系统诊断 | 可选 namespace | 环境和能力报告 |
| `web` | 启动本地控制台 | host、port | Web 服务 |

## 2. Unix socket API

Unix socket API 位于 `memguard/api/server.py`，用于本地 Agent 直接调用。请求是 JSON 对象，统一使用 `command` 字段表示动作名称，并携带对应参数。响应也是 JSON 对象，包含 `ok`、`result` 或 `error`。API 支持 `health`、`ingest`、`admission_preview`、`search`、`batch_search`、`build_context`、`preflight_agent_action`、`agent_action_plan`、`consolidation_plan`、`apply_consolidation`、`forget`、`audit` 等命令。

根据当前代码推断，API 重点服务本机可信 Agent，不是公网服务。它支持请求大小限制和可选 token 校验。若要对外暴露，应增加更完整的鉴权、限流、TLS 和审计策略。

### 治理状态机

`governance_states` 返回记忆生命周期的稳定状态目录和允许的流转。状态流转由服务和元数据写边界共同校验，调用方不能通过普通更新把 `forgotten`、`superseded` 或 `source_invalidated` 直接恢复为 `active`。隔离内容的批准会以父版本受校验的新版本提交，并保留审批理由和审计关系。

```json
{"command":"governance_states"}
```

该目录用于控制台、CLI 和外部 Agent 展示一致的状态含义；具体遗忘仍应调用 `forget` 或 `forget_memory_ids`，不能把状态字段更新当作删除接口。

典型请求示例如下：

```json
{"command":"search","query":"openKylin 服务内存限制","limit":5}
```

启用令牌后，调用方应在请求 JSON 中写入 `token` 字段；Web 控制台则使用 `Authorization: Bearer <token>` 请求头。未携带正确令牌时，接口返回 `{"ok": false, "error": "unauthorized"}`，不会继续执行诊断、抽取解释、写入、检索或导出操作。

### Agent 上下文构建与自适应表示

`build_agent_context` 按 `planning`、`tool_selection` 或 `response_generation` 阶段构建当前 Agent 可用的记忆上下文。除 `task`、`stage`、`agent_name`、`session_id`、`project_root`、`task_type` 和预算参数外，还可传入 `compaction`：`adaptive` 根据阶段和记忆风险选择完整、证据或摘要表示，`full` 用于需要完整正文的离线复核。服务不会修改权威记忆正文；压缩只作用于本轮上下文，并在返回结果中给出每条记忆的 `representation`、`representation_reason`、表示分布和预计节省 token 数。

```json
{
  "command": "build_agent_context",
  "task": "查找项目构建流程",
  "stage": "planning",
  "agent_name": "local-agent",
  "session_id": "session-001",
  "project_root": "/home/luo/project-alpha",
  "task_type": "code_assistance",
  "token_budget": 500,
  "compaction": "adaptive"
}
```

返回的 `context_receipt` 会同时绑定记忆版本和表示映射。记忆更新、遗忘、适用偏好变化或表示策略变化后，调用方必须重新构建上下文，不能沿用旧收据继续执行工具。

### Agent 行动预检

`preflight_agent_action` 将记忆作用于拟执行的工具调用。它接收 `task`、`tool_name`、`tool_input`、`agent_name`、`session_id` 和可选的 `project_root`、`task_type`；服务在工具选择阶段重新构建当前上下文，并返回带有 `context_receipt` 的行动契约。`allow` 表示未发现适用冲突，`redirect` 表示应按偏好改用建议工具，`confirm` 表示需要用户确认，`deny` 表示命中不可执行的安全或过期上下文条件。该接口只判定不执行命令，适合被 OpenHands 或其他 Agent 适配器作为统一的执行前检查点。

```json
{
  "command": "preflight_agent_action",
  "task": "搜索项目代码中的 build_agent_context",
  "tool_name": "terminal",
  "tool_input": {"command": "grep -n build_agent_context ."},
  "agent_name": "local-agent",
  "session_id": "session-001",
  "project_root": "/home/luo/project-alpha",
  "task_type": "code_search"
}
```

返回结果包含 `decision`、`policy_category`、`reason`、`evidence` 和 `context_receipt`。`evidence` 只记录记忆编号与版本编号，不把完整记忆正文复制到行动审计中；上下文发生更新或遗忘后，旧收据会被拒绝。

### 记忆准入预览

`admission_preview` 在写入前把内容送入同一套质量检查、敏感检测、语义提取和来源治理流程，但不创建记忆记录。返回的 `memguardos-memory-admission-v1` 会给出 `admit_long_term`、`retain_session`、`retain_working`、`quarantine` 或 `deny`，并附带判断因素、作用域、证据门槛和下一步动作。人工明确表达的偏好保留用户意图；工具结果、行为轨迹等自动来源默认先留在会话层，避免一次性观测污染长期记忆。

```json
{
  "command": "admission_preview",
  "text": "工具结果：本次搜索使用 rg 成功，后续可能复用。",
  "source_kind": "tool_result",
  "session_id": "session-001",
  "project_root": "/home/luo/project-alpha"
}
```

CLI 等价命令为 `memctl admission-preview --text "以后搜索代码优先使用 rg" --source-kind manual`。该命令明确标注“本次没有写入记忆”，适合用户确认长期保存策略，也适合 Agent 适配器在自动摄取前执行准入检查。`ingest` 请求还可以设置 `enforce_admission: true`；此时自动来源会实际降为会话层，待复核内容不会落盘，人工来源的兼容行为保持不变。

### Agent 记忆驱动动作计划

`agent_action_plan` 将上下文收据、行动预检和反馈契约合并为一个 `memguardos-agent-action-plan-v1`。计划不执行工具，只给适配器一个可强制执行的闸门：`execute`、`replace_then_execute`、`user_confirmation` 或 `stop`。其中 `plan_id` 绑定任务摘要、工具输入摘要、上下文指纹和记忆版本；外部工具完成后应沿计划中的 `record_agent_action_outcome` 回写结构化结果。

```json
{
  "command": "agent_action_plan",
  "task": "搜索项目代码中的 build_agent_context",
  "tool_name": "terminal",
  "tool_input": {"command": "grep -n build_agent_context ."},
  "agent_name": "local-agent",
  "session_id": "session-001"
}
```

CLI 等价命令为 `memctl agent action-plan --task "搜索项目代码中的 build_agent_context" --tool-name terminal --tool-input '{"command":"grep -n build_agent_context ."}'`。当已有偏好要求使用 `rg` 时，计划会把执行闸门标为 `replace_then_execute`，同时给出建议命令和对应记忆版本，避免只展示“检索到了偏好”却无法证明它影响了真实行动。

### Agent 行动结果反馈

`record_agent_action_outcome` 由 Agent 适配器在外部工具执行完成后调用。它不执行命令，只接收预检结果、实际工具输入摘要和退出状态，并再次检查上下文收据及证据记忆版本。返回的 `success`、`failure`、`deviation`、`blocked` 和 `unknown` 分别表示成功遵循、执行失败、成功但偏离偏好、执行前拦截和无法判定；只有成功遵循的结果会作为行动强化证据。命令正文不写入 MemGuardOS，持久化账本只保留摘要哈希、版本、退出码、作用域和上下文指纹。

```json
{
  "command": "record_agent_action_outcome",
  "preflight": {"decision": "redirect", "tool_name": "terminal", "expected_value": "rg", "evidence": []},
  "tool_input": {"command": "rg -n build_agent_context ."},
  "exit_code": 0,
  "agent_name": "local-agent",
  "session_id": "session-001",
  "idempotency_key": "tool-call-001"
}
```

同一个幂等键重复提交不会重复强化；如果记忆已被更新或遗忘，接口返回 `stale_memory_context` 或 `stale_memory_evidence`，调用方应重新构建上下文并重新预检。使用 `action_outcome_stats` 可以查看某条记忆的成功、失败、偏离、拦截和未知计数。

CLI 对应入口为 `memctl agent record-outcome --preflight-file preflight.json --tool-input '{"command":"rg -n target ."}' --exit-code 0`，查询入口为 `memctl agent action-outcomes --memory-id <memory_id>`。

### 会话记忆巩固

巩固接口把会话层内容整理成可审核的长期记忆晋级计划。计划保存 `schema`、`plan_id`、会话范围、项目范围、候选版本映射和候选动作；调用方可以先展示候选，再只提交明确选择的记忆编号。`confirm` 未开启时只返回待确认结果，不改变记忆状态。

```json
{"command":"consolidation_plan","session_id":"session-001","project_root":"/home/luo/project-alpha","limit":100}
{"command":"apply_consolidation","plan":{"schema":"memguardos-consolidation-plan-v1","plan_id":"plan_example","session_id":"session-001","expected_versions":{"mem_example":"ver_1"},"recommended_ids":["mem_example"],"candidates":[{"memory_id":"mem_example","recommended_action":"promote"}]},"selected_memory_ids":["mem_example"],"confirm":true}
```

候选动作包括 `promote`、`retain_session` 和 `evict_review`。服务只允许执行计划中明确标记为 `promote` 的候选；如果版本映射已变化，返回 `stale_consolidation_plan`，调用方应重新生成计划，不能用旧页面覆盖最新记忆。

### 强化与可逆衰减

`decay_plan` 只评估当前有效的长期记忆。系统根据 `hit_count`、`last_hit_at`、`updated_at`、`importance` 和 `confidence` 计算 `decay_score`，并返回 `retain_reinforced`、`retain_explicit_preference`、`retain_sensitive_review` 或 `demote_to_session` 等动作。它不会直接删除记忆。

```json
{"command":"decay_plan","as_of":"2026-01-31T00:00:00+00:00","max_age_days":30,"score_threshold":0.35,"limit":200}
{"command":"apply_decay","plan":{"schema":"memguardos-decay-plan-v1","plan_id":"plan_example","namespace":"default","as_of":"2026-01-31T00:00:00+00:00","max_age_days":30,"score_threshold":0.35,"expected_versions":{"mem_example":"ver_1"},"demotion_ids":["mem_example"],"candidates":[{"memory_id":"mem_example","recommended_action":"demote_to_session"}]},"selected_memory_ids":["mem_example"],"target_session_id":"maintenance-2026-01","confirm":true}
```

降级前必须提供目标会话编号，因为 session 记忆需要明确的可见范围。服务重新核对计划编号、版本和当前健康度；版本不一致返回 `stale_decay_plan`，策略变化返回 `decay_policy_changed`。降级会产生新的 session 版本，之后可以再次经过巩固计划回到长期层。

### 价值感知记忆预算

`budget_plan` 面向长期运行的本地 Agent 维护任务。它扫描当前有效的长期记忆，按重要性、置信度、来源信任、最近使用、检索命中、行动结果、治理风险和估算字节成本计算 `value_per_byte`，并生成可预览的 `memguardos-memory-budget-plan-v1`。显式偏好、高敏感内容和治理状态异常的记录标记为 `retain_protected`；普通记录才可能进入 `demote_to_session`。

```json
{"command":"budget_plan","target_bytes":1048576,"as_of":"2026-01-31T00:00:00+00:00","limit":1000}
```

返回结果中的 `summary` 包含 `current_bytes`、`target_bytes`、`bytes_to_reclaim`、`projected_bytes`、`protected_count`、`demotion_count` 和 `target_reached`；每个候选还包含价值分、风险分、估算占用和单位字节价值。这里的字节数是稳定的治理估算，用于排序和 CAS 复核，不替代文件系统计费。

确认后通过 `apply_budget` 执行，服务会再次检查计划哈希、全部候选的版本和估算大小，并重新判断所选记忆是否仍受保护。版本变化返回 `stale_memory_budget_plan`，策略变化返回 `memory_budget_policy_changed`；成功操作只追加 session 版本，不删除正文。

```json
{"command":"apply_budget","plan":{"schema":"memguardos-memory-budget-plan-v1","plan_id":"plan_example"},"selected_memory_ids":["mem_example"],"target_session_id":"budget-review","confirm":true}
```

CLI 对应入口为 `memctl budget-plan --target-bytes 1048576` 和 `memctl budget --plan-file budget-plan.json --target-session-id budget-review --yes`。预算计划与衰减、巩固共用版本链和审计链，用户仍可通过巩固计划将仍有价值的 session 记忆重新提升。

### 动作质量报告

`action_impact_report` 读取行动反馈账本，按记忆和作用域统计 `success`、`failure`、`deviation`、`blocked` 与 `unknown`。执行次数只包含前三类已实际发生的工具结果；拦截和未知状态保留在总数中，但不会被误计为成功或失败。报告使用 Wilson 95% 成功下界抑制小样本偶然性，并把同一 Agent、项目和任务类型下的其他行动作为观察性基线。

```json
{"command":"action_impact_report","agent_name":"openhands","task_type":"code_search","limit":5000}
```

返回 `memguardos-agent-action-impact-report-v1`。每条记录的 `recommendation` 可能是 `retain_reinforced`、`retain_observe`、`collect_more_evidence`、`review_or_demote` 或 `observe`。该命令只生成报告，不自动改变记忆层级；需要调整生命周期时，应将报告与巩固、衰减或预算计划一起审核。CLI 等价命令为 `memctl agent action-impact --agent-name openhands --task-type code_search`。

### Agent 适配器与通用事件

`agent_adapters` 返回当前进程支持的适配器目录。OpenHands 适配器接收其 SDK 事件对象；`generic-json` 适配器接收提供方无关的 JSON 对象，适合没有 MemGuardOS 专用 SDK 的本地 Agent。通用事件可以使用 `message`、`action`、`observation`、`feedback`、`complete` 和 `error` 等有限别名，服务会将它们映射为统一的 `AgentInteractionEvent`。

```json
{"command":"agent_adapters"}
```

```json
{
  "command": "ingest_agent_events",
  "adapter": "generic-json",
  "agent_name": "local-agent",
  "agent_version": "0.4",
  "session_id": "session-001",
  "project_root": "/home/luo/project-alpha",
  "task_type": "code_search",
  "events": [
    {"type":"message", "role":"user", "content":"以后搜索代码优先使用 rg"},
    {"type":"action", "id":"call-1", "tool":"terminal", "arguments":{"command":"rg build_context"}},
    {"type":"observation", "id":"result-1", "tool":"terminal", "output":"memguard/service.py:4201", "exit_code":0}
  ]
}
```

批量事件会先完成格式转换，再进入统一写入流程；不支持的事件类型、缺少会话编号或参数不是对象时，整批请求失败，不产生部分记忆。适配器的映射结果仍需经过本地敏感检测和递归参数脱敏，工具输出也不会因为包含“以后”“优先”或工具名称就直接升级为用户偏好。SDK 适配器本身不负责保存或删除记忆，所有 Agent 共享同一套作用域、版本、上下文快照和精准遗忘规则。

### 项目与会话范围

`ingest` 接受 `project_root`，文本和自然语言写入均保存规范化的项目目录。`search`、`batch_search`、`build_context`、`graph_search`、`hierarchical_graph_search` 和 `related` 接受相同参数：指定目录时返回该项目与全局记忆，显式传入空字符串时仅返回全局记忆。省略该字段保留管理入口的跨项目查询能力，因此 Agent 接入时应始终传入当前项目目录。项目范围是记忆选择条件，调用者身份与访问权限仍由服务入口控制。

`session_id` 控制临时记忆的可见范围；未提供时只可检索不绑定会话的记录。批量检索的所有工作线程共享请求中的项目与会话条件。作用域在候选查询中先于数量限制生效，关联查询也检查起点记忆的可见性，并按记忆编号去重。

事实写入与核对采用精确项目范围：项目内的更正只更新该项目的事实；若当前只有全局记录，则建立项目记录，保留原有全局内容。全局记忆可参与项目检索，项目纠正不会改变其他项目共享的全局值。

```json
{"command":"ingest","text":"systemd 通过 journalctl 查看服务日志。","natural":true,"project_root":"/home/luo/project-alpha"}
{"command":"batch_search","queries":["服务日志","资源配额"],"project_root":"/home/luo/project-alpha","session_id":"task-001","limit":5}
{"command":"related","memory_id":"mem_example","project_root":"/home/luo/project-alpha","session_id":"task-001","limit":5}
```

对应 CLI 使用 `--project-root`；`--session-id` 可放在相关子命令之后。例如：

```bash
memctl ingest --text 'systemd 通过 journalctl 查看服务日志。' --project-root "$PWD"
memctl search '服务日志' --project-root "$PWD" --session-id task-001
memctl batch-search '服务日志' '资源配额' --project-root "$PWD" --session-id task-001
memctl build-context '服务启动失败' --project-root "$PWD" --session-id task-001
memctl related mem_example --project-root "$PWD" --session-id task-001
```

### 事实核对与生命周期

新旧事实存在冲突而用户没有明确要求替换时，`ingest` 返回 `reason: "fact_conflict_requires_review"`、核对编号及候选信息，原值继续有效。调用 `fact_conflicts` 读取队列后，可通过 `resolve_fact_conflict` 选择 `keep_existing` 或 `accept_incoming`；接受新值会生成版本并关闭该条核对记录。`expected_version_id` 应使用刚读取的候选版本，防止过期页面覆盖后续更新。

```json
{"command":"fact_conflicts","status":"pending","limit":30}
{"command":"resolve_fact_conflict","conflict_id":"conflict_example","decision":"accept_incoming","memory_id":"mem_example","expected_version_id":"ver_example"}
{"command":"lifecycle_snapshot","memory_id":"mem_example"}
```

`ok: false` 且 `result.reason` 为 `candidate_version_changed` 表示候选已更新或遗忘，调用方需刷新队列后重新核对；`conflict_already_closed` 表示这条记录已经处理，不应再次提交。`lifecycle_snapshot` 展示已提交版本、支持来源和操作结果，取消的事实更新标记为 `cancelled`。

面向人工操作的简明入口为 `memguard conflicts`、`memguard resolve-conflict <核对编号> --decision keep_existing` 和 `memguard lifecycle <记忆编号>`。终端默认显示中文处理结果，需要结构化输出时追加 `--json`。

## 3. Web 控制台接口

Web 控制台默认绑定 `127.0.0.1`，用于本地运维和演示。它提供状态总览、写入、检索、RAG 上下文解释、精准遗忘、审计验证、SDK/OS 状态和诊断导出。控制台的业务动作仍调用 service 层，因此和 CLI/API 保持一致。若启动时配置 `--auth-token-file`，所有 `/api/*` 控制台接口都必须携带正确 Bearer Token；根页面可以加载，但没有 token 时不能读取诊断数据、不能导出诊断包，也不能执行写入或遗忘。控制台前端只把 token 放入浏览器 `sessionStorage`，刷新会话内可继续使用，关闭浏览器会话后不再长期保留。

控制台响应默认带有 `Cache-Control: no-store`、`X-Content-Type-Options: nosniff`、`Referrer-Policy: no-referrer`、`X-Frame-Options: DENY`、`Cross-Origin-Opener-Policy: same-origin` 和最小化 `Permissions-Policy`。HTML 页面还带有 Content Security Policy，用于降低本地工具被嵌入、缓存或滥用浏览器权限的风险。

## 4. LLM 生成接口

`memguard/llm/adapter.py` 提供可选生成接口。默认情况下系统不调用外部 LLM。用户显式配置本地命令或 OpenAI-compatible endpoint 后，系统可以把 `build-prompt` 的结果交给模型生成回答。该能力服务演示和扩展，不是核心依赖。

## 5. 错误处理原则

接口错误应返回可理解原因，例如参数缺失、请求过大、token 不匹配、JSON 格式错误、质量校验失败、敏感阻断、遗忘候选不足或审计链失败。调用方不应只依赖错误字符串，而应结合 `ok`、`error`、`code` 或返回字段判断下一步动作。
