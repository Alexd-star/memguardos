from __future__ import annotations

import argparse
import json
import os
import re
import sys
from pathlib import Path

from memguard.api.server import handle_request
from memguard.feedback import memory_summary_for_user
from memguard.natural_language import interpret_user_input, looks_like_forget_request
from memguard.service import MemGuardService


_ANSI_RESET = "\033[0m"
_ANSI_BLUE = "\033[1;34m"
_ANSI_CYAN = "\033[1;36m"
_ANSI_GREEN = "\033[1;32m"
_ANSI_YELLOW = "\033[1;33m"
_ANSI_RED = "\033[1;31m"
_ANSI_DIM = "\033[2m"


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="memguard",
        description="MemGuardOS user-facing memory workspace",
    )
    parser.add_argument("--namespace", default=None, help="记忆空间；默认使用 default。")
    parser.add_argument("--session-id", default=os.environ.get("MEMGUARD_SESSION_ID", ""), help="临时记忆所属会话；可通过环境变量 MEMGUARD_SESSION_ID 设置。")
    parser.add_argument("--store", default="", help="测试或演示用段文件路径。")
    parser.add_argument("--db", default="", help="测试或演示用 SQLite 路径。")
    parser.add_argument("--workspace", default=os.getcwd(), help="Agent 工作区；默认使用当前目录。")
    parser.add_argument(
        "--model",
        default=_user_setting("MEMGUARD_OPENHANDS_MODEL", "openhands_model"),
        help="真实 Agent 模型名称（可选）。",
    )
    parser.add_argument("--json", action="store_true", help="输出机器可读 JSON；默认输出适合用户阅读的摘要。")
    sub = parser.add_subparsers(dest="command", required=True)

    sub.add_parser("status", help="查看本机记忆服务状态")
    remember = sub.add_parser("remember", help="写入一条自然语言记忆")
    remember.add_argument("text")
    memory = sub.add_parser("memory", help="查看最近记忆")
    memory.add_argument("--limit", type=int, default=10)
    search = sub.add_parser("search", help="搜索记忆")
    search.add_argument("query")
    search.add_argument("--limit", type=int, default=5, help="返回结果条数")
    context = sub.add_parser("context", help="预览 Agent 可用上下文")
    context.add_argument("task")
    context.add_argument("--limit", type=int, default=5, help="纳入上下文的记忆条数")
    context.add_argument("--token-budget", type=int, default=1200, help="上下文长度上限")
    context.add_argument("--stage", choices=["planning", "tool_selection", "response_generation"], default="tool_selection", help="预览实际 Agent 阶段；默认工具选择")
    context.add_argument("--compaction", choices=["adaptive", "full"], default="adaptive", help="上下文表示策略；默认按阶段压缩")
    context.add_argument("--agent-name", default="openhands", help="应用偏好的 Agent 名称")
    context.add_argument("--task-type", default="", help="任务类型；用于限定任务偏好")
    learn = sub.add_parser("learn-procedure", help="从本地检查依据形成可复用流程")
    learn.add_argument("spec_json_file", help="流程规范 JSON 文件")
    learn.add_argument("--source", action="append", default=[], required=True, help="来源记忆编号；可重复")
    procedures = sub.add_parser("procedures", help="按任务查找可复用流程")
    procedures.add_argument("task")
    procedures.add_argument("--limit", type=int, default=5, help="返回流程条数")
    verify = sub.add_parser("verify-procedure", help="只读检查流程后置条件并记录反馈")
    verify.add_argument("memory_id")
    verify.add_argument("--use-id", default="", help="本次使用编号；重复反馈使用同一编号")
    dependencies = sub.add_parser("dependencies", help="预览来源变化影响的派生记忆")
    dependencies.add_argument("memory_id")
    for procedure_parser in (learn, procedures, verify):
        procedure_parser.add_argument("--argument", action="append", default=[], help="参数 NAME=VALUE；可重复")
    sub.add_parser("preferences", help="查看当前生效偏好")
    sub.add_parser("conflicts", help="查看待核对的知识事实冲突")
    resolve_conflict = sub.add_parser("resolve-conflict", help="处理一条知识事实冲突")
    resolve_conflict.add_argument("conflict_id")
    resolve_conflict.add_argument(
        "--decision", choices=["accept_incoming", "keep_existing"], required=True,
    )
    resolve_conflict.add_argument("--memory-id", default="")
    resolve_conflict.add_argument("--expected-version-id", default="")
    sub.add_parser("audit", help="查看最近操作记录")
    end_session = sub.add_parser("end-session", help="结束会话并清理该会话的临时记忆")
    end_session.add_argument("--yes", action="store_true", help="确认清理")
    forget = sub.add_parser("forget", help="预览或确认精准遗忘")
    forget.add_argument("request")
    forget.add_argument("--yes", action="store_true", help="确认执行遗忘")
    forget.add_argument("--compact", action="store_true", help="确认后整理存储文件")
    sub.add_parser("evidence", help="导出当前命名空间的轻量复核摘要")
    lifecycle = sub.add_parser("lifecycle", help="查看一条记忆的原子、情节、证据和操作链")
    lifecycle.add_argument("memory_id", nargs="?", help="记忆编号；省略时查看整体生命周期概览")
    lifecycle.add_argument("--overview", action="store_true", help="查看当前记忆空间的生命周期覆盖情况")
    demo = sub.add_parser("demo", help="运行一轮可读的 Agent 记忆闭环演示")
    demo.add_argument("--task", default="查找精准遗忘的实现位置")
    compare_live = sub.add_parser("compare-live", help="用真实 Agent 对比关闭和开启记忆的同一任务")
    compare_live.add_argument("task")
    shell = sub.add_parser("shell", help="进入交互式记忆工作台")
    shell.add_argument("--prompt", default="memguard> ")
    for name in ("remember", "search", "context", "end-session", "shell", "learn-procedure", "procedures", "verify-procedure"):
        sub.choices[name].add_argument("--session-id", default=argparse.SUPPRESS, help="本次会话编号")
    for command_parser in sub.choices.values():
        command_parser.add_argument("--json", dest="command_json", action="store_true", help=argparse.SUPPRESS)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    service = _open_service(args)
    try:
        if args.command in {"learn-procedure", "procedures", "verify-procedure", "dependencies"}:
            return _run_procedure_command(service, args)
        if _json_requested(args):
            return _run_json(service, args)
        if args.command == "status":
            return _status(service)
        if args.command == "remember":
            return _remember(service, args.text, session_id=args.session_id)
        if args.command == "memory":
            return _memory(service, limit=args.limit)
        if args.command == "search":
            return _search(service, args.query, limit=args.limit, session_id=args.session_id)
        if args.command == "context":
            return _context(service, args.task, limit=args.limit, token_budget=args.token_budget, session_id=args.session_id,
                            stage=args.stage, agent_name=args.agent_name, task_type=args.task_type, project_root=args.workspace,
                            compaction=args.compaction)
        if args.command == "end-session":
            return _end_session(service, args.session_id, confirm=args.yes)
        if args.command == "preferences":
            return _preferences(service)
        if args.command == "conflicts":
            return _conflicts(service)
        if args.command == "resolve-conflict":
            result = service.resolve_fact_conflict(
                args.conflict_id,
                decision=args.decision,
                memory_id=args.memory_id,
                expected_version_id=args.expected_version_id,
            )
            _section("事实核对结果")
            if result.get("resolved"):
                print("[已完成] %s" % (
                    "已接受新值并生成记忆版本。" if args.decision == "accept_incoming"
                    else "已保留原值并关闭本条核对记录。"
                ))
                print("核对编号：%s" % args.conflict_id)
                resolved_memory_id = result.get("resolved_memory_id") or result.get("memory_id")
                if resolved_memory_id:
                    print("记忆编号：%s" % resolved_memory_id)
            else:
                message = {
                    "candidate_version_changed": "候选记忆已更新或遗忘，本次没有覆盖现有内容。请重新查看待核对事实。",
                    "conflict_already_closed": "这条核对记录已处理，本次没有再次修改记忆。",
                    "conflict_not_found": "未找到这条核对记录，请检查核对编号。",
                    "candidate_memory_required": "请通过 --memory-id 指定要核对的候选记忆。",
                }.get(str(result.get("reason") or ""), "本次核对未完成，请重新查看待核对事实及候选版本。")
                print("[未执行] %s" % message)
            print("下一步：运行 memguard conflicts 查看待核对事实，或 memguard memory 查看现有记忆。")
            return 0 if result.get("resolved") else 2
        if args.command == "audit":
            return _audit_readable(service)
        if args.command == "forget":
            return _forget(service, args.request, confirm=args.yes, compact=args.compact)
        if args.command == "evidence":
            return _evidence(service)
        if args.command == "lifecycle":
            return _lifecycle(service, args.memory_id, overview=args.overview)
        if args.command == "demo":
            return _demo(service, args.task)
        if args.command == "compare-live":
            return _compare_live(service, args.task, workspace=args.workspace, model=args.model)
        if args.command == "shell":
            return _shell(service, prompt=args.prompt, workspace=args.workspace, model=args.model, session_id=args.session_id)
    finally:
        service.close()
    return 1


