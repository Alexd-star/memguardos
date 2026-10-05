#!/usr/bin/env python3
from __future__ import annotations

import argparse
from contextlib import closing
from pathlib import Path
import hashlib
import json
import re
import shlex
import sqlite3
import sys


HIGH_RISK_MARKERS = (
    "rm -rf /",
    "mkfs.",
    "dd if=",
    ":(){:|:&};:",
    "shutdown",
    "reboot",
)

HIGH_RISK_COMMAND_PATTERNS = (
    (
        re.compile(
            r"(?:^|[;&|]\s*)(?:sudo\s+)?(?:env\s+)?(?:command\s+)?"
            r"rm\s+-[A-Za-z]*r[A-Za-z]*f[A-Za-z]*\b",
            re.IGNORECASE,
        ),
        "rm -rf",
    ),
    (
        re.compile(
            r"(?:^|[;&|]\s*)(?:sudo\s+)?(?:env\s+)?(?:command\s+)?"
            r"systemctl\s+(?:restart|stop|disable|mask|kill|poweroff)\b",
            re.IGNORECASE,
        ),
        "systemctl restart",
    ),
)


def selected_preferences(payload):
    preferences = {}
    for stage in ("planning", "tool_selection", "response_generation"):
        result = dict((payload.get("stages") or {}).get(stage) or {})
        for memory in result.get("memories") or []:
            topic = str(memory.get("preference_topic") or "").strip()
            value = str(memory.get("preference_value") or "").strip()
            if topic and value:
                preferences[topic] = value
    return preferences


def deny(reason, category, **details):
    payload = {
        "decision": "deny",
        "reason": reason,
        "policy_category": category,
        "continue": False,
    }
    payload.update(details)
    print(json.dumps(payload, ensure_ascii=False))
    return 2


RUNTIME_SCOPE_FIELDS = ("namespace", "project_root", "session_id", "agent_name", "task_type")
RUNTIME_TRUST_LEVELS = {"trusted", "verified", "reviewed", "controlled", "limited"}


def _valid_use_scope(scope):
    return (
        isinstance(scope, dict)
        and set(scope) == set(RUNTIME_SCOPE_FIELDS)
        and all(isinstance(scope[key], str) for key in RUNTIME_SCOPE_FIELDS)
        and all(scope[key].strip() for key in ("namespace", "session_id", "agent_name"))
        and (not scope["project_root"] or Path(scope["project_root"]).is_absolute())
    )


def _memory_applies(row, scope):
    allowed = {scope["namespace"], "user"} if scope["namespace"] == "default" else {scope["namespace"]}
    if (
        row["scope"] not in allowed
        or row["retention_session_id"] not in {"", scope["session_id"]}
        or row["project_root"] not in {"", scope["project_root"]}
    ):
        return False
    if row["type"] not in {"preference", "sensitive_policy"} or not row["preference_topic"]:
        return True
    return {
        "global": True,
        "agent": row["agent_name"] == scope["agent_name"],
        "project": bool(scope["project_root"]) and row["project_root"] == scope["project_root"],
        "task_type": bool(scope["task_type"]) and row["task_type"] == scope["task_type"],
        "session": row["preference_session_id"] == scope["session_id"],
    }.get(row["preference_scope"] or "global", False)


def _memory_is_active(row):
    return (
        not row["deleted"] and row["governance_state"] == "active"
        and row["valid_to"] is None and row["trust_level"] in RUNTIME_TRUST_LEVELS
    )


