# 变更日志

本文记录 MemGuardOS 的主要版本和能力变化。

## v0.1.0-agent-adherence - 2026-06-20

- OpenHands PreToolUse Hook 增加工具偏好执行策略，能够在用户已选择 `rg` 时拒绝明显改用 `grep` 的代码搜索命令，并把偏好拒绝与高风险拒绝分开审计。
- Runner 增加一次有界合规修复，检查工具偏好和回答顺序是否真正转化为成功行为；内部修复消息记录为 `system_message`，不会污染用户偏好。
- 新增严格只读结构化工具意图恢复：只允许当前偏好明确选择的 `rg/grep`、固定工作区、单个查询模式、有限参数、`shell=False` 执行，拒绝管道、重定向、绝对路径和父目录跳转。
- 新增失败搜索命令规范化恢复。对退出码 2 的已选只读工具调用，只提取查询词并重建为安全的当前目录命令，原始失败仍保留。
- 新增程序性记忆结果兜底。当已选择历史成功轨迹、没有新的成功工具结果且模型遗漏完整证据时，补充已验证的 `stdout_summary`，并标记 `execution_origin=procedural_memory_fallback`。
- 行为评测新增尝试调用、策略拒绝、实际执行、恢复错误、结构化恢复和程序性兜底等分项指标；Runner 合规状态未收敛时不能仅凭猜中输出判为通过。
- 偏好版本选择在 `updated_at` 平局时改用 Agent 事件时间与 SQLite 插入序号判定新旧，D05 冲突案例连续复跑 10 次均通过。
- 证据采集支持无 Git 元数据的 openKylin 同步树；新增 `refresh_evidence_manifest.py`，跨平台原子重建 SHA256 清单和 manifest。
- 最新本地测试为 `194 passed, 1 skipped`；openKylin 为 `195 passed`。
- 16 组确定性配对实验全部通过；原 12 组真实 OpenHands 会话与新增 R13-R20 扩展会话合计 20 组均已归档，记忆系统层与 Agent 行为层均为 `20/20`，未归因失败为 `0`。

## v0.1.0-performance - 2026-06-19

- 检索候选生成改为按 64 条预算依次使用 FTS、图谱和最新记忆补足，避免 FTS 已充足时重复读取兜底候选。
- Agent 批量检索改为每个工作线程复用一个服务和 SQLite 连接，不再为每条查询重复初始化。
- 新增 `memguard.eval.ablation`，统一比较纯文本、混合无图谱、完整无缓存、完整热缓存和 Kylin SDK 可选增强。
- 新增 `memguard.eval.performance_suite`，默认执行三轮并发试验，同时报告中位数和最差 P95/P99。
- openKylin 三轮 1k/120 查询/6 worker 并发最差 P95 为 `54.381 ms`；完整 10k 评测内并发 P95 为 `139.264 ms`。
- openKylin 10k 主负载 P95 为 `10.105 ms`、P99 为 `75.255 ms`、RSS 为 `130444 KB`。
- 统一消融中纯文本 Recall@5 为 `0.8426`，混合与完整系统为 `1.0000`。
- 本地测试 `141 passed, 1 skipped`，openKylin 测试 `142 passed`。

## v0.1-stable - 2026-05-22

首个稳定快照，已推送到私有 GitHub 仓库并打标签。

### 核心能力

- 多源记忆接入：文本、文件、结构化配置、shell 用户行为、工具结果、OS 事件、auditd 日志。
- 偏好、知识、敏感策略记忆建模。
- working/session/long_term 记忆层级。
- 版本管理、更新、回滚、冲突解决。
- mmap 追加式段文件、SQLite 元数据/FTS、图谱和审计链。
- 敏感检测、脱敏、阻断和自定义敏感模式。
- 自然语言精准遗忘、compact 和明文探针验证。
- RAG 上下文构建、provider-neutral prompt 渲染和默认关闭的 LLM adapter。
- 可解释混合语义抽取器，默认离线规则可复现，显式配置后可接本地 NER/LLM JSON 增强器。
- Agent 批量检索和记忆策略报告，覆盖任务规划、工具调用、结果生成多查询场景，以及冷热分层、存储占用、淘汰候选和 cgroup 状态。
- namespace 多会话隔离、systemd/cgroup 模板实例、journald、auditd、Kylin SDK 探测、Kylin embedding 可选检索重排和向量数据库 SDK 探针。
- UKUI/freedesktop 轻量通知、Peony 右键菜单插件源码、UKUI 控制中心插件源码、BPF LSM 强制拦截增强和 JSON denylist 策略生成。
- 本地 Web 控制台，覆盖状态、写入、抽取解释、检索、RAG 上下文、精准遗忘和审计。

### 验证结果

- 本地单元测试：`51 passed`。
- openKylin 最新证据包单元测试：`51 passed`。
- 本地 1k 负载 P95：`17.991 ms`。
- 本地 6 线程并发检索 P95：`121.101 ms`。
- 本地 Agent 批量检索 P95：`4.888 ms`。
- openKylin 1k 负载 P95：`34.816 ms`。
- openKylin 10k 负载 P95：`89.639 ms`。
- benchmark 扩充到 45/40/25/20/10/13/6，覆盖真实风格 auditd 日志、shell history、手动配置冲突、生产故障、自然语言敏感规则、更多 OS 敏感路径、namespace/cgroup、Kylin 可选检索和并发审计样例。
- Kylin embedding 实际检索：`vector_backend: "kylin_sdk"`。
- BPF LSM：openKylin 上构建并 attach，对 `/tmp/memguardos_lsm_secret` 返回 `Permission denied`，证据为 `enforcement_result=denied`。
- BPF LSM 策略：`scripts/generate_bpf_lsm_policy.py` 可由 JSON 生成静态 `memguard_lsm_policy.h`，用于隔离测试机中的可审查 denylist。
- UKUI 原生插件：Peony 右键菜单插件和 UKUI 控制中心插件均已构建出 `.so`；当前 VM 系统目录安装因只读根文件系统未完成。
- Web 控制台 smoke：页面加载、写入、检索和抽取解释均通过。
- 并发审计写入：6 线程共 72 条事件，审计哈希链验证通过。
- Recall@5、跨语言 Recall@5、敏感 F1、精准遗忘指标、冲突正确率、图谱 Recall@5 均为 `1.0000`。

### 工程化

- Git 仓库初始化并创建 `v0.1-stable` 标签。
- 新增 `.gitattributes`、`.gitignore`、Makefile、提交包脚本和证据采集脚本。
- 新增 GitHub Actions CI、Issue/PR 模板、贡献指南、安全策略和提交材料边界说明。
