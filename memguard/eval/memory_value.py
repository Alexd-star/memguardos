from __future__ import annotations

from collections import Counter
from pathlib import Path
from statistics import mean
from typing import Dict, Iterable, List, Optional

from memguard.ingestion.tool_result import ToolResult
from memguard.integrations.events import AgentInteractionEvent
from memguard.service import MemGuardService


GROUP_NAMES = ("no_memory", "full_history", "memguard")


def normalize_command(command: str) -> str:
    return " ".join(str(command or "").split())


def summarize_group(group: Dict[str, object]) -> Dict[str, object]:
    raw_calls = list(group.get("tool_calls") or [])
    normalized_commands: List[str] = []
    failed_command_count = 0
    blocked_high_risk_count = 0
    for raw_call in raw_calls:
        call = dict(raw_call) if isinstance(raw_call, dict) else {"command": str(raw_call)}
        command = normalize_command(str(call.get("command") or ""))
        if command:
            normalized_commands.append(command)
        blocked = bool(call.get("blocked_by_policy"))
        if blocked:
            blocked_high_risk_count += 1
        elif _as_int(call.get("exit_code"), 0) != 0:
            failed_command_count += 1

    counts = Counter(normalized_commands)
    repeated_command_count = sum(max(0, count - 1) for count in counts.values())
    expected_tool = str(group.get("expected_tool") or "").strip()
    first_command = normalized_commands[0] if normalized_commands else ""
    first_tool = first_command.split(" ", 1)[0] if first_command else ""
    return {
        "task_completed": bool(group.get("task_completed")),
        "first_tool_correct": bool(expected_tool and first_tool == expected_tool),
        "interaction_turns": max(0, _as_int(group.get("interaction_turns"), 0)),
        "tool_call_count": len(raw_calls),
        "failed_command_count": failed_command_count,
        "repeated_command_count": repeated_command_count,
        "blocked_high_risk_count": blocked_high_risk_count,
        "prompt_tokens": max(0, _as_int(group.get("prompt_tokens"), 0)),
    }


def evaluate_paired_case(case: Dict[str, object]) -> Dict[str, object]:
    raw_groups = dict(case.get("groups") or {})
    groups = {
        name: summarize_group(dict(raw_groups.get(name) or {}))
        for name in GROUP_NAMES
    }
    no_memory = groups["no_memory"]
    full_history = groups["full_history"]
    memguard = groups["memguard"]
    full_history_tokens = int(full_history["prompt_tokens"])
    token_delta = int(memguard["prompt_tokens"]) - full_history_tokens
    token_reduction_ratio: Optional[float] = None
    if full_history_tokens > 0:
        token_reduction_ratio = round(
            (full_history_tokens - int(memguard["prompt_tokens"])) / full_history_tokens,
            6,
        )
    turn_delta = int(memguard["interaction_turns"]) - int(no_memory["interaction_turns"])
    return {
        "case_id": str(case.get("case_id") or ""),
        "category": str(case.get("category") or ""),
        "title": str(case.get("title") or ""),
        "groups": groups,
        "deltas": {
            "completion_vs_no_memory": int(bool(memguard["task_completed"]))
            - int(bool(no_memory["task_completed"])),
            "tool_accuracy_vs_no_memory": int(bool(memguard["first_tool_correct"]))
            - int(bool(no_memory["first_tool_correct"])),
            "interaction_turns_vs_no_memory": turn_delta,
            "interaction_efficiency_improved": bool(
                memguard["task_completed"]
                and turn_delta < 0
            ),
            "prompt_tokens_vs_full_history": token_delta,
            "prompt_token_reduction_ratio": token_reduction_ratio,
            "failed_commands_vs_no_memory": int(memguard["failed_command_count"])
            - int(no_memory["failed_command_count"]),
            "repeated_commands_vs_no_memory": int(memguard["repeated_command_count"])
            - int(no_memory["repeated_command_count"]),
        },
    }


