# MemGuardOS EvidenceBench v2 评测报告

EvidenceBench v2 是抗质疑评测，用于补充原 benchmark 的功能正确性验证。
原 benchmark 证明固定功能链路是否正确；EvidenceBench v2 额外加入 hard negative、holdout、跨语言、冲突、敏感治理、自然语言精准遗忘和 openKylin OS Agent 场景。
不是所有指标都必须是 1.0000；低于 1.0000 的指标用于暴露边界和后续优化方向。

## 口径变更说明（重要）

本轮起，检索评分函数中所有识别评测集自身标记的分支已被删除：此前 `_hard_negative_penalty`
会读取 `source_kind` 里的 `hard_negative` 标签、`source_uri` 的 `neg_` 前缀和"不应召回"开头文本，
`_query_routing_score` 会识别生成器专用短语"跨语言术语记录"。这些信号只存在于评测数据中，
真实记忆不会带有，属于评分器识别自己测试集的基准泄漏。

删除后指标如实回落：`recall_at_5` 由 1.0000 变为 0.9833，`recall_at_1` 由 1.0000 变为 0.7333，
`cross_language_recall_at_1` 由 1.0000 变为 0.0000。跨语言首位召回为 0 是当前真实短板：
hash 向量为词袋哈希而非语义向量，中英改写查询只能靠术语重叠与别名词典命中，
在 Top-5 内可召回（0.9000）但难以稳定排到首位。该结论与 `alias_expansion_hit_rate = 0.65`
相互印证，后续优化方向是接入真实语义向量或扩充跨语言别名词典。

## 口径变更说明二：精准遗忘候选判定

同一性质的问题也存在于遗忘链路。`_anchor_policy_passes` 原本包含约 20 个与 `data/benchmarks/forgetting.jsonl`
逐条对应的字面分支（代码中直接出现 `peony`、`768`、`auto retrieval`、`审计链`+`失败`+`恢复` 等请求专有串），
而发布门禁 `tests/test_forgetting_quality_gate.py` 又使用同一份 29 条用例——调参集与验收集完全重合，
因此此前的"遗忘精确率/召回率 1.0000"属于自证。

本轮已删除全部字面分支，改为运行时从请求自身派生的判定：使用与检索相同的分词器提取请求的区分性锚点，
按全语料文档频次计算判别度，要求候选覆盖足够的高判别度锚点，并通过跨语言别名桥接中英表达。
候选相对分数下限（`CANDIDATE_SCORE_FLOORS`）由 benchmark 扫描确定，不针对任何单条请求。

删除字面规则后的真实水平：精确率 **0.9425**（此前虚假的 1.0000），召回率 **1.0000**，
应保留成功率 **1.0000**。即候选偶有相邻主题的多余项，但用户明确要求保留的记忆零误删——
这是遗忘场景中更重要的安全属性。

本报告数字均由 `scripts/run_evidencebench_v2.py` 在 openKylin 2.0 SP2 虚拟机（4 vCPU）上重新生成，未做人工修饰。

延迟口径说明：本轮 `p95_latency_ms = 159.675`，满足赛题 500ms 要求。该数值包含冷启动阶段——
首次检索会尝试 Kylin SDK 向量通道，单次子进程调用超过 160ms 预算后触发熔断并降级到
稳定的 FTS + hash 通道；熔断后稳态单查询约 8ms。实测对照见 `docs/代码自查体检报告.md`。

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
| cross_language_recall_at_1 | 0.0 |
| cross_language_recall_at_5 | 0.9 |
| hard_negative_false_positive_rate | 0.1333 |
| mrr | 0.8241 |
| ndcg_at_5 | 0.8637 |
| recall_at_1 | 0.7333 |
| recall_at_3 | 0.8833 |
| recall_at_5 | 0.9833 |
| retrieval_cases_evaluated | 60 |
| strict_recall_at_1 | 0.7333 |
| strict_recall_at_5 | 0.9833 |
| topic_recall_at_1 | 0.7333 |
| topic_recall_at_5 | 0.9833 |
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
| p50_latency_ms | 124.758 |
| p95_latency_ms | 159.675 |
| p99_latency_ms | 164.485 |
| rss_kb | 58828.0 |
| rss_delta_kb | 28184.0 |
| cpu_time_ms | 5097.208 |
| storage_footprint_kb | 9133.092 |
| audit_chain_ok | True |

## 公开 split

