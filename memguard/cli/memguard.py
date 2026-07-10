from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

from memguard.integrations.events import AgentInteractionEvent
from memguard.service import MemGuardService


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="memguard",
        description="MemGuardOS user-facing memory workspace",
    )
    parser.add_argument("--namespace", default=None, help="记忆空间；默认使用 default。")
    parser.add_argument("--store", default="", help="测试或演示用段文件路径。")
    parser.add_argument("--db", default="", help="测试或演示用 SQLite 路径。")
    parser.add_argument("--workspace", default=os.getcwd(), help="Agent 工作区；默认使用当前目录。")
    parser.add_argument("--model", default=os.environ.get("MEMGUARD_OPENHANDS_MODEL", ""), help="真实 Agent 模型名称（可选）。")
    parser.add_argument("--json", action="store_true", help="输出机器可读 JSON；默认输出适合用户阅读的摘要。")
    sub = parser.add_subparsers(dest="command", required=True)

    sub.add_parser("status", help="查看本机记忆服务状态")
    remember = sub.add_parser("remember", help="写入一条自然语言记忆")
    remember.add_argument("text")
    memory = sub.add_parser("memory", help="查看最近记忆")
    memory.add_argument("--limit", type=int, default=10)
    search = sub.add_parser("search", help="搜索记忆")
    search.add_argument("query")
    context = sub.add_parser("context", help="预览 Agent 可用上下文")
    context.add_argument("task")
    forget = sub.add_parser("forget", help="预览或确认精准遗忘")
    forget.add_argument("request")
    forget.add_argument("--yes", action="store_true", help="确认执行遗忘")
    forget.add_argument("--compact", action="store_true", help="确认后整理存储文件")
    sub.add_parser("evidence", help="导出当前命名空间的轻量复核摘要")
    demo = sub.add_parser("demo", help="运行一轮可读的 Agent 记忆闭环演示")
    demo.add_argument("--task", default="查找精准遗忘的实现位置")
    shell = sub.add_parser("shell", help="进入交互式记忆工作台")
    shell.add_argument("--prompt", default="memguard> ")
    for command_parser in sub.choices.values():
        command_parser.add_argument("--json", dest="command_json", action="store_true", help=argparse.SUPPRESS)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    service = _open_service(args)
    try:
        if _json_requested(args):
            return _run_json(service, args)
        if args.command == "status":
            return _status(service)
        if args.command == "remember":
            return _remember(service, args.text)
        if args.command == "memory":
            return _memory(service, limit=args.limit)
        if args.command == "search":
            return _search(service, args.query)
        if args.command == "context":
            return _context(service, args.task)
        if args.command == "forget":
            return _forget(service, args.request, confirm=args.yes, compact=args.compact)
        if args.command == "evidence":
            return _evidence(service)
        if args.command == "demo":
            return _demo(service, args.task)
        if args.command == "shell":
            return _shell(service, prompt=args.prompt, workspace=args.workspace, model=args.model)
    finally:
        service.close()
    return 1


def _open_service(args: argparse.Namespace) -> MemGuardService:
    kwargs: dict[str, object] = {}
    if args.namespace:
        kwargs["namespace"] = args.namespace
    if args.store:
        kwargs["store_path"] = Path(args.store)
    if args.db:
        kwargs["db_path"] = Path(args.db)
    return MemGuardService(**kwargs)


def _status(service: MemGuardService) -> int:
    namespace = service.namespace_status()
    audit = service.verify_audit()
    retrieval = service.retrieval_status()
    _section("本机状态")
    print("记忆空间：%s" % namespace.get("namespace", service.namespace))
    print("实时记忆：%s 条" % namespace.get("live_record_count", 0))
    print("审计记录：%s 条" % namespace.get("audit_event_count", 0))
    print("审计完整性：%s" % ("正常" if audit.get("ok") else "需检查"))
    print("检索方式：%s" % (retrieval.get("last_backend_used") or retrieval.get("requested_backend") or "自动"))
    print("下一步：输入 `memguard demo` 可体验记忆写入、复用、遗忘预览的完整流程。")
    return 0 if audit.get("ok") else 2


def _remember(service: MemGuardService, text: str) -> int:
    result = service.ingest_text(text, source_kind="user_cli", source_uri="memguard")
    _section("记忆写入")
    if result.get("stored"):
        print("已保存：%s" % result.get("memory_id"))
        print("你可以用 `memguard search 关键词` 或在 Web 控制台查看这条记忆。")
        return 0
    print("未保存：%s" % result.get("reason", "策略未允许"))
    print("处理建议：如果文本包含凭据或高风险信息，系统会阻断长期保存。")
    return 2