def _run_procedure_command(service: MemGuardService, args: argparse.Namespace) -> int:
    commands = {
        "learn-procedure": "learn_procedure", "procedures": "recall_procedures",
        "verify-procedure": "verify_procedure", "dependencies": "dependency_impact",
    }
    try:
        request = {"command": commands[args.command]}
        if args.command == "dependencies":
            request["memory_ids"] = [args.memory_id]
        else:
            arguments = {}
            for argument in args.argument:
                name, separator, value = argument.partition("=")
                if not separator or name in arguments:
                    raise ValueError("--argument 需要 NAME=VALUE，且参数名不能重复")
                arguments[name] = value
            request.update(workspace=args.workspace, session_id=args.session_id, arguments=arguments)
            if args.command == "learn-procedure":
                with Path(args.spec_json_file).open("rb") as stream:
                    payload = stream.read(1024 * 1024 + 1)
                if len(payload) > 1024 * 1024:
                    raise ValueError("流程规范文件不得超过 1 MiB")
                request.update(spec=json.loads(payload.decode("utf-8-sig")), source_memory_ids=args.source)
            elif args.command == "procedures":
                request.update(task=args.task, limit=args.limit)
            else:
                if not args.session_id.strip():
                    raise ValueError("verify-procedure 需要 --session-id 明确会话编号，以便复核反馈")
                request.update(memory_id=args.memory_id, use_id=args.use_id)
        response = handle_request(service, request)
    except (OSError, UnicodeError, ValueError, RecursionError) as exc:
        response = {"ok": False, "error": "invalid_request", "detail": str(exc)}
    if _json_requested(args):
        print(json.dumps(response, ensure_ascii=False, indent=2, default=_json_default))
    else:
        _print_procedure_response(args.command, response, workspace=args.workspace, session_id=args.session_id)
    return 0 if response.get("ok") is True else 2


def _procedure_status(value: object) -> str:
    return {"pass": "通过", "fail": "失败", "unknown": "未知（未证明通过）"}.get(str(value), "未验证")


def _procedure_reason(value: object) -> str:
    reason = str(value or "")
    label = {
        "matched": "内容检查符合条件", "not_matched": "内容检查不符合条件",
        "exists": "路径存在", "missing_path": "路径不存在", "unavailable": "无法读取检查依据",
        "not_utf8_text": "文件不是 UTF-8 文本", "unsafe_path": "检查路径不安全",
        "file_too_large": "文件超过检查大小限制", "file_changed_during_read": "检查期间文件发生变化",
        "source_unavailable": "来源不可用或不符合当前作用范围",
        "source_changed": "来源已变化，需要重新验证", "source_snapshot_changed": "来源版本变化，未保存流程",
        "sensitive_high": "检测到高敏感内容，已阻止处理",
        "environment_not_applicable": "环境条件不适用或未能确认",
        "procedure_or_source_unavailable": "流程或来源不可用",
        "procedure_or_source_changed": "检查期间流程或来源已变化",
        "feedback_snapshot_changed": "反馈版本或使用编号不再有效，未新增反馈",
    }.get(reason)
    return "%s（%s）" % (label, reason) if label else reason


def _print_procedure_checks(verification: dict) -> None:
    print("实际检查：%s" % _procedure_status(verification.get("overall")))
    for check in verification.get("checks") or []:
        print("  [%s] %s · %s；依据：%s" % (
            _procedure_status(check.get("status")), check.get("type") or check.get("key") or "检查",
            check.get("path") or "环境", _procedure_reason(check.get("reason")) or "未提供检查原因",
        ))
        if "actual" in check or "expected" in check:
            print("    期望：%s；实际：%s" % (check.get("expected", "未提供"), check.get("actual", "未知")))


def _print_procedure_item(item: dict) -> None:
    spec = item.get("spec") or item.get("procedure") or {}
    record = item.get("record") or {}
    if not spec and isinstance(record, dict) and record.get("content"):
        try:
            spec = json.loads(record["content"])
        except (TypeError, ValueError):
            spec = {}
    rendered = item.get("rendered") or spec
    print("流程：%s" % (rendered.get("goal") or item.get("goal") or item.get("goal_template") or item.get("summary") or "未提供目标"))
    print("流程状态：%s" % {
        "ready": "可供复用（尚未执行或复核本次任务）", "requires_arguments": "需补充参数",
        "invalid_arguments": "参数不适用", "learned": "已学习并验证", "blocked": "已阻止",
        "not_verified": "未验证通过", "pass": "通过", "fail": "失败", "unknown": "未知（未证明通过）",
    }.get(str(item.get("status")), "未验证"))
    print("记忆编号：%s" % (item.get("memory_id") or "未保存"))
    verification = item.get("verification") or item
    steps = item.get("rendered_steps") or verification.get("rendered_steps") or item.get("steps") or rendered.get("steps") or []
    print("步骤（仅建议，未自动执行）：")
    for index, step in enumerate(steps, 1):
        print("  %s. %s" % (index, step))
    print("风险约束：")
    guards = item.get("failure_guards") or rendered.get("failure_guards") or []
    for guard in guards:
        print("  - %s" % guard)
    if not guards:
        print("  未提供额外约束；不代表无风险。")
    sources = item.get("source_memory_ids") or [dep.get("memory_id") for dep in item.get("dependencies") or []]
    if sources:
        print("来源记忆：%s" % "、".join(str(source) for source in sources))
    else:
        print("来源记忆：服务未返回来源清单；以上述本地检查依据为准。")
    for dependency in item.get("dependencies") or []:
        print("  %s · 来源版本 %s · %s" % (dependency.get("memory_id"), dependency.get("version_id"), dependency.get("relation")))
    _print_procedure_checks(verification)
    for condition in item.get("postconditions") or rendered.get("postconditions") or []:
        print("后置条件：%s · %s%s" % (condition.get("type"), condition.get("path"), " · " + str(condition["value"]) if "value" in condition else ""))
    if item.get("required_arguments"):
        print("待补参数：%s" % "、".join(item["required_arguments"]))
    environment = item.get("environment_check") or item.get("environment")
    if environment:
        print("环境条件：")
        _print_procedure_checks(environment)
    stats = item.get("stats") or item.get("feedback_stats")
    if stats:
        print("历史反馈：通过 %s；失败 %s；未知 %s；成功率 %s" % (
            stats.get("pass", 0), stats.get("fail", 0), stats.get("unknown", 0),
            stats.get("success_rate") if stats.get("success_rate") is not None else "尚无已测量反馈",
        ))


def _print_procedure_response(command: str, response: dict, *, workspace: str, session_id: str) -> None:
    _section({"learn-procedure": "流程学习", "procedures": "可复用流程", "verify-procedure": "流程复核", "dependencies": "派生影响预览"}[command])
    print("状态：%s" % ("已完成" if response.get("ok") else "未完成"))
    raw_result = response.get("result")
    result = raw_result if isinstance(raw_result, dict) else {}
    if command != "dependencies":
        print("工作区：%s" % workspace)
        print("会话：%s" % (result.get("session_id") or session_id or "未指定"))
        print("检查来源：当前本地工作区的只读后置条件检查；不执行步骤或外部调用。")
    if command == "procedures":
        items = raw_result if isinstance(raw_result, list) else []
        for item in items:
            _print_procedure_item(item)
        if not items:
            print("未找到符合当前任务和约束的流程。")
    elif command == "dependencies":
        items = result.get("derived_memories") or result.get("descendants") or result.get("affected_memories") or []
        print("根来源记忆：%s" % "、".join(str(memory_id) for memory_id in result.get("root_memory_ids") or []))
        print("影响数量：%s" % (result.get("affected_count", len(items))))
        for item in items:
            print("  %s · %s · %s" % (item.get("memory_id"), item.get("summary") or "派生记忆", item.get("governance_state") or "待核对"))
        print("本次仅预览，不撤回、不删除记忆。")
    else:
        _print_procedure_item(result)
        if command == "verify-procedure":
            print("使用编号：%s" % (result.get("use_id") or "未提供"))
    feedback = result.get("feedback") or {}
    if "recorded" in result:
        feedback = {"recorded": result["recorded"], "status": result.get("feedback_status") or result.get("status")}
    if isinstance(feedback, dict) and "recorded" in feedback:
        print("反馈：%s；检查状态：%s" % ("已记录" if feedback["recorded"] else "未新增（可能已记录）", _procedure_status(feedback.get("status"))))
    else:
        print("反馈：%s" % (_procedure_reason(result.get("reason")) or ("请求已完成，检查状态以上述实际结果为准。" if response.get("ok") else "请求未完成，没有证明流程成功。")))
    if response.get("error"):
        print("错误：%s；%s" % (response["error"], response.get("detail") or "请检查输入和服务状态"))


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