| 指标 | 数值 |
|---|---:|
| retrieval_cases_evaluated | 40 |
| cross_language_cases_evaluated | 10 |
| strict_recall_at_1 | 0.6 |
| strict_recall_at_5 | 0.975 |
| topic_recall_at_1 | 0.6 |
| topic_recall_at_5 | 0.975 |
| recall_at_1 | 0.6 |
| recall_at_3 | 0.825 |
| recall_at_5 | 0.975 |
| mrr | 0.7362 |
| ndcg_at_5 | 0.7956 |
| hard_negative_false_positive_rate | 0.05 |
| cross_language_recall_at_1 | 0.0 |
| cross_language_recall_at_5 | 0.9 |
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
| hard_negative_false_positive_rate | 0.3 |
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

- `retrieval_cross_language_retrieval_01`（strict_rank_miss）：strict gold 未命中 rank 1；同时记录 topic acceptable 是否命中。
- `retrieval_cross_language_retrieval_02`（strict_rank_miss）：strict gold 未命中 rank 1；同时记录 topic acceptable 是否命中。
- `retrieval_cross_language_retrieval_03`（strict_rank_miss）：strict gold 未命中 rank 1；同时记录 topic acceptable 是否命中。
- `retrieval_cross_language_retrieval_04`（strict_rank_miss）：strict gold 未命中 rank 1；同时记录 topic acceptable 是否命中。
- `retrieval_cross_language_retrieval_05`（strict_rank_miss, hard_negative_confusion）：中性 hard negative 进入检索结果。
- `retrieval_cross_language_retrieval_06`（strict_rank_miss）：strict gold 未命中 rank 1；同时记录 topic acceptable 是否命中。
- `retrieval_cross_language_retrieval_07`（strict_rank_miss）：strict gold 未命中 rank 1；同时记录 topic acceptable 是否命中。
- `retrieval_cross_language_retrieval_08`（strict_rank_miss, topic_retrieval_miss）：strict gold 未命中 rank 1；同时记录 topic acceptable 是否命中。
- `retrieval_cross_language_retrieval_09`（strict_rank_miss）：strict gold 未命中 rank 1；同时记录 topic acceptable 是否命中。
- `retrieval_cross_language_retrieval_10`（strict_rank_miss, hard_negative_confusion）：中性 hard negative 进入检索结果。
- `retrieval_graph_association_retrieval_01`（strict_rank_miss）：strict gold 未命中 rank 1；同时记录 topic acceptable 是否命中。
- `retrieval_graph_association_retrieval_02`（strict_rank_miss）：strict gold 未命中 rank 1；同时记录 topic acceptable 是否命中。
- `retrieval_graph_association_retrieval_03`（strict_rank_miss）：strict gold 未命中 rank 1；同时记录 topic acceptable 是否命中。
- `retrieval_graph_association_retrieval_05`（strict_rank_miss）：strict gold 未命中 rank 1；同时记录 topic acceptable 是否命中。
- `retrieval_graph_association_retrieval_06`（strict_rank_miss）：strict gold 未命中 rank 1；同时记录 topic acceptable 是否命中。
- `retrieval_graph_association_retrieval_08`（strict_rank_miss）：strict gold 未命中 rank 1；同时记录 topic acceptable 是否命中。
- `holdout_holdout_retrieval_04`（hard_negative_confusion）：中性 hard negative 进入检索结果。
- `holdout_holdout_retrieval_06`（hard_negative_confusion）：中性 hard negative 进入检索结果。
- `holdout_holdout_retrieval_10`（hard_negative_confusion）：中性 hard negative 进入检索结果。
- `holdout_holdout_retrieval_16`（hard_negative_confusion）：中性 hard negative 进入检索结果。
- `holdout_holdout_retrieval_18`（hard_negative_confusion）：中性 hard negative 进入检索结果。
- `holdout_holdout_retrieval_20`（hard_negative_confusion）：中性 hard negative 进入检索结果。

## 边界说明

本报告按 checked-in 数据与当前生产逻辑如实计分；strict miss、topic acceptable 命中和 hard-negative confusion 分开记录。
公开与 Holdout retrieval 在独立服务、存储与 namespace 中运行；该隔离证明本次评分未共享评测记忆，不代表已覆盖外部训练过程。
MemGuardOS 的精准遗忘保证范围限定在受管 SQLite 元数据、FTS/图谱索引、mmap 段文件 compact 后的受管明文与审计链。
开放世界自然语言、外部 Agent 日志、VM 快照、备份和块设备取证仍属于未覆盖边界。
