# MemGuardOS EvidenceBench v2

EvidenceBench v2 是 MemGuardOS 的抗质疑评测集，用于补充原有功能正确性 benchmark。
它覆盖 hard negative、holdout、跨语言、冲突、自然语言精准遗忘、敏感治理和 openKylin OS Agent 场景。

所有敏感信息均为合成样例，不包含真实 API Key、真实个人信息或私有竞品数据。
holdout 目录至少占全量样例 20%，用于说明规则调优与最终复验之间的隔离口径。

运行方式：`python scripts/run_evidencebench_v2.py --output docs/eval_v2_result.json --markdown docs/eval_v2_report.md`。
