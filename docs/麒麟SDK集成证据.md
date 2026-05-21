# Kylin AI SDK 集成证据

日期：2026-05-18

赛题鼓励调用 openKylin 系统提供的文本向量化 SDK 和向量数据库 SDK。本文记录 MemGuardOS 当前在 openKylin 上得到的 SDK 证据和工程取舍。

## 1. 文本向量化 SDK

检测到的包和文件：

- `libkylin-coreai-embedding`
- `libkylin-coreai-embedding-dev`
- `libkylin-ondevice-embedding-engine`
- `libkylin-ondevice-embedding-engine-dev`
- 动态库：`libkysdk-coreai-embedding.so.1`
- 头文件：`/usr/include/kylin-ai/coreai/embedding/embedding.h`

验证命令：

```bash
python3 -m memguard.cli.memctl sdk-status
```

当前 openKylin 证据：

```json
{
  "embedding": {
    "library_loadable": true,
    "model_info": {
      "dim": 768,
      "name": "ensemble-embd_gte-base_uint8-text",
      "ondevice": true
    },
    "available": true,
    "fallback": "hash_embedding"
  }
}
```

真实推理探测命令：

```bash
python3 -m memguard.cli.memctl sdk-status --probe-embedding
```

当前 VM 结果：

```json
{
  "embedding_probe": {
    "ok": false,
    "error_code": 3,
    "error_message": "Timeout was reached",
    "length": 0
  }
}
```

解释：

- SDK 动态库、头文件、运行时和端侧模型元数据可用。
- 当前 VM 环境中真实推理调用超时。
- 复查 `/usr/include/kylin-ai/coreai/embedding/embedding.h` 后，当前 ctypes 同步接口签名与头文件基本一致；超时位置在 `text_embedding(session, text, &result)`，不是库加载或模型信息读取阶段。
- 本轮复测中，外层 `timeout 35` 只看到 `Proxy init engine sessionId` 日志，未在 35 秒内返回向量，说明更像 SDK 代理、DBus、runtime engine 或 on-device embedding engine 调度超时。
- 因此 MemGuardOS 保留高性能 FTS/hash fallback 作为默认检索路径，同时提供 SDK 检测和安全探测命令。
- 这避免过度宣称：当前证明的是 SDK 可检测、模型可见；只有推理成功后，才宣称主检索由真实 embedding 驱动。

## 2. 向量数据库 SDK

检测到的包和文件：

- `kylin-ai-vector-engine`
- `libkysdk-vector-engine-client`
- `libkysdk-vector-engine-client-dev`
- 动态库：`libkysdk-vector-engine-client.so.1`
- 头文件：`/usr/include/kysdk-vector-engine-client/Database.h`
- 用户服务：`kylin-ai-vector-engine.service`
- socket：`/tmp/kylin-ai-vector-engine-1000.sock`

验证命令：

```bash
python3 -m memguard.cli.memctl sdk-status
```

当前 openKylin 证据：

```json
{
  "vector_store": {
    "library_loadable": true,
    "user_service": {
      "active": true,
      "state": "active"
    },
    "socket_exists": true,
    "available": true
  }
}
```

## 3. 真实向量搜索探针

MemGuardOS 提供最小 C++ 探针，直接调用 Kylin 向量数据库 SDK：

- 源码：`scripts/kylin_vector_probe.cpp`
- 运行脚本：`scripts/run_kylin_vector_probe.sh`

命令：

```bash
bash scripts/run_kylin_vector_probe.sh "$(pwd)"
```

验证内容：

1. 连接 Kylin vector engine。
2. 创建测试集合。
3. 插入两个 float 向量。
4. 执行余弦相似度搜索。
5. 删除测试集合。

当前 openKylin 结果：

```text
connect: code=0 ok=true msg=OK
has_before: code=0 ok=true msg=
create: code=0 ok=true msg=OK
insert: code=0 ok=true msg=
load: code=0 ok=true msg=
add_vector: code=0 ok=true msg=OK
search: code=0 ok=true msg=
hit id=1 score=1
hit id=2 score=0
cleanup_drop: code=0 ok=true msg=
```

## 4. 工程决策

比赛版本采用两层策略：

1. **默认检索路径**：SQLite FTS + 轻量 hash-vector 重排 + 图谱扩展。该路径已在 10k 记忆下保持 P95 < 500 ms。
2. **openKylin SDK 路径**：SDK 状态和向量数据库真实搜索探针已在 openKylin 验证。文本 embedding 推理超时解决后，再启用完整 embedding-backed retrieval。

这样可以同时获得：

- openKylin 原生 SDK 适配证据；
- 主检索链路的稳定性能和可复现性；
- 对当前 SDK 限制的诚实说明。
