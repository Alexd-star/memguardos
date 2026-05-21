# openKylin 证据采集包

采集时间：20260522_035724

## 文件说明

| 文件 | 内容 |
|---|---|
| `environment.txt` | OS、内核、Python、SQLite、项目解释器等基础环境 |
| `doctor.txt` | MemGuardOS doctor 输出 |
| `pytest.txt` | 单元测试输出 |
| `eval_1k.txt` | 1k 负载评测命令输出 |
| `eval_result_openkylin.json` | 1k 负载评测 JSON |
| `eval_report_openkylin.md` | 1k 负载评测 Markdown |
| `sdk_status.txt` | Kylin SDK 状态 |
| `sdk_embedding_probe.txt` | Kylin embedding 推理探测 |
| `embedding_cpp_probe.txt` | Kylin embedding SDK 官方 C API 风格 C++ 探针 |
| `kylin_embedding_diag/` | Kylin embedding runtime、socket、插件、包版本和日志诊断 |
| `vector_probe.txt` | Kylin 向量数据库 SDK 探针 |
| `quality_report.txt` | 输入清洗、格式标准化与质量校验证据 |
| `behavior_history.txt` | shell 用户行为轨迹接入证据 |
| `build_prompt.txt` | RAG 上下文与 LLM prompt 渲染证据 |
| `generate_answer.txt` | 默认关闭的可选 LLM adapter dry-run 证据 |
| `api_auth.txt` | 本地 Agent API token 鉴权证据 |
| `ukui_notify.txt` | UKUI/freedesktop 桌面通知 smoke 证据 |
| `systemd_cgroup.txt` | systemd/cgroup 服务限制证据 |
| `journald.txt` | journald 日志证据 |
| `auditd_rules.txt` | auditd 规则证据 |
| `auditd_ingest.txt` | auditd 真实日志接入证据 |
| `verify_audit.txt` | 审计哈希链验证 |
| `eval_10k.txt` | 10k 规模评测命令输出 |
| `eval_result_openkylin_10k.json` | 10k 规模评测 JSON |
| `eval_report_openkylin_10k.md` | 10k 规模评测 Markdown |