def _remember(service: MemGuardService, text: str, session_id: str = "") -> int:
    result = service.ingest_natural_memory(
        text,
        source_kind="user_cli",
        source_uri="memguard",
        session_id=session_id,
    )
    _section("记忆更新" if result.get("updated") else "记忆写入")
    if result.get("stored"):
        records = list(result.get("records") or [])
        if not records and result.get("record") is not None:
            records = [result["record"]]
        memory_ids = [
            str(record.get("memory_id") if isinstance(record, dict) else getattr(record, "memory_id", "") or "")
            for record in records
        ]
        feedback = result.get("feedback") or {}
        items = list(feedback.get("new_memories") or []) + list(feedback.get("updated_memories") or [])
        count = max(1, len(items) or len(records))
        if feedback.get("new_memories") and not result.get("updated"):
            print("检测到并保存 %d 项可复用内容。" % count)
        elif result.get("updated") and not feedback.get("new_memories"):
            print("已更新 %d 项可复用内容。" % count)
        else:
            print("已处理 %d 项可复用内容。" % count)
        for index, item in enumerate(items or [{}], 1):
            summary = str(item.get("summary_for_user") or "已形成一条受控记忆。")
            print("%d. [%s · %s] %s" % (
                index,
                item.get("category_label") or "可复用记忆",
                item.get("status_label") or "已保存",
                summary,
            ))
        tiers = {
            str(record.get("tier") if isinstance(record, dict) else getattr(record, "tier", "") or "")
            for record in records
        }
        tier_labels = {
            "working": "当前任务",
            "session": "当前会话",
            "long_term": "长期保留",
        }
        if tiers:
            print("保存时长：%s" % "、".join(tier_labels.get(tier, "自动管理") for tier in sorted(tiers)))
        if tiers & {"working", "session"}:
            print("说明：阶段性记忆不会被当作长期偏好；任务或会话结束后可统一清理。")
        print("记忆编号：%s" % ("、".join(memory_ids) or "可在 Web 控制台的最近记忆中查看"))
        if result.get("partial_conflict"):
            print("另有事实属性与现有记录冲突，尚未覆盖：%s" % "、".join(result.get("candidate_memory_ids") or []))
            print("请先核对原记忆，再明确说明该属性改为的新值。")
        print("下一步：输入 /memory 查看内容，或运行相关 Agent 任务验证是否生效。")
        return 0
    notice = result.get("sensitive_notice") or {}
    if isinstance(notice, dict) and notice.get("user_summary"):
        print(str(notice["user_summary"]))
        labels = [str(item) for item in notice.get("detected_labels") or [] if str(item)]
        if labels:
            print("检测类型：%s" % "、".join(_sensitive_label(item) for item in labels))
        print("下一步：可在“内容保护”或审计记录中查看本次处理依据。")
    else:
        observed = list((result.get("feedback") or {}).get("observed_preferences") or [])
        if result.get("reason") == "fact_conflict_requires_review":
            print("同一事实属性已有不同记录，本次没有覆盖旧值。")
            print("待核对记忆编号：%s" % "、".join(result.get("candidate_memory_ids") or []))
            print("下一步：先输入 /memory 核对旧记录；确认变化后明确说“该项目的对应属性改为新值”。")
        elif observed:
            for item in observed:
                print("检测到%s：%s" % (item.get("category_label") or "用户偏好", item.get("summary_for_user") or ""))
                print("当前状态：%s；已获得 %s 条依据，来自 %s 个会话。" % (
                    item.get("status_label") or "正在观察",
                    item.get("evidence_count", 1),
                    item.get("distinct_sessions", 1),
                ))
            print("说明：一次性用法不会自动升级为长期偏好；明确说“以后”或“记住”可立即保存。")
        elif result.get("reason") == "user_requested_no_memory":
            print("已按你的要求跳过保存，本次内容不会进入长期记忆或检索上下文。")
        elif result.get("reason") == "session_required":
            print("这是临时记忆，需要会话编号。可进入 `memguard shell` 自动创建会话，或添加 `--session-id <编号>`。")
        else:
            print("本次内容未形成记忆：%s" % result.get("reason", "策略未允许"))
            print("处理建议：说明要保存的事实、偏好或可复用步骤，并注明适用范围或保存时长。")
    return 2


def _sensitive_label(label: str) -> str:
    return {
        "password_secret": "密码或口令",
        "weak_password_hint_cn": "密码或口令",
        "one_time_code": "验证码或一次性口令",
        "token_assignment": "访问凭据",
        "api_key": "API 密钥",
        "private_key": "私钥",
        "jwt": "登录令牌",
        "url_credentials": "链接中的账号凭据",
    }.get(str(label or ""), "敏感信息")


def _memory(service: MemGuardService, limit: int = 10) -> int:
    rows = service.list_memories(include_deleted=False)[: max(1, limit)]
    _section("最近记忆")
    if not rows:
        print("暂无可显示的记忆。")
        print("下一步：输入 `memguard remember \"以后查代码优先用 rg\"` 写入一条偏好。")
        return 0
    for index, row in enumerate(rows, 1):
        print("%d. %s" % (index, memory_summary_for_user(row)))
        print("   分类：%s  保存时长：%s" % (
            _memory_category_display(row),
            {"working": "当前任务", "session": "当前会话", "long_term": "长期保留"}.get(
                str(row.get("tier") or ""), "自动管理"
            ),
        ))
        print("   ID：%s" % row.get("memory_id", "-"))
    return 0


def _memory_category_display(row: dict) -> str:
    topic = str(row.get("preference_topic") or "")
    subtype = str(row.get("subtype") or "")
    memory_type = str(row.get("type") or "")
    if topic in {"code_search_tool", "test_runner_command", "service_log_tool"}:
        return "工具偏好"
    if topic in {"response_order", "response_language", "response_verbosity"}:
        return "回答偏好"
    if topic == "high_risk_confirmation" or memory_type == "sensitive_policy":
        return "安全策略"
    if subtype in {"procedural", "procedural_trace", "tool_failure_case", "tool_trace", "workflow"}:
        return "操作经验"
    if memory_type == "preference":
        return "用户偏好"
    return "知识记忆"


def _search(service: MemGuardService, query: str, limit: int = 5, session_id: str = "") -> int:
    hits = service.search(query, limit=max(1, limit), session_id=session_id)
    _section("记忆检索")
    if not hits:
        print("没有找到相关记忆。")
        print("处理建议：换一个更具体的关键词，或先写入一条相关偏好。")
        return 1
    for index, hit in enumerate(hits, 1):
        print("%d. %s" % (index, hit.get("summary") or hit.get("memory_id")))
        print("   分数：%s  来源：%s  ID：%s" % (hit.get("score", "-"), hit.get("match_source", "-"), hit.get("memory_id", "-")))
        reason = hit.get("match_reason")
        if reason:
            print("   命中原因：%s" % reason)
    _print_retrieval_timings(hits[0].get("retrieval_explanation") if hits else None)
    return 0


def _print_retrieval_timings(explanation: object) -> None:
    if not isinstance(explanation, dict):
        return
    timings = explanation.get("timings")
    if not isinstance(timings, dict) or not timings:
        return
    segments = [
        ("候选召回", timings.get("candidate_recall_ms")),
        ("打分", timings.get("scoring_ms")),
        ("融合", timings.get("fusion_ms")),
        ("去重与整理", timings.get("postprocess_ms")),
    ]
    parts = ["%s %s ms" % (name, value) for name, value in segments if value is not None]
    total = timings.get("total_ms")
    if total is not None:
        print("检索耗时：合计 %s ms（%s）" % (total, "，".join(parts) if parts else "未分段"))


def _context_payload(service: MemGuardService, task: str, limit: int, token_budget: int, session_id: str,
                     stage: str, agent_name: str, task_type: str, project_root: str, compaction: str = "adaptive") -> dict:
    return service.build_agent_context(task=task, limit=max(1, limit), token_budget=max(64, token_budget),
        session_id=session_id, stage=stage, agent_name=agent_name, task_type=task_type,
        project_root=str(Path(project_root).expanduser().resolve()) if project_root else "", compaction=compaction)


