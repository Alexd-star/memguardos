# MemGuardOS 详细设计

## 1. 目录结构

```text
memguardos/
  memguard/
    cli/               # memctl 命令行
    eval/              # 基准评测与报告生成
    extraction/        # 实体/关系抽取、可解释混合语义抽取
    forgetting/        # 精准遗忘规划
    ingestion/         # 工具结果、shell 行为与手动配置解析
    integrations/      # OpenHands 事件协议、适配器、上下文注入、习惯聚合和 runner
    os_guard/          # Linux/openKylin OS 机制
    retrieval/         # 混合检索与冲突解决
    sensitivity/       # 敏感检测与自定义模式
    store/             # mmap 段文件与 SQLite 元数据
    web/               # 本地 Web 控制台
    config.py
    models.py
    service.py
  data/benchmarks/
  docs/
  scripts/
  tests/
```

## 2. 记忆卡片

内部记忆卡片由 `MemoryRecord` 表示。

| 字段 | 含义 |
|---|---|
| `memory_id` | 稳定记忆 ID |
| `type` | `preference`、`knowledge`、`sensitive_policy` |
| `subtype` | 输出风格、工具偏好、安全策略、工作流、案例、模板、工具轨迹、用户行为、OS 事件 |
| `scope` | 作用域，等于当前 `namespace`；默认 namespace 为 `default` |
| `tier` | `working`、`session`、`long_term` |
| `content` | 存储内容，必要时脱敏 |
| `summary` | 检索和展示摘要 |
| `source` | 来源类型、URI、时间 |
| `evidence` | 来源证据片段 |
| `confidence` | 提取置信度 |
| `importance` | 保留和检索权重 |
| `entities` | 图谱实体 |
| `relations` | 图谱关系 |
| `sensitivity` | 敏感等级、标签、脱敏标记 |
| `version` | 版本 ID、父版本 ID、操作类型 |
| `temporal` | `valid_from`、`valid_to`、`superseded_by`，描述事实有效窗口 |
| `audit` | 创建时间与内容哈希 |
| `deleted` | tombstone 标记 |

## 3. 抽取设计

`memguard/extraction/semantic.py` 提供可解释的混合抽取器。默认路径不依赖外部模型，使用意图、来源、OS 术语、工具行为、流程词、案例词、风险词等语义信号生成 `type`、`subtype`、`summary` 和置信度，并把信号写入 ingest 审计详情。该设计解决两个评审风险：第一，抽取结果不是不可解释黑盒；第二，openKylin 复验不会依赖网络、GPU、API Key 或模型版本。

抽取链路如下：

```text
clean text -> sensitivity redaction -> semantic signals -> type/subtype/summary/confidence -> MemoryRecord -> audit extraction detail
```

如果设置 `MEMGUARD_EXTRACTOR_COMMAND`，系统会把文本、来源类型和目标 JSON schema 发送给本地 NER/LLM 命令。增强器必须返回 `type`、`subtype`、`summary`、`confidence`、`signals`，且类型必须落在系统允许的枚举内，置信度低于阈值或返回非法 JSON 时自动回退到默认抽取器。这个入口用于扩展泛化能力，但不会改变默认离线可复现基线。

复验命令：

```bash
python -m memguard.cli.memctl explain-extraction --text "真实案例：systemd service failed with exit_code=1，修复后运行 pytest。"
```

### 3.1 用户-Agent 交互偏好提取

OpenHands 交互事件先由 `normalizer.py` 归一为用户消息、Agent 消息、工具调用、工具结果、反馈和错误等标准事件。偏好抽取只把用户表达、用户纠正和经过阈值聚合的重复行为作为偏好证据，避免把 Agent 自己的建议误当成用户偏好。

显式偏好可以由单次明确表达形成；隐式习惯必须跨轮次达到支持度阈值，并保留正反证据。每条偏好记录 `agent_id`、`session_id`、`turn_id`、来源事件和适用阶段。用户纠正会关闭旧偏好的时间有效性窗口并建立新版本，不直接覆盖历史。

## 4. 存储设计

### 4.1 追加式段文件

实现文件：`memguard/store/mmap_store.py`

- 记录以 JSONL 序列化。
- `append()` 只追加，不覆盖。
- `scan()` 使用 `mmap` 读取。
- `tombstone()` 追加删除标记。
- `compact()` 只保留存活记录，作为不可逆清除边界。

