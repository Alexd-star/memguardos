from pathlib import Path
from tempfile import TemporaryDirectory
import json
import sys

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from memguard.ingestion.tool_result import ToolResult  # noqa: E402
from memguard.service import MemGuardService  # noqa: E402


def main() -> int:
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
                    generated_files=["docs/test_report.md", "docs/eval_report.md"],
                    tool_name="pytest",
                ),
                source_uri="demo_tool_result",
            )
            os_event = service.ingest_os_event('{"event_type":"fanotify_open","path":"/home/demo/.ssh/id_rsa","process":"cat","action":"read"}')
            print_json({"preference": summarize_result(pref), "workflow": summarize_result(workflow), "tool_result": summarize_result(tool), "os_event": summarize_result(os_event)})

            print_step("2. Hybrid and graph retrieval")
            print_json({"search": service.search("openKylin 服务内存限制", limit=5)})
            print_json({"tool_search": service.search("pytest passed test_report", limit=5)})
            print_json({"graph_search": service.graph_search("systemd", limit=5)})
            print_json({"related": service.related(workflow["record"].memory_id, limit=5)})

            print_step("3. Sensitive blocking")
            blocked = service.ingest_text("HF token is hf_abcdefghijklmnopqrstuvwxyz123456", source_uri="/home/demo/.env")
            print_json(summarize_result(blocked))

            print_step("4. Precise forgetting")
            secret_text = "temporary project codename blue-river"
            secret = service.ingest_text(secret_text)
            plan = service.forget("忘掉 temporary project codename")
            done = service.forget("忘掉 temporary project codename", confirm=True)
            before = service.verify_forget_probe(secret["record"].memory_id, secret_text)
            service.compact()
            after = service.verify_forget_probe(secret["record"].memory_id, secret_text)
            print_json({"plan": plan, "done": done, "before_compact": before, "after_compact": after})

            print_step("5. Audit verification")
            print_json(service.verify_audit())
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


if __name__ == "__main__":
    raise SystemExit(main())
