# 外部基准与 Holdout 补充报告

本文用于解释 MemGuardOS 为什么不只依赖原始自建样例，也说明 EvidenceBench v2 与外部长期记忆基准之间的关系。

## 1. 已补充的抗质疑评测

本项目新增 `data/benchmarks_v2/`，共 260 条样例，其中 holdout 60 条，占比 23.08%。样例覆盖抽取、检索、精准遗忘、敏感治理、OS Agent 场景和 hard negative。与原始 benchmark 不同，v2 不要求所有指标都达到 1.0000，而是保留失败样例、边界原因和不适用说明，用来证明系统知道自己的适用范围。

运行方式：

```powershell
python scripts/build_evidencebench_v2.py
python scripts/run_evidencebench_v2.py --output docs/eval_v2_result.json --markdown docs/eval_v2_report.md
```

## 2. 与外部基准的对应关系

| 外部基准关注点 | 本项目对应验证 |
|---|---|
| LongMemEval / LongMemEval-V2：长期记忆、时间更新、响应延迟 | EvidenceBench v2 的时间范围遗忘、跨会话 Agent 场景、P50/P95/P99 延迟和 holdout |
| LoCoMo：长期对话、跨会话事实一致性 | Agent 交互 benchmark、真实 OpenHands 场景、偏好冲突与新版覆盖 |
| Graph / hierarchical memory：摘要、实体关系、原文证据回溯 | `hierarchical_graph_search` 三层检索、`build-context` selection trace |
| Memory value：记忆是否提升 Agent 任务完成 | 16 组配对实验、真实 Agent 运行、工具选择准确率和重复交互轮数下降 |
| Hard negative：相似但不该命中的样例 | EvidenceBench v2 hard negative false positive rate 与失败样例清单 |

## 3. 当前边界

本项目已经提供外部基准风格的代表性子集和接入脚本，但不宣称完整复现所有外部论文 benchmark 的全量排行榜。原因是赛题目标是 openKylin 本地 OS Agent 记忆系统，核心评价是多源融合、用户-Agent 交互习惯提取、精准遗忘、敏感治理、低延迟和 OS 机制集成。

答辩推荐口径：原始 benchmark 证明固定功能链路正确，EvidenceBench v2 证明系统面对 holdout、hard negative 和边界样例时仍有可解释表现；外部基准报告用于说明项目与 LongMemEval/LoCoMo 等长期记忆评价方向对齐，但不把论文 leaderboard 作为赛题功能的唯一依据。
