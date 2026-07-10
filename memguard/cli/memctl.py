from __future__ import annotations

import argparse
import hashlib
import json
import os
import platform
import secrets
import shutil
import stat
import subprocess
import sys
from pathlib import Path

from memguard.config import list_namespaces
from memguard.desktop.peony_scan import scan_peony_file
from memguard.desktop.scan_manifest import apply_scan_manifest, create_scan_manifest, forget_imported_memories
from memguard.ingestion.tool_result import ToolResult
from memguard.integrations.replay import load_event_file, load_event_jsonl
from memguard.llm.adapter import LLMAdapterError, generate_answer
from memguard.llm.prompt import render_generation_prompt
from memguard.os_guard.auditd_events import parse_auditd_events
from memguard.os_guard.auditd import audit_rules_file, auditctl_rules, ausearch_command
from memguard.os_guard.doctor import doctor_report
from memguard.os_guard.cgroup import cgroup_status
from memguard.os_guard.openkylin_native import openkylin_native_status
from memguard.os_guard.privileged_actions import ALLOWED_ACTIONS, run_privileged_action
from memguard.os_guard.service_control import service_resource_status
from memguard.retrieval.kylin_sdk import kylin_sdk_status
from memguard.service import MemGuardService


_SERVICE_FREE_COMMANDS = {"doctor", "status", "namespaces", "sdk-status", "auditd-rules", "openkylin-status", "native-action"}


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
    evidence = sub.add_parser("collect-openkylin-evidence", help="Export an openKylin acceptance evidence package with manifest and SHA256 checksums")
    evidence.add_argument("--output-dir", default="", help="Evidence output directory. Defaults to ~/MemGuardOS-openKylin-evidence")
    evidence.add_argument("--probe-sdk", action="store_true", help="Include a real Kylin SDK inference probe")
    evidence.add_argument("--probe-timeout", type=float, default=90.0, help="Timeout in seconds for the optional SDK probe")
    evidence.add_argument("--include-readiness", action="store_true", help="Run scripts/check_competition_ready.py when available")
    evidence.add_argument("--include-pytest", action="store_true", help="Run local pytest when a source checkout with tests is available")
    evidence.add_argument("--timeout", type=float, default=300.0, help="Timeout in seconds for optional readiness/pytest commands")

    web = sub.add_parser("web", help="Run a localhost Web dashboard for local operation and inspection")
    web.add_argument("--host", default="127.0.0.1")
    web.add_argument("--port", type=int, default=8765)
    web.add_argument("--auth-token-file", default="", help="Bearer token file for dashboard API calls")
    web.add_argument("--state-file", default="", help="Runtime state file written after the server binds its port")
    web.add_argument("--lock-file", default="", help="Single-instance runtime lock file")
    web.add_argument("--idle-timeout", type=float, default=900.0, help="Stop after this many idle seconds; 0 disables cleanup")

    desktop = sub.add_parser("desktop", help="Desktop integration operations for UKUI/Peony")
    desktop_sub = desktop.add_subparsers(dest="desktop_command", required=True)
    scan_file = desktop_sub.add_parser("scan-file", help="Preview or ingest one Peony-selected file")
    scan_file.add_argument("--file", required=True, help="Path selected from Peony")
    scan_file.add_argument("--ingest", action="store_true", help="Record OS event and import safe text files")
    scan_file.add_argument("--notify", action="store_true", help="Send a desktop notification when notify-send is available")
    scan_file.add_argument("--max-bytes", type=int, default=1024 * 1024, help="Maximum text file size for direct import")
    scan_files = desktop_sub.add_parser("scan-files", help="Create a read-only preview manifest for Peony-selected files")
    scan_files.add_argument("--file", action="append", required=True, help="Selected file path; repeat for multiple files")
    scan_files.add_argument("--max-files", type=int, default=100)
    scan_files.add_argument("--max-file-bytes", type=int, default=1024 * 1024)
    scan_files.add_argument("--max-total-bytes", type=int, default=10 * 1024 * 1024)
    scan_files.add_argument("--ttl", type=int, default=300, help="Manifest lifetime in seconds")
    apply_scan = desktop_sub.add_parser("apply-scan", help="Import explicitly selected safe files from a preview manifest")
    apply_scan.add_argument("--manifest", required=True, help="Manifest path returned by scan-files")
    apply_scan.add_argument("--select", action="append", required=True, help="Confirmed safe file path; repeat for multiple files")
    apply_scan.add_argument("--notify", action="store_true", help="Send one batch completion notification")
    forget_import = desktop_sub.add_parser("forget-import", help="Forget memories created by one Peony import result")
    forget_import.add_argument("--memory-id", action="append", required=True, help="Imported memory id to forget; repeat for multiple files")
    forget_import.add_argument("--reason", default="撤销 Peony 文件导入", help="Audit reason stored with the forgetting tombstone")
    forget_import.add_argument("--compact", action="store_true", help="Compact segment storage after forgetting")
    forget_import.add_argument("--notify", action="store_true", help="Send one desktop notification")

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
    feedback_home = sub.add_parser("feedback-home", help="Show recent memory changes and user-facing Agent feedback")
    feedback_home.add_argument("--limit", type=int, default=8)
    notifications = sub.add_parser("notifications", help="Show recent user-facing memory and risk notifications")
    notifications.add_argument("--limit", type=int, default=30)
    sdk_status = sub.add_parser("sdk-status", help="Detect openKylin Kylin AI embedding/vector SDK capabilities")
    sdk_status.add_argument("--probe-embedding", action="store_true", help="Run one real embedding call; may be slow if the model service is not ready")
    sdk_status.add_argument("--probe-timeout", type=float, default=45.0, help="Timeout in seconds for the protected embedding probe subprocess")
    openkylin_status = sub.add_parser("openkylin-status", help="Show openKylin-native desktop, SDK, service and security status")
    openkylin_status.add_argument("--fast", action="store_true", help="Use lightweight checks suitable for desktop UI startup; this is also the default unless --probe-sdk is set")
    openkylin_status.add_argument("--probe-sdk", action="store_true", help="Run one protected Kylin embedding probe")
    openkylin_status.add_argument("--probe-timeout", type=float, default=45.0)
    native_action = sub.add_parser("native-action", help="Run a fixed openKylin system action through Polkit")
    native_action.add_argument("action", choices=sorted(ALLOWED_ACTIONS))
    native_action.add_argument("--timeout", type=float, default=180.0)

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