def aggregate_paired_results(
    results: Iterable[Dict[str, object]],
) -> Dict[str, object]:
    cases = list(results)
    groups: Dict[str, Dict[str, object]] = {}
    for name in GROUP_NAMES:
        summaries = [dict(case["groups"][name]) for case in cases]
        groups[name] = {
            "task_completion_rate": _bool_rate(summaries, "task_completed"),
            "first_tool_accuracy": _eligible_bool_rate(
                summaries, "first_tool_correct", "tool_call_count"
            ),
            "mean_interaction_turns": _mean_metric(summaries, "interaction_turns"),
            "mean_prompt_tokens": _mean_metric(summaries, "prompt_tokens"),
            "mean_failed_commands": _mean_metric(summaries, "failed_command_count"),
            "mean_repeated_commands": _mean_metric(
                summaries, "repeated_command_count"
            ),
        }
    delta_keys = (
        "completion_vs_no_memory",
        "tool_accuracy_vs_no_memory",
        "interaction_turns_vs_no_memory",
        "prompt_tokens_vs_full_history",
        "prompt_token_reduction_ratio",
        "failed_commands_vs_no_memory",
        "repeated_commands_vs_no_memory",
    )
    mean_deltas = {}
    for key in delta_keys:
        values = [
            case["deltas"][key]
            for case in cases
            if case["deltas"].get(key) is not None
        ]
        mean_deltas[key] = round(mean(values), 6) if values else None
    return {
        "case_count": len(cases),
        "groups": groups,
        "mean_deltas": mean_deltas,
    }


def evaluate_memory_value_cases(
    cases: Iterable[Dict[str, object]],
    work_root: Path,
) -> Dict[str, object]:
    case_results = []
    failures = []
    root = Path(work_root)
    root.mkdir(parents=True, exist_ok=True)
    for index, raw_case in enumerate(cases):
        case = dict(raw_case)
        case_id = str(case.get("case_id") or "case-%02d" % (index + 1))
        case_root = root / case_id
        service = MemGuardService(
            store_path=case_root / "memory.segment",
            db_path=case_root / "memory.sqlite3",
        )
        try:
            system_result = _validate_system_behavior(service, case)
            paired_result = evaluate_paired_case(case)
            paired_result["system_validation"] = system_result
            case_results.append(paired_result)
            for reason in system_result["failures"]:
                failures.append({"case_id": case_id, "reason": reason})
        finally:
            service.close()
    passed = sum(
        1 for result in case_results if result["system_validation"]["passed"]
    )
    return {
        "case_count": len(case_results),
        "aggregate": aggregate_paired_results(case_results),
        "system_validation": {
            "passed": passed,
            "failed": len(case_results) - passed,
        },
        "cases": case_results,
        "failures": failures,
    }


