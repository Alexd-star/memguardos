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
- namespace 多会话隔离、systemd/cgroup 模板实例、journald、auditd、Kylin SDK 探测、Kylin embedding 可选检索重排和向量数据库 SDK 探针。
- UKUI/freedesktop 轻量通知、Peony 右键菜单插件源码、UKUI 控制中心插件源码和 BPF LSM 强制拦截增强。

### 验证结果

- 本地与 openKylin 单元测试：`42 passed`。
- openKylin 1k 负载 P95：`76.182 ms`。
- openKylin 10k 负载 P95：`343.328 ms`。
- benchmark 扩充到 30/25/25/17/10/13/6，覆盖自然语言敏感规则、更多 OS 敏感路径、namespace/cgroup、Kylin 可选检索和并发审计样例。
- Kylin embedding 实际检索：`vector_backend: "kylin_sdk"`。
- BPF LSM：openKylin 上构建并 attach，对 `/tmp/memguardos_lsm_secret` 返回 `Permission denied`，证据为 `enforcement_result=denied`。
- UKUI 原生插件：Peony 右键菜单插件和 UKUI 控制中心插件均已构建出 `.so`；当前 VM 系统目录安装因只读根文件系统未完成。
- 并发审计写入：6 线程共 72 条事件，审计哈希链验证通过。
- Recall@5、跨语言 Recall@5、敏感 F1、精准遗忘指标、冲突正确率、图谱 Recall@5 均为 `1.0000`。

### 工程化

- Git 仓库初始化并创建 `v0.1-stable` 标签。
- 新增 `.gitattributes`、`.gitignore`、Makefile、提交包脚本和证据采集脚本。
- 新增 GitHub Actions CI、Issue/PR 模板、贡献指南、安全策略和提交材料边界说明。