### 4.2 SQLite 元数据

| 表 | 作用 |
|---|---|
| `memory` | 最新元数据和可搜索内容 |
| `memory_fts` | FTS5 文本检索 |
| `audit` | 审计哈希链 |
| `entity` | 图谱实体字典 |
| `memory_entity` | 记忆到实体映射 |
| `relation` | 实体关系 |

### 4.3 namespace 隔离存储

namespace 由 `memguard/config.py` 统一解析。默认 namespace 为 `default`，继续使用 `~/.memguardos/memory.segment`、`~/.memguardos/memguard.sqlite3` 和 `~/.memguardos/sensitive_patterns.json`，兼容旧数据。

显式 namespace 使用独立目录：

```text
~/.memguardos/namespaces/<name>/memory.segment
~/.memguardos/namespaces/<name>/memguard.sqlite3
~/.memguardos/namespaces/<name>/sensitive_patterns.json
```

隔离边界包括：

- 记忆段文件；
- SQLite 元数据、FTS、图谱和审计链；
- 自定义敏感规则；
- daemon 实例的 Unix socket 和 systemd cgroup。

namespace 名只允许 1-64 位 ASCII 字母、数字、点、下划线和横线，并禁止路径穿越、斜杠、空格和 Windows 保留设备名。当前实现是用户态逻辑 namespace + systemd/cgroup 实例隔离，不宣称 Linux kernel namespace 或 LSM 强隔离。

使用自定义存储路径时，每个 namespace 仍须独占存储目录、SQLite 元数据库与段文件；敏感规则文件位于数据库同目录。服务启动会核对现有记录的作用域，并在数据库和段文件锁中绑定 namespace；不同 namespace 指向同一数据库或段文件会直接拒绝启动，避免从列表、编号详情或遗忘入口跨空间读取与修改。无归属标记的旧库可在记录作用域一致时接管；默认空间兼容历史 `user` 作用域。旧库只有 Agent 事件、没有可核验的记忆作用域时，非默认空间不会自动认领。自定义目录也应分别配置，目前不自动检测两个不同数据库文件是否共用同一个敏感规则文件。

## 5. 检索设计

普通检索流程：

```text
query -> FTS/别名候选与最近记忆补充 -> 低相关候选过滤 -> 混合重排 -> top-k
```

当 FTS 没有命中时，系统会从当前 namespace 的最近存活记忆中取有界候选再做混合重排；低于相关性阈值的候选会被过滤，避免只因为时间较新就返回无关记忆。关系查询另行融合图谱候选。这条默认路径不能保证零词面重合的旧记忆进入候选集。

Agent 批量检索流程：

```text
queries[] -> 每个 worker 独立服务实例/SQLite 连接 -> 并发只读检索 -> 保序合并 -> 单次 batch_search 审计
```

`batch-search` 面向真实 OS Agent 的多阶段调用：任务规划、工具调用和结果生成可以在一次本地 API 请求中同时获取多组记忆证据。实现上每个 worker 使用独立 `MemGuardService` 和 SQLite 连接，避免跨线程复用连接；单个子查询默认不逐条写审计，整批请求写入一次 `batch_search` 审计事件，兼顾读路径吞吐和事后追踪。

生成前上下文构建：

```text
query -> 统一检索 -> 敏感脱敏结果复用 -> 摘要/证据裁剪 -> RAG context package -> OS Agent 规划/工具调用/结果生成
```

`build-context` 不直接调用外部大模型，而是输出稳定、可审计、已脱敏的上下文包。这样可以把生成能力交给任意 OS Agent 或本地模型，同时保证记忆系统的边界清晰。

上下文包包含 `budget` 字段，用于报告上下文字符数、估算 token、显式 token 预算、是否截断和是否超预算。默认情况下不传 `--token-budget`，系统不会为了节省 token 改变检索结果；只有用户显式设置预算时才会按预算裁剪。这保证 token 成本控制不会牺牲赛题核心指标。

上下文包同时提供 `explainability` 字段。`source_distribution` 统计不同来源在上下文中的占比，`type_distribution` 和 `tier_distribution` 展示偏好、知识、策略和工作/会话/长期记忆的组成，`selection_trace` 记录每条记忆的排名、分数、匹配来源、是否进入上下文、是否因预算截断。该设计用于支撑 Agent 在任务规划、工具调用和结果生成时进行可审计的上下文编译，也方便集成方复核 RAG 生成前的安全过滤边界。

