# Kylin AI SDK 集成证据

日期：2026-05-22

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
python3 -m memguard.cli.memctl sdk-status --probe-embedding --probe-timeout 180
```

openKylin 验证结果：

```json
{
  "embedding_probe": {
    "ok": true,
    "dim": 768,
    "sample": [-0.086721, -0.023629, -0.007548, 0.027401, 0.011692]
  },
  "inference_ready": true,
  "ready_for_default_retrieval": true
}
```

当前结论：

- SDK 动态库、头文件、运行时和端侧模型元数据可用。
- `scripts/fix_kylin_embedding_runtime.sh` 可桥接 openKylin 模型仓库、重启 kytensor/runtime、等待 socket，并避免重复 runtime 进程。
- `sdk-status --probe-embedding` 已改为子进程隔离执行，并支持 `--probe-timeout`，避免 SDK 卡住主 CLI。
- `sdk-status` 现在把 `available` 与 `inference_ready` 分开表达：`available=true` 表示 SDK 元数据路径可达，只有真实向量调用成功才是 `inference_ready=true`。
- `scripts/diagnose_kylin_embedding.sh` 用于采集 runtime、socket、插件、包版本、动态符号、用户服务日志和 session 初始化返回码。
- `scripts/kylin_embedding_probe.cpp` 与 `scripts/run_kylin_embedding_probe.sh` 按官方 C API 风格直接调用文本向量化 SDK，可排除 Python `ctypes` 适配层因素。
- Python 探针和 C++ 探针均已返回 768 维向量。
- 因此 MemGuardOS 保留高性能 FTS/hash/graph 作为默认检索路径，同时提供 Kylin embedding 可选增强后端和安全回退机制。最新 openKylin 证据中，`MEMGUARD_EMBEDDING_BACKEND=auto` 的实际检索返回 `vector_backend: "kylin_sdk"`，说明 SDK 已进入可选检索重排链路。

实际检索验证命令：

```bash
MEMGUARD_EMBEDDING_BACKEND=auto MEMGUARD_KYLIN_EMBEDDING_TIMEOUT=90 \
  python3 -m memguard.cli.memctl search "openKylin 服务内存限制"
```

证据文件：`docs/evidence/latest/kylin_retrieval.txt`。

## 2. 文本向量化 C++ 探针

MemGuardOS 提供官方 C API 风格的最小 C++ 探针：

- 源码：`scripts/kylin_embedding_probe.cpp`
- 运行脚本：`scripts/run_kylin_embedding_probe.sh`

命令：

```bash
bash scripts/run_kylin_embedding_probe.sh "openKylin agent memory"
```

验证内容：

1. 创建 `TextEmbeddingSession`。
2. 调用 `text_embedding_init_session()` 并打印初始化返回码。
3. 调用 `text_embedding_get_model_info()` 读取模型元数据。
4. 调用 `text_embedding()` 做真实文本向量化。
5. 打印 error code、error message、向量长度和前 5 个向量样本。
6. 调用 `embedding_result_destroy()` 和 `text_embedding_destroy_session()` 释放资源。

该探针的作用不是替代默认检索路径，而是作为专项定位工具和 SDK 真实推理证据。当前 VM 中 Python 探针和 C++ 探针均已返回 768 维向量；如果在其他环境中探针返回 `error_code=3` 或没有返回向量，则优先检查 kytensor 模型仓库、openKylin AI runtime、socket、ondevice 插件、模型加载或 VM 资源环境。

## 3. 向量数据库 SDK

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

## 4. 真实向量搜索探针

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

## 5. 工程决策

比赛版本采用两层策略：

1. **默认检索路径**：SQLite FTS + 轻量 hash-vector 重排 + 图谱扩展。该路径已在 10k 记忆下保持 P95 < 500 ms。
2. **openKylin SDK 路径**：SDK 状态、文本 embedding 真实推理探针、实际可选检索重排和向量数据库真实搜索探针已在当前 openKylin VM 验证。Kylin embedding 可作为可选重排后端启用，但不替代默认稳定路径。

诊断命令：

```bash
python3 -m memguard.cli.memctl sdk-status
python3 -m memguard.cli.memctl sdk-status --probe-embedding --probe-timeout 180
bash scripts/fix_kylin_embedding_runtime.sh --probe-timeout 180
bash scripts/diagnose_kylin_embedding.sh --no-probe
bash scripts/diagnose_kylin_embedding.sh --restart-runtime --probe-timeout 180
bash scripts/run_kylin_embedding_probe.sh "openKylin agent memory"
```

这样可以同时获得：

- openKylin 原生 SDK 适配证据；
- 主检索链路的稳定性能和可复现性；
- 对 SDK 环境依赖和默认回退策略的诚实说明。