def _context(service: MemGuardService, task: str, limit: int = 5, token_budget: int = 1200, session_id: str = "",
             stage: str = "tool_selection", agent_name: str = "openhands", task_type: str = "", project_root: str = "",
             compaction: str = "adaptive") -> int:
    context = _context_payload(service, task, limit, token_budget, session_id, stage, agent_name, task_type, project_root, compaction)
    _section("记忆注入")
    memories = context.get("memories", [])
    print("任务：%s" % task)
    print("阶段：%s；Agent：%s" % ({"planning": "任务规划", "tool_selection": "工具选择", "response_generation": "回答生成"}[stage], agent_name))
    print("上下文策略：%s；表示分布：%s；预计节省：%s tokens" % (
        context.get("compaction", compaction),
        context.get("budget", {}).get("representation_counts", {}),
        context.get("budget", {}).get("estimated_saved_tokens", 0),
    ))
    print("可用记忆：%d 条" % len(memories))
    for index, memory in enumerate(memories[: max(1, limit)], 1):
        if memory.get("preference_topic"):
            label, value = _preference_display(memory["preference_topic"], memory.get("preference_value", ""))
            print("%d. [%s] %s" % (index, label, value))
            print("   记忆编号：%s · 当前来源版本：%s" % (memory.get("memory_id"), memory.get("version_id")))
        else:
            print("%d. %s" % (index, memory.get("summary") or memory.get("memory_id")))
    print("说明：采用与 Agent 相同的阶段、范围和安全检查；这是生成前的上下文预览，不代表模型已执行任务。")
    return 0


def _end_session(service: MemGuardService, session_id: str, confirm: bool = False) -> int:
    try:
        result = service.evict_session(session_id, confirm=confirm)
    except Exception as exc:
        _section("结束会话")
        print("会话清理未完成（%s）。临时记忆仍需复核，请查看操作记录。" % type(exc).__name__)
        return 2
    _section("结束会话")
    if result.get("reason") == "session_required":
        print("请先提供 `--session-id <编号>`；交互式工作台会自动管理会话。")
        return 2
    if not confirm:
        print("将清理 %d 条当前会话的临时记忆；长期记忆不受影响。" % result.get("candidate_count", 0))
        print("确认请重新执行并添加 `--yes`。")
        return 0
    if not result.get("evicted"):
        print("会话清理未完成：%s。请检查操作记录。" % result.get("reason", "验证未通过"))
        return 2
    print("已结束当前会话，清理 %d 条临时记忆；长期记忆保持可用。" % len(result.get("memory_ids") or []))
    return 0


def _forget(service: MemGuardService, request: str, confirm: bool = False, compact: bool = False) -> int:
    result = service.forget(request, confirm=confirm, compact=compact or confirm)
    if confirm:
        return _print_confirmed_forget(result)
    _print_forget_preview(result, interactive=False)
    return 0


def _print_confirmed_forget(result: dict) -> int:
    _section("精准遗忘")
    if result.get("reason") == "stale_preview":
        print("[未执行] 影响范围预览已失效，本次未删除记忆。")
        if result.get("derivation_impact"):
            print("来源的派生记忆必须一并确认；请重看影响范围。")
        else:
            print("记忆版本已变化；请重看影响范围并重新选择确认。")
        stale_ids = result.get("stale_memory_ids") or []
        if stale_ids:
            print("需重新核对的记忆编号：%s" % "、".join(str(memory_id) for memory_id in stale_ids))
        return 2
    forgotten = list(
        result.get("forgotten") or result.get("forgotten_memory_ids") or []
    )
    report = result.get("verification_report") or {}
    report_ok = bool(result.get("ok", True)) and not any(
        step.get("status") == "failed"
        for step in report.get("verification_steps") or []
    )
    if forgotten and report_ok:
        print("[已完成] 已遗忘 %d 条目标记忆。" % len(forgotten))
    elif forgotten:
        print("[需检查] 已写入遗忘结果，但验证报告未全部通过。")
    else:
        print("[未变更] 没有找到需要遗忘的目标记忆。")
    steps = list(report.get("verification_steps") or [])
    if steps:
        print("验证结果")
        labels = {"passed": "通过", "failed": "需检查", "skipped": "本次不适用"}
        step_labels = {
            "main_record": "主记忆记录",
            "agent_event_payload": "Agent 交互记录",
            "sqlite_fts": "文本检索索引",
            "graph_relations": "关联关系索引",
            "fact_conflict": "事实核对队列",
            "search_cache": "检索缓存",
            "mmap_segment_compact": "本机存储整理",
            "plaintext_probe": "明文残留检查",
            "agent_context_absence": "Agent 上下文复检",
            "audit_chain": "操作记录完整性",
        }
        for step in steps:
            print("- %s：%s" % (
                step_labels.get(step.get("name"), "验证项"),
                labels.get(step.get("status"), step.get("status", "未知")),
            ))
    if report.get("user_summary"):
        print("结论：%s" % report.get("user_summary"))
    print("边界说明：只清理 MemGuardOS 受管数据；外部 Agent 日志和备份需在对应系统中另行处理。")
    print("下一步：输入 /preferences 查看保留偏好，或 /search 关键词复核目标内容已不可检索。")
    return 0


def _print_forget_preview(result: dict, interactive: bool) -> None:
    _section("精准遗忘")
    print("影响范围预览")
    candidates = list(result.get("candidates") or [])
    if candidates:
        for index, item in enumerate(candidates, 1):
            print("%d. %s" % (index, item.get("summary") or item.get("memory_id")))
            category = _memory_category_display(item)
            if item.get("preference_topic_match"):
                reason = "与当前生效的结构化偏好一致"
            else:
                reason = "内容与遗忘目标相关"
            current_value = str(item.get("preference_value") or "").strip()
            value_text = " · 当前设置：%s" % current_value if current_value else ""
            print("   类别：%s%s · 匹配依据：%s" % (category, value_text, reason))
            print("   ID：%s" % item.get("memory_id", "-"))
    else:
        print("没有发现候选。")
    if interactive and candidates:
        if len(candidates) == 1:
            print("输入 yes 或 确认执行；输入 no 或 取消停止。")
        else:
            print("请输入候选编号选择，例如 `1` 或 `1,3`；输入“全部”可选择所有候选。")
            print("选择后输入 yes 或 确认执行；输入 no 或 取消停止。")
    elif interactive:
        print("没有需要确认的内容，你可以换一种更具体的说法。")
    else:
        print("确认执行请追加 --yes；也可以进入交互工作台，在预览后直接输入 yes。")


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


def _lifecycle(service: MemGuardService, memory_id: str | None, *, overview: bool = False) -> int:
    """Print the unified lifecycle projection in a readable product form."""

    if overview or not memory_id:
        result = service.lifecycle_overview()
        counts = result.get("counts") or {}
        tiers = result.get("tiers") or {}
        _section("记忆生命周期概览")
        print("记忆空间：%s" % result.get("namespace", service.namespace))
        print("原子记忆：%s 条；有来源支撑：%s 条" % (
            counts.get("active_atoms", 0), counts.get("with_support", 0),
        ))
        print("临时任务：%s 条；当前会话：%s 条；长期保留：%s 条" % (
            tiers.get("working", 0), tiers.get("session", 0), tiers.get("long_term", 0),
        ))
        print("待处理写入：%s；待处理遗忘：%s" % (
            counts.get("pending_writes", 0), counts.get("pending_erasures", 0),
        ))
        print("说明：原子负责可检索和可遗忘，情节与来源负责解释形成依据，操作链负责证明生命周期变化。")
        return 0
    result = service.lifecycle_snapshot(memory_id, include_events=True)
    if not result.get("found"):
        print("没有找到这条记忆：%s" % memory_id)
        return 1
    atom = result.get("atom") or {}
    counts = result.get("counts") or {}
    _section("记忆生命周期证据")
    print("记忆编号：%s" % atom.get("memory_id", memory_id))
    print("原子类型：%s · 子类型：%s · 状态：%s" % (
        atom.get("kind", ""), atom.get("subtype", ""), atom.get("lifecycle_state", ""),
    ))
    print("保存层级：%s · 作用范围：%s · 版本：%s" % (
        atom.get("tier", ""), atom.get("scope", ""), atom.get("version_id", ""),
    ))
    print("摘要：%s" % atom.get("summary", ""))
    print("证据链：%s 个情节 · %s 个来源支持 · %s 次受管操作 · %s 次进入 Agent 上下文" % (
        counts.get("episodes", 0), counts.get("supports", 0),
        counts.get("operations", 0), counts.get("context_uses", 0),
    ))
    for episode in result.get("episodes", [])[:5]:
        print("- 情节 %s：%s / %s，%s 个事件，任务类型 %s" % (
            episode.get("episode_id", ""), episode.get("agent_name", ""),
            episode.get("session_id", ""), episode.get("event_count", 0),
            episode.get("task_type") or "未标注",
        ))
    boundary = result.get("forgetting_boundary") or {}
    print("遗忘边界：%s" % boundary.get("state", ""))
    print("受管面：%s" % "、".join(boundary.get("managed_surfaces") or []))
    return 0


