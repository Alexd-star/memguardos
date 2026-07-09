from __future__ import annotations

import argparse
import json
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
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    service = _open_service(args)
    try:
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
            return _shell(service, prompt=args.prompt)
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


def _demo(service: MemGuardService, task: str) -> int:
    session_id = "memguard-demo-session"
    _ingest_demo_events(service, session_id=session_id, task=task)
    snapshot = service.agent_dashboard_snapshot(session_id=session_id, task=task)
    context = snapshot.get("contexts", {}).get("tool_selection", {})
    forget_preview = service.forget_agent_interactions(
        {"agent_name": "openhands", "session_id": session_id, "preference_topic": "code_search_tool"},
        confirm=False,
    )

    _section("记忆注入")
    print("任务：%s" % task)
    print("工具选择阶段获得记忆：%d 条" % len(context.get("memories", [])))
    for memory in context.get("memories", [])[:3]:
        print("- %s" % (memory.get("preference_topic") or memory.get("summary") or memory.get("memory_id")))

    _section("工具调用")
    print("$ rg -n \"forget_agent_interactions\" memguard")
    print("结果：找到精准遗忘相关实现。")

    _section("新增记忆")
    for item in snapshot.get("preference_aggregates", []):
        print("- %s = %s（依据 %s 条）" % (item.get("topic"), item.get("value"), item.get("evidence_count")))
    print("- code_search_tool = rg")

    _section("遗忘验证")
    affected = forget_preview.get("affected", {})
    print("影响范围预览：事件 %d 条，记忆 %d 条。" % (len(affected.get("event_ids", [])), len(affected.get("memory_ids", []))))
    print("确认执行可在 Web 控制台“Agent 习惯”页点击“确认遗忘”，或使用 `memguard forget --yes`。")
    return 0


def _shell(service: MemGuardService, prompt: str) -> int:
    _section("MemGuardOS 记忆工作台")
    print("输入自然语言会写入记忆；内置命令：/help /status /memory /search 关键词 /context 任务 /forget 请求 /evidence /exit")
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
        print("可用操作：/status /memory /search 关键词 /context 任务 /forget 请求 /evidence /exit")
        return 0
        code = _run_shell_line(service, line)
        if code not in {0, 1}:
            return code


def _run_shell_line(service: MemGuardService, line: str) -> int:
    if line == "/status":
        return _status(service)
    if line == "/memory":
        return _memory(service)
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
