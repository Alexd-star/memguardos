# MemGuardOS 用户手册

## 基础命令

写入文本记忆：

```bash
python -m memguard.cli.memctl ingest --text "以后回答 Linux 命令时直接给命令和风险提示。"
```

写入文件记忆：

```bash
python -m memguard.cli.memctl ingest --file data/demo/linux_workflow.md
```

写入结构化工具执行结果：

```bash
python -m memguard.cli.memctl ingest-tool-result --file data/demo/tool_result_success.json --session-id demo-01
```

写入结构化手动配置：

```bash
python -m memguard.cli.memctl ingest-config --file data/demo/user_config.json
```

写入 shell 历史中的用户行为轨迹：

```bash
python -m memguard.cli.memctl ingest-history --file ~/.bash_history --limit 200 --session-id demo-01
```

该命令会过滤 `ls`、`cd`、`history` 等低价值噪声，并保留 `pytest`、`rg`、`git`、`systemctl`、`journalctl`、`ssh`、`scp`、`pip`、`make` 等对智能体偏好、工具使用习惯、调试路径有价值的行为记忆。

查看记忆：

```bash
python -m memguard.cli.memctl list
python -m memguard.cli.memctl list --tier long_term
```

查看抽取解释：

```bash
python -m memguard.cli.memctl explain-extraction --text "真实案例：systemd service failed with exit_code=1，修复后运行 pytest。"
```

该命令不会写入记忆，只展示质量校验、敏感检测结果、抽取类型、子类型、摘要、置信度和命中的语义信号。默认抽取器离线可复现；如需接入本地 NER 或 LLM 增强器，可设置 `MEMGUARD_EXTRACTOR_COMMAND`，增强器必须返回合法 JSON schema，失败或超时会自动回退。

检索记忆：

```bash
python -m memguard.cli.memctl search "openKylin 服务内存限制"
```

批量检索记忆：

```bash
python -m memguard.cli.memctl batch-search \
  "任务规划 systemd cgroup" \
  "工具调用 pytest journalctl" \
  "结果生成 运维报告 证据" \
  --top-k 3 \
  --workers 3
```

批量检索用于 OS Agent 同时为任务规划、工具调用和结果生成准备记忆证据。系统会为每个子查询返回独立结果和延迟指标，并写入一次 `batch_search` 审计事件，避免在并发读路径上被逐条审计写入放大开销。

## 多会话隔离

如需为不同项目、智能体会话或演示场景隔离记忆，可以使用 `--namespace`。默认 namespace 为 `default`，显式 namespace 会使用独立的段文件、SQLite 元数据库和自定义敏感规则文件。

```bash
python -m memguard.cli.memctl --namespace alpha ingest --text "alpha 会话只保存 openKylin systemd MemoryMax 方案。"
python -m memguard.cli.memctl --namespace beta ingest --text "beta 会话只保存 auditd shadow access 方案。"
python -m memguard.cli.memctl --namespace alpha search MemoryMax
python -m memguard.cli.memctl --namespace beta search MemoryMax
python -m memguard.cli.memctl --namespace alpha namespace-status
python -m memguard.cli.memctl --namespace beta namespace-status
python -m memguard.cli.memctl namespaces
```

预期效果是：`alpha` 能检索到自己的 `MemoryMax` 记忆，`beta` 不会返回 `alpha` 的记忆；`namespace-status` 会显示当前 namespace 的 `store_path`、`db_path`、`custom_patterns_path`、记录数和审计事件数。

构建 OS Agent 生成前上下文包：

```bash
python -m memguard.cli.memctl build-context "openKylin 服务内存限制" --top-k 5 --max-chars 2000
```

该命令会把偏好记忆、知识记忆和不同层级记忆统一检索后组织为已脱敏上下文，供任务规划、工具调用和结果生成阶段使用。

上下文包会包含 `explainability` 字段，用于查看来源分布、类型分布、层级分布、敏感等级分布和候选选择轨迹。排查“为什么这条记忆进入了生成上下文”时，优先查看 `selection_trace`。