def _preferences(service: MemGuardService) -> int:
    status = service.interaction_status()
    _section("当前生效偏好")
    stable = [item for item in status.get("preference_aggregates", []) if item.get("stable")]
    if not stable:
        print("暂无稳定偏好。")
        print("下一步：在 Agent 工作台运行一次任务，或输入自然语言说明你的使用习惯。")
        return 0
    for index, item in enumerate(stable, 1):
        category, description = _preference_display(
            str(item.get("topic") or ""),
            str(item.get("value") or ""),
        )
        print("%d. [%s] %s" % (index, category, description))
        print("   作用范围：%s · 依据：%s 条 · 来自 %s 个会话" % (
            _scope_display(str(item.get("preference_scope") or "global")),
            item.get("evidence_count", 0),
            item.get("distinct_sessions", 0),
        ))
    print("下一步：直接运行相关任务，系统会按适用阶段自动采用这些偏好。")
    return 0


def _conflicts(service: MemGuardService) -> int:
    result = service.fact_conflicts(status="pending", limit=30)
    _section("待核对事实")
    cases = list(result.get("cases") or [])
    if not cases:
        print("暂无待核对的事实冲突。")
        print("明确说“把某属性改为新值”后，系统会生成新版本并关闭对应核对记录。")
        return 0
    for index, case in enumerate(cases, 1):
        print("%d. [%s] %s · %s" % (
            index,
            case.get("conflict_id", ""),
            case.get("subject", ""),
            case.get("predicate", ""),
        ))
        print("   新值：%s · 候选：%s" % (
            case.get("incoming_value", ""),
            "、".join(str(item) for item in case.get("candidate_memory_ids") or []) or "无",
        ))
        for candidate in case.get("candidates") or []:
            print("   - %s [%s] %s" % (
                candidate.get("memory_id", ""),
                candidate.get("state", ""),
                candidate.get("summary", ""),
            ))
        print("   处理：确认新值可用时输入“%s的%s改为%s”；保留原值可调用冲突编号单独关闭。" % (
            case.get("subject", ""), case.get("predicate", ""), case.get("incoming_value", ""),
        ))
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
        result = service.ingest_natural_memory(args.text, source_kind="user_cli", source_uri="memguard", session_id=args.session_id)
        record = result.get("record")
        value = {
            "stored": bool(result.get("stored")),
            "updated": bool(result.get("updated")),
            "memory_id": record.get("memory_id", "") if isinstance(record, dict) else getattr(record, "memory_id", ""),
            "memory_ids": result.get("memory_ids") or [],
            "candidate_memory_ids": result.get("candidate_memory_ids") or [],
            "feedback": result.get("feedback"),
            "sensitive_notice": result.get("sensitive_notice"),
            "reason": result.get("reason", ""),
        }
    elif args.command == "memory":
        value = service.list_memories(include_deleted=False)[: max(1, args.limit)]
    elif args.command == "search":
        value = service.search(args.query, limit=max(1, args.limit), session_id=args.session_id)
    elif args.command == "context":
        value = _context_payload(service, args.task, args.limit, args.token_budget, args.session_id,
                                 args.stage, args.agent_name, args.task_type, args.workspace, args.compaction)
    elif args.command == "end-session":
        value = service.evict_session(args.session_id, confirm=args.yes)
    elif args.command == "preferences":
        value = service.interaction_status().get("preference_aggregates", [])
    elif args.command == "conflicts":
        value = service.fact_conflicts(status="pending", limit=30)
    elif args.command == "resolve-conflict":
        value = service.resolve_fact_conflict(
            args.conflict_id,
            decision=args.decision,
            memory_id=args.memory_id,
            expected_version_id=args.expected_version_id,
        )
    elif args.command == "audit":
        value = service.audit(limit=12)
    elif args.command == "forget":
        value = service.forget(args.request, confirm=args.yes, compact=args.compact or args.yes)
    elif args.command == "evidence":
        value = {
            "namespace": service.namespace_status(),
            "audit": service.verify_audit(),
            "agent": service.agent_dashboard_snapshot(limit=20),
            "notifications": service.recent_notifications(limit=20),
        }
    elif args.command == "lifecycle":
        value = (
            service.lifecycle_overview()
            if args.overview or not args.memory_id
            else service.lifecycle_snapshot(args.memory_id)
        )
    elif args.command == "demo":
        value = _demo_result(service, args.task)
    else:
        value = {"ok": False, "error": "unsupported_json_command", "command": args.command}
    print(json.dumps(value, ensure_ascii=False, indent=2, default=_json_default))
    if isinstance(value, dict) and value.get("ok") is False:
        return 2
    if args.command == "remember" and not value.get("stored"):
        return 2
    if args.command == "resolve-conflict" and not value.get("resolved"):
        return 2
    if args.command == "end-session" and args.yes and not value.get("evicted"):
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


SHELL_COMMANDS = (
    "/guide /status /memory /memory on|off /preferences /search 关键词 "
    "/conflicts /context 任务 /lifecycle 记忆编号 /forget 请求 /run 任务 /audit /evidence /demo /session /model /workspace /exit"
)

_FORGET_CONFIRM_WORDS = {"yes", "y", "确认", "确定", "是", "继续"}
_FORGET_CANCEL_WORDS = {"no", "n", "取消", "不要", "否", "返回"}
_FORGET_ALL_WORDS = {"all", "全部", "全选", "全部候选"}


def _shell(service: MemGuardService, prompt: str, workspace: str = "", model: str = "", session_id: str = "") -> int:
    _section("MemGuardOS 记忆工作台")
    state = {
        "workspace": str(Path(workspace or Path.cwd()).expanduser()),
        "model": str(model or "未配置"),
        "memory_enabled": True,
        "pending_forget": None,
        "session_id": str(session_id or "shell-%s" % os.urandom(8).hex()),
    }
    print("用于体验 Agent 学习、复用和遗忘。输入 /guide 查看四步演示流程。")
    print("当前记忆辅助：[开启]  输入 /help 查看全部操作。")
    while True:
        try:
            try:
                raw_line = input(_prompt_text(prompt))
            finally:
                _restore_terminal_color()
            line = raw_line.strip()
        except EOFError:
            print()
            return _end_session(service, state["session_id"], confirm=True)
        if not line:
            continue
        if line in {"/exit", "exit", "quit"}:
            return _end_session(service, state["session_id"], confirm=True)
        if line == "/help":
            print("可用操作：" + SHELL_COMMANDS)
            print("说明：/run 只运行真实 Agent；/demo 是明确标注的本地事件回放。")
            print("遗忘可以直接说“忘掉……”。单候选输入 yes；多候选先输入编号或“全部”，再确认。")
            continue
        if line == "/guide":
            _shell_guide(state)
            continue
        if line == "/model":
            print("当前模型：%s" % state["model"])
            print("设置方式：输入 `/model 模型名称`，随后可用 `/run 任务` 运行真实 Agent。")
            continue
        if line.startswith("/model "):
            state["model"] = line[len("/model "):].strip() or "未配置"
            print("已设置当前模型：%s" % state["model"])
            continue
        if line == "/workspace":
            print("当前工作区：%s" % state["workspace"])
            continue
        if line.startswith("/workspace "):
            candidate = Path(line[len("/workspace "):].strip()).expanduser()
            state["workspace"] = str(candidate.resolve())
            print("已设置工作区：%s" % state["workspace"])
            continue
        # 任何单条操作的返回码都不再终止工作台：敏感阻断和审计异常同样返回非零，
        # 直接退出会让"敏感内容保护"演示中途闪退。
        try:
            _dispatch_shell_line(service, line, state)
        except KeyboardInterrupt:
            print("\n已取消当前操作，工作台继续运行。输入 /exit 退出。")


