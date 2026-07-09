# 性能与消融证据口径说明

本目录保存 MemGuardOS 性能、并发和消融实验的最新证据。正式性能结论以默认端侧检索路径为准：SQLite FTS、轻量 hash-vector、图谱扩展和缓存组合。在该口径下，openKylin 10k 负载 P95/P99 均低于 500 ms。

目录中的 `full_kylin_sdk` 变体是 **Kylin SDK 可选增强消融**，用于记录端侧 768 维 embedding 路径的真实边界。该变体包含首次推理冷启动尾延迟，因此 P99 可能高于默认路径。它不代表默认检索路径违反 500 ms 要求，也不应被截取为“MemGuardOS 最新主路径超时”。正式答辩时应说明：Kylin SDK 用于可选重排和 openKylin 特色增强，默认主路径保留稳定 fallback。

如果重新采集证据，应同步更新 `docs/evidence/performance_latest/manifest.json`、`SHA256SUMS.txt`、`docs/测试报告.md` 和 `docs/竞赛就绪自检报告.md`，避免历史冷启动数据被误解为当前默认性能。
