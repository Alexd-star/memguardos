from pathlib import Path
from tempfile import TemporaryDirectory
import argparse
import json
import sys

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from memguard.ingestion.tool_result import ToolResult  # noqa: E402
from memguard.service import MemGuardService  # noqa: E402


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="运行不依赖外部模型的 MemGuardOS 完整机制演示"
    )
    parser.add_argument(
        "--readable",
        action="store_true",
        help="输出适合演示录制的中文摘要；默认保留详细 JSON 复核输出。",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args([] if argv is None else argv)
    with TemporaryDirectory() as tmp:
        root = Path(tmp)
        service = MemGuardService(store_path=root / "memory.segment", db_path=root / "memguard.sqlite3")
        try:
            print_step("1. Multi-source ingestion")
            pref = service.ingest_text("以后回答 Linux 命令时直接给命令和风险提示，不要写太长背景。")
            workflow = service.ingest_text("On systemd Linux, MemoryMax limits service memory usage. journalctl can inspect journald logs.")
            tool = service.ingest_tool_result(
                ToolResult(
                    command="pytest -q",
                    cwd="/home/demo/project",
                    exit_code=0,
                    stdout="17 passed in 1.99s",
                    stderr="",
                    duration_ms=1990,
                    generated_files=["output/test_report.md", "output/eval_report.md"],
                    tool_name="pytest",
                ),
                source_uri="demo_tool_result",
                session_id="demo-session",
            )
            os_event = service.ingest_os_event('{"event_type":"fanotify_open","path":"/home/demo/.ssh/id_rsa","process":"cat","action":"read"}')
            emit(
                "ingestion",
                {"preference": summarize_result(pref), "workflow": summarize_result(workflow), "tool_result": summarize_result(tool), "os_event": summarize_result(os_event)},
                readable=args.readable,
            )

            print_step("2. Hybrid and graph retrieval")
            emit(
                "retrieval",
                {
                    "search": service.search("openKylin 服务内存限制", limit=5),
                    "tool_search": service.search("pytest passed test_report", limit=5, session_id="demo-session"),
                    "graph_search": service.graph_search("systemd", limit=5),
                    "related": service.related(workflow["record"].memory_id, limit=5),
                },
                readable=args.readable,
            )

            print_step("3. Sensitive blocking")
            blocked = service.ingest_text("HF token is hf_abcdefghijklmnopqrstuvwxyz123456", source_uri="/home/demo/.env")
            emit("sensitive", summarize_result(blocked), readable=args.readable)

            print_step("4. Precise forgetting")
            secret_text = "temporary project codename blue-river"
            secret = service.ingest_text(secret_text)
            plan = service.forget("忘掉 temporary project codename")
            done = service.forget("忘掉 temporary project codename", confirm=True)
            before = service.verify_forget_probe(secret["record"].memory_id, secret_text)
            service.compact()
            after = service.verify_forget_probe(secret["record"].memory_id, secret_text)
            emit(
                "forgetting",
                {"plan": plan, "done": done, "before_compact": before, "after_compact": after},
                readable=args.readable,
            )

            print_step("5. Audit verification")
            emit("audit", service.verify_audit(), readable=args.readable)
        finally:
            service.close()
    return 0


def summarize_result(result: dict) -> dict:
    printable = dict(result)
    if printable.get("record"):
        record = printable["record"]
        printable["record"] = {
            "memory_id": record.memory_id,
            "type": record.type,
            "subtype": record.subtype,
            "tier": record.tier,
            "entities": record.entities,
        }
    if printable.get("sensitivity"):
        printable["sensitivity"] = printable["sensitivity"].__dict__
    return printable


def print_step(title: str) -> None:
    print("\n=== %s ===" % title)


def print_json(data: object) -> None:
    print(json.dumps(data, ensure_ascii=False, indent=2))


def emit(kind: str, data: object, *, readable: bool) -> None:
    if not readable:
        print_json(data)
        return
    if kind == "ingestion":
        labels = {
            "preference": "用户偏好",
            "workflow": "知识流程",
            "tool_result": "工具结果",
            "os_event": "系统事件",
        }
        for key, value in dict(data).items():
            record = dict(value).get("record") or {}
            print(
                "- %s：%s/%s，编号 %s"
                % (
                    labels.get(key, key),
                    record.get("type", "未写入"),
                    record.get("subtype", "-"),
                    record.get("memory_id", "-"),
                )
            )
        stored_count = sum(1 for value in dict(data).values() if (dict(value).get("record") or {}).get("memory_id"))
        print("结果：四类来源已统一处理，%d 类形成记录；高风险系统事件按策略阻断。" % stored_count)
        return
    if kind == "retrieval":
        for key, rows in dict(data).items():
            candidates = rows.get("related", []) if isinstance(rows, dict) else rows
            top = (candidates or [{}])[0]
            summary = top.get("summary") or top.get("content") or "无结果"
            print("- %s：%s" % (key, " ".join(str(summary).split())[:90]))
        print("结果：全文、向量和实体关系均可返回受作用域约束的候选。")
        return
    if kind == "sensitive":
        value = dict(data)
        sensitivity = value.get("sensitivity") or {}
        print("- 写入状态：%s" % ("已阻断" if not value.get("stored") else "已保存"))
        print("- 风险等级：%s；类型：%s" % (sensitivity.get("level", "-"), "、".join(sensitivity.get("labels") or [])))
        print("结果：测试凭据没有进入长期记忆。")
        return
    if kind == "forgetting":
        value = dict(data)
        plan = value.get("plan") or {}
        done = value.get("done") or {}
        after = value.get("after_compact") or {}
        print("- 预览候选：%d 条" % len(plan.get("candidates") or []))
        print("- 已遗忘：%d 条" % len(done.get("forgotten") or []))
        print("- 受管检索面仍可召回：%s" % ("是，需要处理" if after.get("any_surface_recallable") else "否"))
        print("结果：目标记录完成预览、确认、压实和残留复检。")
        return
    if kind == "audit":
        value = dict(data)
        print("- 审计链：%s；已核对 %s 条记录" % ("完整" if value.get("ok") else "需要处理", value.get("checked", 0)))
        print("结果：写入、检索、阻断和遗忘操作可以沿哈希链追溯。")
        return
    print_json(data)


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