def _read_dashboard_token_file(path: Path) -> str:
    flags = os.O_RDONLY
    if hasattr(os, "O_NOFOLLOW"):
        flags |= os.O_NOFOLLOW
    try:
        descriptor = os.open(path, flags)
    except OSError as exc:
        raise ValueError("Dashboard token file must be a readable regular file") from exc
    try:
        file_stat = os.fstat(descriptor)
        if not stat.S_ISREG(file_stat.st_mode):
            raise ValueError("Dashboard token file must be a regular file")
        if os.name == "posix" and file_stat.st_mode & (stat.S_IRWXG | stat.S_IRWXO):
            raise ValueError("Dashboard token file permissions must be owner-only (0600)")
        with os.fdopen(descriptor, "r", encoding="utf-8") as token_stream:
            descriptor = -1
            token = token_stream.read().strip()
    finally:
        if descriptor >= 0:
            os.close(descriptor)
    if not token:
        raise ValueError("Dashboard token file is empty")
    return token


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    if args.command in _SERVICE_FREE_COMMANDS:
        return _run_service_free_command(args)

    service = MemGuardService(namespace=args.namespace)
    try:
        if args.command == "desktop":
            if args.desktop_command == "scan-file":
                print(
                    json.dumps(
                        scan_peony_file(
                            args.file,
                            service,
                            ingest=args.ingest,
                            notify=args.notify,
                            max_bytes=args.max_bytes,
                        ),
                        ensure_ascii=False,
                        indent=2,
                    )
                )
                return 0
            if args.desktop_command == "scan-files":
                print(
                    json.dumps(
                        create_scan_manifest(
                            args.file,
                            service,
                            ttl_seconds=args.ttl,
                            max_files=args.max_files,
                            max_file_bytes=args.max_file_bytes,
                            max_total_bytes=args.max_total_bytes,
                        ),
                        ensure_ascii=False,
                        indent=2,
                    )
                )
                return 0
            if args.desktop_command == "apply-scan":
                result = apply_scan_manifest(
                    args.manifest,
                    args.select,
                    service,
                    notify=args.notify,
                )
                print(json.dumps(result, ensure_ascii=False, indent=2))
                return 0 if result.get("ok") else 2
            if args.desktop_command == "forget-import":
                result = forget_imported_memories(
                    args.memory_id,
                    service,
                    reason=args.reason,
                    compact=args.compact,
                    notify=args.notify,
                )
                print(json.dumps(result, ensure_ascii=False, indent=2))
                return 0 if result.get("ok") else 2

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
        if args.command == "feedback-home":
            print(json.dumps(service.feedback_home_snapshot(limit=args.limit), ensure_ascii=False, indent=2))
            return 0
        if args.command == "notifications":
            print(json.dumps(service.recent_notifications(limit=args.limit), ensure_ascii=False, indent=2))
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

        if args.command == "collect-openkylin-evidence":
            result = _collect_openkylin_evidence(args, service)
            print(json.dumps(result, ensure_ascii=False, indent=2))
            return 0 if result.get("ok") else 2

        if args.command == "web":
            try:
                token = (
                    _read_dashboard_token_file(Path(args.auth_token_file))
                    if args.auth_token_file
                    else secrets.token_urlsafe(32)
                )
            except ValueError:
                print(
                    "无法启动本地控制台：访问凭据文件不可用或权限不安全。",
                    file=sys.stderr,
                )
                return 2
            from memguard.web.dashboard import run_dashboard

            run_dashboard(
                service,
                host=args.host,
                port=args.port,
                auth_token=token,
                state_file=Path(args.state_file) if args.state_file else "",
                lock_file=Path(args.lock_file) if args.lock_file else "",
                idle_timeout=max(0.0, float(args.idle_timeout)),
                show_access_url=not bool(args.auth_token_file),
            )
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

    if args.command == "openkylin-status":
        lightweight_status = args.fast or not args.probe_sdk
        print(
            json.dumps(
                openkylin_native_status(
                    namespace=args.namespace,
                    probe_sdk=args.probe_sdk,
                    probe_timeout=args.probe_timeout,
                    fast=lightweight_status,
                ),
                ensure_ascii=False,
                indent=2,
            )
        )
        return 0

    if args.command == "native-action":
        result = run_privileged_action(args.action, timeout=args.timeout)
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return 0 if result.get("ok") else 2

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