`build-prompt` 是 provider-neutral 的提示词渲染器，只把已脱敏上下文包组织成可交给任意 LLM 的 prompt，不调用外部 API，不引入网络、费用或模型版本依赖。

工具执行结果支持程序性记忆建模。普通工具结果保存为 `tool_trace` 或 `tool_failure_case`；当输入包含 `state_before`、`state_after`、`lesson` 字段时，系统会将其编码为 `procedural_memory=state_action_result`，并抽取为 `procedural_trace`。这类记忆描述“在某个系统状态下执行什么动作、得到什么结果、沉淀什么经验”，适合 OS Agent 在自动调试、服务恢复、SDK 探针、测试复跑和证据包生成时复用。

图谱检索提供两种形态。`graph-search` 返回传统实体命中列表；`hierarchical-graph-search` 返回摘要层、实体关系层和原文 chunk 层。摘要层用于快速定位相关记忆，实体关系层说明记忆之间通过哪些系统实体、组件或操作相关联，原文 chunk 层保留可追溯证据和时间有效性。该设计保持 SQLite + mmap 的端侧部署形态，同时提升关联检索的可解释性。

`generate-answer` 是默认关闭的可选 LLM adapter。默认 `dry-run` 只返回 prompt，不联网、不花费 API；显式选择 `local-command` 时会把 prompt 通过 stdin 传给本地模型/脚本；显式选择 `openai-compatible` 时才会调用兼容 Chat Completions 的远程端点。该设计用于展示完整 Agent 生成链路，同时保证比赛核心评测不依赖网络、API Key 或模型版本。

Kylin embedding 可选重排链路：

```text
MEMGUARD_EMBEDDING_BACKEND=auto
  -> 默认 FTS/hash/关系图谱先召回稳定候选
  -> 后台回填当前模型版本的记忆向量；查询时只生成 query 向量
  -> 使用已持久化的候选向量做可选重排
  -> SDK 不可用、超时或维度异常时回退到 hash-vector 结果
```

独立语义候选召回仍处于显式启用的验证阶段。设置 `MEMGUARD_EMBEDDING_BACKEND=kylin`（或在 openKylin 使用 `auto`）及 `MEMGUARD_DENSE_CANDIDATES=1` 后，搜索会在当前 namespace 内读取已就绪、未删除、治理状态有效、内容哈希和模型版本都匹配的向量，计算与 query 的相似度，并把达到暂定阈值的候选独立送入融合排序。`MEMGUARD_DENSE_SCAN_LIMIT` 默认 2000、最高 5000；超限时完整放弃这一通道，不以较新的部分向量冒充全集。查询向量超时、SDK 不可用或扫描超预算同样回退。阈值、Top-1 和 P95 尚需冻结评测集及 openKylin 实测，不应将此开关直接视为已验证的默认生产配置。向量就绪版本会参与搜索缓存键，后台回填后同一查询可重新检索。

图谱检索：

```text
entity query -> memory_entity -> memory rows
```

关联检索：

```text
memory_id -> 当前记忆实体 -> 共享实体的其他记忆
```

## 6. 版本、时间有效性与回滚

- `ingest`：创建初始版本。
- `update`：追加更新版本，并把旧版本的 `valid_to` 设置为新版本生效时间。
- `rollback`：追加回滚版本，指向目标版本内容，并关闭回滚前版本的有效窗口。
- `forget`：追加旧版本失效记录和 tombstone。
- `promote`：追加层级变更版本，并记录旧层级事实被新版本取代。
- `temporal-status`：展示当前有效记忆样例、已失效版本样例和时间有效性策略。

段文件历史不会被普通操作覆盖；只有显式 `compact()` 才会重写段文件并清除已遗忘明文。

时间有效性用于解决长期 Agent 记忆中的新旧事实冲突。SQLite `memory` 表保存当前有效元数据，普通检索只读取 `deleted = 0` 的最新行；mmap 段文件保留完整历史。更新、提升、回滚和精准遗忘都会关闭旧版本窗口，写入 `valid_to` 和 `superseded_by`。因此系统既能避免旧偏好继续污染 RAG 上下文，也能在审计和故障排查中追溯旧事实何时失效、被哪个版本或 tombstone 取代。

## 7. 敏感检测

