"""Stage-aware memory representations for Agent context assembly.

The retriever decides *which* memories are relevant.  This module decides
*how much* of an eligible memory should enter a particular Agent stage.  The
policy is deliberately provider-neutral and deterministic so that the same
context receipt can be audited or reproduced without an LLM call.
"""

from __future__ import annotations

from typing import Dict, Tuple


CONTEXT_COMPACTION_POLICIES = {"adaptive", "full"}
REPRESENTATIONS = {"full", "evidence", "summary"}


def normalize_compaction_policy(policy: str) -> str:
    clean = str(policy or "adaptive").strip().lower()
    if clean not in CONTEXT_COMPACTION_POLICIES:
        raise ValueError("unsupported context compaction policy: %s" % clean)
    return clean


def _source_kind(memory: Dict[str, object]) -> str:
    source_kind = str(memory.get("source_kind") or "")
    if source_kind:
        return source_kind
    source = memory.get("source")
    if isinstance(source, dict):
        return str(source.get("kind") or "")
    return ""


def choose_representation(
    memory: Dict[str, object],
    *,
    stage: str,
    policy: str = "adaptive",
    remaining_tokens: int = 0,
    sensitivity_level: str = "none",
) -> Tuple[str, str]:
    """Return ``(representation, reason)`` for one selected memory.

    Protected procedures and tool-selection preferences retain the actionable
    part of their content.  Routine knowledge is allowed to enter planning or
    answer generation as a compact summary.  Sensitive records never receive
    a larger representation merely because the context budget is available.
    """

    clean_policy = normalize_compaction_policy(policy)
    subtype = str(memory.get("subtype") or "")
    memory_type = str(memory.get("type") or "")
    source_kind = _source_kind(memory)
    preference_topic = str(memory.get("preference_topic") or "")
    if clean_policy == "full":
        return "full", "caller_requested_full_representation"
    if source_kind == "verified_procedure" or subtype in {"workflow", "procedural_trace"}:
        if stage in {"planning", "tool_selection"}:
            return "full", "procedure_constraints_are_actionable"
        return "evidence", "procedure_summary_with_provenance_for_later_stage"
    if preference_topic or memory_type in {"preference", "sensitive_policy"}:
        return "evidence", "preference_directive_and_provenance_preserved"
    if str(sensitivity_level).lower() in {"high", "critical", "secret"}:
        return "evidence", "sensitive_record_uses_minimal_safe_evidence"
    if stage == "planning":
        return "summary", "planning_keeps_routine_knowledge_compact"
    if stage == "response_generation" and remaining_tokens < 220:
        return "summary", "answer_stage_budget_prefers_compact_knowledge"
    return "evidence", "evidence_keeps_source_and_confidence_visible"


def render_representation(
    memory: Dict[str, object],
    *,
    representation: str,
    selection_reason: str,
) -> str:
    """Render a bounded, redaction-compatible context line."""

    if representation not in REPRESENTATIONS:
        raise ValueError("unsupported memory representation: %s" % representation)
    memory_id = str(memory.get("memory_id") or "")
    summary = " ".join(str(memory.get("summary") or "").split())
    content = " ".join(str(memory.get("content") or "").split())
    source_kind = _source_kind(memory) or "unknown"
    confidence = memory.get("confidence", "")
    version_id = str(memory.get("version_id") or "")
    base = "[%s] type=%s subtype=%s tier=%s reason=%s" % (
        memory_id,
        memory.get("type", ""),
        memory.get("subtype", ""),
        memory.get("tier", ""),
        selection_reason,
    )
    if representation == "summary":
        short_summary = summary or content[:220]
        return "%s\nsummary: %s" % (base, short_summary[:300])
    if representation == "evidence":
        directive = ""
        if memory.get("preference_topic"):
            directive = "\ndirective: %s=%s" % (
                memory.get("preference_topic"),
                str(memory.get("preference_value") or "")[:180],
            )
        return (
            "%s\nsummary: %s\nevidence: source=%s confidence=%s version=%s%s"
            % (base, (summary or content[:220])[:300], source_kind, confidence, version_id, directive)
        )
    return (
        "%s\nsummary: %s\nevidence: source=%s confidence=%s version=%s\ncontent: %s"
        % (base, summary[:300], source_kind, confidence, version_id, content[:500])
    )