def _memory(service: MemGuardService, limit: int = 10) -> int:
    rows = service.list_memories(include_deleted=False)[: max(1, limit)]
    _section("最近记忆")
    if not rows:
        print("暂无可显示的记忆。")
        print("下一步：输入 `memguard remember \"以后查代码优先用 rg\"` 写入一条偏好。")
        return 0
    for index, row in enumerate(rows, 1):
        print("%d. %s" % (index, row.get("summary") or row.get("content") or row.get("memory_id")))
        print("   类型：%s / %s  层级：%s" % (row.get("type", "-"), row.get("subtype", "-"), row.get("tier", "-")))
        print("   ID：%s" % row.get("memory_id", "-"))
    return 0


def _search(service: MemGuardService, query: str) -> int:
    hits = service.search(query, limit=5)
    _section("记忆检索")
    if not hits:
        print("没有找到相关记忆。")
        print("处理建议：换一个更具体的关键词，或先写入一条相关偏好。")
        return 1
    for index, hit in enumerate(hits, 1):
        print("%d. %s" % (index, hit.get("summary") or hit.get("memory_id")))
        print("   分数：%s  来源：%s  ID：%s" % (hit.get("score", "-"), hit.get("match_source", "-"), hit.get("memory_id", "-")))
    return 0


def _context(service: MemGuardService, task: str) -> int:
    context = service.build_context(task, limit=5, token_budget=1200)
    _section("记忆注入")
    memories = context.get("memories", [])
    print("任务：%s" % task)
    print("可用记忆：%d 条" % len(memories))
    for index, memory in enumerate(memories[:5], 1):
        print("%d. %s" % (index, memory.get("summary") or memory.get("memory_id")))
    print("说明：这里只展示 Agent 生成前会被纳入的受控上下文，不包含敏感原文。")
    return 0


def _forget(service: MemGuardService, request: str, confirm: bool = False, compact: bool = False) -> int:
    result = service.forget(request, confirm=confirm, compact=compact or confirm)
    _section("精准遗忘")
    if confirm:
        print("已执行遗忘。")
        print("影响摘要：%s" % _compact_json(result.get("summary", result)))
        print("建议：执行后可用 `memguard search 关键词` 和 `memguard evidence` 复核。")
        return 0
    print("影响范围预览")
    candidates = result.get("candidates", [])
    if candidates:
        for index, item in enumerate(candidates, 1):
            print("%d. %s" % (index, item.get("summary") or item.get("memory_id")))
            print("   ID：%s  分数：%s" % (item.get("memory_id", "-"), item.get("score", "-")))
    else:
        print("没有发现候选。")
    print("确认执行请追加 --yes；建议先在 Web 控制台查看候选卡片。")
    return 0


def _evidence(service: MemGuardService) -> int:
    namespace = service.namespace_status()
    audit = service.verify_audit()
    agent = service.agent_dashboard_snapshot(limit=20)
    _section("轻量复核摘要")
    print("记忆空间：%s" % namespace.get("namespace", service.namespace))
    print("实时记忆：%s 条" % namespace.get("live_record_count", 0))
    print("审计完整性：%s" % ("正常" if audit.get("ok") else "需检查"))
    print("Agent 交互事件：%s 条" % agent.get("summary", {}).get("event_count", 0))
    print("稳定偏好：%s 条" % agent.get("summary", {}).get("stable_preference_count", 0))
    print("建议：最终验收请打开 Web 控制台的“复核中心”逐项运行并保存截图。")
    return 0 if audit.get("ok") else 2


def _preferences(service: MemGuardService) -> int:
    status = service.interaction_status()
    _section("当前生效偏好")
    stable = [item for item in status.get("preference_aggregates", []) if item.get("stable")]
    if not stable:
        print("暂无稳定偏好。")
        print("下一步：在 Agent 工作台运行一次任务，或输入自然语言说明你的使用习惯。")
        return 0
    for index, item in enumerate(stable, 1):
        print("%d. %s = %s" % (index, item.get("topic", ""), item.get("value", "")))
        print("   作用范围：%s · 依据：%s 条 · 会话：%s 个" % (
            item.get("preference_scope", "global"),
            item.get("evidence_count", 0),
            item.get("distinct_sessions", 0),
        ))
    print("下一步：输入 `memguard context 任务` 查看这些偏好会进入哪一阶段。")
    return 0


def _audit_readable(service: MemGuardService) -> int:
    rows = service.audit(limit=12)
    _section("最近操作记录")
    if not rows:
        print("暂无操作记录。")
        return 0
    labels = {
        "ingest": "新增记忆",
        "sensitive_block": "敏感内容保护",
        "forget": "遗忘操作",
        "agent_forget": "Agent 记忆遗忘",
        "agent_context_build": "Agent 上下文构建",
        "preference_select": "偏好选择",
    }
    for row in rows:
        event_type = str(row.get("event_type") or "")
        print("- %s：%s" % (labels.get(event_type, event_type or "系统操作"), row.get("created_at", "")))
    print("说明：完整哈希链和受管明细请在管理员视图或 `memguard evidence --json` 中复核。")
    return 0