def _dispatch_shell_line(service: MemGuardService, line: str, state: dict) -> int:
    normalized = " ".join(line.strip().split())
    answer = normalized.casefold()
    pending = state.get("pending_forget")
    if pending:
        if answer in _FORGET_CONFIRM_WORDS:
            return _confirm_pending_forget(service, state)
        if answer in _FORGET_CANCEL_WORDS:
            state["pending_forget"] = None
            print("已取消本次遗忘，任何记忆都没有改变。")
            return 0
        if answer in _FORGET_ALL_WORDS:
            pending["selected_ids"] = list(pending.get("memory_ids") or [])
            state["pending_forget"] = pending
            _print_forget_selection(pending)
            return 0
        selection = _parse_forget_selection(normalized, len(pending.get("memory_ids") or []))
        if selection is not None:
            if not selection:
                print("没有可用的候选编号，请按列表中的编号重新选择。")
                return 0
            pending["selected_ids"] = [pending["memory_ids"][index - 1] for index in selection]
            state["pending_forget"] = pending
            _print_forget_selection(pending)
            return 0
        print("当前有一项遗忘等待确认，本次输入未执行。")
        _print_forget_pending_help(pending)
        return 0
    if answer in _FORGET_CONFIRM_WORDS | _FORGET_CANCEL_WORDS:
        print("当前没有等待确认的遗忘操作。请先输入“忘掉……”查看影响范围。")
        return 0
    if line in {"/memory on", "/memory off", "/memory status"}:
        return _memory_mode(line, state)
    if line == "/session":
        print("当前会话：%s。输入 /session end 可清理本次临时记忆并开启新会话。" % state["session_id"])
        return 0
    if line == "/session end":
        result = _end_session(service, state["session_id"], confirm=True)
        if result == 0:
            state["session_id"] = "shell-%s" % os.urandom(8).hex()
        return result
    if line == "/preferences":
        return _preferences(service)
    if line == "/conflicts":
        return _conflicts(service)
    if line == "/audit":
        return _audit_readable(service)
    if line == "/demo":
        return _demo(service, "查找精准遗忘的实现位置")
    if line.startswith("/run "):
        task = line[len("/run "):].strip()
        if _looks_like_forget_request(task):
            print("已识别为记忆管理请求，不会把它交给 Agent 当作普通任务执行。")
            return _preview_forget_in_shell(service, task, state)
        return _run_agent_task(service, task, state)
    if line == "/run":
        print("用法：/run 任务描述。当前模型：%s，工作区：%s" % (state.get("model"), state.get("workspace")))
        return 0
    if line.startswith("/forget "):
        request = line[len("/forget "):].strip()
        for flag in (" --yes", " --confirm"):
            if request.endswith(flag):
                clean_request = request[: -len(flag)].strip()
                _preview_forget_in_shell(service, clean_request, state)
                return _confirm_pending_forget(service, state)
        return _preview_forget_in_shell(service, request, state)
    if _looks_like_forget_request(line):
        print("已识别为精准遗忘请求。")
        return _preview_forget_in_shell(service, line, state)
    action, payload = _natural_shell_intent(line)
    if action == "memory_list":
        print("已识别为记忆查看请求。")
        return _memory(service)
    if action == "preference_list":
        print("已识别为偏好查看请求。")
        return _preferences(service)
    if action == "fact_conflict_list":
        print("已识别为事实核对请求。")
        return _conflicts(service)
    if action == "status":
        print("已识别为状态查看请求。")
        return _status(service)
    if action == "search":
        print("已识别为记忆搜索请求。")
        return _search(service, payload, session_id=state["session_id"])
    if action == "remember":
        print("已识别为记忆保存请求。")
        return _remember(service, payload, session_id=state["session_id"])
    if action == "run":
        print("已识别为 Agent 任务。")
        return _run_agent_task(service, payload, state)
    if line.startswith("/"):
        return _run_shell_line(service, line, session_id=state["session_id"], project_root=state["workspace"])
    print("本次输入尚未执行：请确认它是 Agent 任务还是要保存的记忆。")
    print("- 运行任务：输入 `/run %s`" % line)
    print("- 保存记忆：输入 `记住：%s`" % line)
    return 0


def _natural_shell_intent(text: str) -> tuple[str, str]:
    decision = interpret_user_input(text)
    return ("" if decision.action in {"empty", "ambiguous", "forget"} else decision.action, decision.payload)


def _looks_like_forget_request(text: str) -> bool:
    return looks_like_forget_request(text)


def _preview_forget_in_shell(service: MemGuardService, request: str, state: dict) -> int:
    result = service.forget(request, confirm=False, compact=False)
    _print_forget_preview(result, interactive=True)
    candidates = list(result.get("candidates") or [])
    memory_ids = [str(item.get("memory_id") or "") for item in candidates if item.get("memory_id")]
    if memory_ids:
        state["pending_forget"] = {
            "request": request,
            "memory_ids": memory_ids,
            "candidates": candidates,
            "selected_ids": list(memory_ids) if len(memory_ids) == 1 else [],
        }
    else:
        state["pending_forget"] = None
    return 0


def _confirm_pending_forget(service: MemGuardService, state: dict) -> int:
    pending = dict(state.get("pending_forget") or {})
    request = str(pending.get("request") or "").strip()
    expected_ids = [str(item) for item in pending.get("memory_ids") or [] if str(item)]
    selected_ids = [str(item) for item in pending.get("selected_ids") or [] if str(item)]
    if not request or not expected_ids:
        state["pending_forget"] = None
        print("待确认信息已失效，请重新输入遗忘请求。")
        return 0
    if len(expected_ids) > 1 and not selected_ids:
        print("检测到多条候选，尚未选择要遗忘的内容，本次没有执行。")
        _print_forget_pending_help(pending)
        return 0
    if not selected_ids:
        selected_ids = list(expected_ids)

    refreshed = service.forget(request, confirm=False, compact=False)
    refreshed_ids = [
        str(item.get("memory_id") or "")
        for item in refreshed.get("candidates") or []
        if item.get("memory_id")
    ]
    if set(refreshed_ids) != set(expected_ids):
        print("候选内容在确认前发生了变化，为避免误删，本次没有执行。")
        _print_forget_preview(refreshed, interactive=True)
        state["pending_forget"] = (
            {
                "request": request,
                "memory_ids": refreshed_ids,
                "candidates": list(refreshed.get("candidates") or []),
                "selected_ids": list(refreshed_ids) if len(refreshed_ids) == 1 else [],
            }
            if refreshed_ids
            else None
        )
        return 0

    expected_versions = {
        str(item["memory_id"]): str(item.get("version_id") or "")
        for item in pending.get("candidates") or []
        if item.get("memory_id")
    }
    refreshed_versions = {
        str(item["memory_id"]): str(item.get("version_id") or "")
        for item in refreshed.get("candidates") or []
        if item.get("memory_id")
    }
    if refreshed_versions != expected_versions:
        state["pending_forget"] = None
        print("记忆内容在预览后发生变化，请重新预览再选择；本次没有执行。")
        return 0

    if any(memory_id not in refreshed_ids for memory_id in selected_ids):
        state["pending_forget"] = None
        print("已选择的候选不再有效，本次没有执行，请重新发起遗忘。")
        return 0

    state["pending_forget"] = None
    result = service.forget_memory_ids(
        selected_ids, reason=request, compact=True,
        expected_versions={memory_id: expected_versions[memory_id] for memory_id in selected_ids},
    )
    result["verification_report"] = service.forget_verification_report(
        result,
        request=request,
        dry_run=False,
    )
    return _print_confirmed_forget(result)


def _parse_forget_selection(text: str, candidate_count: int) -> list[int] | None:
    compact = str(text or "").strip()
    compact = re.sub(r"^(?:请选择|选择|选)", "", compact).strip()
    compact = compact.replace("和", ",")
    compact = re.sub(r"[第条项个号]", "", compact)
    if not compact or not re.fullmatch(r"\d+(?:\s*[,，、\s]\s*\d+)*", compact):
        return None
    numbers = [int(item) for item in re.findall(r"\d+", compact)]
    if any(number < 1 or number > candidate_count for number in numbers):
        return []
    return list(dict.fromkeys(numbers))


def _print_forget_selection(pending: dict) -> None:
    selected = set(str(item) for item in pending.get("selected_ids") or [])
    candidates = list(pending.get("candidates") or [])
    print("已选择 %d 条候选：" % len(selected))
    for index, item in enumerate(candidates, 1):
        if str(item.get("memory_id") or "") in selected:
            print("- %d. %s" % (index, item.get("summary") or item.get("memory_id")))
    print("输入 yes 或 确认执行；输入其他编号可重新选择；输入 no 或 取消停止。")


def _print_forget_pending_help(pending: dict) -> None:
    count = len(pending.get("memory_ids") or [])
    if count > 1:
        print("输入候选编号选择，例如 `1` 或 `1,3`；输入“全部”选择所有候选。")
        print("完成选择后输入 yes 或 确认执行；输入 no 或 取消停止。")
    else:
        print("输入 yes 或 确认执行；输入 no 或 取消停止。")


