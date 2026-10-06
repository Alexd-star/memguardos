# 赛题符合性

MemGuardOS 对应第三届中国研究生操作系统开源创新大赛“面向 openKylin 的智能体记忆提取与精准遗忘机制”赛题。本页把赛题要求映射到代码、用户操作、测试和证据，便于复验时从要求直接定位实现。

## 1. 总体闭环

```text
用户与 Agent 交互
  -> 消息、反馈、工具调用和工具结果进入统一事件层
  -> 质量检查与敏感检测
  -> 偏好、知识、程序性经验和安全策略提取
  -> 版本化本地存储与图谱
  -> 规划、工具选择、回答生成三阶段检索注入
  -> 新会话复用并回收新的执行结果
  -> 用户预览并精准遗忘指定范围
  -> 索引、图谱、缓存、段文件清理与审计验证
```

## 2. 逐项对应

| 赛题要求 | MemGuardOS 方案 | 用户可见效果 | 代码与证据 |
|---|---|---|---|
| 多源融合数据 | 统一 Agent 事件模型接收用户消息、Agent 消息、纠正、工具调用、工具结果；OS 事件作为授权补充源 | 用户纠正和工具成功/失败经验可在后续会话复用 | `memguard/integrations/`、`memguard/os_guard/events.py` |
| 用户与 Agent 交互习惯自动提取 | 显式偏好解析、重复工具统计、纠正反馈和来源角色约束 | 不必每次重复说明工具、回答格式和确认习惯 | `interaction_extractor.py`、`habit_aggregator.py`、Agent 200 条专项结果 |
| 偏好记忆与知识记忆 | 统一记忆卡片记录类型、子类型、来源、置信度、版本、层级和有效期 | 可查看记忆编号、来源、版本和当前是否生效 | `memguard/models.py`、`memguard/extraction/` |
| RAG 生成前检索 | FTS、稳定 hash-vector、图谱与可选 Kylin SDK 组成混合检索 | Agent 只收到当前任务相关、已脱敏且受 token 预算约束的上下文 | `memguard/retrieval/`、`build-context` |
| 跨语言检索 | 归一化、技术别名和稳定语义特征共同参与排序 | 中文问题可召回英文 Linux/openKylin 经验 | `memguard/resources/technical_aliases.json`、检索评测 |
| 动态更新与冲突处理 | 更新追加新版本，时间窗口关闭旧版本；冲突解析保留来源和审计 | 新偏好覆盖旧偏好，仍可追溯变更原因 | `memguard/conflicts.py`、`versions`、`rollback` |
| 精准遗忘 | 自然语言转结构化过滤条件，先预览，再确认；清理元数据、FTS、图谱、缓存和段文件 | 可只忘掉某主题或某类偏好，同时保留无关记忆 | `memguard/forgetting/`、`forget`、`verify-forget` |
| 敏感信息保护 | 高风险凭据阻断，中低风险个人信息脱敏，支持自定义规则与来源路径风险 | Token 等内容不会进入长期记忆；文件右键扫描先显示风险 | `memguard/sensitivity/`、Peony 扫描 |
| OS 机制 | mmap 追加式段、namespace、systemd/cgroup、journald、auditd 和可选 BPF LSM | 服务受资源约束，敏感访问和遗忘动作可审计 | `memguard/store/`、`memguard/os_guard/`、`native/bpf_lsm/` |
| openKylin 适配 | UKUI 设置插件、Peony 右键、托盘通知、Kylin SDK 状态、Polkit 白名单操作和五个 Debian 包 | 用户从系统设置、托盘或文件管理器进入，不必记长命令 | `native/ukui/`、`packaging/`、openKylin 精选证据 |
| 真实 Agent 集成 | OpenHands 第一方适配器加标准事件、Python API、JSONL 和 Unix socket 通用协议 | 可用自然语言发起真实 Agent 任务，并在新会话观察记忆复用 | `openhands_runner.py`、`memguard shell --runtime real` |
| 行动结果闭环 | 工具执行前版本化预检，执行后按因果证据记录成功、偏离、失败、拦截或未知结果；重复回写幂等，遗忘同步清理 | 能区分“系统提供过建议”和“Agent 实际遵循并成功执行”，真实行动效果可追溯 | `action_feedback.py`、`record_agent_action_outcome`、`docs/evidence/openkylin/action_feedback_evidence_20261007.json` |
| 性能优化 | 候选预算、线程内服务复用、缓存、持久化候选向量和 SDK 延迟预算 | 10k 记忆下仍保持交互级检索延迟 | `hybrid.py`、`kylin_sdk.py`、performance evidence |
| 可验证交付 | 单元/集成/真实 Agent/openKylin/安全/归档六层门禁 | 发布包可安装、可复现、可检查 SHA-256 | `tests/`、`scripts/check_competition_ready.py` |