如需估算 LLM 输入成本，可附加 token 预算。该参数只在显式传入时裁剪上下文，不影响默认检索效果：

```bash
python -m memguard.cli.memctl build-context "openKylin 服务内存限制" --top-k 5 --max-chars 2000 --token-budget 1200
```

如需生成可交给任意 LLM 或 OS Agent 的提示词，可使用不联网、不消耗 API 的渲染命令：

```bash
python -m memguard.cli.memctl build-prompt "openKylin 服务内存限制" --top-k 5 --token-budget 1200
```

如需查看图谱证据链，可使用层次图谱检索。该命令按摘要层、实体关系层、原文 chunk 层返回结果：

```bash
python -m memguard.cli.memctl hierarchical-graph-search systemd --limit 5
```

工具执行结果可以作为程序性记忆写入。JSON 中的 `state_before`、`state_after` 和 `lesson` 是可选字段；存在这些字段时，系统会把工具轨迹建模为状态-动作-结果经验：

```json
{
  "command": "systemctl daemon-reload && systemctl restart memguardd && systemctl is-active memguardd",
  "cwd": "/opt/memguardos",
  "exit_code": 0,
  "stdout": "active",
  "state_before": "memguardd unit changed and service inactive",
  "state_after": "memguardd active after daemon reload and restart",
  "lesson": "after editing memguardd.service, reload, restart, then verify active state"
}
```

如需把已脱敏 prompt 交给外部或本地 LLM，可使用默认关闭的可选 adapter。默认 `dry-run` 只输出 prompt 和调用计划，不联网、不花费 API：

```bash
python -m memguard.cli.memctl generate-answer "openKylin 服务内存限制" --top-k 5 --token-budget 1200 --provider dry-run
```

本地模型或本地脚本可通过标准输入接收 prompt：

```bash
python -m memguard.cli.memctl generate-answer "openKylin 服务内存限制" --provider local-command --command "python3 local_llm_stub.py"
```

兼容 Chat Completions 格式的远程端点也可以显式启用，但不作为默认运行路径：

```bash
python -m memguard.cli.memctl generate-answer "openKylin 服务内存限制" \
  --provider openai-compatible \
  --endpoint http://127.0.0.1:8000/v1/chat/completions \
  --model local-model \
  --api-key-file ~/.memguardos-llm.key
```

## 本地 Agent API

### 已验证的流程经验

`memguard context "任务描述"` 使用 Agent 的实际范围选择与阶段策略，默认展示工具选择阶段的上下文。查看规划或回答阶段时分别添加 `--stage planning`、`--stage response_generation`；使用 `--agent-name`、`--task-type`、`--workspace` 和 `--session-id` 对应真实任务的适用条件。该命令只预览上下文，不执行任务，也不表示模型已经遵循记忆。

需要复用一组操作步骤时，可把目标、参数、步骤、风险约束和完成条件放入流程规范。系统先检查本地实际结果，再保存带来源版本的经验；下次任务可以换文件参数取回步骤，完成后再次复核结果。流程学习与复核不会执行步骤，也不调用外部模型。当前完成条件支持路径存在、UTF-8 文件包含或不包含指定内容；文件读取限制为 1 MiB，检查路径必须位于明确选择的工作区内。

项目提供的 [`release_review_procedure.json`](../data/demo/release_review_procedure.json) 是发布说明复核的规范示例。先在专用记忆空间写入来源要求，记下输出的记忆编号，并选择实际处理发布说明的工作区。以下 `SOURCE_ID`、`PROCEDURE_ID` 应替换为对应命令的真实输出，工作区路径需改成已有目录的绝对路径。

```bash
memguard --namespace review-practice remember "项目发布说明在提交前需要复核，并保留检查结果。"
SOURCE_ID=实际来源记忆编号
WORKSPACE=/home/你的用户名/实际项目目录
SPEC=/绝对路径/memguardos/data/demo/release_review_procedure.json
memguard --namespace review-practice --workspace "$WORKSPACE" learn-procedure "$SPEC" \
  --source "$SOURCE_ID" --session-id review-first --argument file=release-a.md
```