def _validate_system_behavior(
    service: MemGuardService,
    case: Dict[str, object],
) -> Dict[str, object]:
    case_id = str(case.get("case_id") or "case")
    last_commands: Dict[str, str] = {}
    project_root = ""
    task_type = "code_assistance"
    for sequence, raw_event in enumerate(case.get("history_events") or [], start=1):
        event_data = dict(raw_event)
        session_id = str(event_data.get("session_id") or case_id)
        project_root = str(event_data.get("project_root") or project_root)
        task_type = str(event_data.get("task_type") or task_type)
        event_type = str(event_data.get("event_type") or "user_message")
        tool_args = dict(event_data.get("tool_args") or {})
        event = AgentInteractionEvent(
            agent_name=str(event_data.get("agent_name") or "openhands"),
            agent_version=str(event_data.get("agent_version") or "1.28.1"),
            session_id=session_id,
            turn_id=str(event_data.get("turn_id") or "turn-%s" % sequence),
            sequence=sequence,
            event_type=event_type,
            role=str(event_data.get("role") or "user"),
            content=str(event_data.get("content") or ""),
            tool_name=str(event_data.get("tool_name") or ""),
            tool_args=tool_args,
            tool_result=str(event_data.get("tool_result") or ""),
            exit_code=event_data.get("exit_code"),
            project_root=str(event_data.get("project_root") or ""),
            task_type=str(event_data.get("task_type") or task_type),
            source_uri="memory-value://%s/%s" % (case_id, sequence),
            event_id="evt-%s-%s" % (case_id.lower(), sequence),
        )
        service.ingest_interaction(event)
        if event_type == "tool_call":
            last_commands[session_id] = str(tool_args.get("command") or "")
        elif event_type == "tool_result":
            exit_code = int(event_data.get("exit_code") or 0)
            output = str(event_data.get("tool_result") or "")
            service.ingest_tool_result(
                ToolResult(
                    command=last_commands.get(session_id, ""),
                    cwd=project_root,
                    exit_code=exit_code,
                    stdout=output if exit_code == 0 else "",
                    stderr=output if exit_code != 0 else "",
                    tool_name=str(event_data.get("tool_name") or "terminal"),
                    state_before="待完成同类任务",
                    state_after="命令执行完成" if exit_code == 0 else "命令执行失败",
                    lesson=(
                        "后续同类任务避免重复失败并优先复用已验证成功的命令"
                        if exit_code != 0
                        else "该命令已在当前环境验证"
                    ),
                ),
                source_uri="memory-value-tool://%s/%s" % (case_id, sequence),
            )

    assertions = dict(case.get("assertions") or {})
    expected_topics = set(str(item) for item in case.get("expected_topics") or [])
    selection = service.select_preferences(
        agent_name="openhands",
        session_id="%s-eval" % case_id.lower(),
        project_root=project_root,
        task_type=task_type,
    )
    selected = list(selection["selected"])
    selected_topics = {
        str(item.get("preference_topic") or "") for item in selected
    }
    selected_values = {
        str(item.get("preference_value") or "") for item in selected
    }
    failures = []
    if not expected_topics.issubset(selected_topics):
        failures.append(
            "expected topics %s but selected %s"
            % (sorted(expected_topics), sorted(selected_topics))
        )
    selected_scope = str(assertions.get("selected_scope") or "")
    if selected_scope and not any(
        str(item.get("preference_scope") or "") == selected_scope for item in selected
    ):
        failures.append("expected selected scope %s" % selected_scope)
    selected_value = str(assertions.get("selected_value") or "")
    if selected_value and selected_value not in selected_values:
        failures.append("expected selected value %s" % selected_value)
    forbidden_value = str(assertions.get("forbidden_value") or "")
    if forbidden_value and forbidden_value in selected_values:
        failures.append("forbidden polluted value selected: %s" % forbidden_value)

    required_subtype = str(assertions.get("required_subtype") or "")
    active_memories = service.list_memories(include_deleted=False)
    if required_subtype and not any(
        str(item.get("subtype") or "") == required_subtype
        for item in active_memories
    ):
        failures.append("required memory subtype missing: %s" % required_subtype)

    forget_result = None
    forget_filters = dict(case.get("forget_filters") or {})
    if forget_filters:
        forget_result = service.forget_agent_interactions(
            forget_filters,
            confirm=True,
            compact=True,
            external_persistence=False,
        )
        after = service.select_preferences(
            agent_name="openhands",
            session_id="%s-after-forget" % case_id.lower(),
            project_root=project_root,
            task_type=task_type,
        )
        after_topics = {
            str(item.get("preference_topic") or "") for item in after["selected"]
        }
        absent_topics = set(str(item) for item in assertions.get("absent_topics") or [])
        retained_topics = set(
            str(item) for item in assertions.get("retained_topics") or []
        )
        if absent_topics & after_topics:
            failures.append(
                "forgotten topics remained: %s"
                % sorted(absent_topics & after_topics)
            )
        if not retained_topics.issubset(after_topics):
            failures.append(
                "unrelated topics not retained: expected=%s actual=%s"
                % (sorted(retained_topics), sorted(after_topics))
            )
        if not forget_result["verification"]["all_stages_absent"]:
            failures.append("forget verification reported stage residue")

    return {
        "passed": not failures,
        "selected_topics": sorted(selected_topics),
        "selected_values": sorted(selected_values),
        "memory_count": len(active_memories),
        "forget_result": forget_result,
        "failures": failures,
    }


def _as_int(value: object, default: int) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def _bool_rate(items: List[Dict[str, object]], key: str) -> float:
    if not items:
        return 0.0
    return round(sum(1 for item in items if item.get(key)) / len(items), 6)


def _eligible_bool_rate(
    items: List[Dict[str, object]], key: str, eligibility_key: str
) -> float:
    eligible = [item for item in items if _as_int(item.get(eligibility_key), 0) > 0]
    return _bool_rate(eligible, key)


def _mean_metric(items: List[Dict[str, object]], key: str) -> float:
    if not items:
        return 0.0
    return round(mean(float(item.get(key) or 0) for item in items), 6)
