# MemGuardOS 文档中心

本目录只保留面向使用者、集成开发者、运维人员和复验人员的稳定文档。设计草稿、调研资料与历史证据不属于公开产品文档，版本演进仍可通过 Git 历史追溯。

## 从哪里开始

| 读者 | 建议阅读顺序 |
|---|---|
| 普通用户 | [安装部署](installation.md) → [用户指南](user-guide.md) |
| Agent 开发者 | [Agent 接入](agent-integration.md) → [接口参考](api.md) → [系统架构](architecture.md) |
| openKylin 管理员 | [安装部署](installation.md) → [安全与遗忘边界](security.md) → [测试与评测](testing.md) |
| 贡献者 | [开发者指南](developer-guide.md) |
| 竞赛评审 | [赛题符合性](competition.md) → [测试与评测](testing.md) → [证据索引](evidence/README.md) |

## 稳定文档

- [系统架构](architecture.md)：模块边界、数据流、存储、检索、遗忘与 OS 集成。
- [安装部署](installation.md)：Python 环境、openKylin、五包安装、服务与卸载。
- [用户指南](user-guide.md)：CLI、Web、UKUI、Peony 和完整操作流程。
- [Agent 接入](agent-integration.md)：标准事件协议、OpenHands 和第三方适配器。
- [接口参考](api.md)：CLI、Python、Unix socket 和数据结构。
- [开发者指南](developer-guide.md)：代码结构、扩展点、测试和发布约束；专项工具可从 [`scripts/README.md`](../scripts/README.md) 按职责查找。
- [安全与遗忘边界](security.md)：威胁模型、敏感治理、精准遗忘与准确边界。
- [测试与评测](testing.md)：测试分层、数据集、指标含义、复现方法和限制。
- [赛题符合性](competition.md)：赛题要求到代码、操作与证据的逐项映射。

## 文档约定

命令默认从仓库根目录执行。`memguard` 是面向普通用户的简化 CLI；`memctl` 是面向开发者和管理员的完整 CLI。涉及 `sudo`、BPF、auditd、systemd 或桌面插件安装的操作仅适用于 Linux/openKylin。
