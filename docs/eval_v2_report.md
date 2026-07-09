# MemGuardOS EvidenceBench v2 评测报告

EvidenceBench v2 是抗质疑评测，用于补充原 benchmark 的功能正确性验证。
原 benchmark 证明固定功能链路是否正确；EvidenceBench v2 额外加入 hard negative、holdout、跨语言、冲突、敏感治理、自然语言精准遗忘和 openKylin OS Agent 场景。
不是所有指标都必须是 1.0000；低于 1.0000 的指标用于暴露边界和后续优化方向。

## 数据集

| 项目 | 数值 |
|---|---:|
| 总样例数 | 260 |
| Holdout 样例数 | 60 |
| Holdout 占比 | 0.2308 |

## 指标

| 指标 | 数值 |
|---|---:|
| recall_at_1 | 0.15 |
| recall_at_3 | 0.45 |
| recall_at_5 | 0.75 |
| mrr | 0.3425 |
| ndcg_at_5 | 0.4423 |
| hard_negative_false_positive_rate | 0.0 |
| cross_language_recall_at_1 | 0.0 |
| cross_language_recall_at_5 | 0.9 |
| forget_precision | 0.9 |
| forget_recall | 0.9 |
| forget_keep_success | 1.0 |
| forget_residual_leak_rate | 0.0 |
| sensitive_block_f1 | 0.8421 |
| sensitive_redaction_f1 | 0.8571 |
| sensitive_false_positive_rate | 0.0 |
| tool_result_to_preference_extraction_accuracy | 1.0 |
| conflict_resolution_accuracy | 1.0 |
| agent_context_adherence_rate | 1.0 |
| p50_latency_ms | 0.088 |
| p95_latency_ms | 3.102 |
| p99_latency_ms | 3.498 |
| rss_kb | None |
| rss_delta_kb | None |
| cpu_time_ms | 4968.75 |
| storage_footprint_kb | 4957.006 |
| audit_chain_ok | True |

## 失败样例与原因

- `holdout_holdout_retrieval_09`：召回不足或 hard negative 进入结果。
- `holdout_holdout_retrieval_19`：召回不足或 hard negative 进入结果。
- `retrieval_cross_language_retrieval_09`：召回不足或 hard negative 进入结果。
- `retrieval_graph_association_retrieval_09`：召回不足或 hard negative 进入结果。
- `retrieval_hard_negative_retrieval_09`：召回不足或 hard negative 进入结果。
- `retrieval_temporal_retrieval_01`：召回不足或 hard negative 进入结果。
- `retrieval_temporal_retrieval_02`：召回不足或 hard negative 进入结果。
- `retrieval_temporal_retrieval_03`：召回不足或 hard negative 进入结果。
- `retrieval_temporal_retrieval_04`：召回不足或 hard negative 进入结果。
- `retrieval_temporal_retrieval_05`：召回不足或 hard negative 进入结果。
- `retrieval_temporal_retrieval_06`：召回不足或 hard negative 进入结果。
- `retrieval_temporal_retrieval_07`：召回不足或 hard negative 进入结果。
- `retrieval_temporal_retrieval_08`：召回不足或 hard negative 进入结果。
- `retrieval_temporal_retrieval_09`：召回不足或 hard negative 进入结果。
- `retrieval_temporal_retrieval_10`：召回不足或 hard negative 进入结果。
- `forgetting_forget_keep_hard_negatives_09`：遗忘候选未完全命中目标或误伤应保留样例。
- `forgetting_natural_language_erasure_09`：遗忘候选未完全命中目标或误伤应保留样例。
- `forgetting_scoped_agent_forgetting_09`：遗忘候选未完全命中目标或误伤应保留样例。
- `forgetting_time_range_forgetting_09`：遗忘候选未完全命中目标或误伤应保留样例。
- `holdout_holdout_forgetting_09`：遗忘候选未完全命中目标或误伤应保留样例。

## 边界说明

失败样例不代表系统无效，而是说明开放世界自然语言、外部 Agent 持久化、VM 快照、块设备取证和实机增强项需要单独治理。
MemGuardOS 的精准遗忘保证范围限定在受管 SQLite 元数据、FTS/图谱索引、mmap 段文件 compact 后的受管明文与审计链。
