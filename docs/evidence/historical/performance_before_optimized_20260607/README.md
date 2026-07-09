# 历史性能证据归档

本目录保存优化前的 openKylin 10k 评测文件，仅用于追溯性能优化过程，不作为最终验收口径。

归档原因：原 `docs/evidence/latest/eval_result_openkylin_10k.json` 中并发检索 P95 为 654.302 ms，超过 500 ms 目标。后续已通过批量检索连接复用、读路径审计收敛和候选生成预算化优化，将最新 `docs/evidence/latest/eval_result_openkylin_10k.json` 的并发检索 P95 降至 139.264 ms。

正式提交、答辩和验收时应引用：

- `docs/evidence/latest/eval_result_openkylin_10k.json`
- `docs/evidence/latest/eval_report_openkylin_10k.md`
- `docs/evidence/performance_latest/eval_result_openkylin_10k_optimized.json`
- `docs/evidence/performance_latest/performance_suite_openkylin.json`

本目录中的 `*_before_optimized.*` 文件不得作为当前性能能力证明。