检测来源：

1. 内置正则：邮箱、手机号、身份证、上下文中文姓名、上下文中文地址、银行卡号、API key、AWS Access Key ID、token/password/secret 赋值、private key。
2. 用户自定义模式：`~/.memguardos/sensitive_patterns.json`。
3. 自然语言自定义模式：`patterns suggest/add-natural` 将受限格式描述转换为安全正则，再写入当前 namespace 的 `sensitive_patterns.json`。
4. OS 上下文：`.env`、`.ssh`、`id_rsa`、`authorized_keys`、`.aws/credentials`、`.kube/config`、service account key、`/etc/shadow`、`/etc/ssh` 等路径。

策略：

| 等级 | 动作 |
|---|---|
| high | 阻断存储并审计 |
| medium/low | 脱敏后存储 |
| none | 正常存储 |

## 8. 精准遗忘

```text
自然语言请求
  -> 关键词/实体候选搜索
  -> dry-run 预览
  -> 确认后 tombstone
  -> 时间有效性窗口关闭
  -> 元数据/FTS/图谱清除
  -> 可选 compact
  -> 明文探针验证
```

常用命令：

```bash
python -m memguard.cli.memctl forget "忘掉 temporary project codename"
python -m memguard.cli.memctl forget "忘掉 temporary project codename" --yes
python -m memguard.cli.memctl compact
python -m memguard.cli.memctl verify-forget <memory_id> --probe "temporary project codename blue-river"
```

Agent 交互遗忘还支持按 `agent_id`、`session_id`、`turn_id`、主题和时间范围选择。确认后，系统清理关联事件载荷、派生记忆、FTS、图谱与段文件明文，同时保留不相关偏好。结果明确报告外部持久化风险，避免把 MemGuardOS 的存储边界错误扩展到 OpenHands、终端历史或模型提供方。

## 9. 审计设计

所有关键事件调用 `_audit()`，包括 ingest、search、update、rollback、forget、compact、pattern add、OS event、conflict resolve、graph search、related retrieval。

新增的结构化配置导入、shell 用户行为导入和上下文构建也会写入审计：

- `config_ingest`：记录配置来源、条目数、存储数和阻断数。
- `behavior_ingest`：记录 shell history 来源、候选命令数、写入数和过滤数。
- `context_build`：记录查询、候选数量和上下文长度限制。
- `batch_search`：记录批量检索查询数、worker 数、错误数、总耗时和 P95 延迟。
- `memory_policy_status`：记录分层记忆数量、可淘汰候选、旧版本/墓碑数量和存储占用。
- `ingest` 中的 `extraction` 字段：记录抽取方法、类型、子类型、信号、置信度以及可选语义增强器是否参与。
- `agent_event_ingest`：记录 Agent、会话、轮次、事件类型和幂等键。
- `agent_context_build`：记录规划、工具选择和结果生成三个阶段的候选、选择及过滤原因。
- `agent_action_outcome`：记录实际工具结果与因果记忆证据；成功遵循、偏离、失败、拦截和未知状态分开统计，命令正文仅保留摘要哈希。
- `agent_interaction_forget`：记录交互遗忘范围、候选、确认状态、外部持久化风险和三阶段验证结果。

审计行包含：

- 事件类型；
- 记忆 ID；
- 详情 JSON；
- 创建时间；
- 前序哈希；
- 当前事件哈希。

`verify-audit` 会重新计算哈希链并验证完整性。

并发写入时，SQLite 元数据库使用 `busy_timeout`、WAL 兼容设置和 `BEGIN IMMEDIATE` 将“读取前序哈希 -> 写入审计事件”放入同一写锁临界区，避免多连接并发审计产生哈希链分叉。该能力由单元测试 `test_concurrent_audit_writes_preserve_hash_chain` 和精选 openKylin 审计证据验证。

行动反馈账本使用独立表和幂等键，避免把执行结果混入记忆正文或重复写入统计。服务回写前会同时核对上下文收据和证据记忆的当前版本；行动契约携带的完整上下文只用于解释，只有触发本次策略的偏好或安全记忆才会获得行动结果。普通 `allow` 不会把所有上下文候选误判为强化证据。精准遗忘在同一 SQLite 事务中删除关联账本行，随后沿既有段文件、索引、缓存和审计验证链完成清理。

## 10. OS 集成

