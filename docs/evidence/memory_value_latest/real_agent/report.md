# MemGuardOS 真实 OpenHands 记忆价值评测

本报告记录全部正式运行案例。失败案例不会被删除或择优替换。

- 案例数：12
- 记忆系统层通过率：1.0000
- Agent 行为层通过率：0.7500
- 状态分布：`{"memory_pass_agent_fail": 3, "pass": 9}`
- 失败案例：`["R02", "R04", "R12"]`
- OpenHands 运行时：`{"required_version": "1.28.1", "python_version": "3.12.2", "python_compatible": true, "installed": true, "available": true, "sdk_version": "1.28.1", "tools_version": "1.28.1", "versions_match": true, "symbols": {"LLM": true, "Agent": true, "Conversation": true, "TerminalTool": true}, "install_hint": ""}`

记忆系统层与 Agent 行为层分开统计。本地小模型执行失败不自动等同于记忆检索失败，反之亦然。
