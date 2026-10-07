"""Explainable memory-budget scoring for reversible long-term curation.

The policy is intentionally provider-neutral.  It does not call an LLM and it
never decides to erase data.  A plan only identifies long-term records whose
value-per-byte is lowest; the service turns an approved selection into a new
session-tier version so the operation remains reversible and auditable.
"""

from __future__ import annotations

from datetime import datetime, timezone
import math
from typing import Any, Dict, Mapping


def _bounded_float(value: Any, default: float = 0.0) -> float:
    try:
        return max(0.0, min(float(value), 1.0))
    except (TypeError, ValueError):
        return default


def _age_days(value: Any, as_of: str) -> float:
    try:
        activity = datetime.fromisoformat(str(value or "").replace("Z", "+00:00"))
        reference = datetime.fromisoformat(str(as_of).replace("Z", "+00:00"))
        if activity.tzinfo is None:
            activity = activity.replace(tzinfo=timezone.utc)
        if reference.tzinfo is None:
            reference = reference.replace(tzinfo=timezone.utc)
        return max(0.0, (reference - activity.astimezone(timezone.utc)).total_seconds() / 86400.0)
    except (TypeError, ValueError):
        return 3650.0


def estimate_memory_bytes(row: Mapping[str, Any]) -> int:
    """Estimate the durable payload size without exposing or storing plaintext.

    SQLite metadata and the append-only segment have framing overhead, so this
    is a planning estimate rather than a filesystem accounting API.  The same
    deterministic estimate is used when applying a plan and is therefore
    suitable for stale-plan checks.
    """

    content = str(row.get("content") or "")
    summary = str(row.get("summary") or "")
    metadata = sum(
        len(str(row.get(key) or "").encode("utf-8"))
        for key in (
            "memory_id", "version_id", "type", "subtype", "tier", "scope",
            "project_root", "preference_topic", "preference_value", "task_type",
        )
    )
    return max(128, len(content.encode("utf-8")) + len(summary.encode("utf-8")) + metadata + 256)


def score_memory_budget(row: Mapping[str, Any], *, as_of: str) -> Dict[str, Any]:
    """Score value, risk, and byte efficiency using auditable signals."""

    stats = row.get("action_outcome_stats") if isinstance(row.get("action_outcome_stats"), Mapping) else {}
    hit_count = max(0, int(row.get("hit_count") or 0))
    success_count = max(0, int(stats.get("success_count") or 0))
    failure_count = max(0, int(stats.get("failure_count") or 0))
    deviation_count = max(0, int(stats.get("deviation_count") or 0))
    adherence = _bounded_float(stats.get("adherence_rate"), 0.0)
    activity = str(row.get("last_hit_at") or row.get("updated_at") or "")
    age_days = _age_days(activity, as_of)
    recency = math.exp(-age_days / 45.0)
    frequency = min(1.0, math.log1p(hit_count) / math.log1p(12))
    action_success = min(1.0, math.log1p(success_count) / math.log1p(6))
    confidence = _bounded_float(row.get("confidence"), 0.0)
    importance = _bounded_float(row.get("importance"), 0.0)
    trust = _bounded_float(row.get("trust_score"), 0.0)
    explicit = bool(row.get("preference_explicit")) and str(row.get("type") or "") in {"preference", "sensitive_policy"}
    procedural = str(row.get("subtype") or "") in {"tool_trace", "procedural_trace", "tool_failure_case", "workflow"}
    sensitivity = str(row.get("sensitivity_level") or "none")
    governance_state = str(row.get("governance_state") or "active")
    value = (
        0.20 * importance
        + 0.18 * confidence
        + 0.16 * trust
        + 0.14 * recency
        + 0.10 * frequency
        + 0.12 * action_success
        + 0.05 * adherence
        + (0.12 if explicit else 0.0)
        + (0.05 if procedural else 0.0)
    )
    risk = (
        (0.75 if sensitivity == "high" else 0.35 if sensitivity == "medium" else 0.0)
        + (0.35 if governance_state != "active" else 0.0)
        + min(0.35, failure_count * 0.08)
        + min(0.20, deviation_count * 0.06)
    )
    risk = min(1.0, risk)
    protected = explicit or sensitivity == "high" or governance_state != "active"
    net_value = max(0.0, min(1.0, value - 0.25 * risk))
    storage_bytes = estimate_memory_bytes(row)
    return {
        "memory_id": str(row.get("memory_id") or ""),
        "version_id": str(row.get("version_id") or ""),
        "storage_bytes": storage_bytes,
        "age_days": round(age_days, 3),
        "recency_score": round(recency, 4),
        "frequency_score": round(frequency, 4),
        "confidence": round(confidence, 4),
        "importance": round(importance, 4),
        "trust_score": round(trust, 4),
        "action_success_count": success_count,
        "action_failure_count": failure_count,
        "action_deviation_count": deviation_count,
        "action_adherence_rate": round(adherence, 4),
        "value_score": round(value, 4),
        "risk_score": round(risk, 4),
        "net_value": round(net_value, 4),
        "value_per_byte": round(net_value / storage_bytes, 8),
        "protected": protected,
        "protection_reason": (
            "explicit_preference" if explicit
            else "sensitive_memory" if sensitivity == "high"
            else "governance_review" if governance_state != "active"
            else ""
        ),
    }


def recommend_budget_action(score: Mapping[str, Any]) -> str:
    if bool(score.get("protected")):
        return "retain_protected"
    return "demote_to_session"