如果 `release-a.md` 尚不存在、没有复核标记或还有 `TODO`，学习结果应为未通过，不形成可复用流程。完成真实复核并在文件中记录 `REVIEWED` 后，再运行学习命令。这里的标记表示维护者已经完成检查；应保留未处理事项，不通过删除标记伪造复核完成。若希望检查服务状态、测试结果或其他任务结果，需由适配器提供相应安全检查能力，不能把此文件示例解释为任意任务都已验证。

```bash
PROCEDURE_ID=实际流程记忆编号
memguard --namespace review-practice --workspace "$WORKSPACE" procedures "复核项目发布说明" \
  --session-id review-next --argument file=release-b.md
memguard --namespace review-practice --workspace "$WORKSPACE" verify-procedure "$PROCEDURE_ID" \
  --session-id review-next --use-id review-b-first --argument file=release-b.md
```

查询会展示新的参数、步骤、约束、完成条件和历史实测反馈，尚未完成本次工作时复核仍应失败。完成工作后使用新的 `--use-id` 再复核；相同会话和使用编号的重复反馈不重复计数，未知结果不计为成功。不提供必须参数时，查询明确列出待补参数。

```bash
memguard --namespace review-practice dependencies "$SOURCE_ID"
memguard --namespace review-practice forget "忘记关于项目发布说明的记忆"
```

来源更新后，依赖旧来源版本的流程立即停止进入新任务上下文。撤回来源时，预览也列出其硬派生流程，需一并选择和确认；相似主题但没有派生关系的独立记忆保留。Web 记忆详情展示流程步骤、检查条件、来源版本、反馈次数和派生影响，从详情发起遗忘也会确认派生数量。通用文本编辑和回滚不能绕过流程验证，修改后的流程应重新学习。

流程来源必须处于可信、有效状态，且属于当前记忆空间及兼容项目。限定某个 Agent、任务类型或独立会话的偏好不能直接提炼成通用流程；系统会拒绝这类不兼容来源。工作区使用已存在目录的绝对路径，不能使用系统目录、凭据目录或符号链接。openKylin 的 `/tmp` 可能指向 `/var/tmp`，临时工作区可通过 `realpath` 获取实际路径；选择其中的独立项目子目录，不把共享临时目录本身作为工作区。

开发者可以通过同一 API 调用 `learn_procedure`、`recall_procedures`、`verify_procedure` 和 `dependency_impact`。前三者要求明确的 `workspace`，复核还要求 `session_id`；学习要求 `spec` 与 `source_memory_ids`，参数通过 `arguments` 字符串映射传递。`dependency_impact` 接收 `memory_ids`，返回派生清单与版本快照。检查失败返回 `ok: false`，CLI 退出码为 2；查询成功不代表步骤已经执行。

systemd 服务启动后，会在 openKylin 上创建本地 Unix socket：

```bash
/run/memguardos/memguard.sock
```

请求格式为一行 JSON。示例：

```bash
python3 - <<'PY'
import json, socket
with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as client:
    client.connect("/run/memguardos/memguard.sock")
    client.sendall(json.dumps({"command": "build_context", "query": "openKylin 服务内存限制", "limit": 5}).encode("utf-8") + b"\n")
    print(client.recv(65536).decode("utf-8"))
PY
```

支持命令：

- `health`
- `ingest`
- `search`
- `batch_search`
- `build_context`
- `hierarchical_graph_search`
- `forget`
- `audit`
- `verify_audit`
- `namespace_status`
- `memory_policy`
- `retrieval_status`

