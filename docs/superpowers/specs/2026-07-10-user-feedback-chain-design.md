# 用户反馈链与可视化验证设计

## 目标

在不重写记忆引擎和存储层的前提下，把 MemGuardOS 已有的写入、抽取、检索、Agent 上下文、偏好生效、敏感阻断、精准遗忘、审计和 openKylin 状态，统一成一条可以被普通用户理解、被评委演示、被开发者复验的反馈链：

`记忆产生 -> 为什么产生 -> 在哪里生效 -> 风险如何处理 -> 遗忘如何验证 -> 审计如何留证`。

## 方案边界

1. 业务真相仍由 `MemGuardService`、SQLite 元数据、段文件和审计链提供；Web、CLI、UKUI、托盘和 Peony 不各自复制业务逻辑。
2. 新增 `memguard/feedback.py` 作为用户反馈投影层，输出四种稳定结构：`MemoryChangeSummary`、`PreferenceEffectProof`、`ForgetVerificationReport`、`SensitiveBlockNotice`。
3. 反馈结构只对用户展示受控摘要，不泄露高风险原文、完整工具参数或外部日志内容。
4. 证据结构区分“真实执行”“回放/演示”和“未验证”，不把 dry-run 或规则判断包装成真实 Agent 结果。
5. Web 仍使用现有无构建依赖的本地控制台，但增加 Agent 工作台、记忆详情、偏好证明、遗忘验证、通知中心和一键演示入口；管理员原始 JSON 仅保留在折叠的高级信息区。
6. UKUI/托盘/Peony 只调用已有 CLI/API，并把统一反馈结构转换为克制、可读的系统界面，不在 C++ 中重新实现抽取或遗忘算法。

## 统一反馈结构

- `MemoryChangeSummary`：描述一次写入、导入或 Agent 运行带来的新增、更新、阻断和跳过，并给出适用范围与下一步。
- `PreferenceEffectProof`：描述某条偏好是否被注入、实际工具/回答行为是什么、是否通过以及证据来源。没有真实行为时必须为 `not_applicable`，不能伪造通过。
- `ForgetVerificationReport`：描述候选、确认状态、受管主记录/索引/图谱/缓存/段文件/上下文/审计的逐项验证，并保留 OpenHands 日志、模型提供商日志、shell history、快照和备份不受管的边界。
- `SensitiveBlockNotice`：描述敏感来源、标签、风险等级、阻断/脱敏/隔离结果，明确“未进入长期记忆和 RAG”，并提供审计入口。

## Web 信息架构

普通用户入口按“工作台、记忆、Agent 工作台、遗忘验证、通知”组织；管理员入口按“审计、系统维护、复核中心”组织。首页只呈现状态、最近变化和直接行动，不把 JSON、时间戳、内部 ID 作为首屏内容。

所有耗时操作使用统一的处理中、成功、失败和下一步反馈。失败时显示可执行的处理入口，不使用无期限的“正在执行”。所有新接口均由 dashboard smoke 覆盖，并保留 JSON 响应供自动化和审计使用。

## UKUI、托盘和 Peony

- UKUI 日常页显示最近学到的内容、当前生效偏好、最近 Agent 任务、敏感阻断和遗忘验证；管理员页显示审计、BPF/auditd、Kylin SDK、cgroup 和服务日志的用户解释；维护页保留证据和提交检查。
- 托盘只对高风险或明确完成/失败的事件弹窗，普通变化进入通知历史；菜单提供最近记忆、最近偏好、最近任务、最近遗忘和 Agent 工作台入口。
- Peony 导入前展示每个文件的建议动作，导入后展示 `MemoryChangeSummary`，撤销后展示 `ForgetVerificationReport`；具体算法和边界由 Python CLI 返回。

## 验证策略

1. 先测试四种结构的字段、状态和边界。
2. 再测试 Service 生成的写入/阻断/遗忘/偏好证明与真实存储状态一致。
3. 再测试 Web endpoint、HTML 控件和 dashboard smoke。
4. 最后测试 CLI 可读输出、原生资源中的文字和 C++ 构建契约。
5. openKylin 实机安装、桌面截图和当前 HEAD 证据必须在 VM 可达后单独采集；本地测试不能替代实机证据。
