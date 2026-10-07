# 流程记忆与来源撤回验证

本目录保存 2026 年 10 月 6 日的实机运行证据。核心界面、1751 项全量回归和 137 项 CLI 回归属于 V6（`1.0.1-6+finals20261006`）；SQL 安全与交付修订属于 V7，openKylin 五包版本为 `1.0.1-7+finals20261006`。两批结果分别标注，不用 V6 结果代替 V7 复测。

| 文件 | 验证内容 | 结果 |
| --- | --- | --- |
| `pytest_openkylin.txt` | V6：openKylin 源码全量回归 | 1751 通过，3 项平台条件跳过 |
| `cli_interfaces.txt` | V6：CLI、演示入口与流程接口同组回归 | 137 项通过 |
| `web_ui.json` | V6：openKylin 安装态核心界面的来源更新与派生遗忘 | 1600/390 两种视口；页面脚本无错误，来源撤回同时移除派生流程 |
| `procedures_installed.json` | 安装态流程学习、三种文件参数、失败与成功检查、来源更新、撤回清理及审计复跑 | 23 项再次通过；实际加载路径与 SHA-256 见 `modules`，复跑时间见 `generated_at` |
| `daily_scenarios_installed.json` | 既有安装态偏好复用、纠正、事实冲突、临时记忆、密码拒存及单项遗忘 | 17 项通过，不作为 V7 全量复测 |
| `forgetting_v2.json` | 未修改数据清单的 EvidenceBench v2 遗忘专项 | 60 条请求、240 条候选（预期删除 120、保留 120）；精确率、召回率和保留成功率均为 1.0，受管残留率为 0 |
| `sql_security_tests.txt` | V7：openKylin SQL 安全、生命周期与遗忘针对性回归 | 87 项通过 |
| `security_summary.json` | V7：Bandit 与运行时依赖 pip audit | 0 高危、0 中危、119 低危；runtime 范围 0 个已知漏洞，未放宽安全门禁 |
| `normal_mode.json` | V7：最终普通模式启动、五包版本、服务状态及 11 个源码文件的校验 | 普通模式启动通过，两项服务 active，五包版本一致，11 个核心源码匹配 |
| `package_payload_normal_v7.json` | V7：普通模式下逐项比对安装归档与提交源码 | 146 个安装文件匹配；70 个必需源码与提交 `53f5eaa` 一致 |
| `runtime_revocation.json` | V7：真实独立 PreTool Hook 进程检查 | 来源更新前 allow，更新后 deny；新上下文不再选择失效流程，无模型请求或工具执行 |
| `trace_export_tests.txt` | 离线运行记录阅读器与打包契约实机回归 | openKylin 112 项通过；阅读器 Windows 62 项通过、1 项符号链接条件跳过 |

V6 全量回归后，CLI 上下文预览直接调用 Agent 的分阶段上下文构建，加入阶段、Agent、任务类型、项目与会话条件；Windows 和 openKylin 同组回归均为 137 项通过，实机输出见 `cli_interfaces.txt`。1751、137、87 等测试集合存在交叠，通过数不相加；V7 的 87 项也不等同于再次完成全量回归。

`normal_mode.json` 记录实际重启时间、normal 启动模式、五包版本、服务状态和加载模块路径。`source_match_normalized_newlines` 是源码一致性布尔结果，归档和提交的 SHA-256 则保存在载荷报告及各自清单中。安装态 23 项流程验证已在这次普通模式重启后再次通过。

提交 `53f5eaa` 的 [GitHub CI](https://github.com/Alexd-star/memguardos/actions/runs/37399090364) 已全部通过：Python 3.10、3.11、3.12 的全量测试、安全扫描、Web 冒烟、评测冒烟和提交包复现均完成。旧运行中的安全扫描失败保留在对应记录中。

首次回归保留了失败结果。发现的毫秒级截止时钟、取消写入的正文残留、来源范围扩大、反馈信任状态竞态及 Web 详情残留均已修复并补充回归。旧数据库测试的造库步骤也随新增触发器更新：先移除当年不存在的派生触发器，再模拟缺列，升级后确认列与触发器重新建立。

流程实验使用实际产品 API 和本地文件条件。文件准备由脚本执行，没有调用外部模型；这些结果证明流程可被验证、复用与撤回，不作为自主 Agent 完整任务成功率。遗忘数据集是项目的固定公开与留出样例，指标不等同于任意自然语言的正确率。Web 截图由 Windows 浏览器经 SSH 转发访问 openKylin 实际安装服务取得，临时记忆与正式资料隔离。

PPT 引用时注明证据文件、版本、测试范围及报告时间；核心界面截图按 V6 标注，V7 使用实际安全与安装态复跑报告，不将固定样例数量夸大为外部真实任务规模。

新增 `scripts/render_agent_trace.py` 将已有逐案例结果导出为离线 HTML，支持原始对话、工具输出、失败状态与适配器执行来源。相关浏览器排版检查使用明确标注的合成夹具，1600 和 390 两种视口无溢出或脚本错误；这些检查画面不纳入真实 Agent 展示素材。阅读器不调用模型，也不重新评分。它会核对实际 `run.memory_enabled`，不将旧套件只跳过历史初始化的 off 侧标为“完全关闭记忆”。

复跑流程验证：

```bash
cd /var/tmp
PYTHONPATH=/opt/memguardos python3 /实际源码路径/scripts/verify_procedure_showcase.py \
  --installed --output /实际输出路径/procedures.json
```

项目内的 `scripts/verify_finals_scenarios.py` 提供日常记忆管理链路复跑。软件包安装使用现有五包安装器及官方维护模式，安装器校验包版本和归档载荷，不以安装命令退出码代替实际安装确认。
