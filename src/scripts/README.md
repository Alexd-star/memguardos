# MemGuardOS 脚本索引

本目录保存安装、构建、评测、实机集成和发布验收所需的稳定脚本。现有文件名和路径同时被 Debian 打包、CI、正式文档及 openKylin 证据命令引用，因此发布版不对脚本进行破坏性搬迁；需要完成某项任务时，可先按下表找到对应入口。

| 任务 | 推荐入口 | 说明 |
|---|---|---|
| openKylin 安装与同步 | `install_openkylin.sh`、`deploy_to_openkylin.sh` | 安装核心服务与原生组件，或把当前工作树同步到实机 |
| Debian 分包构建与验证 | `build_deb_packages.py`、`install_deb_package_set.sh`、`verify_deb_package_set.sh` | 构建核心、桌面、Agent 桥接、文档和聚合包并验证依赖关系 |
| UKUI、Peony 与托盘 | `build_ukui_plugins.sh`、`install_ukui_plugins.sh`、`refresh_ukui_desktop.sh` | 构建、安装和刷新 openKylin 桌面组件 |
| Kylin SDK | `diagnose_kylin_embedding.sh`、`warmup_kylin_sdk.sh`、`run_kylin_embedding_probe.sh` | 检查文本向量 SDK、执行预热并采集真实推理结果 |
| Agent 接入与演示 | `run_openhands_memguard_demo.py`、`run_openhands_behavior_suite.py`、`memguard-agent-showcase.sh` | 运行真实 OpenHands 任务、配对场景和现场演示流程 |
| 功能与性能评测 | `run_evidencebench_v2.py`、`evaluate_memory_value.py`、`evaluate_retrieval_v2.py` | 复验抽取、检索、遗忘、记忆价值和性能指标 |
| 流程经验与来源撤回验证 | `verify_procedure_showcase.py` | 临时工作区中复验实际完成检查、参数迁移、派生失效、选择性撤回和反馈去重；不调用模型 |
| 安全与系统机制 | `install_auditd_rules.sh`、`generate_bpf_lsm_policy.py`、`run_bpf_lsm_enforcer.sh`、`run_resource_control.py` | 配置审计、生成路径策略、验证 BPF LSM 与资源控制 |
| 快速质量检查 | `check_competition_ready.py`、`check_metric_consistency.py`、`check_docs_links.py` | 检查发布门禁、指标一致性和文档链接 |
| 实机证据采集 | `collect_openkylin_evidence.sh`、`collect_openkylin_native_evidence.sh`、`write_evidence_manifest.py` | 采集系统、服务、原生组件和结果清单 |
| 发布与交付 | `release_preflight.py`、`package_submission.py`、`inspect_submission_package.py` | 执行发布前检查、生成提交包并复核归档内容 |

日常使用优先调用安装后的 `memguard`、`memctl` 和 `memguardd`，无需直接执行本目录中的专项脚本。完整参数和执行顺序见 `docs/installation.md`、`docs/testing.md` 与 `docs/developer-guide.md`。