def _demo_result(service: MemGuardService, task: str) -> dict[str, object]:
    replay = service.run_feedback_replay(task, mode="replay")
    session_id = str(replay.get("session_id") or "")
    sensitive = service.ingest_text(
        "password=MemGuardOS-demo-secret，仅用于验证敏感信息阻断。",
        source_kind="agent",
        source_uri=session_id,
    )
    forget_preview = service.forget_agent_interactions(
        {"agent_name": "openhands", "session_id": session_id, "preference_topic": "code_search_tool"},
        confirm=False,
    )
    forget_result = service.forget_agent_interactions(
        {"agent_name": "openhands", "session_id": session_id, "preference_topic": "code_search_tool"},
        confirm=True,
        compact=True,
    )
    return {
        "execution_mode": "replay",
        "task": task,
        "replay": replay,
        "sensitive_notice": sensitive.get("sensitive_notice"),
        "forget_preview": forget_preview,
        "forget_result": forget_result,
        "audit": service.verify_audit(),
        "truthful_note": "本流程使用可复现的本地事件回放，不代表外部模型独立完成了任意任务；真实 OpenHands 需单独运行并留存原始证据。",
    }


def _demo(service: MemGuardService, task: str) -> int:
    result = _demo_result(service, task)
    replay = result["replay"]
    context = replay.get("context", {}).get("tool_selection", {})
    proofs = replay.get("preference_effect_proofs", [])
    forget_preview = result["forget_preview"]
    forget_result = result["forget_result"]

    _section("记忆注入")
    print("任务：%s" % task)
    print("工具选择阶段获得记忆：%d 条" % len(context.get("memories", [])))
    for memory in context.get("memories", [])[:3]:
        print("- %s" % (memory.get("preference_topic") or memory.get("summary") or memory.get("memory_id")))

    _section("工具调用")
    print("命令：rg -n \"MemGuardService\" memguard")
    print("结果：本地回放记录了工具调用和成功结果；这不是外部模型的实时执行证明。")

    _section("新增记忆")
    for item in replay.get("memory_changes", []):
        print("- %s" % (item.get("summary_for_user") or item.get("memory_id")))
    if not replay.get("memory_changes"):
        print("本次没有新增可显示记忆。")

    _section("偏好生效证明")
    for proof in proofs:
        print("- %s = %s：%s" % (
            proof.get("preference", {}).get("topic", ""),
            proof.get("preference", {}).get("value", ""),
            {"passed": "已观察到并通过", "failed": "未遵循", "not_applicable": "暂无实际行为证据"}.get(proof.get("compliance"), proof.get("compliance")),
        ))

    _section("敏感内容处理")
    print((result.get("sensitive_notice") or {}).get("user_summary", "本次没有触发敏感内容保护。"))

    _section("遗忘验证")
    affected = forget_preview.get("affected", {})
    print("影响范围预览：事件 %d 条，记忆 %d 条。" % (len(affected.get("event_ids", [])), len(affected.get("memory_ids", []))))
    report = forget_result.get("verification_report", {})
    for step in report.get("verification_steps", []):
        print("- %s：%s" % (step.get("name", ""), {"passed": "已通过", "failed": "需要处理", "skipped": "未执行"}.get(step.get("status"), step.get("status"))))
    print("说明：%s" % report.get("user_summary", ""))
    print("本流程模式：%s。%s" % (result.get("execution_mode"), result.get("truthful_note")))
    return 0


def _json_requested(args: argparse.Namespace) -> bool:
    return bool(getattr(args, "json", False) or getattr(args, "command_json", False))


def _run_json(service: MemGuardService, args: argparse.Namespace) -> int:
    if args.command == "shell":
        print(json.dumps({"ok": False, "error": "interactive_shell_does_not_support_json"}, ensure_ascii=False))
        return 2
    if args.command == "status":
        value = {
            "namespace": service.namespace_status(),
            "audit": service.verify_audit(),
            "retrieval": service.retrieval_status(),
        }
    elif args.command == "remember":
        result = service.ingest_text(args.text, source_kind="user_cli", source_uri="memguard")
        record = result.get("record")
        value = {
            "stored": bool(result.get("stored")),
            "memory_id": getattr(record, "memory_id", ""),
            "feedback": result.get("feedback"),
            "sensitive_notice": result.get("sensitive_notice"),
            "reason": result.get("reason", ""),
        }
    elif args.command == "memory":
        value = service.list_memories(include_deleted=False)[: max(1, args.limit)]
    elif args.command == "search":
        value = service.search(args.query, limit=5)
    elif args.command == "context":
        value = service.build_context(args.task, limit=5, token_budget=1200)
    elif args.command == "forget":
        value = service.forget(args.request, confirm=args.yes, compact=args.compact or args.yes)
    elif args.command == "evidence":
        value = {
            "namespace": service.namespace_status(),
            "audit": service.verify_audit(),
            "agent": service.agent_dashboard_snapshot(limit=20),
            "notifications": service.recent_notifications(limit=20),
        }
    elif args.command == "demo":
        value = _demo_result(service, args.task)
    else:
        value = {"ok": False, "error": "unsupported_json_command", "command": args.command}
    print(json.dumps(value, ensure_ascii=False, indent=2, default=_json_default))
    if isinstance(value, dict) and value.get("ok") is False:
        return 2
    if args.command == "status" and not value["audit"].get("ok"):
        return 2
    return 0


