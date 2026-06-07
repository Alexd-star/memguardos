# 14 常见问题 FAQ

本文档汇总 MemGuardOS 使用、部署、测试和提交中常见问题。

## 1. 是否必须购买 LLM API？

不必须。MemGuardOS 的默认主链路不依赖外部 LLM API。系统提供 RAG 上下文包和 prompt，但是否交给模型生成回答由上游 Agent 决定。比赛重点是记忆提取、检索、遗忘、安全和 OS 机制，不是云端模型调用。

## 2. 是否需要 GPU？

默认不需要 GPU。hash-vector、FTS、图谱和规则抽取均可在 CPU 上运行。Kylin embedding SDK 如果可用，可以作为端侧向量增强，但不是默认必需项。

## 3. Kylin SDK 不可用怎么办？

运行：

```bash
python -m memguard.cli.memctl sdk-status --probe-embedding
```

如果库、头文件、服务或模型缺失，诊断报告会说明原因。系统默认会回退到 FTS/hash/graph 路径，因此普通检索不应失败。若要展示 SDK 真实推理，需要在 openKylin 目标环境中检查 kytensor、runtime socket、模型仓库和插件依赖。

## 4. 为什么默认抽取不是大模型？

默认抽取采用可解释规则和轻量策略，是为了端侧稳定、可审计、可复现。大模型抽取可以作为增强器接入，但外部 API 可能带来网络、费用、隐私和不可复验问题，不适合作为比赛默认主链路。

## 5. `forget --compact` 是否危险？

`--compact` 会重写段文件，清理已删除记忆的明文载荷。它是精准遗忘闭环的重要能力，但属于高风险维护操作。正式环境应先备份数据目录，确认 dry-run 候选无误后再执行。

## 6. Web 控制台能否公开部署？

不建议。当前 Web 控制台定位为本机运维和评审演示，默认绑定 `127.0.0.1`。如果要公开部署，需要增加完整鉴权、TLS、限流、审计和权限模型。

## 7. 为什么正式包不包含所有脚本？

正式竞赛包只包含评审和运行需要的主体材料。打包脚本、自检脚本、CI、研究源材料和过程追踪文档属于完整工程仓库支撑，不进入正式包主体，以免评审材料混乱。

## 8. 审计链失败怎么办？

先确认是否手工改动过 SQLite 数据库或运行时段文件。然后运行：

```bash
python -m memguard.cli.memctl verify-audit
python -m memguard.cli.memctl doctor
```

审计链失败说明关键事件记录可能被修改、缺失或顺序破坏，应停止把当前数据作为正式证据，重新生成干净数据或恢复备份。

## 9. openKylin `.deb` 安装被拦截怎么办？

openKylin 普通模式可能有系统保护策略。正式安装复验证据建议在维护模式执行 `sudo apt install ./dist/memguardos_0.1.0_amd64.deb`，安装后再采集 `dpkg`、systemd、SDK 和审计证据。

## 10. 哪些文件可以清理？

缓存、旧打包产物和个人笔记可以清理或归档，但不要删除 `memguard/`、`scripts/` 正式脚本、`native/`、`tests/`、`data/benchmarks/`、`docs/evidence/latest/` 和正式中文文档。具体建议见 [16_历史文件说明.md](16_历史文件说明.md)。
