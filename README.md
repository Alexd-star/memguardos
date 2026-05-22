# MemGuardOS

MemGuardOS 是面向研究生操作系统开源创新大赛赛题 3 的 Linux/openKylin 智能体记忆原型系统。

项目重点解决：**智能体记忆提取、结构化检索、敏感信息识别、精准遗忘、审计追踪，以及 openKylin/Linux OS 级集成**。

## 核心亮点

- 多源记忆接入：用户文本、文件、结构化手动配置、工具执行结果、OS 事件、auditd 审计事件。
- 记忆类型：偏好记忆、知识记忆、敏感策略记忆。
- 记忆子类型：输出风格、工具偏好、安全策略、工作流、案例、模板、工具轨迹、OS 事件。
- 记忆层级：`working`、`session`、`long_term`。
- 版本管理：追加式版本、回滚、墓碑删除、压缩清除。
- OS 机制：mmap 段文件读取、namespace 多会话隔离、systemd/cgroup 服务限制、journald 桥接、auditd 规则与真实日志接入。
- 敏感检测：PII、凭据、私钥、自定义正则、OS 高风险路径信号。
- 精准遗忘：自然语言 dry-run、确认删除、元数据/索引/图谱清除、不可逆探针验证。
- 知识结构化：实体/关系抽取、图谱搜索、关联记忆查询。
- RAG 上下文：将检索到的偏好、知识、短中长期记忆组织为已脱敏的生成前上下文包。
- 评测体系：Recall@5、敏感检测 F1、遗忘精确率/召回率、冲突解决正确率、图谱召回、负载延迟、CPU/内存/存储指标。
- openKylin 适配：检测麒麟文本向量化 SDK 和向量数据库 SDK，保留稳定轻量默认路径，提供真实 SDK 探针和 Kylin 可选检索重排证据。

## 快速开始

```powershell
cd <项目目录>\memguardos
$env:PYTHONPATH = (Get-Location).Path
python -m memguard.cli.memctl ingest --text "以后回答 Linux 命令时直接给命令和风险提示。"
python -m memguard.cli.memctl ingest-config --file data/demo/user_config.json
python -m memguard.cli.memctl ingest-tool-result --file data/demo/tool_result_success.json
python -m memguard.cli.memctl list
python -m memguard.cli.memctl search "Linux 命令回答偏好"
python -m memguard.cli.memctl build-context "openKylin 服务内存限制"
python -m memguard.cli.memctl --namespace demo-a ingest --text "demo-a 会话只保存 systemd MemoryMax 方案。"
python -m memguard.cli.memctl --namespace demo-a namespace-status
python -m memguard.cli.memctl versions <memory_id>
python -m memguard.cli.memctl forget "忘掉 Linux 命令回答偏好" --yes
python -m memguard.cli.memctl audit
python scripts/demo_smoke.py
python -m memguard.eval.run_eval --latency-size 1000 --output docs/eval_result.json --markdown docs/eval_report.md
```

openKylin 上可采集提交证据包：

```bash
bash scripts/collect_openkylin_evidence.sh --with-10k
```

默认数据目录为 `~/.memguardos`。如需指定目录，可设置 `MEMGUARD_HOME`；如需多项目或多会话隔离，可设置 `MEMGUARD_NAMESPACE` 或使用 `memctl --namespace <name>`。默认 namespace 继续使用旧路径，显式 namespace 会写入 `~/.memguardos/namespaces/<name>/`。

## 重要文档

建议按下面顺序阅读，避免在过程性材料里来回跳：

1. [项目介绍总览](docs/项目介绍总览.md)
2. [概要设计说明书](docs/概要设计说明书.md)
3. [详细设计说明书](docs/详细设计说明书.md)
4. [安全设计说明书](docs/安全设计说明书.md)
5. [操作系统机制说明](docs/操作系统机制说明.md)
6. [部署手册](docs/部署手册.md)
7. [用户手册](docs/用户手册.md)
8. [测试报告](docs/测试报告.md)
9. [评委证据包索引](docs/评委证据包索引.md)
10. [赛题要求逐句符合性核查](docs/赛题要求逐句符合性核查.md)
11. [麒麟 SDK 集成证据](docs/麒麟SDK集成证据.md)
12. [答辩提纲](docs/答辩提纲.md)
13. [演示视频脚本](docs/演示视频脚本.md)

## 工程化

仓库包含 GitHub Actions、Issue/PR 模板、贡献指南、安全策略、变更日志、打包脚本和提交清单。这些文件用于开源协作、持续集成和回溯备份；参赛功能主体仍是 `memguard/`、`scripts/`、`tests/`、`data/` 和中文 `docs/`。

```powershell
python -m pip install -e ".[dev]"
make ci
python scripts/package_submission.py
```

如果 Windows 环境没有 `make`，使用以下等价命令：

```powershell
python -m compileall -q memguard scripts
python -m ruff check memguard tests scripts
python -m pytest -q
python scripts/check_docs_links.py
```

`scripts/package_submission.py` 会同时生成 `memguardos_competition_submission_*.zip` 和 `memguardos_full_repository_*.zip`。竞赛平台提交优先使用前者；后者用于 GitHub release、备份或完整工程交付。

## 验证命令

```powershell
python -m pytest -q
python -m memguard.eval.run_eval --latency-size 1000 --output docs/eval_result.json --markdown docs/eval_report.md
python scripts/demo_smoke.py
```