`health` 返回当前 API 实例的 `namespace`、`store_path` 和 `db_path`。`namespace_status` 返回更完整的隔离状态，包括记录数、审计事件数、自定义敏感规则路径和实际存储根目录。`batch_search` 接收 `queries` 数组、`limit` 和 `workers`，用于本地 Agent 一次请求获取多组检索结果。`hierarchical_graph_search` 接收 `entity` 或 `query`，返回摘要层、实体关系层和原文 chunk 层。`forget` 支持自然语言遗忘 dry-run 与确认执行；`audit` 和 `verify_audit` 用于审计记录查看与哈希链校验；`memory_policy` 返回分层记忆数量、存储占用、淘汰候选和 cgroup 状态；`retrieval_status` 返回当前检索后端、Kylin SDK 可选增强状态和 fallback 策略。

如果 daemon 使用 `--auth-token-file` 启动，所有 API 请求需要附带 `token` 字段：

```json
{"command": "health", "token": "本地令牌内容"}
```

该令牌只用于本机 Unix socket 调用鉴权，不需要外部网络服务。

## 版本管理

查看版本：

```bash
python -m memguard.cli.memctl versions <memory_id>
```

追加新版本：

```bash
python -m memguard.cli.memctl update <memory_id> --text "新的记忆内容"
```

回滚到指定版本：

```bash
python -m memguard.cli.memctl rollback <memory_id> <version_id>
```

## 记忆层级

支持三类层级：

- `working`：短期工作记忆。
- `session`：会话级记忆。
- `long_term`：长期记忆。

写入指定层级：

```bash
python -m memguard.cli.memctl ingest --text "临时工作记忆" --tier working --session-id demo-01
```

提升记忆层级：

```bash
python -m memguard.cli.memctl promote <memory_id> long_term
```

清理会话记忆：

```bash
python -m memguard.cli.memctl evict-session --session-id demo-01
python -m memguard.cli.memctl evict-session --session-id demo-01 --yes
```

临时写入、检索和上下文预览均使用同一会话编号；未提供编号时不会创建无归属的临时记忆。`evict-session` 只清理该会话的 `working` 与 `session` 记忆，长期记忆保持可用；`evict-tier` 是面向管理员的全层级维护操作，不用于结束单个会话。桌面文件检查产生的 OS 事件默认只进入审计链，不会因一次检查自动变成长期记忆。

终端异常退出后，可先运行 `python -m memguard.cli.memctl stale-sessions --older-than-hours 72` 查看长时间没有写入的临时会话。此命令只列候选，不判断进程是否仍在运行，也不删除数据；核实会话确已结束后，再对单个编号执行上面的 `evict-session` 预览与确认命令。

工具调用结果先保留在当前会话。批量统计也是会话内的观察，不能仅凭一次批量导入形成长期偏好。真实 Agent 在任务完成且行为校验通过后，只把代码搜索、文件查找和测试工具的成功/失败类别纳入汇总；同一工具至少在两个独立完成的会话中成功，才生成项目范围内的长期工具经验。摘要不含原始命令或输出，也不等同于用户明确表达的工具偏好；用户撤回该经验后不会自动重新生成。

查看冷热分层、资源策略和淘汰候选：

```bash
python -m memguard.cli.memctl memory-policy --eviction-limit 10
```

该命令会输出 `working`、`session`、`long_term` 三层记忆数量、来源分布、敏感等级分布、mmap 段文件和 SQLite 占用、可淘汰候选、compact 建议以及当前 cgroup 状态。它用于展示赛题要求中的冷热数据分层、缓存/存储占用和端侧资源管控能力。

## 本地 Web 控制台

需要现场演示时，可以启动本地 Web 控制台：

```bash
python -m memguard.cli.memctl web --host 127.0.0.1 --port 8765
```

浏览器访问启动命令输出的本机地址。控制台按使用对象分为两组：普通用户使用“总览、Agent 工作台、记忆中心、我的偏好、内容保护、精准遗忘”，管理员使用“系统维护、审计与证据、本机验收”。控制台默认只监听本机地址，不作为公网管理台。若需要启用本地角色权限，可使用 `--auth-token-file` 提供 viewer、operator 或 admin 访问凭证。

