# 真实 Agent 交互 Benchmark 报告

本报告由固定 JSONL 数据集和实际 MemGuardOS 代码自动生成。指标同时给出失败样例，不能替代真实 openKylin 演示证据。

## 数据组成

- 总样例数：200
- 来源分布：`{"real_anonymized": 60, "synthetic": 140}`
- 类别分布：`{"correction_exception": 20, "cross_session": 20, "explicit_preference": 40, "implicit_habit": 20, "precise_forgetting": 20, "role_pollution": 30, "sensitive_interaction": 20, "tool_trace": 30}`

## 核心指标

| 指标 | 结果 |
|---|---:|
| 提取 Precision | 1.0000 |
| 提取 Recall | 1.0000 |
| 提取 F1 | 1.0000 |
| 角色污染率 | 0.0000 |
| 敏感泄漏率 | 0.0000 |
| 精准遗忘成功率 | 1.0000 |
| 跨会话遵守率 | 1.0000 |
| 单样例端到端 P95 | 113.966 ms |

## 失败样例与误差归因

本次固定数据集未出现失败样例。该结论仅适用于当前数据集，仍需结合真实 Agent 会话和扩展难例持续验证。

## 口径边界

“real_anonymized”表示来自项目实际开发与演示模式的脱敏重构样例，不代表公开第三方基准。“synthetic”表示为覆盖边界条件而人工构造的可复现样例。所有样例均含双标注字段；当前版本的双标注一致性为数据文件中的记录值，正式提交前仍建议由第二名真实标注者独立复核。
