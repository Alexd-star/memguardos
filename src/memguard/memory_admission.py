"""Policy-gated admission for agent memory.

The admission layer is deliberately provider-neutral.  It does not replace
the extractor or the sensitivity detector; it combines their outputs into a
reversible retention decision that can be shown to a user before persistence.
Automatic observations are kept short-lived until they have enough evidence,
while explicit user instructions retain their natural authority.
"""

from __future__ import annotations

from typing import Dict


SCHEMA = "memguardos-memory-admission-v1"
_EXPLICIT_SOURCES = {"manual", "user_cli", "api", "dashboard", "user_message"}
_AUTOMATIC_SOURCES = {
    "tool_result", "tool_result_verified", "agent_interaction", "user_behavior",
    "shell_history", "os_event", "auditd", "desktop_event",
}


def _score(value: object, default: float = 0.0) -> float:
    try:
        return max(0.0, min(1.0, float(value)))
    except (TypeError, ValueError):
        return default


def _clean(value: object) -> str:
    return str(value or "").strip()


def assess_memory_admission(
    *,
    source_kind: str,
    requested_tier: str = "",
    inferred_tier: str = "",
    memory_type: str = "",
    memory_subtype: str = "",
    quality_score: object = 1.0,
    extraction_confidence: object = 1.0,
    sensitivity_level: str = "none",
    governance_state: str = "active",
    explicit_request: bool = False,
    verified_tool_evidence: bool = False,
    storage_prohibited: bool = False,
    quality_rejected: bool = False,
    scope: Dict[str, object] | None = None,
) -> Dict[str, object]:
    """Return a deterministic, explainable retention recommendation.

    ``admit_long_term`` is a recommendation, not an irreversible write.  A
    caller may enforce it for automatic ingestion or use report mode while
    migrating an existing deployment.  This separation keeps compatibility
    with older adapters and makes policy changes auditable.
    """

    source = _clean(source_kind) or "unknown"
    requested = _clean(requested_tier)
    inferred = _clean(inferred_tier)
    tier = requested or inferred or "long_term"
    quality = _score(quality_score, 0.0)
    confidence = _score(extraction_confidence, 0.0)
    sensitivity = _clean(sensitivity_level).lower() or "none"
    governance = _clean(governance_state).lower() or "active"
    explicit = bool(explicit_request) or source in _EXPLICIT_SOURCES
    automatic = source in _AUTOMATIC_SOURCES
    scope = dict(scope or {})
    factors = []
    evidence_requirements = []

    if explicit:
        factors.append("用户明确输入或人工来源")
    elif automatic:
        factors.append("自动观测来源")
    else:
        factors.append("非交互来源")
    if verified_tool_evidence:
        factors.append("工具结果已通过内部证据校验")
    if quality < 0.6:
        factors.append("内容质量低于长期记忆建议阈值")
    if confidence < 0.65:
        factors.append("语义抽取置信度不足")
    if scope.get("project_root"):
        factors.append("已绑定项目范围")

    base = {
        "schema": SCHEMA,
        "source_kind": source,
        "requested_tier": requested,
        "inferred_tier": inferred,
        "memory_type": _clean(memory_type),
        "memory_subtype": _clean(memory_subtype),
        "scope": {
            "namespace": _clean(scope.get("namespace")),
            "agent_name": _clean(scope.get("agent_name")),
            "session_id": _clean(scope.get("session_id")),
            "project_root": _clean(scope.get("project_root")),
            "task_type": _clean(scope.get("task_type")),
        },
        "signals": {
            "explicit_request": explicit,
            "automatic_source": automatic,
            "verified_tool_evidence": bool(verified_tool_evidence),
            "quality_score": round(quality, 4),
            "extraction_confidence": round(confidence, 4),
            "sensitivity_level": sensitivity,
            "governance_state": governance,
        },
        "factors": factors,
        "evidence_requirements": evidence_requirements,
        "reversible": True,
    }

    if storage_prohibited or quality_rejected:
        return {
            **base,
            "decision": "deny",
            "recommended_tier": "none",
            "review_required": False,
            "reason": "用户要求不保存内容。" if storage_prohibited else "内容质量检查未通过。",
            "next_action": "不写入记忆。",
        }
    if sensitivity == "high":
        return {
            **base,
            "decision": "deny",
            "recommended_tier": "none",
            "review_required": False,
            "reason": "高风险敏感内容不得进入记忆层。",
            "next_action": "阻断写入；如确有业务需要，请在外部受控密钥或凭据系统中管理。",
        }
    if governance in {"quarantined", "quarantine", "pending_review"}:
        return {
            **base,
            "decision": "quarantine",
            "recommended_tier": "session" if tier != "working" else "working",
            "review_required": True,
            "reason": "来源治理状态要求人工复核后才能进入长期层。",
            "next_action": "保留可撤回的临时记录，完成来源复核后再提升保留级别。",
        }
    if tier == "working":
        return {
            **base,
            "decision": "retain_working",
            "recommended_tier": "working",
            "review_required": False,
            "reason": "内容被限定在当前工作阶段。",
            "next_action": "任务结束时按生命周期规则淘汰，或在证据充分后提交巩固。",
        }
    if explicit and tier not in {"working", "session"}:
        return {
            **base,
            "decision": "admit_long_term",
            "recommended_tier": "long_term",
            "review_required": False,
            "reason": "用户明确表达了可复用意图，且未触发敏感或治理阻断。",
            "next_action": "写入长期层，并保留来源、版本和后续遗忘路径。",
        }
    if automatic and not verified_tool_evidence:
        evidence_requirements.extend(["至少 2 次独立观测", "同一项目或任务范围内重复出现", "至少 1 次成功使用反馈"])
        return {
            **base,
            "decision": "retain_session",
            "recommended_tier": "session",
            "review_required": quality < 0.6 or confidence < 0.65,
            "reason": "自动观测尚未达到长期记忆的证据门槛。",
            "next_action": "先保留在会话层，收集重复出现和动作结果后再进入巩固队列。",
        }
    if quality < 0.6 or confidence < 0.65:
        evidence_requirements.append("补充来源或用户确认")
        return {
            **base,
            "decision": "retain_session",
            "recommended_tier": "session",
            "review_required": True,
            "reason": "内容可以暂存，但质量或抽取置信度不足以直接长期保存。",
            "next_action": "补充证据后重新评估，避免把一次性噪声固化为事实。",
        }
    return {
        **base,
        "decision": "admit_long_term",
        "recommended_tier": "long_term",
        "review_required": False,
        "reason": "来源、质量和语义置信度满足长期复用条件。",
        "next_action": "写入长期层，并通过版本链、检索命中和遗忘验证持续治理。",
    }
