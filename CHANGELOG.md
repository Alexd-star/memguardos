# 变更日志

本文记录 MemGuardOS 的主要版本和能力变化。

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
- systemd/cgroup、journald、auditd、Kylin SDK 探测和向量数据库 SDK 探针。
- UKUI/freedesktop 轻量通知和默认关闭的 bpftrace openat 探针脚本。

### 验证结果

- 本地与 openKylin 单元测试：`33 passed`。
- openKylin 1k 负载 P95：`83.761 ms`。
- openKylin 10k 负载 P95：`242.053 ms`。
- Recall@5、跨语言 Recall@5、敏感 F1、精准遗忘指标、冲突正确率、图谱 Recall@5 均为 `1.0000`。

### 工程化

- Git 仓库初始化并创建 `v0.1-stable` 标签。
- 新增 `.gitattributes`、`.gitignore`、Makefile、提交包脚本和证据采集脚本。
- 新增 GitHub Actions CI、Issue/PR 模板、贡献指南、安全策略和提交材料边界说明。
