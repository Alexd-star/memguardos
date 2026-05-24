# openKylin 证据采集包

采集时间：20260523_233923

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
| `kylin_retrieval.txt` | Kylin embedding 作为实际检索重排后端的证据 |
| `kylin_embedding_diag/` | Kylin embedding runtime、socket、插件、包版本和日志诊断 |
| `vector_probe.txt` | Kylin 向量数据库 SDK 探针 |
| `quality_report.txt` | 输入清洗、格式标准化与质量校验证据 |
| `natural_patterns.txt` | 自然语言自定义敏感规则证据 |
| `semantic_extraction.txt` | 可解释混合抽取与可选语义增强入口证据 |
| `behavior_history.txt` | shell 用户行为轨迹接入证据 |
| `build_prompt.txt` | RAG 上下文与 LLM prompt 渲染证据 |
| `generate_answer.txt` | 默认关闭的可选 LLM adapter dry-run 证据 |
| `web_dashboard.txt` | 本地 Web 控制台 smoke 证据 |
| `agent_batch_search.txt` | OS Agent 任务规划、工具调用、结果生成多查询并发检索证据 |
| `api_auth.txt` | 本地 Agent API token 鉴权证据 |
| `namespace_isolation.txt` | namespace 多会话记忆隔离证据 |
| `systemd_namespace.txt` | systemd 模板实例与独立 cgroup 证据 |
| `audit_concurrency.txt` | 并发审计写入与哈希链连续性证据 |
| `ukui_notify.txt` | UKUI/freedesktop 桌面通知 smoke 证据 |
| `ukui_deep_plugins.txt` | Peony/UKUI 控制中心深度插件源码与构建环境探针 |
| `ukui_plugin_build.txt` | Peony 右键插件与 UKUI 控制中心插件真实构建证据 |
| `ukui_plugin_install.txt` | UKUI 插件系统目录安装尝试与只读根文件系统边界 |
| `ukui_plugin_visible.txt` | UKUI 插件安装产物、依赖和人工可见性检查步骤 |
| `ukui_plugin_visible_final.txt` | 当前 Peony/UKUI 插件安装产物、依赖和元数据最终核查 |
| `ukui_peony_visible_manual_confirmation.txt` | Peony 右键菜单项人工截图可见确认记录 |
| `ukui_control_center_visible_manual_confirmation.txt` | UKUI 控制中心入口人工截图可见确认记录 |
| `deb_package.txt` | MemGuardOS Debian 安装包构建、包内容和离线解包 smoke 证据 |
| `bpf_lsm_policy.txt` | BPF LSM JSON denylist 策略生成证据 |
| `bpf_lsm_enforcer.txt` | BPF LSM 强制拦截增强环境、构建和 attach 探针 |
| `systemd_cgroup.txt` | systemd/cgroup 服务限制证据 |
| `journald.txt` | journald 日志证据 |
| `auditd_rules.txt` | auditd 规则证据 |
| `auditd_ingest.txt` | auditd 真实日志接入证据 |
| `verify_audit.txt` | 审计哈希链验证 |
| `memory_policy.txt` | 记忆冷热分层、存储占用、cgroup 状态和淘汰候选证据 |
| `manifest.json` | 证据包文件大小与 SHA256 摘要清单 |
| `SHA256SUMS.txt` | 可由 `sha256sum -c SHA256SUMS.txt` 复核的证据包哈希 |
| `eval_10k.txt` | 10k 规模评测命令输出 |
| `eval_result_openkylin_10k.json` | 10k 规模评测 JSON |
| `eval_report_openkylin_10k.md` | 10k 规模评测 Markdown |