def _collect_openkylin_evidence(args: argparse.Namespace, service: MemGuardService) -> dict[str, object]:
    output_dir = Path(args.output_dir or (Path.home() / "MemGuardOS-openKylin-evidence")).expanduser().resolve()
    output_dir.mkdir(parents=True, exist_ok=True)

    source_revision = _source_revision_payload()
    _write_json(output_dir / "source_revision.json", source_revision)
    _write_json(
        output_dir / "environment.json",
        {
            "schema": "memguardos-openkylin-evidence-environment-v1",
            "platform": platform.platform(),
            "python": sys.version.split()[0],
            "executable": sys.executable,
            "cwd": str(Path.cwd()),
            "distribution": _os_release_fields(),
            "desktop": {
                "XDG_CURRENT_DESKTOP": os.environ.get("XDG_CURRENT_DESKTOP", ""),
                "DESKTOP_SESSION": os.environ.get("DESKTOP_SESSION", ""),
                "XDG_SESSION_TYPE": os.environ.get("XDG_SESSION_TYPE", ""),
            },
        },
    )
    _write_json(
        output_dir / "openkylin_status.json",
        openkylin_native_status(
            namespace=service.namespace,
            probe_sdk=args.probe_sdk,
            probe_timeout=args.probe_timeout,
            fast=not args.probe_sdk,
        ),
    )
    _write_json(output_dir / "audit_chain.json", service.verify_audit())
    _write_json(output_dir / "systemd_cgroup.json", service_resource_status())
    _write_json(output_dir / "desktop_assets.json", _desktop_asset_payload())
    _write_json(
        output_dir / "diagnostics.json",
        {
            "schema": "memguardos-openkylin-evidence-diagnostics-v1",
            "namespace": service.namespace,
            "doctor": doctor_report(namespace=service.namespace),
            "runtime_status": cgroup_status(),
            "namespace_status": service.namespace_status(),
            "retrieval_status": service.retrieval_status(),
            "memory_policy": service.memory_policy_status(),
            "kylin_sdk": kylin_sdk_status(probe_embedding=args.probe_sdk, probe_timeout=args.probe_timeout),
            "recent_audit": service.audit(limit=20),
        },
    )

    if args.include_readiness:
        _write_command_capture(
            output_dir / "readiness.txt",
            [sys.executable, "scripts/check_competition_ready.py"],
            timeout=args.timeout,
            cwd=Path.cwd(),
            require_path=Path("scripts/check_competition_ready.py"),
        )
    if args.include_pytest:
        _write_command_capture(
            output_dir / "pytest.txt",
            [sys.executable, "-m", "pytest", "-q"],
            timeout=args.timeout,
            cwd=Path.cwd(),
            require_path=Path("tests"),
        )

    manifest = _write_evidence_manifest(output_dir)
    return {
        "ok": True,
        "schema": "memguardos-openkylin-evidence-package-v1",
        "output_dir": str(output_dir),
        "file_count": manifest["file_count"],
        "source_revision_status": source_revision["status"],
        "include_readiness": bool(args.include_readiness),
        "include_pytest": bool(args.include_pytest),
    }


