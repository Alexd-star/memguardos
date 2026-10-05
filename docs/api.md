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
| `doctor` | 系统诊断 | 可选 namespace | 环境和能力报告 |
| `web` | 启动本地控制台 | host、port | Web 服务 |

## 2. Unix socket API

Unix socket API 位于 `memguard/api/server.py`，用于本地 Agent 直接调用。请求是 JSON 对象，统一使用 `command` 字段表示动作名称，并携带对应参数。响应也是 JSON 对象，包含 `ok`、`result` 或 `error`。API 支持 `health`、`ingest`、`search`、`batch_search`、`build_context`、`forget`、`audit` 等命令。

根据当前代码推断，API 重点服务本机可信 Agent，不是公网服务。它支持请求大小限制和可选 token 校验。若要对外暴露，应增加更完整的鉴权、限流、TLS 和审计策略。

典型请求示例如下：

```json
{"command":"search","query":"openKylin 服务内存限制","limit":5}
```

启用令牌后，调用方应在请求 JSON 中写入 `token` 字段；Web 控制台则使用 `Authorization: Bearer <token>` 请求头。未携带正确令牌时，接口返回 `{"ok": false, "error": "unauthorized"}`，不会继续执行诊断、抽取解释、写入、检索或导出操作。

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
