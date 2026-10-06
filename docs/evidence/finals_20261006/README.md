# 流程记忆与来源撤回验证

本批证据对应 2026 年 10 月 6 日的开发工作树。流程报告记录实际导入模块的 SHA-256；openKylin 五包修订为 `1.0.1-6+finals20261006`。与更早批次的数据分开保存，避免用旧结果代替当前实现。

| 文件 | 验证内容 | 结果 |
| --- | --- | --- |
| `pytest_openkylin.txt` | openKylin 源码全量回归 | 1751 通过，3 项平台条件跳过 |
| `procedures_installed.json` | `/opt/memguardos` 安装态流程学习、三种文件参数、失败与成功检查、来源更新、撤回清理及审计 | 23 项通过 |
| `daily_scenarios_installed.json` | 安装态偏好复用、纠正、事实冲突、临时记忆、密码拒存及单项遗忘 | 17 项通过 |
| `forgetting_v2.json` | 未修改数据清单的 EvidenceBench v2 遗忘专项 | 60 条请求、240 条候选；精确率、召回率和保留成功率均为 1.0，受管残留率为 0 |
| `web_ui.json` | openKylin 安装态 Web 页面的来源更新与派生遗忘 | 桌面及移动视口无溢出，页面脚本无错误，来源与派生流程两条记录同时移除 |

全量回归后，对 CLI 的上下文预览做了最后一次接口收口：直接调用 Agent 的分阶段上下文构建，加入阶段、Agent、任务类型、项目与会话条件。Windows 和 openKylin 的 CLI、演示入口与流程接口同组回归均为 137 项通过，原始实机结果另存于 `cli_interfaces.txt`。测试集合存在交叠，通过数不相加。

首次回归保留了失败结果。发现的毫秒级截止时钟、取消写入的正文残留、来源范围扩大、反馈信任状态竞态及 Web 详情残留均已修复并补充回归。旧数据库测试的造库步骤也随新增触发器更新：先移除当年不存在的派生触发器，再模拟缺列，升级后确认列与触发器重新建立。

流程实验使用实际产品 API 和本地文件条件。文件准备由脚本执行，没有调用外部模型；这些结果证明流程可被验证、复用与撤回，不作为自主 Agent 完整任务成功率。遗忘数据集是项目的固定公开与留出样例，指标不等同于任意自然语言的正确率。Web 截图由 Windows 浏览器经 SSH 转发访问 openKylin 实际安装服务取得，临时记忆与正式资料隔离。

复跑流程验证：

```bash
cd /var/tmp
PYTHONPATH=/opt/memguardos python3 /实际源码路径/scripts/verify_procedure_showcase.py \
  --installed --output /实际输出路径/procedures.json
```

项目内的 `scripts/verify_finals_scenarios.py` 提供日常记忆管理链路复跑。软件包安装使用现有五包安装器及官方维护模式，安装器校验包版本和归档载荷，不以安装命令退出码代替实际安装确认。