def _write_json(path: Path, payload: dict[str, object]) -> None:
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def _source_revision_payload() -> dict[str, object]:
    env_revision = os.environ.get("MEMGUARD_SOURCE_REVISION", "").strip()
    if env_revision:
        return {
            "schema": "memguardos-source-revision-v1",
            "source_revision": env_revision,
            "source_mode": os.environ.get("MEMGUARD_SOURCE_MODE", "environment"),
            "dirty": os.environ.get("MEMGUARD_SOURCE_DIRTY", "false").lower() == "true",
            "status": "provided",
        }
    result = subprocess.run(["git", "rev-parse", "HEAD"], stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, text=True, check=False)
    revision = result.stdout.strip()
    if result.returncode == 0 and revision:
        dirty = subprocess.run(["git", "status", "--porcelain"], stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, text=True, check=False)
        return {
            "schema": "memguardos-source-revision-v1",
            "source_revision": revision,
            "source_mode": "git_worktree",
            "dirty": bool(dirty.stdout.strip()),
            "status": "git",
        }
    return {
        "schema": "memguardos-source-revision-v1",
        "source_revision": "",
        "source_mode": "installed_package_or_unknown",
        "dirty": None,
        "status": "not_available",
        "note": "Git metadata was not available in this runtime. Run from the source checkout or set MEMGUARD_SOURCE_REVISION for final contest evidence.",
    }


def _os_release_fields() -> dict[str, str]:
    path = Path("/etc/os-release")
    if not path.exists():
        return {}
    fields: dict[str, str] = {}
    for line in path.read_text(encoding="utf-8", errors="replace").splitlines():
        if "=" not in line:
            continue
        key, value = line.split("=", 1)
        fields[key] = value.strip().strip('"')
    return fields


def _desktop_asset_payload() -> dict[str, object]:
    peony_plugins = sorted(str(path) for path in Path("/usr/lib").glob("*/peony-extensions/libpeony-menu-plugin-memguardos.so"))
    ukcc_plugins = sorted(str(path) for path in Path("/usr/lib").glob("*/ukui-control-center/libmemguardos-ukcc-plugin.so"))
    return {
        "schema": "memguardos-desktop-assets-v1",
        "peony_plugins": peony_plugins,
        "ukui_control_center_plugins": ukcc_plugins,
        "tray": {
            "binary": "/usr/bin/memguardos-tray",
            "binary_exists": Path("/usr/bin/memguardos-tray").exists(),
            "autostart": "/etc/xdg/autostart/memguardos-tray.desktop",
            "autostart_exists": Path("/etc/xdg/autostart/memguardos-tray.desktop").exists(),
        },
        "menu_entry": {
            "desktop_file": "/usr/share/applications/memguardos-center.desktop",
            "exists": Path("/usr/share/applications/memguardos-center.desktop").exists(),
        },
        "notify_send": bool(shutil.which("notify-send")),
    }


def _write_command_capture(path: Path, command: list[str], *, timeout: float, cwd: Path, require_path: Path) -> None:
    if not require_path.exists():
        path.write_text(
            "$ %s\nskipped=missing_%s\n" % (" ".join(command), require_path.as_posix()),
            encoding="utf-8",
        )
        return
    completed = subprocess.run(
        command,
        cwd=cwd,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        timeout=timeout,
        check=False,
    )
    path.write_text("$ %s\n%s" % (" ".join(command), completed.stdout), encoding="utf-8")


def _write_evidence_manifest(root: Path) -> dict[str, object]:
    checksum_path = root / "SHA256SUMS.txt"
    manifest_path = root / "manifest.json"
    files = [
        path
        for path in sorted(root.rglob("*"), key=lambda item: item.relative_to(root).as_posix())
        if path.is_file() and path not in {checksum_path, manifest_path}
    ]
    checksum_path.write_text(
        "\n".join("%s  %s" % (hashlib.sha256(path.read_bytes()).hexdigest(), path.relative_to(root).as_posix()) for path in files) + "\n",
        encoding="utf-8",
    )
    manifest = {
        "schema": "memguardos-openkylin-evidence-manifest-v1",
        "file_count": len(files),
        "files": [
            {
                "path": path.relative_to(root).as_posix(),
                "bytes": path.stat().st_size,
                "sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
            }
            for path in files
        ],
    }
    _write_json(manifest_path, manifest)
    return manifest


if __name__ == "__main__":
    sys.exit(main())
