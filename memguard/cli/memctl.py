from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from memguard.config import list_namespaces
from memguard.ingestion.tool_result import ToolResult
from memguard.integrations.replay import load_event_file, load_event_jsonl
from memguard.llm.adapter import LLMAdapterError, generate_answer
from memguard.llm.prompt import render_generation_prompt
from memguard.os_guard.auditd_events import parse_auditd_events
from memguard.os_guard.auditd import audit_rules_file, auditctl_rules, ausearch_command
from memguard.os_guard.doctor import doctor_report
from memguard.os_guard.cgroup import cgroup_status
from memguard.retrieval.kylin_sdk import kylin_sdk_status
from memguard.service import MemGuardService


_SERVICE_FREE_COMMANDS = {"doctor", "status", "namespaces", "sdk-status", "auditd-rules"}


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="memctl", description="MemGuardOS command-line tool")
    parser.add_argument(
        "--namespace",
        default=None,
        help="Memory namespace/session id. Defaults to MEMGUARD_NAMESPACE or 'default'.",
    )
    sub = parser.add_subparsers(dest="command", required=True)

    ingest = sub.add_parser("ingest", help="Ingest text or a file into memory")
    ingest.add_argument("--text", help="Text content to ingest")
    ingest.add_argument("--file", help="File path to ingest")
    ingest.add_argument("--source-kind", default="manual")
    ingest.add_argument("--tier", choices=["working", "session", "long_term"], help="Memory tier")
    explain_extraction = sub.add_parser("explain-extraction", help="Explain preference/knowledge extraction without storing memory")
    explain_extraction.add_argument("--text", required=True)
    explain_extraction.add_argument("--source-kind", default="manual")

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
    memory_policy = sub.add_parser("memory-policy", help="Show tiered memory policy, storage footprint, cgroup status, and eviction candidates")
    memory_policy.add_argument("--eviction-limit", type=int, default=10)
    temporal = sub.add_parser("temporal-status", help="Show temporal validity windows for active and superseded memories")
    temporal.add_argument("--limit", type=int, default=20)

    governance = sub.add_parser("governance", help="List and review quarantined memory sources")
    governance_sub = governance.add_subparsers(dest="governance_command", required=True)
    governance_list = governance_sub.add_parser("list", help="List quarantined memories")
    governance_list.add_argument("--limit", type=int, default=100)
    governance_review = governance_sub.add_parser("review", help="Approve or reject one quarantined memory")
    governance_review.add_argument("memory_id")
    governance_review.add_argument("decision", choices=["approve", "reject"])
    governance_review.add_argument("--reviewer", required=True)
    governance_review.add_argument("--reason", required=True)

    versions = sub.add_parser("versions", help="List all versions of a memory")
    versions.add_argument("memory_id")

    rollback = sub.add_parser("rollback", help="Rollback a memory to a previous version")
    rollback.add_argument("memory_id")
    rollback.add_argument("version_id")

    search = sub.add_parser("search", help="Search memories")
    search.add_argument("query")
    search.add_argument("--top-k", type=int, default=5)
    batch_search = sub.add_parser("batch-search", help="Search several OS Agent planning/tool/generation queries concurrently")
    batch_search.add_argument("queries", nargs="+", help="One or more queries. Repeat arguments or pass a JSON array with --json.")
    batch_search.add_argument("--json", action="store_true", help="Interpret the first argument as a JSON array of query strings")
    batch_search.add_argument("--top-k", type=int, default=5)
    batch_search.add_argument("--workers", type=int, default=4)

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

    hierarchical_graph = sub.add_parser("hierarchical-graph-search", help="Search graph memory as summary/entity-relation/chunk layers")
    hierarchical_graph.add_argument("entity")
    hierarchical_graph.add_argument("--limit", type=int, default=10)

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
    sub.add_parser("quality-report", help="Show recent input quality validation audit events")

    diagnostics = sub.add_parser("diagnostics", help="Export local operational diagnostics")
    diagnostics.add_argument("--probe-sdk", action="store_true", help="Run a real Kylin embedding SDK probe in the diagnostics report")
    diagnostics.add_argument("--probe-timeout", type=float, default=90.0, help="Timeout in seconds for the optional SDK probe")
    diagnostics.add_argument("--audit-limit", type=int, default=20, help="Number of recent audit events to include")

    web = sub.add_parser("web", help="Run a localhost Web dashboard for local operation and inspection")
    web.add_argument("--host", default="127.0.0.1")
    web.add_argument("--port", type=int, default=8765)
    web.add_argument("--auth-token-file", default="", help="Optional bearer token file for dashboard API calls")

    patterns = sub.add_parser("patterns", help="List or add custom sensitive patterns")
    pattern_sub = patterns.add_subparsers(dest="pattern_command", required=True)
    pattern_sub.add_parser("list", help="List custom patterns")
    pattern_add = pattern_sub.add_parser("add", help="Add a custom sensitive pattern")
    pattern_add.add_argument("name")
    pattern_add.add_argument("regex")
    pattern_suggest = pattern_sub.add_parser("suggest", help="Suggest a validated sensitive regex from a natural-language instruction")
    pattern_suggest.add_argument("instruction")
    pattern_suggest.add_argument("--name", default="", help="Optional pattern name; inferred when omitted")
    pattern_add_natural = pattern_sub.add_parser("add-natural", help="Add a custom sensitive pattern from a natural-language instruction")
    pattern_add_natural.add_argument("instruction")
    pattern_add_natural.add_argument("--name", default="", help="Optional pattern name; inferred when omitted")

    sub.add_parser("doctor", help="Check local OS/runtime capabilities")
    sub.add_parser("status", help="Show runtime OS guard status")
    sub.add_parser("namespace-status", help="Show current namespace storage isolation status")
    sub.add_parser("namespaces", help="List local memory namespaces")
    sub.add_parser("retrieval-status", help="Show hybrid retrieval backend status")
    sdk_status = sub.add_parser("sdk-status", help="Detect openKylin Kylin AI embedding/vector SDK capabilities")
    sdk_status.add_argument("--probe-embedding", action="store_true", help="Run one real embedding call; may be slow if the model service is not ready")
    sdk_status.add_argument("--probe-timeout", type=float, default=45.0, help="Timeout in seconds for the protected embedding probe subprocess")

    auditd = sub.add_parser("auditd-rules", help="Print auditd rules for sensitive path monitoring")
    auditd.add_argument("--format", choices=["auditctl", "rules-file", "ausearch"], default="auditctl")
    auditd.add_argument("--key", default="memguard_sensitive")
    auditd.add_argument("--existing-only", action="store_true", help="Only emit rules for paths that exist on this host")

    agent = sub.add_parser("agent", help="Ingest, replay, inspect, and build context for external Agent interactions")
    agent_sub = agent.add_subparsers(dest="agent_command", required=True)
    agent_ingest = agent_sub.add_parser("ingest-event", help="Ingest one normalized Agent event JSON file")
    agent_ingest.add_argument("--file", required=True)
    agent_session = agent_sub.add_parser("ingest-session", help="Ingest one Agent event JSONL session")
    agent_session.add_argument("--file", required=True)
    agent_replay = agent_sub.add_parser("replay", help="Replay one Agent event JSONL session idempotently")
    agent_replay.add_argument("--file", required=True)
    agent_context = agent_sub.add_parser("context", help="Build stage-specific memory context for an Agent")
    agent_context.add_argument("--task", required=True)
    agent_context.add_argument("--stage", required=True, choices=["planning", "tool_selection", "response_generation"])
    agent_context.add_argument("--agent-name", required=True)
    agent_context.add_argument("--session-id", required=True)
    agent_context.add_argument("--project-root", default="")
    agent_context.add_argument("--task-type", default="")
    agent_context.add_argument("--limit", type=int, default=8)
    agent_context.add_argument("--max-chars", type=int, default=4000)
    agent_context.add_argument("--token-budget", type=int, default=800)
    agent_sub.add_parser("status", help="Show Agent event and preference aggregation status")
    agent_evidence = agent_sub.add_parser("evidence", help="Show interaction evidence for one memory")
    agent_evidence.add_argument("memory_id")
    agent_forget = agent_sub.add_parser("forget", help="Preview or execute scoped Agent interaction forgetting")
    agent_forget.add_argument("--agent-name", default="")
    agent_forget.add_argument("--session-id", default="")
    agent_forget.add_argument("--turn-id", default="")
    agent_forget.add_argument("--preference-topic", default="")
    agent_forget.add_argument("--start-time", default="")
    agent_forget.add_argument("--end-time", default="")
    agent_forget.add_argument("--confirm", action="store_true")
    agent_forget.add_argument("--no-compact", action="store_true")
    agent_forget.add_argument("--no-external-persistence", action="store_true")

    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    if args.command in _SERVICE_FREE_COMMANDS:
        return _run_service_free_command(args)

    service = MemGuardService(namespace=args.namespace)
    try:
        if args.command == "agent":
            if args.agent_command == "ingest-event":
                print(json.dumps(service.ingest_interaction(load_event_file(Path(args.file))), ensure_ascii=False, indent=2))
                return 0
            if args.agent_command in {"ingest-session", "replay"}:
                result = service.ingest_interactions(load_event_jsonl(Path(args.file)))
                print(json.dumps(result, ensure_ascii=False, indent=2))
                return 0
            if args.agent_command == "context":
                result = service.build_agent_context(
                    args.task,
                    stage=args.stage,
                    agent_name=args.agent_name,
                    session_id=args.session_id,
                    project_root=args.project_root,
                    task_type=args.task_type,
                    limit=args.limit,
                    max_chars=args.max_chars,
                    token_budget=args.token_budget,
                )
                print(json.dumps(result, ensure_ascii=False, indent=2))
                return 0
            if args.agent_command == "status":
                print(json.dumps(service.interaction_status(), ensure_ascii=False, indent=2))
                return 0
            if args.agent_command == "evidence":
                result = service.preference_evidence(args.memory_id)
                print(json.dumps(result, ensure_ascii=False, indent=2))
                return 0 if result.get("found") else 2
            if args.agent_command == "forget":
                filters = {
                    "agent_name": args.agent_name,
                    "session_id": args.session_id,
                    "turn_id": args.turn_id,
                    "preference_topic": args.preference_topic,
                    "start_time": args.start_time,
                    "end_time": args.end_time,
                }
                result = service.forget_agent_interactions(
                    filters,
                    confirm=args.confirm,
                    compact=not args.no_compact,
                    external_persistence=not args.no_external_persistence,
                )
                print(json.dumps(result, ensure_ascii=False, indent=2))
                return 0

        if args.command == "ingest":
            text, source_uri = _read_ingest(args)
            result = service.ingest_text(text, source_kind=args.source_kind, source_uri=source_uri, tier=args.tier)
            if result["stored"]:
                record = result["record"]
                print(
                    json.dumps(
                        {
                            "stored": True,
                            "memory_id": record.memory_id,
                            "type": record.type,
                            "subtype": record.subtype,
                            "tier": record.tier,
                            "confidence": record.confidence,
                            "quality": result.get("quality"),
                            "extraction": result.get("extraction"),
                        },
                        ensure_ascii=False,
                    )
                )
            else:
                payload = {"stored": False, "reason": result["reason"], "quality": result.get("quality")}
                if result.get("sensitivity"):
                    payload["sensitivity"] = result["sensitivity"].__dict__
                print(json.dumps(payload, ensure_ascii=False))
            return 0

        if args.command == "explain-extraction":
            print(json.dumps(service.explain_extraction(args.text, source_kind=args.source_kind), ensure_ascii=False, indent=2))
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

        if args.command == "memory-policy":
            print(json.dumps(service.memory_policy_status(eviction_limit=args.eviction_limit), ensure_ascii=False, indent=2))
            return 0

        if args.command == "temporal-status":
            print(json.dumps(service.temporal_status(limit=args.limit), ensure_ascii=False, indent=2))
            return 0

        if args.command == "governance":
            if args.governance_command == "list":
                print(json.dumps(service.list_quarantined_memories(limit=args.limit), ensure_ascii=False, indent=2))
                return 0
            result = service.review_quarantined_memory(
                args.memory_id,
                decision=args.decision,
                reviewer=args.reviewer,
                reason=args.reason,
            )
            printable = dict(result)
            if printable.get("record"):
                record = printable["record"]
                printable["record"] = {
                    "memory_id": record.memory_id,
                    "version_id": record.version.version_id,
                    "governance_state": record.governance_state,
                }
            print(json.dumps(printable, ensure_ascii=False, indent=2))
            return 0 if result.get("reviewed") else 2

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

        if args.command == "batch-search":
            queries = _read_batch_queries(args)
            print(json.dumps(service.batch_search(queries, limit=args.top_k, workers=args.workers), ensure_ascii=False, indent=2))
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

        if args.command == "hierarchical-graph-search":
            print(json.dumps(service.hierarchical_graph_search(args.entity, limit=args.limit), ensure_ascii=False, indent=2))
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
            result = service.verify_audit()
            print(json.dumps(result, ensure_ascii=False, indent=2))
            return 0 if result.get("ok") else 2

        if args.command == "resolve-conflicts":
            print(json.dumps(service.resolve_conflicts(), ensure_ascii=False, indent=2))
            return 0

        if args.command == "audit":
            print(json.dumps(service.audit(limit=args.limit, event_type=args.event_type), ensure_ascii=False, indent=2))
            return 0

        if args.command == "quality-report":
            events = service.audit(limit=20, event_type="quality_reject")
            ingests = service.audit(limit=20, event_type="ingest")
            print(json.dumps({"quality_reject": events, "recent_ingest_quality": ingests}, ensure_ascii=False, indent=2))
            return 0

        if args.command == "diagnostics":
            namespace = service.namespace_status()
            retrieval = service.retrieval_status()
            memory_policy = service.memory_policy_status()
            temporal_status = service.temporal_status(limit=10)
            sdk = kylin_sdk_status(probe_embedding=args.probe_sdk, probe_timeout=args.probe_timeout)
            audit_chain = service.verify_audit()
            payload = {
                "ok": bool(audit_chain.get("ok")),
                "namespace": service.namespace,
                "doctor": doctor_report(namespace=service.namespace),
                "runtime_status": cgroup_status(),
                "namespace_status": namespace,
                "retrieval_status": retrieval,
                "memory_policy": memory_policy,
                "temporal_status": temporal_status,
                "kylin_sdk": sdk,
                "audit_chain": audit_chain,
                "recent_audit": service.audit(limit=args.audit_limit),
            }
            print(json.dumps(payload, ensure_ascii=False, indent=2))
            return 0 if payload["ok"] else 2

        if args.command == "web":
            token = Path(args.auth_token_file).read_text(encoding="utf-8").strip() if args.auth_token_file else ""
            from memguard.web.dashboard import run_dashboard

            run_dashboard(service, host=args.host, port=args.port, auth_token=token)
            return 0

        if args.command == "patterns":
            if args.pattern_command == "list":
                print(json.dumps(service.list_patterns(), ensure_ascii=False, indent=2))
                return 0
            if args.pattern_command == "add":
                result = service.try_add_pattern(args.name, args.regex)
                print(json.dumps(result, ensure_ascii=False, indent=2))
                return 0 if result["ok"] else 2
            if args.pattern_command == "suggest":
                result = service.suggest_pattern_from_instruction(args.instruction, name=args.name or None)
                print(json.dumps(result, ensure_ascii=False, indent=2))
                return 0 if result["ok"] else 2
            if args.pattern_command == "add-natural":
                result = service.add_pattern_from_instruction(args.instruction, name=args.name or None)
                print(json.dumps(result, ensure_ascii=False, indent=2))
                return 0 if result["ok"] else 2

        if args.command == "namespace-status":
            print(json.dumps(service.namespace_status(), ensure_ascii=False, indent=2))
            return 0

        if args.command == "retrieval-status":
            print(json.dumps(service.retrieval_status(), ensure_ascii=False, indent=2))
            return 0

    finally:
        service.close()
    return 1


