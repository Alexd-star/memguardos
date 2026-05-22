# Kylin embedding SDK 诊断包

采集时间：2026-05-22 13:01:59

## 重点判断

- 如果 `sdk_status.txt` 里 `model_info` 有 768 维模型，但 `embedding_probe.txt` 返回 `Timeout was reached`，优先检查 `kytensor_service.txt` 和 `model_repositories.txt`：常见根因是 `kytensor.service` 使用的 `/opt/appdata/kylin-ai/model-repository` 未包含 `ensemble-embd_gte-base_uint8-text` 等模型。
- 如果 `/tmp/.kylin-ai-runtime-unix/1000/core-textembedding.sock` 不存在，优先检查 `kylin-ai-runtime.service` 是否运行。
- 如果 socket 存在但 probe 超时，优先看 `runtime_journal.txt` 中的模型加载、插件加载、ondevice engine 错误。
- 如果插件目录没有 ondevice 相关 so，检查 `libkylin-ondevice-ai-engine-plugin` 或 `libkylin-ondevice-traditional-ai-engine-plugin` 是否安装。

## 文件说明

| 文件 | 内容 |
|---|---|
| `environment.txt` | OS、用户、DBus 和 XDG 环境 |
| `packages.txt` | Kylin AI runtime、kytensor、embedding、ondevice 插件包版本 |
| `kytensor_service.txt` | kytensor/Triton 服务、启动参数和 repository index |
| `model_repositories.txt` | kytensor 运行仓库与系统模型仓库的目录/软链接状态 |
| `runtime_service.txt` | 用户态 `kylin-ai-runtime.service` 状态 |
| `runtime_sockets.txt` | runtime Unix socket 状态 |
| `plugin_dirs.txt` | AI engine 插件目录 |
| `plugin_dependencies.txt` | AI engine 插件动态依赖摘要 |
| `headers_and_symbols.txt` | embedding 头文件和动态符号 |
| `sdk_status.txt` | MemGuardOS SDK 结构化诊断 |
| `embedding_probe.txt` | 真实 embedding 调用探测 |
| `kytensor_journal.txt` | kytensor/Triton 系统服务日志 |
| `runtime_journal.txt` | runtime 用户服务日志 |
| `runtime_file_log.txt` | runtime 文件日志尾部 |