def _active_preference_signature(connection, scope):
    # Sign all applicable candidates, not just winners: new overrides must invalidate
    # old context even when the old memory version has not changed.
    rows = connection.execute(
        """SELECT memory_id, version_id, type, subtype, tier, scope, project_root,
                  retention_session_id, preference_scope, preference_session_id,
                  agent_name, task_type, preference_topic, preference_value,
                  preference_explicit, updated_at, deleted, governance_state,
                  valid_to, trust_level
           FROM memory WHERE type IN ('preference', 'sensitive_policy')
             AND preference_topic != '' ORDER BY memory_id"""
    ).fetchall()
    candidates = [
        dict(row) for row in rows
        if _memory_is_active(row) and _memory_applies(row, scope)
    ]
    event_orders = {}
    if candidates:
        # json_each is already used by the store and avoids SQLite's parameter
        # limit without introducing one evidence query per preference candidate.
        evidence = connection.execute(
            """SELECT l.memory_id, e.timestamp, e.rowid AS ingest_order, e.sequence, e.event_id
               FROM memory_event_links l JOIN agent_events e ON e.event_id = l.event_id
               WHERE l.memory_id IN (SELECT value FROM json_each(?))""",
            (json.dumps([item["memory_id"] for item in candidates]),),
        ).fetchall()
        for event in evidence:
            memory_id = event["memory_id"]
            order = (event["timestamp"] or "", event["ingest_order"], event["sequence"], event["event_id"])
            event_orders[memory_id] = max(event_orders.get(memory_id, ("", 0, 0, "")), order)
    for item in candidates:
        item["event_order"] = event_orders.get(item["memory_id"], ("", 0, 0, ""))
    canonical = json.dumps({"use_scope": scope, "candidates": candidates}, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def runtime_preference_signature(db_path, use_scope):
    """Read-only baseline captured before building any stage context."""
    if not _valid_use_scope(use_scope) or not Path(db_path).is_absolute():
        raise ValueError("invalid runtime scope or memory store path")
    uri = Path(db_path).resolve().as_uri() + "?mode=ro"
    with closing(sqlite3.connect(uri, uri=True, timeout=1.0)) as connection:
        connection.row_factory = sqlite3.Row
        connection.execute("BEGIN")
        return _active_preference_signature(connection, use_scope)


def context_snapshot_status(payload):
    """Validate at supported hook checkpoints, not during an executing tool.

    A denial requires a new conversation; it does not erase existing model state.
    """
    if not isinstance(payload, dict):
        return {"ok": False, "reason": "invalid_snapshot", "stale_memory_ids": []}
    snapshot = payload.get("memory_snapshot")
    stages = payload.get("stages")
    if stages is None and not payload.get("trace_id") and snapshot is None:
        return {"ok": True, "reason": "", "stale_memory_ids": []}
    if not isinstance(stages, dict) or (payload.get("trace_id") and not stages):
        return {"ok": False, "reason": "invalid_snapshot", "stale_memory_ids": []}
    selected = []
    for stage in stages.values():
        if not isinstance(stage, dict):
            return {"ok": False, "reason": "invalid_snapshot", "stale_memory_ids": []}
        memories = stage.get("memories", [])
        if not isinstance(memories, list) or any(not isinstance(item, dict) for item in memories):
            return {"ok": False, "reason": "invalid_snapshot", "stale_memory_ids": []}
        if any(not isinstance(item.get("memory_id"), str) or not item["memory_id"] for item in memories):
            return {"ok": False, "reason": "invalid_snapshot", "stale_memory_ids": []}
        selected.extend(memories)
    if not isinstance(snapshot, dict):
        if snapshot is not None:
            return {"ok": False, "reason": "invalid_snapshot", "stale_memory_ids": []}
        if selected:
            return {"ok": False, "reason": "missing_snapshot", "stale_memory_ids": []}
        return {"ok": True, "reason": "", "stale_memory_ids": []}
    versions = snapshot.get("versions")
    if not isinstance(versions, dict):
        return {"ok": False, "reason": "invalid_snapshot", "stale_memory_ids": []}
    if set(versions) != {str(memory["memory_id"]) for memory in selected}:
        return {"ok": False, "reason": "invalid_snapshot", "stale_memory_ids": []}
    if snapshot.get("memory_enabled") is False:
        if versions or any(stage.get("context") for stage in stages.values()):
            return {"ok": False, "reason": "invalid_snapshot", "stale_memory_ids": []}
        return {"ok": True, "reason": "", "stale_memory_ids": []}
    if not versions and set(snapshot) == {"versions"} and not any(stage.get("context") for stage in stages.values()):
        return {"ok": True, "reason": "", "stale_memory_ids": []}
    if any(
        not isinstance(memory_id, str) or not memory_id
        or not isinstance(version_id, str) or not version_id
        for memory_id, version_id in versions.items()
    ):
        return {"ok": False, "reason": "invalid_snapshot", "stale_memory_ids": []}
    if any(versions.get(str(memory["memory_id"])) != memory.get("version_id") for memory in selected):
        return {"ok": False, "reason": "invalid_snapshot", "stale_memory_ids": []}
    db_path = str(snapshot.get("db_path") or "")
    namespace = str(snapshot.get("namespace") or "")
    if not db_path or not Path(db_path).is_absolute() or not namespace:
        return {"ok": False, "reason": "invalid_snapshot", "stale_memory_ids": []}
    scope = snapshot.get("use_scope")
    signature = snapshot.get("active_preference_signature")
    if (
        not _valid_use_scope(scope) or scope["namespace"] != namespace
        or not isinstance(signature, str) or not re.fullmatch(r"[0-9a-f]{64}", signature)
    ):
        return {"ok": False, "reason": "invalid_snapshot", "stale_memory_ids": []}
    if any(payload.get(key) != scope[key] for key in RUNTIME_SCOPE_FIELDS):
        return {"ok": False, "reason": "scope_mismatch", "stale_memory_ids": []}
    if any(
        key in stage and stage[key] != scope[key]
        for stage in stages.values() for key in RUNTIME_SCOPE_FIELDS
    ):
        return {"ok": False, "reason": "scope_mismatch", "stale_memory_ids": []}
    try:
        uri = Path(db_path).resolve().as_uri() + "?mode=ro"
        with closing(sqlite3.connect(uri, uri=True, timeout=1.0)) as connection:
            connection.row_factory = sqlite3.Row
            connection.execute("BEGIN")
            stale = []
            for memory_id, version_id in versions.items():
                row = connection.execute(
                    "SELECT * FROM memory WHERE memory_id = ?",
                    (memory_id,),
                ).fetchone()
                if (
                    row is None or row["version_id"] != version_id
                    or not _memory_is_active(row) or not _memory_applies(row, scope)
                ):
                    stale.append(memory_id)
            preferences_changed = _active_preference_signature(connection, scope) != signature
    except (OSError, sqlite3.Error, ValueError):
        return {"ok": False, "reason": "memory_store_unavailable", "stale_memory_ids": []}
    return {
        "ok": not stale and not preferences_changed,
        "reason": "memory_changed" if stale else "preferences_changed" if preferences_changed else "",
        "stale_memory_ids": stale,
    }


def violates_code_search_preference(command, preferred_tool):
    executables = _shell_command_executables(command)
    if preferred_tool == "rg":
        return any(item in {"grep", "egrep", "fgrep", "git grep"} for item in executables)
    if preferred_tool == "grep":
        return "rg" in executables
    return False


def _shell_command_executables(command):
    normalized = " ".join(str(command or "").strip().split())
    if not normalized:
        return []
    executables = []
    for segment in re.split(r"(?:&&|\|\||[;|])", normalized):
        try:
            tokens = shlex.split(segment.strip())
        except ValueError:
            tokens = segment.strip().split()
        while tokens:
            name = Path(tokens[0]).name
            if name == "sudo":
                tokens.pop(0)
                while tokens and tokens[0].startswith("-"):
                    tokens.pop(0)
                continue
            if name == "env":
                tokens.pop(0)
                while tokens and (tokens[0].startswith("-") or "=" in tokens[0]):
                    tokens.pop(0)
                continue
            if name == "command":
                tokens.pop(0)
                continue
            if name in {"time", "nice", "nohup", "timeout"}:
                tokens.pop(0)
                while tokens and tokens[0].startswith("-"):
                    tokens.pop(0)
                if name == "timeout" and tokens:
                    tokens.pop(0)
                continue
            break
        if not tokens:
            continue
        executable = Path(tokens[0]).name
        if executable in {"bash", "sh", "dash", "zsh"}:
            command_flag = next(
                (
                    index
                    for index, token in enumerate(tokens[1:], start=1)
                    if token.startswith("-") and "c" in token[1:]
                ),
                -1,
            )
            if command_flag >= 0 and command_flag + 1 < len(tokens):
                executables.extend(
                    _shell_command_executables(tokens[command_flag + 1])
                )
                continue
        if executable == "git" and len(tokens) > 1 and tokens[1] == "grep":
            executable = "git grep"
        executables.append(executable)
        if executable == "xargs":
            candidates = [item for item in tokens[1:] if not item.startswith("-")]
            if candidates:
                executables.append(Path(candidates[0]).name)
        if executable == "find" and "-exec" in tokens:
            index = tokens.index("-exec")
            if index + 1 < len(tokens):
                executables.append(Path(tokens[index + 1]).name)
    return executables


def violates_test_runner_preference(command, preferred_command):
    normalized = " ".join(str(command).strip().split())
    if not normalized:
        return False
    if preferred_command != "python -m pytest":
        return False
    return bool(
        re.search(
            r"(^|(?:\|\||&&|[|;])\s*)(?:sudo\s+)?"
            r"(?:env\s+)?(?:command\s+)?pytest\b",
            normalized,
        )
    )


def high_risk_marker(tool_input):
    serialized = json.dumps(tool_input, ensure_ascii=False).lower()
    blocked_marker = next((marker for marker in HIGH_RISK_MARKERS if marker in serialized), "")
    if blocked_marker:
        return blocked_marker
    command = " ".join(str((tool_input or {}).get("command") or "").split())
    for pattern, marker in HIGH_RISK_COMMAND_PATTERNS:
        if pattern.search(command):
            return marker
    return ""


def finish_high_risk_violation_marker(tool_input, preferences):
    if preferences.get("high_risk_confirmation") != "required":
        return ""
    text = json.dumps(tool_input or {}, ensure_ascii=False).lower()
    risk_markers = (
        "高风险",
        "危险",
        "删除",
        "rm",
        "systemctl",
        "reboot",
        "shutdown",
    )
    has_risk = any(marker in text for marker in risk_markers)
    false_confirmation_markers = (
        "已获得用户确认",
        "您已确认",
        "用户已确认",
        "already confirmed",
        "confirmed by user",
    )
    if has_risk and any(marker in text for marker in false_confirmation_markers):
        return "finish_false_confirmation"
    safe_refusal_markers = (
        "未确认",
        "等待确认",
        "需要确认",
        "请确认",
        "不能直接执行",
        "不能执行",
        "无法执行",
        "拒绝执行",
        "will not execute",
        "won't execute",
        "cannot execute",
        "not execute",
        "confirm first",
        "requires confirmation",
    )
    if any(marker in text for marker in safe_refusal_markers):
        return ""
    completion_markers = (
        "执行完成",
        "已执行",
        "完成",
        "done",
        "executed",
        "completed",
        "confirmed",
        "removed",
        "deleted",
    )
    if has_risk and any(marker in text for marker in completion_markers):
        return "finish_false_completion"
    return ""


def parse_args():
    parser = argparse.ArgumentParser()
    parser.add_argument("--context-file", required=True)
    parser.add_argument("--guard-tool", action="store_true")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    try:
        payload = json.loads(Path(args.context_file).read_text(encoding="utf-8"))
        if not isinstance(payload, dict):
            raise ValueError("context payload must be an object")
    except (OSError, ValueError):
        return deny("记忆上下文不可用，请重新开始当前任务。", "memory_validation_unavailable")
    snapshot_status = context_snapshot_status(payload)
    if not snapshot_status["ok"]:
        return deny(
            "记忆上下文已更新或撤回，请结束当前任务并重新开始，以获取最新记忆。",
            "stale_memory_context",
            validation_reason=snapshot_status["reason"],
            affected_memory_ids=snapshot_status["stale_memory_ids"],
        )
    if not args.guard_tool:
        print(
            json.dumps(
                {
                    "decision": "allow",
                    "additionalContext": payload.get("additional_context", ""),
                    "continue": True,
                },
                ensure_ascii=False,
            )
        )
        return 0

    hook_input = json.load(sys.stdin)
    tool_name = str(hook_input.get("tool_name") or "")
    allowed_tools = [str(item) for item in payload.get("allowed_tools") or []]
    if allowed_tools and tool_name not in allowed_tools:
        return deny(
            "Tool %s is not allowed by the active MemGuardOS profile." % tool_name,
            "tool_profile",
            observed_tool=tool_name,
        )
    tool_input = hook_input.get("tool_input") or {}
    preferences = selected_preferences(payload)
    finish_violation = (
        finish_high_risk_violation_marker(tool_input, preferences)
        if tool_name == "finish"
        else ""
    )
    if finish_violation:
        return deny(
            "MemGuardOS blocked a high-risk finish message that claimed execution "
            "without an observed confirmation gate.",
            "high_risk",
            blocked_marker=finish_violation,
        )
    blocked_marker = high_risk_marker(tool_input)
    if blocked_marker:
        return deny(
            "MemGuardOS blocked a high-risk tool command: %s" % blocked_marker,
            "high_risk",
            blocked_marker=blocked_marker,
        )
    preferred_search = preferences.get("code_search_tool", "")
    command = str(tool_input.get("command") or "")
    if (
        tool_name == "terminal"
        and preferred_search
        and violates_code_search_preference(command, preferred_search)
    ):
        return deny(
            "MemGuardOS requires the active code-search preference `%s`; "
            "revise the command and retry." % preferred_search,
            "preference_enforcement",
            preference_topic="code_search_tool",
            expected_value=preferred_search,
            observed_command=command,
        )
    preferred_test_runner = preferences.get("test_runner_command", "")
    if (
        tool_name == "terminal"
        and preferred_test_runner
        and violates_test_runner_preference(command, preferred_test_runner)
    ):
        return deny(
            "MemGuardOS requires the active Python test runner preference `%s`; "
            "revise the command and retry." % preferred_test_runner,
            "preference_enforcement",
            preference_topic="test_runner_command",
            expected_value=preferred_test_runner,
            observed_command=command,
        )
    print(json.dumps({"decision": "allow", "continue": True}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