| 功能 | 文件/命令 |
|---|---|
| cgroup 状态 | `memctl status` |
| namespace 状态 | `memctl --namespace <name> namespace-status`、`memctl namespaces` |
| systemd 服务 | `scripts/memguardd.service`、`scripts/memguardd@.service` |
| openKylin 安装 | `scripts/install_openkylin.sh` |
| journald 桥接 | `memguard/os_guard/journald.py` |
| auditd 规则 | `memctl auditd-rules` |
| auditd 日志接入 | `memctl ingest-auditd`、`scripts/auditd_to_memguard.sh` |
| OS 事件接入 | `memctl ingest-os-event` |
| shell 用户行为接入 | `memctl ingest-history` |
| SDK 状态 | `memctl sdk-status` |
| BPF LSM 策略生成 | `scripts/generate_bpf_lsm_policy.py`、`native/bpf_lsm/memguard_lsm_policy.h` |
| UKUI 桌面插件 | `native/ukui/peony-memguard/`、`native/ukui/ukcc-memguard/`、`scripts/build_ukui_plugins.sh`、`scripts/install_ukui_plugins.sh`、`scripts/verify_ukui_plugins.sh`；Peony 右键菜单和 UKUI 控制中心入口已完成可见验证 |
| 记忆策略报告 | `memctl memory-policy` |
| Agent 批量检索 | `memctl batch-search`、Unix socket 命令 `batch_search` |
| 本地 Agent API | Unix socket 命令 `health`、`ingest`、`search`、`batch_search`、`build_context`、`preflight_agent_action`、`record_agent_action_outcome`、`action_outcome_stats`、`namespace_status`、`memory_policy` |
| 本地 Web 控制台 | `memctl web`、`memguard/web/dashboard.py`，默认绑定 `127.0.0.1` |
| OpenHands runner | `scripts/run_openhands_memguard_demo.py`、`memguard/integrations/openhands_runner.py` |
| Agent systemd bridge | `scripts/memguard_agent_bridge.py`、`scripts/memguard-agent@.service` |

## 11. 本地 Web 控制台

Web 控制台采用 Python 标准库 `http.server` 实现，不引入 Flask、Node 或数据库服务等额外依赖，适合在 openKylin 本机快速部署。页面提供运行状态、写入、抽取解释、检索、RAG 上下文、精准遗忘、审计和系统诊断视图；诊断视图可校验审计链、查看检索后端和冷热分层策略。API 默认绑定 `127.0.0.1`，可选 bearer token，定位是本地运维、调试和验证入口，不是公网 SaaS 管理台。

为避免 SQLite 跨线程连接问题，HTTP handler 每次请求使用同一 store/db 路径创建独立 `MemGuardService` 和 SQLite 连接，请求结束后立即关闭。这与 `batch-search` 的并发检索设计一致，避免把单个连接跨线程复用。

控制台接口增加了请求体大小上限、JSON 类型校验、安全响应头、错误提示条、按钮忙碌态和高风险遗忘确认。Agent 治理页提供交互时间线、偏好及其证据、三阶段上下文选择解释、范围化遗忘和诊断导出。这样做的目标是让评审和本机管理员看到的是可长期维护的本地运维面板，而不是一次性命令拼接页面。

复验命令：

```bash
python -m memguard.cli.memctl web --host 127.0.0.1 --port 8765
python scripts/web_dashboard_smoke.py
```

## 12. 评测设计

基准文件：

- `memory_seed.jsonl`
- `retrieval.jsonl`
- `sensitive_detection.jsonl`
- `forgetting.jsonl`
- `conflict_resolution.jsonl`
- `graph_retrieval.jsonl`
- `quality_validation.jsonl`

指标：

- 类型/子类型准确率；
- Recall@5、跨语言 Recall@5；
- 图谱实体 Recall@5；
- 敏感阻断 F1、脱敏 F1；
- 遗忘候选精确率/召回率、保留成功率；
- 冲突解决正确率；
- 记忆层级流转；
- 分层策略报告、淘汰候选和 compact 建议；
- Agent 批量检索；
- 并发检索；
- RAG token 预算和脱敏保持；
- P50/P95/P99；
- CPU、tracemalloc、RSS、存储 footprint。
- Agent 交互专项 200 条：显式偏好、角色污染负例、隐式习惯、纠正、工具轨迹、敏感交互、精准遗忘和跨会话复用。