def _run_shell_line(service: MemGuardService, line: str, session_id: str = "", project_root: str = "") -> int:
    if line == "/status":
        return _status(service)
    if line == "/memory":
        return _memory(service)
    if line == "/preferences":
        return _preferences(service)
    if line == "/conflicts":
        return _conflicts(service)
    if line.startswith("/search "):
        return _search(service, line[len("/search "):].strip(), session_id=session_id)
    if line.startswith("/context "):
        return _context(service, line[len("/context "):].strip(), session_id=session_id, project_root=project_root)
    if line.startswith("/forget "):
        request = line[len("/forget "):].strip()
        confirm = False
        for flag in (" --yes", " --confirm"):
            if request.endswith(flag):
                request = request[: -len(flag)].strip()
                confirm = True
        return _forget(service, request, confirm=confirm, compact=confirm)
    if line == "/evidence":
        return _evidence(service)
    if line == "/lifecycle":
        return _lifecycle(service, None)
    if line.startswith("/lifecycle "):
        return _lifecycle(service, line[len("/lifecycle "):].strip())
    print("未识别的工作台命令：%s" % line)
    print("可用命令：/status、/memory、/search 关键词、/context 任务、/preferences、/conflicts、/forget 目标、/run 任务、/evidence、/lifecycle [记忆编号]、/help")
    print("这条输入没有写入记忆，也没有交给 Agent 执行。")
    return 0


def _run_agent_task(service: MemGuardService, task: str, state: dict) -> int:
    """Run a real OpenHands task and present a concise, user-facing result."""

    if not task:
        print("用法：/run 任务描述")
        return 0
    os.environ.setdefault("OPENHANDS_SUPPRESS_BANNER", "1")
    os.environ.setdefault("LOG_LEVEL", "WARNING")
    os.environ.setdefault("LITELLM_LOCAL_MODEL_COST_MAP", "True")
    from memguard.integrations.openhands_adapter import detect_openhands_runtime

    runtime = detect_openhands_runtime()
    _section("真实 Agent 运行")
    if not runtime.get("available"):
        print(_color("[未开始] 当前环境不能运行 OpenHands。", _ANSI_RED))
        print("处理方法：安装 Agent 桥接依赖后重试；仅验证机制可单独输入 /demo。")
        return 2

    model = str(state.get("model") or "").strip()
    if not model or model == "未配置":
        print("尚未设置模型：请先输入 `/model 模型名称`，再运行 `/run 任务`。")
        return 0
    api_key = _user_setting("OPENHANDS_API_KEY", "openhands_api_key", secret=True)
    if not api_key:
        print(_color("[未开始] 尚未配置模型访问凭据。", _ANSI_RED))
        print("处理方法：从权限受限的本地配置文件加载 OPENHANDS_API_KEY 后重试。")
        return 2

    from memguard.integrations.openhands_runner import OpenHandsMemoryRunner

    workspace = Path(str(state.get("workspace") or Path.cwd())).expanduser().resolve()
    session_id = str(state.get("session_id") or "shell-%s" % os.urandom(8).hex())
    run_id = "run-%s" % os.urandom(8).hex()
    state_root = Path(
        os.environ.get("XDG_STATE_HOME", str(Path.home() / ".local" / "state"))
    ).expanduser()
    state_dir = state_root / "memguardos" / "agent-shell" / run_id
    memory_enabled = bool(state.get("memory_enabled", True))
    runner = OpenHandsMemoryRunner(
        service=service,
        session_id=session_id,
        workspace=workspace,
        state_dir=state_dir,
        model=model,
        api_key=api_key,
        base_url=_user_setting("OPENHANDS_BASE_URL", "openhands_base_url"),
        memory_enabled=memory_enabled,
        allow_in_place_workspace=True,
    )
    print("任务：%s" % task)
    print("记忆辅助：%s" % ("开启（会检索并学习）" if memory_enabled else "关闭（本次不读取或写入记忆）"))
    print("工作区：%s" % workspace)
    print(_color("[1/3] 正在准备 Agent 和记忆上下文…", _ANSI_CYAN))
    print(_color("[2/3] Agent 正在执行自然语言任务…", _ANSI_CYAN))
    try:
        result = runner.run(task)
    except Exception as exc:
        print(_color("[未完成] Agent 运行失败：%s" % _friendly_agent_error(exc), _ANSI_RED))
        print("工作台仍可继续使用。检查网络、模型名称和工作区后重新运行 /run。")
        return 2

    context = result.get("context") or {}
    selected_ids = {
        memory_id
        for rows in (context.get("selected_memory_ids") or {}).values()
        for memory_id in rows
        if memory_id
    }
    summary = result.get("event_summary") or {}
    trace = result.get("execution_trace") or []
    commands = [
        item
        for item in trace
        if item.get("event_type") == "tool_call"
        and item.get("tool_name") != "finish"
        and item.get("status") == "success"
    ]
    blocked_commands = [
        item
        for item in trace
        if item.get("event_type") == "tool_call" and item.get("status") == "blocked"
    ]
    failed_commands = [
        item
        for item in trace
        if item.get("event_type") == "tool_call"
        and item.get("tool_name") == "terminal"
        and item.get("status") == "failure"
    ]
    final_messages = [
        str(item.get("message") or "").strip()
        for item in trace
        if (
            item.get("event_type") == "assistant_message"
            or (
                item.get("event_type") == "tool_call"
                and item.get("tool_name") == "finish"
            )
        )
    ]
    final_messages = [message for message in final_messages if message]
    execution_observed = bool(
        commands
        or final_messages
        or any(item.get("event_type") == "task_complete" for item in trace)
    )
    run_ok = bool(result.get("ok")) and execution_observed
    if run_ok:
        print(_color("[3/3] 已完成运行并整理结果。", _ANSI_GREEN))
    else:
        print(_color("[未完成] Agent 没有产生可验证的任务结果。", _ANSI_RED))
        completion = result.get("task_completion") or {}
        if completion.get("reason"):
            print("原因：%s" % completion["reason"])
        if not execution_observed:
            print("处理方法：检查上方 Hook、模型或工具错误，修复后重新运行本任务。")
    _subsection("结果概览")
    print("- 任务状态：%s" % ("完成" if run_ok else "未完成，需要检查"))
    print("- 采用记忆：%d 条" % len(selected_ids))
    print("- 终端命令：尝试 %d 次；成功 %d 次；失败 %d 次；策略阻止 %d 次" % (
        summary.get("terminal_command_attempts", 0),
        summary.get("successful_terminal_commands", 0),
        summary.get("failed_terminal_commands", 0),
        summary.get("blocked_tool_attempts", 0),
    ))
    if commands:
        _subsection("Agent 实际操作")
        for index, item in enumerate(commands[:6], 1):
            command = item.get("command") or "（%s 工具调用）" % (item.get("tool_name") or "未知")
            print("%d. %s" % (index, command))
        if len(commands) > 6:
            print("   另有 %d 次工具调用已折叠，可在审计记录中查看。" % (len(commands) - 6))
    if blocked_commands:
        _subsection("已阻止的 Agent 尝试")
        for item in blocked_commands[:3]:
            command = item.get("command") or "（%s 工具调用）" % (item.get("tool_name") or "未知")
            print("- %s" % command)
        print("这些尝试没有进入真实工具执行，也不会作为成功经验沉淀。")
    if failed_commands:
        _subsection("未成功的 Agent 操作")
        for item in failed_commands[:3]:
            command = item.get("command") or "（终端命令）"
            print("- %s（退出码 %s）" % (command, item.get("exit_code", "未知")))
        print("这些操作不会作为成功经验沉淀；若任务最终完成，说明 Agent 已自动换用可行方案。")
    if final_messages:
        _subsection("Agent 最终答复")
        print(final_messages[-1])

    changes = result.get("memory_change_summary") or {}
    consolidation = result.get("tool_consolidation") or {}
    learned = list(changes.get("new_memories") or []) + list(changes.get("updated_memories") or [])
    if memory_enabled:
        _subsection("本次记忆变化")
        if learned:
            categories = {}
            for item in learned:
                label = str(item.get("category_label") or "可复用记忆")
                categories[label] = categories.get(label, 0) + 1
            category_summary = "、".join("%s %d 项" % pair for pair in categories.items())
            print("检测到并处理 %d 项可复用内容：%s。" % (len(learned), category_summary))
            for index, item in enumerate(learned[:6], 1):
                print("%d. [%s · %s]" % (
                    index,
                    item.get("category_label") or "可复用记忆",
                    item.get("status_label") or "已保存",
                ))
                print("   %s" % (item.get("summary_for_user") or item.get("summary") or item.get("memory_id")))
                evidence = str(item.get("evidence_label") or "").strip()
                scopes = "、".join(str(value) for value in item.get("where_it_will_apply") or [])
                if evidence or scopes:
                    print("   来源：%s%s" % (
                        evidence or "已验证内容",
                        " · 后续用于：%s" % scopes if scopes else "",
                    ))
            if len(learned) > 6:
                print("另有 %d 项已保存，可在记忆中心查看。" % (len(learned) - 6))
        else:
            print("- 本轮没有新增稳定偏好或可复用经验；任务记录仍可在审计页查看。")
        if consolidation.get("promoted"):
            print("- 跨会话工具经验：新增 %d 条，后续同项目任务可检索；这是执行证据，不代表用户偏好。" % len(consolidation["promoted"]))
        elif consolidation.get("observations"):
            print("- 工具结果已纳入验证观察；至少两个独立完成的会话成功后，才会形成长期经验。")
        if summary.get("procedural_memories"):
            print("- 本轮工具执行记录 %d 条仅在当前会话可见，结束会话时清理。" % summary["procedural_memories"])
        blocked_items = list(changes.get("blocked_items") or [])
        if blocked_items:
            print("- 内容保护：%d 项敏感内容已阻止进入长期记忆和检索上下文。" % len(blocked_items))
        observed = [
            item
            for item in changes.get("observed_preferences") or []
            if item.get("status") in {"candidate", "reinforced", "negative_observation"}
        ]
        for item in observed[:4]:
            print("- 检测到%s：%s" % (
                item.get("category_label") or "用户偏好",
                item.get("summary_for_user") or "",
            ))
            print("  状态：%s · 依据 %s 条 · 来自 %s 个会话" % (
                item.get("status_label") or "正在观察",
                item.get("evidence_count", 1),
                item.get("distinct_sessions", 1),
            ))
    else:
        _subsection("本次记忆变化")
        print("- 记忆辅助已关闭，本次运行没有读取或写入长期记忆。")

    proofs = result.get("preference_effect_proofs", []) or []
    if proofs:
        _subsection("偏好作用验证")
    for proof in proofs:
        preference = proof.get("preference", {})
        category, description = _preference_display(
            str(preference.get("topic") or ""),
            str(preference.get("value") or ""),
        )
        print("- [%s] %s：%s" % (
            category,
            description,
            {"passed": "已观察到并通过", "failed": "未遵循", "not_applicable": "暂无实际行为证据"}.get(
                proof.get("compliance"), proof.get("compliance")
            ),
        ))
    _subsection("下一步")
    if memory_enabled:
        print("- 输入 /preferences 查看已形成的偏好。")
        print("- 开启新一轮 /run 且不重复偏好要求，观察 Agent 是否自动遵循。")
    else:
        print("- 输入 /memory on 后运行同一任务，可直观看到记忆辅助前后差异。")
    print("- 输入 /audit 查看本次记忆操作记录。")
    return 0 if run_ok else 2