普通用户最常用的流程如下：

1. 打开“总览”，确认页面显示“Agent 记忆服务可以正常使用”。
2. 打开“记忆中心”查看最近记忆。每条记忆显示短编号、类型、来源、作用范围和保护状态，完整编号可在详情页查看或复制。
3. 在搜索框输入主题、工具、项目或记忆内容。系统默认返回 5 条以内结果，前三条直接展示，其余折叠；结果按综合相关度排序，并以中文说明命中原因。
4. 相同内容重复写入时不会生成新编号；升级前遗留的同类偏好和历史相似记录会合并展示，但原始记录仍保留用于审计，不会被界面静默删除。
5. 需要移除内容时打开“精准遗忘”，先预览候选编号、匹配依据和作用范围，再确认执行。确认前不会删除数据。

管理员页面默认使用轻量状态接口，打开页面不会自动触发完整 Kylin SDK 推理探针。完整诊断只在管理员主动点击检查或导出报告时执行。审计与证据页会把重复状态事件合并计数，避免大量低价值记录遮挡写入、保护和遗忘等关键事件。

## 敏感检测

添加自定义敏感模式：

```bash
python -m memguard.cli.memctl patterns add lab_account "LAB-[0-9]{4}"
```

用自然语言生成并添加自定义敏感模式：

```bash
python -m memguard.cli.memctl patterns suggest "把员工工号，格式以 MG- 开头后接 6 位数字，作为敏感信息"
python -m memguard.cli.memctl patterns add-natural "把员工工号，格式以 MG- 开头后接 6 位数字，作为敏感信息"
```

自然语言规则会先生成受限正则，再经过同一套编译、空匹配和过宽匹配校验。当前支持前缀加固定位数、示例格式和显式正则三类保守指令，不宣称通用自然语言正则生成。

查看自定义模式：

```bash
python -m memguard.cli.memctl patterns list
```

写入 OS 事件：

```bash
python -m memguard.cli.memctl ingest-os-event --json '{"event_type":"fanotify_open","path":"/home/u/.ssh/id_rsa","process":"cat","action":"read"}'
```

写入 auditd 日志：

```bash
python -m memguard.cli.memctl ingest-auditd --file /var/log/audit/audit.log --key memguard_sensitive
```

## 精准遗忘

预演候选，不真正删除：

```bash
python -m memguard.cli.memctl forget "忘掉 temporary project codename"
```

确认遗忘：

```bash
python -m memguard.cli.memctl forget "忘掉 temporary project codename" --yes
```

压缩段文件并验证不可逆清除：

```bash
python -m memguard.cli.memctl compact
python -m memguard.cli.memctl verify-forget <memory_id> --probe "temporary project codename blue-river"
```

## 图谱记忆

按实体检索：

```bash
python -m memguard.cli.memctl graph-search systemd
```

查看关联记忆：

```bash
python -m memguard.cli.memctl related <memory_id>
```

## 审计

查看审计记录：

```bash
python -m memguard.cli.memctl audit
```

验证审计哈希链：

```bash
python -m memguard.cli.memctl verify-audit
```

## openKylin SDK 状态

查看 Kylin 文本向量化 SDK 和向量数据库 SDK 状态：

```bash
python -m memguard.cli.memctl sdk-status
python -m memguard.cli.memctl sdk-status --probe-embedding --probe-timeout 90
```

在 openKylin 环境中验证 Kylin embedding 作为可选检索重排后端：

```bash
MEMGUARD_EMBEDDING_BACKEND=auto MEMGUARD_KYLIN_EMBEDDING_TIMEOUT=90 \
  python -m memguard.cli.memctl search "openKylin 服务内存限制" --top-k 2
```

如果 SDK 推理成功，检索结果会显示 `vector_backend: "kylin_sdk"`；如果 SDK 不可用或超时，系统会回退到默认 FTS/hash/graph 检索路径。