def _json_default(value: object) -> object:
    if hasattr(value, "to_dict"):
        return value.to_dict()
    if hasattr(value, "__dict__"):
        return value.__dict__
    return str(value)


def _shell(service: MemGuardService, prompt: str, workspace: str = "", model: str = "") -> int:
    _section("MemGuardOS 记忆工作台")
    state = {"workspace": str(Path(workspace or Path.cwd()).expanduser()), "model": str(model or "未配置")}
    print("输入自然语言会写入记忆；内置命令：/help /status /memory /preferences /search 关键词 /context 任务 /forget 请求 /audit /evidence /demo /model /workspace /exit")
    while True:
        try:
            line = input(prompt).strip()
        except EOFError:
            print()
            return 0
        if not line:
            continue
        if line in {"/exit", "exit", "quit"}:
            return 0
        if line == "/help":
            print("可用操作：/status /memory /preferences /search 关键词 /context 任务 /forget 请求 /audit /evidence /demo /model /workspace /exit")
            continue
        if line == "/model":
            print("当前模型：%s" % state["model"])
            print("设置方式：输入 `/model 模型名称`；真实 OpenHands 仍需要可用的模型服务和 API 配置。")
            continue
        if line.startswith("/model "):
            state["model"] = line.removeprefix("/model ").strip() or "未配置"
            print("已设置当前模型：%s" % state["model"])
            continue
        if line == "/workspace":
            print("当前工作区：%s" % state["workspace"])
            continue
        if line.startswith("/workspace "):
            candidate = Path(line.removeprefix("/workspace ").strip()).expanduser()
            state["workspace"] = str(candidate.resolve())
            print("已设置工作区：%s" % state["workspace"])
            continue
        if line == "/preferences":
            return_code = _preferences(service)
        elif line == "/audit":
            return_code = _audit_readable(service)
        elif line == "/demo":
            return_code = _demo(service, "查找精准遗忘的实现位置")
        else:
            return_code = _run_shell_line(service, line)
        if return_code not in {0, 1}:
            return return_code


def _run_shell_line(service: MemGuardService, line: str) -> int:
    if line == "/status":
        return _status(service)
    if line == "/memory":
        return _memory(service)
    if line == "/preferences":
        return _preferences(service)
    if line.startswith("/search "):
        return _search(service, line.removeprefix("/search ").strip())
    if line.startswith("/context "):
        return _context(service, line.removeprefix("/context ").strip())
    if line.startswith("/forget "):
        return _forget(service, line.removeprefix("/forget ").strip(), confirm=False)
    if line == "/evidence":
        return _evidence(service)
    return _remember(service, line)


def _ingest_demo_events(service: MemGuardService, session_id: str, task: str) -> None:
    events = [
        AgentInteractionEvent(
            agent_name="openhands",
            agent_version="demo",
            session_id=session_id,
            turn_id="turn-1",
            sequence=1,
            event_type="user_message",
            role="user",
            content="以后查代码优先使用 rg。%s" % task,
            task_type="code_search",
        ),
        AgentInteractionEvent(
            agent_name="openhands",
            agent_version="demo",
            session_id=session_id,
            turn_id="turn-1",
            sequence=2,
            event_type="tool_call",
            role="assistant",
            tool_name="terminal",
            tool_args={"command": 'rg -n "forget_agent_interactions" memguard'},
            state_before="需要查找精准遗忘实现位置",
            task_type="code_search",
        ),
        AgentInteractionEvent(
            agent_name="openhands",
            agent_version="demo",
            session_id=session_id,
            turn_id="turn-1",
            sequence=3,
            event_type="tool_result",
            role="tool",
            tool_name="terminal",
            tool_result="memguard/service.py:2098:def forget_agent_interactions(",
            exit_code=0,
            state_after="rg 成功找到目标定义",
            task_type="code_search",
        ),
    ]
    for event in events:
        service.ingest_interaction(event)


def _section(title: str) -> None:
    print("\n## %s" % title)


def _compact_json(value: object) -> str:
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"))


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
