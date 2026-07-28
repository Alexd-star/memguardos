# 性能与消融证据口径说明

本目录保存 MemGuardOS 性能、并发和消融实验的最新证据。正式性能结论以默认端侧检索路径为准：SQLite FTS、轻量 hash-vector、图谱扩展和缓存组合。在该口径下，openKylin 10k 负载 P95/P99 均低于 500 ms。

目录中的 `full_kylin_sdk` 变体是 **Kylin SDK 可选增强消融**，用于记录端侧 768 维 embedding 路径的真实边界。该变体包含首次推理冷启动尾延迟，因此 P99 可能高于默认路径。它不代表默认检索路径违反 500 ms 要求。对外解释时应说明：Kylin SDK 用于可选重排和 openKylin 特色增强，默认主路径保留稳定 fallback。

重新采集后应同步更新本目录清单以及 `docs/testing.md` 中的公开数字，避免历史冷启动数据被误解为当前默认性能。