def _run_service_free_command(args: argparse.Namespace) -> int:
    if args.command == "doctor":
        print(json.dumps(doctor_report(namespace=args.namespace), ensure_ascii=False, indent=2))
        return 0

    if args.command == "status":
        print(json.dumps(cgroup_status(), ensure_ascii=False, indent=2))
        return 0

    if args.command == "namespaces":
        print(json.dumps(list_namespaces(), ensure_ascii=False, indent=2))
        return 0

    if args.command == "sdk-status":
        print(
            json.dumps(
                kylin_sdk_status(probe_embedding=args.probe_embedding, probe_timeout=args.probe_timeout),
                ensure_ascii=False,
                indent=2,
            )
        )
        return 0

    if args.command == "auditd-rules":
        if args.format == "auditctl":
            print("\n".join(auditctl_rules(key=args.key, existing_only=args.existing_only)))
        elif args.format == "rules-file":
            print(audit_rules_file(key=args.key, existing_only=args.existing_only), end="")
        else:
            print(ausearch_command(key=args.key))
        return 0

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


def _read_batch_queries(args: argparse.Namespace) -> list[str]:
    if args.json:
        try:
            payload = json.loads(args.queries[0])
        except json.JSONDecodeError as exc:
            raise SystemExit("batch-search --json requires a valid JSON array") from exc
        if not isinstance(payload, list):
            raise SystemExit("batch-search --json requires a JSON array")
        return [str(item) for item in payload]
    return [str(item) for item in args.queries]


def _read_auditd_lines(path: str, limit: int) -> list[str]:
    if path == "-":
        return sys.stdin.read().splitlines()
    lines = Path(path).read_text(encoding="utf-8", errors="replace").splitlines()
    return lines[-max(1, limit) :]


if __name__ == "__main__":
    sys.exit(main())
