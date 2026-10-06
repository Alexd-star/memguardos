"""Conservative, explainable quality metrics for memory-conditioned actions.

The action feedback ledger tells us what happened after a memory influenced an
Agent action.  This module turns that ledger into a review report without
claiming that observational data is a randomized causal experiment.  The
report is intentionally deterministic and provider-neutral so it can be used
on a workstation, in a benchmark, or in the openKylin maintenance UI.
"""

from __future__ import annotations

from math import sqrt
from typing import Any, Dict, Iterable, Mapping, Sequence


OUTCOME_KEYS = ("success", "failure", "deviation", "blocked", "unknown")


def wilson_lower_bound(successes: int, trials: int, z: float = 1.96) -> float:
    """Return a conservative lower confidence bound for a Bernoulli rate."""

    n = max(0, int(trials))
    s = max(0, min(int(successes), n))
    if n == 0:
        return 0.0
    p = s / n
    z2 = z * z
    denominator = 1.0 + z2 / n
    centre = p + z2 / (2.0 * n)
    margin = z * sqrt((p * (1.0 - p) + z2 / (4.0 * n)) / n)
    return max(0.0, min(1.0, (centre - margin) / denominator))


def summarize_action_outcomes(outcomes: Iterable[Mapping[str, Any]]) -> Dict[str, Any]:
    """Summarize outcomes while excluding unknown/blocked from executions."""

    counts = {key: 0 for key in OUTCOME_KEYS}
    for outcome in outcomes:
        key = str(outcome.get("outcome") or "unknown")
        if key not in counts:
            key = "unknown"
        counts[key] += 1
    executed = counts["success"] + counts["failure"] + counts["deviation"]
    adverse = counts["failure"] + counts["deviation"]
    success_rate = counts["success"] / executed if executed else 0.0
    adherence_trials = counts["success"] + counts["deviation"] + counts["failure"]
    adherence_rate = counts["success"] / adherence_trials if adherence_trials else 0.0
    lower = wilson_lower_bound(counts["success"], executed)
    evidence_confidence = min(1.0, executed / 5.0)
    utility_score = min(
        1.0,
        max(0.0, 0.60 * lower + 0.25 * adherence_rate + 0.15 * evidence_confidence),
    )
    risk_score = min(1.0, adverse / executed if executed else 0.0)
    if executed == 0:
        recommendation = "observe"
        gate = "observe"
    elif executed < 3:
        recommendation = "collect_more_evidence"
        gate = "observe"
    elif risk_score >= 0.5 or lower < 0.30:
        recommendation = "review_or_demote"
        gate = "review"
    elif lower >= 0.70 and adherence_rate >= 0.80:
        recommendation = "retain_reinforced"
        gate = "pass"
    else:
        recommendation = "retain_observe"
        gate = "observe"
    return {
        **counts,
        "success_count": counts["success"],
        "failure_count": counts["failure"],
        "deviation_count": counts["deviation"],
        "blocked_count": counts["blocked"],
        "unknown_count": counts["unknown"],
        "total_count": sum(counts.values()),
        "executed_count": executed,
        "adverse_count": adverse,
        "success_rate": round(success_rate, 4),
        "adherence_rate": round(adherence_rate, 4),
        "wilson_success_lower_bound": round(lower, 4),
        "evidence_confidence": round(evidence_confidence, 4),
        "utility_score": round(utility_score, 4),
        "risk_score": round(risk_score, 4),
        "recommendation": recommendation,
        "quality_gate": gate,
    }


def cohort_key(outcome: Mapping[str, Any]) -> tuple[str, str, str]:
    """Use only non-sensitive routing metadata for an observational cohort."""

    return (
        str(outcome.get("agent_name") or ""),
        str(outcome.get("project_root") or ""),
        str(outcome.get("task_type") or ""),
    )


def attach_observational_baseline(
    metrics: Dict[str, Any],
    *,
    memory_outcomes: Sequence[Mapping[str, Any]],
    cohort_outcomes: Sequence[Mapping[str, Any]],
) -> Dict[str, Any]:
    """Add same-cohort comparison, explicitly labelled as observational."""

    memory_ids = {str(item.get("memory_id") or "") for item in memory_outcomes}
    peers = [
        item for item in cohort_outcomes
        if str(item.get("memory_id") or "") not in memory_ids
    ]
    peer_metrics = summarize_action_outcomes(peers)
    if peer_metrics["executed_count"]:
        metrics["cohort_baseline_success_rate"] = peer_metrics["success_rate"]
        metrics["observed_success_lift"] = round(
            metrics["success_rate"] - peer_metrics["success_rate"], 4
        )
    else:
        metrics["cohort_baseline_success_rate"] = None
        metrics["observed_success_lift"] = None
    metrics["comparison_basis"] = "same agent/project/task cohort; observational, not causal"
    return metrics
