# MemGuardOS 智能体记忆价值证据

- 采集时间：20260620_131531
- 操作系统：openKylin
- OpenHands 模型：ollama/qwen3:1.7b
- 确定性实验：16 组
- 真实 OpenHands 场景：12 组

`deterministic/` 保存可复现配对评测；`real_agent/` 保存真实 OpenHands
会话、上下文、工具事件、工作区前后清单和逐案例判定。真实失败不会被删除，
记忆系统层与 Agent 行为层分别统计。