def _preference_display(topic: str, value: str) -> tuple[str, str]:
    if topic == "code_search_tool":
        return "工具偏好", "搜索项目代码时优先使用 %s" % value
    if topic == "test_runner_command":
        return "工具偏好", "运行 Python 测试时优先使用 %s" % value
    if topic == "service_log_tool":
        return "工具偏好", "查看服务日志时优先使用 %s" % value
    if topic == "response_order":
        return "回答偏好", "先给可执行命令或明确结论，再说明原因"
    if topic == "response_language":
        return "回答偏好", "使用中文回答"
    if topic == "response_verbosity":
        return "回答偏好", "保持简洁并突出关键步骤"
    if topic == "high_risk_confirmation":
        return "安全策略", "高风险操作必须先获得确认"
    return "用户偏好", "%s：%s" % (topic or "交互方式", value)


def _scope_display(scope: str) -> str:
    return {
        "global": "全部会话",
        "agent": "当前 Agent",
        "project": "当前项目",
        "task_type": "同类任务",
        "session": "当前会话",
    }.get(scope, "相关任务")


def _memory_mode(line: str, state: dict) -> int:
    action = line.split()[-1]
    if action == "on":
        state["memory_enabled"] = True
        print("记忆辅助已开启：后续 /run 会检索相关记忆，并沉淀新的有效经验。")
    elif action == "off":
        state["memory_enabled"] = False
        print("记忆辅助已关闭：后续 /run 不读取或写入长期记忆，适合录制对照组。")
    else:
        print("当前记忆辅助：%s" % ("开启" if state.get("memory_enabled", True) else "关闭"))
    return 0


def _compare_live(
    service: MemGuardService,
    task: str,
    *,
    workspace: str,
    model: str,
) -> int:
    _section("真实 Agent 记忆对照")
    print("同一任务将运行两次：第一轮关闭记忆，第二轮开启记忆。")
    print("请确保已经写入与任务相关的偏好或经验；本命令不会自动制造测试记忆。")
    common = {
        "workspace": str(Path(workspace or Path.cwd()).expanduser()),
        "model": str(model or "未配置"),
    }
    print("\n对照组：关闭记忆")
    off_code = _run_agent_task(service, task, {**common, "memory_enabled": False})
    if off_code != 0:
        print("对照组未完成，已停止第二轮，避免形成不可比较的结果。")
        return off_code
    print("\n实验组：开启记忆")
    on_code = _run_agent_task(service, task, {**common, "memory_enabled": True})
    if on_code == 0:
        print("\n对照完成：请比较两轮的工具调用、最终答复和偏好作用验证。")
    return on_code


def _shell_guide(state: dict) -> None:
    _section("四步效果演示")
    print("1. 基线：/memory off，然后 /run 一个自然语言任务。")
    print("2. 学习：/memory on，再通过正常交互纠正工具或回答格式。")
    print("3. 复用：开启新一轮 /run，不重复偏好要求，观察 Agent 自动遵循。")
    print("4. 遗忘：直接说“忘掉……”，查看预览后输入 yes/确认，随后重新运行任务。")
    print("当前模型：%s" % state.get("model", "未配置"))
    print("当前工作区：%s" % state.get("workspace", ""))
    print("当前记忆辅助：%s" % ("开启" if state.get("memory_enabled", True) else "关闭"))


def _friendly_agent_error(exc: Exception) -> str:
    message = " ".join(str(exc).split())
    lowered = message.lower()
    if "model_not_found" in lowered or ("model" in lowered and "disabled" in lowered):
        return "当前模型不存在、已停用或不可访问。请在 /model 中选择当前接口可用的模型；记忆数据不受影响。"
    if "workspace" in lowered:
        return "工作区不可用，请用 /workspace 设置一个存在且可访问的项目目录。"
    if any(marker in lowered for marker in ("timeout", "timed out")):
        return "模型服务响应超时，请检查网络后重试。"
    if any(marker in lowered for marker in ("401", "unauthorized", "api key")):
        return "模型访问凭据无效或已失效。"
    if any(marker in lowered for marker in ("404", "model not found")):
        return "模型名称或服务地址不正确。"
    return message[:240] or "运行环境返回了未知错误。"


def _user_setting(
    env_name: str,
    file_name: str,
    *,
    secret: bool = False,
    home: Path | None = None,
) -> str:
    value = str(os.environ.get(env_name) or "").strip()
    if value:
        return value
    path = (home or Path.home()) / ".config" / "memguardos" / file_name
    try:
        if not path.is_file():
            return ""
        if secret and os.name == "posix" and path.stat().st_mode & 0o077:
            return ""
        return path.read_text(encoding="utf-8").strip()
    except OSError:
        return ""


def _section(title: str) -> None:
    divider = "─" * 54
    print("\n%s" % _color(divider, _ANSI_DIM))
    print(_color(title, _ANSI_BLUE))
    print(_color(divider, _ANSI_DIM))


def _subsection(title: str) -> None:
    print("\n%s" % _color(title, _ANSI_CYAN))


def _prompt_text(prompt: str) -> str:
    if not _colors_enabled():
        return prompt
    # Keep the input color active until input() returns so the command the user
    # types is visually distinct from the result printed on the following lines.
    return "%s%s" % (_ANSI_BLUE, prompt)


def _restore_terminal_color() -> None:
    if not _colors_enabled():
        return
    sys.stdout.write(_ANSI_RESET)
    sys.stdout.flush()


def _color(text: str, code: str) -> str:
    if not _colors_enabled():
        return text
    return "%s%s%s" % (code, text, _ANSI_RESET)


def _colors_enabled() -> bool:
    if os.environ.get("NO_COLOR") is not None or os.environ.get("TERM") == "dumb":
        return False
    if os.environ.get("FORCE_COLOR") == "1":
        return True
    return bool(getattr(sys.stdout, "isatty", lambda: False)())


def _compact_json(value: object) -> str:
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"))


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
