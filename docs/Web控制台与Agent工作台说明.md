# Web 控制台与 Agent 工作台说明

## 页面组织

控制台分为普通用户和管理员两层。普通用户看到个人工作台、记忆写入、记忆检索、上下文、Agent 工作台、偏好生效、记忆详情、遗忘验证和通知中心；管理员看到审计记录、系统维护和本机验收复核。

## Agent 工作台

任务输入、会话、工作区和 Agent 选择位于同一操作区。运行方式必须显式选择：本地回放用于确定性复核，只读预览用于查看上下文，真实 OpenHands 需要外部模型。结果区域按“注入记忆、工具调用、工具结果、偏好证明、新增记忆、敏感阻断、遗忘建议”排列，并提供高级回执折叠区。

## 记忆详情与偏好证明

记忆详情展示摘要、类型、作用域、层级、来源事件、版本和图谱关系。偏好证明只在实际行为可观察时判定通过：`code_search_tool=rg` 检查工具命令是否真正包含 `rg`；`response_order=command_then_explanation` 检查回答顺序；`high_risk_confirmation=required` 检查是否出现阻断或确认要求。

## 遗忘验证

遗忘先 dry-run，再确认执行。验证报告使用统一结构，逐项显示主记录、Agent 事件载荷、SQLite/FTS、图谱关系、检索缓存、段文件整理、明文探针、Agent 上下文缺失和审计链。外部日志和备份边界始终保留。

## 接口

主要接口为 `/api/feedback-home`、`/api/notifications`、`/api/agent-run`、`/api/memory-detail`、`/api/preference-proof`、`/api/forget-memory`。所有 API 仍受本地控制台角色授权、请求大小限制和安全响应头保护。
