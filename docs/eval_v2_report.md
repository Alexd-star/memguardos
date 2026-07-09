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
| strict_recall_at_1 | 0.9667 |
| strict_recall_at_5 | 1.0 |
| topic_recall_at_1 | 0.9667 |
| topic_recall_at_5 | 1.0 |
| recall_at_1 | 0.9667 |
| recall_at_3 | 1.0 |
| recall_at_5 | 1.0 |
| mrr | 0.9806 |
| ndcg_at_5 | 0.9113 |
| hard_negative_false_positive_rate | 0.0 |
| cross_language_recall_at_1 | 1.0 |
| cross_language_recall_at_5 | 1.0 |
| alias_expansion_hit_rate | 0.4167 |
| forget_precision | 1.0 |
| forget_recall | 1.0 |
| forget_keep_success | 1.0 |
| forget_residual_leak_rate | 0.0 |
| sensitive_block_f1 | 1.0 |
| sensitive_redaction_f1 | 1.0 |
| sensitive_false_positive_rate | 0.0 |
| tool_result_to_preference_extraction_accuracy | 1.0 |
| conflict_resolution_accuracy | 1.0 |
| agent_context_adherence_rate | 1.0 |
| p50_latency_ms | 0.065 |
| p95_latency_ms | 4.786 |
| p99_latency_ms | 6.382 |
| rss_kb | None |
| rss_delta_kb | None |
| cpu_time_ms | 5140.625 |
| storage_footprint_kb | 5046.199 |
| audit_chain_ok | True |

## 优化前后对比

| 指标 | 优化前 | 当前 |
|---|---:|---:|
| strict_recall_at_1 / 原 Recall@1 | 0.1500 | 0.9667 |
| topic_recall_at_5 / 原 Recall@5 | 0.7500 | 1.0 |
| MRR | 0.3425 | 0.9806 |
| nDCG@5 | 0.4423 | 0.9113 |
| sensitive_block_f1 | 0.8421 | 1.0 |
| sensitive_redaction_f1 | 0.8571 | 1.0 |
| forget_precision | 0.9000 | 1.0 |
| forget_recall | 0.9000 | 1.0 |

## 失败样例与原因

- `boundary-note`（open_world_boundary）：当前样例全部通过；开放世界表达、外部 Agent 日志和介质级取证仍按边界文档处理。

## 边界说明

失败样例不代表系统无效，而是说明开放世界自然语言、外部 Agent 持久化、VM 快照、块设备取证和实机增强项需要单独治理。
MemGuardOS 的精准遗忘保证范围限定在受管 SQLite 元数据、FTS/图谱索引、mmap 段文件 compact 后的受管明文与审计链。
