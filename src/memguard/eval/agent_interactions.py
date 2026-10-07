from __future__ import annotations

from collections import Counter
from pathlib import Path
from time import perf_counter
from typing import Dict, Iterable, List

from memguard.integrations.events import AgentInteractionEvent
from memguard.service import MemGuardService


def evaluate_agent_interaction_cases(
    cases: Iterable[Dict[str, object]],
    work_root: Path,
) -> Dict[str, object]:
    case_list = list(cases)
    root = Path(work_root)
    root.mkdir(parents=True, exist_ok=True)
    source_distribution = Counter()
    category_distribution = Counter()
    failures = []
    latencies = []
    true_positive = false_positive = false_negative = 0
    pollution_cases = pollution_failures = 0
    sensitive_cases = sensitive_leaks = 0
    forget_cases = forget_success = 0
    cross_cases = cross_success = 0

    for index, case in enumerate(case_list):
        case_id = str(case.get("case_id") or "case-%04d" % index)
        category = str(case.get("category") or "unknown")
        source_origin = str(case.get("source_origin") or "unspecified")
        source_distribution[source_origin] += 1
        category_distribution[category] += 1
        case_root = root / ("%04d_%s" % (index, _safe_name(case_id)))
        service = MemGuardService(
            store_path=case_root / "memory.segment",
            db_path=case_root / "memory.sqlite3",
        )
        started = perf_counter()
        try:
            for sequence, raw in enumerate(case.get("events") or [], start=1):
                event = _event_from_case(case_id, sequence, dict(raw))
                service.ingest_interaction(event)
            expected = set(str(item) for item in case.get("expected_topics") or [])
            actual = {
                str(item["preference_topic"])
                for item in service.list_memories(include_deleted=False)
                if item.get("type") in {"preference", "sensitive_policy"}
                and item.get("preference_topic")
            }
            true_positive += len(expected & actual)
            false_positive += len(actual - expected)
            false_negative += len(expected - actual)

            reasons = []
            if actual != expected:
                reasons.append(
                    "preference topics expected=%s actual=%s"
                    % (sorted(expected), sorted(actual))
                )
            if category == "role_pollution":
                pollution_cases += 1
                if actual:
                    pollution_failures += 1
            if category == "sensitive_interaction":
                sensitive_cases += 1
                secret = str(case.get("secret_probe") or "")
                stored = service.meta.list_agent_events(limit=1000)
                if secret and secret in str(stored):
                    sensitive_leaks += 1
                    reasons.append("sensitive probe remained in stored event")
            if category == "precise_forgetting":
                forget_cases += 1
                result = service.forget_agent_interactions(
                    dict(case.get("forget_filters") or {}),
                    confirm=True,
                    compact=True,
                    external_persistence=False,
                )
                if result["verification"]["all_stages_absent"]:
                    forget_success += 1
                else:
                    reasons.append("forgotten memory remained in Agent context")
            if category == "cross_session":
                cross_cases += 1
                context = service.build_agent_context(
                    task=str(case.get("context_task") or "继续处理任务"),
                    stage=str(case.get("context_stage") or "planning"),
                    agent_name="openhands",
                    session_id=case_id + "-new-session",
                )
                selected_topics = {
                    str(item.get("preference_topic"))
                    for item in context["memories"]
                    if item.get("preference_topic")
                }
                if expected.issubset(selected_topics):
                    cross_success += 1
                else:
                    reasons.append(
                        "cross-session context expected=%s selected=%s"
                        % (sorted(expected), sorted(selected_topics))
                    )
            if reasons:
                failures.append(
                    {
                        "case_id": case_id,
                        "category": category,
                        "reasons": reasons,
                    }
                )
        finally:
            latencies.append((perf_counter() - started) * 1000.0)
            service.close()

    precision = _ratio(true_positive, true_positive + false_positive)
    recall = _ratio(true_positive, true_positive + false_negative)
    f1 = _ratio(2.0 * precision * recall, precision + recall)
    ordered_latencies = sorted(latencies)
    return {
        "case_count": len(case_list),
        "source_distribution": dict(sorted(source_distribution.items())),
        "category_distribution": dict(sorted(category_distribution.items())),
        "metrics": {
            "extraction": {
                "true_positive": true_positive,
                "false_positive": false_positive,
                "false_negative": false_negative,
                "precision": round(precision, 6),
                "recall": round(recall, 6),
                "f1": round(f1, 6),
            },
            "role_pollution_rate": round(_ratio(pollution_failures, pollution_cases), 6),
            "sensitive_leak_rate": round(_ratio(sensitive_leaks, sensitive_cases), 6),
            "forget_success_rate": round(_ratio(forget_success, forget_cases), 6),
            "cross_session_adherence": round(_ratio(cross_success, cross_cases), 6),
        },
        "latency_ms": {
            "mean": round(_ratio(sum(ordered_latencies), len(ordered_latencies)), 3),
            "p50": round(_percentile(ordered_latencies, 0.50), 3),
            "p95": round(_percentile(ordered_latencies, 0.95), 3),
            "max": round(max(ordered_latencies) if ordered_latencies else 0.0, 3),
        },
        "failures": failures,
    }


def _event_from_case(
    case_id: str,
    sequence: int,
    raw: Dict[str, object],
) -> AgentInteractionEvent:
    event_type = str(raw.get("event_type") or "user_message")
    role = str(raw.get("role") or "user")
    session_id = str(raw.get("session_id") or case_id)
    return AgentInteractionEvent(
        agent_name=str(raw.get("agent_name") or "openhands"),
        agent_version=str(raw.get("agent_version") or "1.28.1"),
        session_id=session_id,
        turn_id=str(raw.get("turn_id") or "turn-%s" % sequence),
        sequence=int(raw.get("sequence") or sequence),
        event_type=event_type,
        role=role,
        content=str(raw.get("content") or ""),
        tool_name=str(raw.get("tool_name") or ""),
        tool_args=dict(raw.get("tool_args") or {}),
        tool_result=str(raw.get("tool_result") or ""),
        exit_code=raw.get("exit_code"),
        project_root=str(raw.get("project_root") or "/workspace/demo"),
        task_type=str(raw.get("task_type") or "code_assistance"),
        source_uri="benchmark://%s/%s" % (case_id, sequence),
        event_id="evt-%s-%s" % (_safe_name(case_id), sequence),
    )


def _ratio(numerator: float, denominator: float) -> float:
    return float(numerator) / float(denominator) if denominator else 0.0


def _percentile(values: List[float], quantile: float) -> float:
    if not values:
        return 0.0
    index = int(round((len(values) - 1) * quantile))
    return float(values[max(0, min(index, len(values) - 1))])


def _safe_name(value: str) -> str:
    return "".join(character if character.isalnum() or character in "-_" else "-" for character in value)[:80]
