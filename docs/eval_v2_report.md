# MemGuardOS EvidenceBench v2 评测报告

EvidenceBench v2 是抗质疑评测，用于补充原 benchmark 的功能正确性验证。
原 benchmark 证明固定功能链路是否正确；EvidenceBench v2 额外加入 hard negative、holdout、跨语言、冲突、敏感治理、自然语言精准遗忘和 openKylin OS Agent 场景。
不是所有指标都必须是 1.0000；低于 1.0000 的指标用于暴露边界和后续优化方向。

## 可追溯性

| 项目 | 值 |
|---|---|
| Manifest SHA-256 | `abf3b16b4e129e8d30e3d047b7ce2c25bebadac0bb7e1546065c8d7d7af276b0` |
| Data revision | `2fbde0891347afed12aa51b6f7dcde48f528e071eef4c9dc02a534b82fbaf211` |
| 公开 namespace | `evidencebench-v2-public` |
| Holdout namespace | `evidencebench-v2-holdout` |
| 生成器 | `scripts/run_evidencebench_v2.py` |

## 数据集

| 项目 | 数值 |
|---|---:|
| 总样例数 | 260 |
| Holdout 样例数 | 60 |
| Holdout 占比 | 0.2308 |
| 规范化 split 重叠数 | 0 |

## 总体指标

| 指标 | 数值 |
|---|---:|
| alias_expansion_hit_rate | 0.65 |
| cross_language_cases_evaluated | 10 |
| cross_language_recall_at_1 | 1.0 |
| cross_language_recall_at_5 | 1.0 |
| hard_negative_false_positive_rate | 0.0 |
| mrr | 1.0 |
| ndcg_at_5 | 1.0 |
| recall_at_1 | 1.0 |
| recall_at_3 | 1.0 |
| recall_at_5 | 1.0 |
| retrieval_cases_evaluated | 60 |
| strict_recall_at_1 | 1.0 |
| strict_recall_at_5 | 1.0 |
| topic_recall_at_1 | 1.0 |
| topic_recall_at_5 | 1.0 |
| forget_candidates_evaluated | 240 |
| forget_cases_evaluated | 60 |
| forget_expected_delete_count | 120 |
| forget_expected_keep_count | 120 |
| forget_keep_success | 1.0 |
| forget_precision | 1.0 |
| forget_recall | 1.0 |
| forget_residual_leak_rate | 0.0 |
| sensitive_block_f1 | 1.0 |
| sensitive_redaction_f1 | 1.0 |
| sensitive_false_positive_rate | 0.0 |
| extraction_cases_evaluated | 40 |
| extraction_type_accuracy | 1.0 |
| extraction_subtype_accuracy | 1.0 |
| extraction_topic_accuracy | 1.0 |
| tool_result_to_preference_extraction_accuracy | 1.0 |
| conflict_cases_evaluated | 5 |
| conflict_resolution_accuracy | 1.0 |
| memory_update_accuracy | 1.0 |
| agent_context_adherence_rate | 1.0 |
| agent_context_behavior_accuracy | 1.0 |
| agent_context_cases_evaluated | 60 |
| agent_context_hint_accuracy | 1.0 |
| agent_context_scope_accuracy | 1.0 |
| p50_latency_ms | 20.3 |
| p95_latency_ms | 26.598 |
| p99_latency_ms | 31.61 |
| rss_kb | None |
| rss_delta_kb | None |
| cpu_time_ms | 10312.5 |
| storage_footprint_kb | 9185.115 |
| audit_chain_ok | True |

## 公开 split

| 指标 | 数值 |
|---|---:|
| retrieval_cases_evaluated | 40 |
| cross_language_cases_evaluated | 10 |
| strict_recall_at_1 | 1.0 |
| strict_recall_at_5 | 1.0 |
| topic_recall_at_1 | 1.0 |
| topic_recall_at_5 | 1.0 |
| recall_at_1 | 1.0 |
| recall_at_3 | 1.0 |
| recall_at_5 | 1.0 |
| mrr | 1.0 |
| ndcg_at_5 | 1.0 |
| hard_negative_false_positive_rate | 0.0 |
| cross_language_recall_at_1 | 1.0 |
| cross_language_recall_at_5 | 1.0 |
| alias_expansion_hit_rate | 0.925 |
| forget_cases_evaluated | 40 |
| forget_candidates_evaluated | 160 |
| forget_expected_delete_count | 80 |
| forget_expected_keep_count | 80 |
| forget_precision | 1.0 |
| forget_recall | 1.0 |
| forget_keep_success | 1.0 |
| forget_residual_leak_rate | 0.0 |
| agent_context_cases_evaluated | 40 |
| agent_context_hint_accuracy | 1.0 |
| agent_context_scope_accuracy | 1.0 |
| agent_context_behavior_accuracy | 1.0 |
| agent_context_adherence_rate | 1.0 |

## Holdout split

| 指标 | 数值 |
|---|---:|
| retrieval_cases_evaluated | 20 |
| cross_language_cases_evaluated | 0 |
| strict_recall_at_1 | 1.0 |
| strict_recall_at_5 | 1.0 |
| topic_recall_at_1 | 1.0 |
| topic_recall_at_5 | 1.0 |
| recall_at_1 | 1.0 |
| recall_at_3 | 1.0 |
| recall_at_5 | 1.0 |
| mrr | 1.0 |
| ndcg_at_5 | 1.0 |
| hard_negative_false_positive_rate | 0.0 |
| cross_language_recall_at_1 | 0.0 |
| cross_language_recall_at_5 | 0.0 |
| alias_expansion_hit_rate | 0.1 |
| forget_cases_evaluated | 20 |
| forget_candidates_evaluated | 80 |
| forget_expected_delete_count | 40 |
| forget_expected_keep_count | 40 |
| forget_precision | 1.0 |
| forget_recall | 1.0 |
| forget_keep_success | 1.0 |
| forget_residual_leak_rate | 0.0 |
| agent_context_cases_evaluated | 20 |
| agent_context_hint_accuracy | 1.0 |
| agent_context_scope_accuracy | 1.0 |
| agent_context_behavior_accuracy | 1.0 |
| agent_context_adherence_rate | 1.0 |

## 失败样例与原因

- `boundary-note`（open_world_boundary）：本轮未产生样例内失败；开放世界表达、外部 Agent 日志和介质级取证仍需按边界文档单独验证。

## 边界说明

本报告按 checked-in 数据与当前生产逻辑如实计分；strict miss、topic acceptable 命中和 hard-negative confusion 分开记录。
公开与 Holdout retrieval 在独立服务、存储与 namespace 中运行；该隔离证明本次评分未共享评测记忆，不代表已覆盖外部训练过程。
MemGuardOS 的精准遗忘保证范围限定在受管 SQLite 元数据、FTS/图谱索引、mmap 段文件 compact 后的受管明文与审计链。
开放世界自然语言、外部 Agent 日志、VM 快照、备份和块设备取证仍属于未覆盖边界。