## 3. 实际使用效果

### 3.1 形成偏好

用户告诉 Agent“以后搜索代码优先使用 rg”。适配器把消息记录为用户事件，提取器生成工具偏好记忆。若后续多次工具结果表明 `rg` 成功，习惯聚合器会增加证据；若用户纠正为 `grep`，系统追加新版本并关闭旧偏好的有效窗口。

### 3.2 跨会话复用

新会话收到“查找配置加载逻辑”时，任务规划上下文提供相关项目知识，工具选择上下文提供当前有效的搜索工具偏好，结果生成上下文提供回答格式偏好。三阶段拆分避免把所有记忆一次性塞进 Prompt。

### 3.3 敏感阻断

用户或工具结果中出现符合凭据模式的假 Token 时，写入链返回阻断原因并记录审计，原文不会进入长期存储。Peony 扫描会先显示“不可导入”和风险类型，再由用户决定是否处理源文件。

### 3.4 精准遗忘

用户提出“忘掉项目 Alpha 的搜索工具偏好”后，系统先展示候选记忆编号和影响范围。确认后只删除目标偏好，并验证 FTS、图谱、缓存和压缩后的段文件均不能继续返回它；回答格式等无关偏好保持生效。

### 3.5 验证记忆是否真正改善行动

工具选择前，适配器调用行动预检；工具执行完成后，再提交实际工具和退出状态。若用户偏好要求使用 `rg`，预检会把 `grep` 改道为 `rg`，随后成功执行会形成一条成功遵循证据；若命令仍使用 `grep`，系统记录为偏离，即使命令本身返回 0，也不会把它当作偏好强化。重复提交同一工具调用不会重复计数，记忆更新或遗忘后，旧收据不能继续回写。

## 4. 指标与评分点

代表性指标及其原始文件位于 [测试与评测](testing.md) 和 [证据索引](evidence/README.md)。关键评分点包括：

- 功能完整性：抽取、存储、检索、更新、冲突、遗忘、敏感治理和真实 Agent 闭环均有代码和测试。
- 性能优化：10k 规模 P95/P99、并发三轮和消融实验均有原始 JSON。
- 代码规范：Python 3.10–3.12 CI、Ruff、模块边界、五包职责和统一发布清单。
- 文档质量：README、架构、部署、用户、Agent、API、安全和测试文档按读者角色拆分。
- openKylin 特色：UKUI、Peony、托盘、Polkit、systemd/cgroup、auditd、BPF LSM 与 Kylin SDK 不是仅写在说明中，均有代码和实机采集路径。

## 5. 准确边界

1. 已端到端验证的第一方真实 Agent 是 OpenHands。其他 Agent 需按标准事件或 Unix socket 协议编写适配器，不能宣称零配置接管任意 Agent。
2. 自建 benchmark 的 1.0000 说明固定数据集通过，不代表开放世界全部表达都达到 100%。
3. 精准遗忘覆盖 MemGuardOS 管理的数据面，不自动擦除外部 Agent 日志、备份、快照和介质物理残留。
4. BPF LSM 是路径策略强制拦截，不是内核态通用内容 DLP。
5. Kylin SDK 是可选增强路径；超出延迟预算或不可用时，系统透明回退到稳定主路径。

这些边界不会削弱赛题主线：它们明确了可复验范围，使每项能力都能从用户操作追溯到代码、测试和证据。
