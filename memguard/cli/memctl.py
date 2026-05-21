from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from memguard.ingestion.tool_result import ToolResult
from memguard.llm.adapter import LLMAdapterError, generate_answer
from memguard.llm.prompt import render_generation_prompt
from memguard.os_guard.auditd_events import parse_auditd_events
from memguard.os_guard.auditd import audit_rules_file, auditctl_rules, ausearch_command
from memguard.os_guard.doctor import doctor_report
from memguard.os_guard.cgroup import cgroup_status
from memguard.retrieval.kylin_sdk import kylin_sdk_status
from memguard.service import MemGuardService


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="memctl", description="MemGuardOS command-line tool")
    sub = parser.add_subparsers(dest="command", required=True)

    ingest = sub.add_parser("ingest", help="Ingest text or a file into memory")
    ingest.add_argument("--text", help="Text content to ingest")
    ingest.add_argument("--file", help="File path to ingest")
    ingest.add_argument("--source-kind", default="manual")
    ingest.add_argument("--tier", choices=["working", "session", "long_term"], help="Memory tier")

    tool_result = sub.add_parser("ingest-tool-result", help="Ingest a structured tool execution result JSON")
    tool_result.add_argument("--file", required=True, help="JSON file containing command/cwd/exit_code/stdout/stderr/duration_ms")

    ingest_config = sub.add_parser("ingest-config", help="Ingest structured manual configuration from JSON or a simple YAML file")
    ingest_config.add_argument("--file", required=True, help="Config file path")

    ingest_history = sub.add_parser("ingest-history", help="Ingest memory-worthy shell history as user behavior data")
    ingest_history.add_argument("--file", required=True, help="Shell history file such as ~/.bash_history or ~/.zsh_history")
    ingest_history.add_argument("--limit", type=int, default=200)

    os_event = sub.add_parser("ingest-os-event", help="Ingest a JSON/auditd/fanotify-style OS event")
    os_event.add_argument("--json", help="Raw JSON event")
    os_event.add_argument("--line", help="Raw audit/fanotify line")
    os_event.add_argument("--file", help="File containing one event line")

    auditd_ingest = sub.add_parser("ingest-auditd", help="Parse auditd log lines and ingest matching sensitive access events")
    auditd_ingest.add_argument("--file", help="Audit log file or '-' for stdin", default="-")
    auditd_ingest.add_argument("--key", default="memguard_sensitive")
    auditd_ingest.add_argument("--limit", type=int, default=1000, help="Only parse the last N lines from a file")
    auditd_ingest.add_argument("--summary", action="store_true", help="Print compact counts and blocked samples")

    update = sub.add_parser("update", help="Append a new version of an existing memory")
    update.add_argument("memory_id")
    update.add_argument("--text", required=True)
    update.add_argument("--reason", default="manual update")

    listing = sub.add_parser("list", help="List memories")
    listing.add_argument("--include-deleted", action="store_true")
    listing.add_argument("--tier", choices=["working", "session", "long_term"])

    promote = sub.add_parser("promote", help="Promote or demote a memory to another tier")
    promote.add_argument("memory_id")
    promote.add_argument("tier", choices=["working", "session", "long_term"])

    evict = sub.add_parser("evict-tier", help="Evict working/session tier memories")
    evict.add_argument("tier", choices=["working", "session"])
    evict.add_argument("--yes", action="store_true")

    versions = sub.add_parser("versions", help="List all versions of a memory")
    versions.add_argument("memory_id")

    rollback = sub.add_parser("rollback", help="Rollback a memory to a previous version")
    rollback.add_argument("memory_id")
    rollback.add_argument("version_id")

    search = sub.add_parser("search", help="Search memories")
    search.add_argument("query")
    search.add_argument("--top-k", type=int, default=5)

    context = sub.add_parser("build-context", help="Build a redacted RAG context package for OS Agent generation")
    context.add_argument("query")
    context.add_argument("--top-k", type=int, default=5)
    context.add_argument("--max-chars", type=int, default=2000)
    context.add_argument("--token-budget", type=int, default=None, help="Approximate token budget for the returned context package")

    prompt = sub.add_parser("build-prompt", help="Render a provider-neutral LLM prompt from a redacted context package")
    prompt.add_argument("query")
    prompt.add_argument("--top-k", type=int, default=5)
    prompt.add_argument("--max-chars", type=int, default=2000)
    prompt.add_argument("--token-budget", type=int, default=None)

    generate = sub.add_parser("generate-answer", help="Optionally call an external/local LLM using the redacted context prompt")
    generate.add_argument("query")
    generate.add_argument("--top-k", type=int, default=5)
    generate.add_argument("--max-chars", type=int, default=2000)
    generate.add_argument("--token-budget", type=int, default=None)
    generate.add_argument("--provider", choices=["dry-run", "local-command", "openai-compatible"], default="dry-run")
    generate.add_argument("--command", dest="local_command", default="", help="Command used by provider=local-command; prompt is passed on stdin")
    generate.add_argument("--endpoint", default="", help="Chat completions endpoint used by provider=openai-compatible")
    generate.add_argument("--model", default="", help="Model name used by provider=openai-compatible")
    generate.add_argument("--api-key-file", default="", help="Optional API key file; never required for dry-run")
    generate.add_argument("--timeout", type=float, default=30.0)

    graph_search = sub.add_parser("graph-search", help="Search memories by extracted entity")
    graph_search.add_argument("entity")
    graph_search.add_argument("--limit", type=int, default=10)

    related = sub.add_parser("related", help="Show graph entities and related memories")
    related.add_argument("memory_id")
    related.add_argument("--limit", type=int, default=10)

    forget = sub.add_parser("forget", help="Plan or execute natural-language forgetting")
    forget.add_argument("request")
    forget.add_argument("--yes", action="store_true", help="Actually forget candidates")
    forget.add_argument("--compact", action="store_true", help="Compact segment after forgetting")

    verify = sub.add_parser("verify-forget", help="Verify one memory is forgotten")
    verify.add_argument("memory_id")
    verify.add_argument("--probe", default="", help="Plaintext probe that must not remain in segment storage")

    sub.add_parser("compact", help="Compact append-only segment and purge forgotten plaintext")
    sub.add_parser("verify-audit", help="Verify SQLite audit hash chain")
    sub.add_parser("resolve-conflicts", help="Resolve preference-memory conflicts")

    audit = sub.add_parser("audit", help="Show audit events")
    audit.add_argument("--limit", type=int, default=20)
    audit.add_argument("--type", dest="event_type")

    patterns = sub.add_parser("patterns", help="List or add custom sensitive patterns")
    pattern_sub = patterns.add_subparsers(dest="pattern_command", required=True)
    pattern_sub.add_parser("list", help="List custom patterns")
    pattern_add = pattern_sub.add_parser("add", help="Add a custom sensitive pattern")
    pattern_add.add_argument("name")
    pattern_add.add_argument("regex")

    sub.add_parser("doctor", help="Check local OS/runtime capabilities")
    sub.add_parser("status", help="Show runtime OS guard status")
    sdk_status = sub.add_parser("sdk-status", help="Detect openKylin Kylin AI embedding/vector SDK capabilities")
    sdk_status.add_argument("--probe-embedding", action="store_true", help="Run one real embedding call; may be slow if the model service is not ready")

    auditd = sub.add_parser("auditd-rules", help="Print auditd rules for sensitive path monitoring")
    auditd.add_argument("--format", choices=["auditctl", "rules-file", "ausearch"], default="auditctl")
    auditd.add_argument("--key", default="memguard_sensitive")
    auditd.add_argument("--existing-only", action="store_true", help="Only emit rules for paths that exist on this host")

    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    service = MemGuardService()
    try:
        if args.command == "ingest":
            text, source_uri = _read_ingest(args)
            result = service.ingest_text(text, source_kind=args.source_kind, source_uri=source_uri, tier=args.tier)
            if result["stored"]:
                record = result["record"]
                print(json.dumps({"stored": True, "memory_id": record.memory_id, "type": record.type, "subtype": record.subtype, "tier": record.tier}, ensure_ascii=False))
            else:
                print(json.dumps({"stored": False, "reason": result["reason"], "sensitivity": result["sensitivity"].__dict__}, ensure_ascii=False))
            return 0

        if args.command == "ingest-tool-result":
            path = Path(args.file)
            result = service.ingest_tool_result(ToolResult.from_json_file(path), source_uri=str(path))
            printable = dict(result)
            if printable.get("record"):
                record = printable["record"]
                printable["record"] = {
                    "memory_id": record.memory_id,
                    "version_id": record.version.version_id,
                    "type": record.type,
                    "subtype": record.subtype,
                    "tier": record.tier,
                }
            if printable.get("sensitivity"):
                printable["sensitivity"] = printable["sensitivity"].__dict__
            print(json.dumps(printable, ensure_ascii=False, indent=2))
            return 0

        if args.command == "ingest-config":
            print(json.dumps(service.ingest_config(Path(args.file)), ensure_ascii=False, indent=2))
            return 0

        if args.command == "ingest-history":
            print(json.dumps(service.ingest_shell_history(Path(args.file), limit=args.limit), ensure_ascii=False, indent=2))
            return 0

        if args.command == "ingest-os-event":
            raw = _read_os_event(args)
            result = service.ingest_os_event(raw)
            printable = dict(result)
            if printable.get("record"):
                record = printable["record"]
                printable["record"] = {"memory_id": record.memory_id, "version_id": record.version.version_id, "type": record.type, "subtype": record.subtype}
            if printable.get("sensitivity"):
                printable["sensitivity"] = printable["sensitivity"].__dict__
            print(json.dumps(printable, ensure_ascii=False, indent=2))
            return 0

        if args.command == "ingest-auditd":
            lines = _read_auditd_lines(args.file, args.limit)
            events = parse_auditd_events(lines, key=args.key)
            ingested = []
            for event in events:
                result = service.ingest_os_event(json.dumps(event.to_os_event_json(), ensure_ascii=False))
                ingested.append(
                    {
                        "audit_id": event.audit_id,
                        "path": event.path,
                        "process": event.process,
                        "action": event.action,
                        "stored": bool(result.get("stored")),
                        "reason": result.get("reason"),
                    }
                )
            if args.summary:
                blocked = [item for item in ingested if item["reason"] == "sensitive_block"]
                print(
                    json.dumps(
                        {
                            "parsed": len(events),
                            "ingested_count": len(ingested),
                            "blocked_count": len(blocked),
                            "blocked_samples": blocked[-8:],
                        },
                        ensure_ascii=False,
                        indent=2,
                    )
                )
            else:
                print(json.dumps({"parsed": len(events), "ingested": ingested}, ensure_ascii=False, indent=2))
            return 0

        if args.command == "list":
            print(json.dumps(service.list_memories(include_deleted=args.include_deleted, tier=args.tier), ensure_ascii=False, indent=2))
            return 0

        if args.command == "promote":
            result = service.promote_memory(args.memory_id, args.tier)
            printable = dict(result)
            if printable.get("record"):
                record = printable["record"]
                printable["record"] = {"memory_id": record.memory_id, "version_id": record.version.version_id, "subtype": record.subtype, "tier": record.tier}
            print(json.dumps(printable, ensure_ascii=False, indent=2))
            return 0

        if args.command == "evict-tier":
            print(json.dumps(service.evict_tier(args.tier, confirm=args.yes), ensure_ascii=False, indent=2))
            return 0

        if args.command == "update":
            result = service.update_memory(args.memory_id, args.text, reason=args.reason)
            printable = dict(result)
            if printable.get("record"):
                record = printable["record"]
                printable["record"] = {"memory_id": record.memory_id, "version_id": record.version.version_id, "type": record.type, "subtype": record.subtype}
            if printable.get("sensitivity"):
                printable["sensitivity"] = printable["sensitivity"].__dict__
            print(json.dumps(printable, ensure_ascii=False, indent=2))
            return 0

        if args.command == "versions":
            print(json.dumps(service.versions(args.memory_id), ensure_ascii=False, indent=2))
            return 0

        if args.command == "rollback":
            result = service.rollback(args.memory_id, args.version_id)
            printable = dict(result)
            if printable.get("record"):
                record = printable["record"]
                printable["record"] = {"memory_id": record.memory_id, "version_id": record.version.version_id, "type": record.type, "subtype": record.subtype}
            print(json.dumps(printable, ensure_ascii=False, indent=2))
            return 0

        if args.command == "search":
            print(json.dumps(service.search(args.query, limit=args.top_k), ensure_ascii=False, indent=2))
            return 0

        if args.command == "build-context":
            print(
                json.dumps(
                    service.build_context(args.query, limit=args.top_k, max_chars=args.max_chars, token_budget=args.token_budget),
                    ensure_ascii=False,
                    indent=2,
                )
            )
            return 0

        if args.command == "build-prompt":
            context_package = service.build_context(args.query, limit=args.top_k, max_chars=args.max_chars, token_budget=args.token_budget)
            print(render_generation_prompt(context_package))
            return 0

        if args.command == "generate-answer":
            context_package = service.build_context(args.query, limit=args.top_k, max_chars=args.max_chars, token_budget=args.token_budget)
            prompt_text = render_generation_prompt(context_package)
            api_key = Path(args.api_key_file).read_text(encoding="utf-8").strip() if args.api_key_file else ""
            try:
                result = generate_answer(
                    prompt_text,
                    provider=args.provider,
                    model=args.model,
                    endpoint=args.endpoint,
                    api_key=api_key,
                    command=args.local_command,
                    timeout=args.timeout,
                )
                print(json.dumps(result.to_dict(), ensure_ascii=False, indent=2))
                return 0
            except LLMAdapterError as exc:
                print(json.dumps({"ok": False, "error": str(exc)}, ensure_ascii=False, indent=2))
                return 2

        if args.command == "graph-search":
            print(json.dumps(service.graph_search(args.entity, limit=args.limit), ensure_ascii=False, indent=2))
            return 0

        if args.command == "related":
            print(json.dumps(service.related(args.memory_id, limit=args.limit), ensure_ascii=False, indent=2))
            return 0

        if args.command == "forget":
            print(json.dumps(service.forget(args.request, confirm=args.yes, compact=args.compact), ensure_ascii=False, indent=2))
            return 0

        if args.command == "verify-forget":
            print(json.dumps(service.verify_forget_probe(args.memory_id, args.probe), ensure_ascii=False, indent=2))
            return 0

        if args.command == "compact":
            print(json.dumps(service.compact(), ensure_ascii=False, indent=2))
            return 0

        if args.command == "verify-audit":
            print(json.dumps(service.verify_audit(), ensure_ascii=False, indent=2))
            return 0

        if args.command == "resolve-conflicts":
            print(json.dumps(service.resolve_conflicts(), ensure_ascii=False, indent=2))
            return 0

        if args.command == "audit":
            print(json.dumps(service.audit(limit=args.limit, event_type=args.event_type), ensure_ascii=False, indent=2))
            return 0

        if args.command == "patterns":
            if args.pattern_command == "list":
                print(json.dumps(service.list_patterns(), ensure_ascii=False, indent=2))
                return 0
            if args.pattern_command == "add":
                result = service.try_add_pattern(args.name, args.regex)
                print(json.dumps(result, ensure_ascii=False, indent=2))
                return 0 if result["ok"] else 2

        if args.command == "doctor":
            print(json.dumps(doctor_report(), ensure_ascii=False, indent=2))
            return 0

        if args.command == "status":
            print(json.dumps(cgroup_status(), ensure_ascii=False, indent=2))
            return 0

        if args.command == "sdk-status":
            print(json.dumps(kylin_sdk_status(probe_embedding=args.probe_embedding), ensure_ascii=False, indent=2))
            return 0

        if args.command == "auditd-rules":
            if args.format == "auditctl":
                print("\n".join(auditctl_rules(key=args.key, existing_only=args.existing_only)))
            elif args.format == "rules-file":
                print(audit_rules_file(key=args.key, existing_only=args.existing_only), end="")
            else:
                print(ausearch_command(key=args.key))
            return 0
    finally:
        service.close()
    return 1


def _read_ingest(args: argparse.Namespace) -> tuple[str, str]:
    if args.text:
        return args.text, "inline"
    if args.file:
        path = Path(args.file)
        return path.read_text(encoding="utf-8"), str(path)
    raise SystemExit("ingest requires --text or --file")


def _read_os_event(args: argparse.Namespace) -> str:
    if args.json:
        return args.json
    if args.line:
        return args.line
    if args.file:
        return Path(args.file).read_text(encoding="utf-8").splitlines()[0]
    raise SystemExit("ingest-os-event requires --json, --line, or --file")


def _read_auditd_lines(path: str, limit: int) -> list[str]:
    if path == "-":
        return sys.stdin.read().splitlines()
    lines = Path(path).read_text(encoding="utf-8", errors="replace").splitlines()
    return lines[-max(1, limit) :]


if __name__ == "__main__":
    sys.exit(main())
