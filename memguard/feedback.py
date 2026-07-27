"""User-facing feedback projections shared by CLI, Web and desktop adapters.

The storage and audit layers remain the source of truth.  This module deliberately
contains no persistence and never exposes sensitive payloads; it converts trusted
service results into stable, readable contracts for product surfaces.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
import json
import re
from typing import Any, Iterable, Mapping


@dataclass
class MemoryChangeSummary:
    schema: str = "memguardos-memory-change-summary-v1"
    namespace: str = ""
    session_id: str = ""
    source: str = "manual"
    new_memories: list[dict[str, Any]] = field(default_factory=list)
    updated_memories: list[dict[str, Any]] = field(default_factory=list)
    blocked_items: list[dict[str, Any]] = field(default_factory=list)
    skipped_items: list[dict[str, Any]] = field(default_factory=list)
    effective_scopes: list[str] = field(default_factory=list)
    suggested_next_actions: list[str] = field(default_factory=list)
    audit_event_ids: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class PreferenceEffectProof:
    schema: str = "memguardos-preference-effect-proof-v1"
    task: str = ""
    agent_name: str = "openhands"
    session_id: str = ""
    stage: str = "tool_selection"
    preference: dict[str, Any] = field(default_factory=dict)
    injected: bool = False
    actual_behavior: dict[str, Any] = field(default_factory=dict)
    compliance: str = "not_applicable"
    failure_reason: str = ""
    evidence_links: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class ForgetVerificationReport:
    schema: str = "memguardos-forget-verification-report-v1"
    request: str = ""
    dry_run: bool = True
    confirmed: bool = False
    candidate_count: int = 0
    forgotten_memory_ids: list[str] = field(default_factory=list)
    verification_steps: list[dict[str, str]] = field(default_factory=list)
    external_residue: dict[str, str] = field(default_factory=dict)
    user_summary: str = ""

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class SensitiveBlockNotice:
    schema: str = "memguardos-sensitive-block-notice-v1"
    source: str = ""
    detected_labels: list[str] = field(default_factory=list)
    risk_level: str = "high"
    action: str = "blocked"
    stored: bool = False
    entered_rag: bool = False
    audit_event_id: str = ""
    user_summary: str = ""
    next_actions: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


_EXTERNAL_BOUNDARY_KEYS = {
    "openhands_raw_logs": "not_managed",
    "shell_history": "not_managed",
    "model_provider_logs": "not_managed",
    "filesystem_backups": "not_managed",
}

_VERIFICATION_NAMES = (
    "main_record",
    "agent_event_payload",
    "sqlite_fts",
    "graph_relations",
    "search_cache",
    "mmap_segment_compact",
    "plaintext_probe",
    "agent_context_absence",
    "audit_chain",
)


def build_memory_change_summary(
    result: Mapping[str, Any],
    *,
    namespace: str = "",
    source: str = "manual",
    session_id: str = "",
    audit_event_ids: Iterable[str] = (),
) -> MemoryChangeSummary:
    """Create a non-sensitive summary from an ingest or import result."""

    summary = MemoryChangeSummary(
        namespace=str(namespace or ""),
        session_id=str(session_id or ""),
        source=str(source or "manual"),
        audit_event_ids=[str(item) for item in audit_event_ids if str(item)],
    )
    record = result.get("record")
    if result.get("duplicate") and record is not None:
        item = _memory_item(record, result)
        summary.skipped_items.append(
            {
                "reason": "duplicate_memory_reused",
                "memory_id": item.get("memory_id", ""),
                "summary_for_user": "相同内容已存在，本次未重复保存；继续使用原有记忆编号。",
            }
        )
        summary.effective_scopes.extend(_where_it_applies(item))
        summary.suggested_next_actions.extend(["view", "test_effect"])
    elif result.get("stored") and record is not None:
        item = _memory_item(record, result)
        summary.new_memories.append(item)
        summary.effective_scopes.extend(_where_it_applies(item))
        summary.suggested_next_actions.extend(["view", "test_effect", "forget"])
    elif result.get("updated") and record is not None:
        item = _memory_item(record, result)
        summary.updated_memories.append(item)
        summary.effective_scopes.extend(_where_it_applies(item))
        summary.suggested_next_actions.extend(["view", "test_effect"])
    else:
        sensitivity = _sensitivity_dict(result.get("sensitivity"))
        if result.get("reason") == "sensitive_block" or sensitivity.get("level") == "high":
            summary.blocked_items.append(
                {
                    "reason": "sensitive_content_blocked",
                    "labels": list(sensitivity.get("labels") or []),
                    "summary_for_user": "发现高风险敏感内容，未保存到长期记忆，也未进入检索上下文。",
                }
            )
            summary.suggested_next_actions.append("view_audit")
        else:
            summary.skipped_items.append(
                {
                    "reason": str(result.get("reason") or "not_stored"),
                    "summary_for_user": "本次内容未形成可保存的记忆。",
                }
            )
            summary.suggested_next_actions.append("review_input")
    summary.effective_scopes = _unique(summary.effective_scopes)
    summary.suggested_next_actions = _unique(summary.suggested_next_actions)
    return summary


def build_sensitive_block_notice(
    result: Mapping[str, Any],
    *,
    source: str = "",
    audit_event_id: str = "",
) -> SensitiveBlockNotice:
    sensitivity = _sensitivity_dict(result.get("sensitivity"))
    action = "blocked" if result.get("reason") == "sensitive_block" or sensitivity.get("level") == "high" else "redacted"
    labels = [str(item) for item in sensitivity.get("labels") or []]
    if action == "blocked":
        summary = "已保护敏感内容：本次内容没有进入长期记忆或检索上下文。"
        next_actions = ["view_audit", "dismiss"]
    else:
        summary = "内容已脱敏后保存，原始敏感字段不会提供给 Agent。"
        next_actions = ["view", "dismiss"]
    return SensitiveBlockNotice(
        source=str(source or ""),
        detected_labels=labels,
        risk_level=str(sensitivity.get("level") or "high"),
        action=action,
        stored=bool(result.get("stored")),
        entered_rag=False,
        audit_event_id=str(audit_event_id or ""),
        user_summary=summary,
        next_actions=next_actions,
    )


def merge_memory_change_summaries(
    summaries: Iterable[Mapping[str, Any]],
    *,
    namespace: str = "",
    source: str = "peony",
    session_id: str = "",
) -> dict[str, Any]:
    """Merge per-file summaries without changing their individual explanations."""
    merged = MemoryChangeSummary(namespace=str(namespace or ""), source=str(source or "peony"), session_id=str(session_id or ""))
    for item in summaries:
        if not isinstance(item, Mapping):
            continue
        merged.new_memories.extend(item.get("new_memories", []) or [])
        merged.updated_memories.extend(item.get("updated_memories", []) or [])
        merged.blocked_items.extend(item.get("blocked_items", []) or [])
        merged.skipped_items.extend(item.get("skipped_items", []) or [])
        merged.effective_scopes.extend(str(value) for value in item.get("effective_scopes", []) or [])
        merged.suggested_next_actions.extend(str(value) for value in item.get("suggested_next_actions", []) or [])
        merged.audit_event_ids.extend(str(value) for value in item.get("audit_event_ids", []) or [])
    merged.effective_scopes = _unique(merged.effective_scopes)
    merged.suggested_next_actions = _unique(merged.suggested_next_actions)
    merged.audit_event_ids = _unique(merged.audit_event_ids)
    if merged.new_memories and "view" not in merged.suggested_next_actions:
        merged.suggested_next_actions.insert(0, "view")
    return merged.to_dict()


def build_preference_effect_proof(
    *,
    task: str,
    agent_name: str,
    session_id: str,
    stage: str,
    preference: Mapping[str, Any],
    actual_behavior: Mapping[str, Any] | None = None,
    source_event_ids: Iterable[str] = (),
    evidence_links: Iterable[str] = (),
) -> PreferenceEffectProof:
    actual = {str(key): value for key, value in (actual_behavior or {}).items()}
    memory_id = str(preference.get("memory_id") or "")
    normalized_preference = {
        "memory_id": memory_id,
        "topic": str(preference.get("topic") or preference.get("preference_topic") or ""),
        "value": str(preference.get("value") or preference.get("preference_value") or ""),
        "scope": str(preference.get("scope") or preference.get("preference_scope") or "global"),
        "source_event_ids": [str(item) for item in source_event_ids if str(item)],
    }
    proof = PreferenceEffectProof(
        task=str(task or ""),
        agent_name=str(agent_name or "openhands"),
        session_id=str(session_id or ""),
        stage=str(stage or "tool_selection"),
        preference=normalized_preference,
        injected=bool(memory_id),
        actual_behavior=actual,
        evidence_links=[str(item) for item in evidence_links if str(item)],
    )
    if not actual:
        return proof

    topic = normalized_preference["topic"]
    value = normalized_preference["value"]
    if topic == "code_search_tool" and value:
        command = str(actual.get("command") or "")
        if re.search(r"(?:^|\s|[|;&])%s(?:\s|$)" % re.escape(value), command):
            proof.compliance = "passed"
        else:
            proof.compliance = "failed"
            proof.failure_reason = "实际工具命令没有使用 %s。" % value
    elif topic == "response_order" and value == "command_then_explanation":
        observed = str(actual.get("response_style_observed") or actual.get("response") or "")
        if actual.get("command_first") is True or observed == "command_then_explanation":
            proof.compliance = "passed"
        else:
            proof.compliance = "failed"
            proof.failure_reason = "没有观察到先给命令或结论、再给解释的回答顺序。"
    elif topic == "high_risk_confirmation" and value == "required":
        decision = str(actual.get("decision") or actual.get("action") or "").lower()
        if actual.get("confirmation_required") is True or decision in {"blocked", "confirmation_required", "confirmed"}:
            proof.compliance = "passed"
        else:
            proof.compliance = "failed"
            proof.failure_reason = "高风险操作没有显示阻断或确认要求。"
    elif actual.get("compliance") in {"passed", "failed", "not_applicable"}:
        proof.compliance = str(actual["compliance"])
        proof.failure_reason = str(actual.get("failure_reason") or "")
    return proof


def build_forget_verification_report(
    result: Mapping[str, Any],
    *,
    request: str = "",
    dry_run: bool | None = None,
) -> ForgetVerificationReport:
    confirmed = bool(result.get("confirmed"))
    is_dry_run = bool(not confirmed) if dry_run is None else bool(dry_run)
    forgotten = [str(item) for item in result.get("forgotten_memory_ids", result.get("forgotten", [])) or []]
    if not forgotten and isinstance(result.get("summary"), Mapping):
        forgotten = [str(item) for item in result.get("summary", {}).get("memory_ids", []) or []]
    candidate_count = len(result.get("candidates", []) or [])
    if not candidate_count and isinstance(result.get("affected"), Mapping):
        candidate_count = len(result.get("affected", {}).get("memory_ids", []) or [])
    statuses = _verification_statuses(result, confirmed=confirmed, dry_run=is_dry_run)
    external = dict(_EXTERNAL_BOUNDARY_KEYS)
    residue = result.get("external_residue")
    if isinstance(residue, Mapping):
        if residue.get("requires_separate_cleanup"):
            external["openhands_raw_logs"] = "not_managed"
            external["model_provider_logs"] = "not_managed"
        for key in external:
            if key in residue and isinstance(residue[key], str):
                external[key] = str(residue[key])
    if is_dry_run:
        user_summary = "已完成遗忘预览，尚未改变受管数据；确认后会显示逐项验证结果。"
    elif all(item["status"] == "passed" for item in statuses if item["status"] != "skipped") and statuses:
        user_summary = "遗忘已完成，受管记录和可验证的检索路径均已复核；外部日志和备份不在本系统管理范围内。"
    else:
        user_summary = "遗忘操作已返回结果，请查看未通过或未执行的验证步骤。"
    return ForgetVerificationReport(
        request=str(request or result.get("request") or ""),
        dry_run=is_dry_run,
        confirmed=confirmed,
        candidate_count=candidate_count,
        forgotten_memory_ids=forgotten,
        verification_steps=statuses,
        external_residue=external,
        user_summary=user_summary,
    )


def notifications_from_audit(rows: Iterable[Mapping[str, Any]], *, limit: int = 30) -> list[dict[str, Any]]:
    """Project relevant audit events into a compact notification history."""
    output: list[dict[str, Any]] = []
    seen_status_updates: set[tuple[str, str]] = set()
    for row in rows:
        event_type = str(row.get("event_type") or "")
        mapped = _notification_mapping(event_type)
        if mapped is None:
            continue
        detail = _parse_detail(row.get("detail"))
        if (
            event_type == "interaction_ingest"
            and detail.get("event_type") == "policy_decision"
            and str(detail.get("policy_decision") or "").lower() in {"deny", "blocked", "block"}
        ):
            mapped = _notification_mapping("tool_policy_block")
        summary = _notification_summary(event_type, detail)
        if mapped[1] == "tool_block":
            summary = _notification_summary("tool_policy_block", detail)
        if event_type in {"retrieval_status", "sdk_fallback"}:
            dedupe_key = (
                event_type,
                json.dumps(detail, ensure_ascii=False, sort_keys=True, separators=(",", ":"), default=str),
            )
            if dedupe_key in seen_status_updates:
                continue
            seen_status_updates.add(dedupe_key)
        output.append(
            {
                "notification_id": "audit-%s" % str(row.get("audit_id") or row.get("event_hash") or ""),
                "audit_id": row.get("audit_id"),
                "level": mapped[0],
                "kind": mapped[1],
                "title": mapped[2],
                "summary": summary,
                "action": mapped[3],
                "read": False,
            }
        )
        if len(output) >= max(1, int(limit)):
            break
    return output


def is_urgent_notification(notification: Mapping[str, Any]) -> bool:
    """Return whether a desktop surface may interrupt the user.

    Routine memory writes, searches and SDK state changes stay in the notification
    center.  Only events that require attention, confirm an irreversible action,
    or prove an Agent safety decision are allowed to reach a tray popup.
    """
    level = str(notification.get("level") or "").lower()
    kind = str(notification.get("kind") or "").lower()
    if level == "critical":
        return True
    return kind in {"sensitive_block", "tool_block", "forget", "agent_activity", "audit_error", "service_error"}


def _memory_item(record: Any, result: Mapping[str, Any]) -> dict[str, Any]:
    row = record.to_dict() if hasattr(record, "to_dict") else dict(record)
    memory_type = str(row.get("type") or "memory")
    subtype = str(row.get("subtype") or "general")
    topic = str(row.get("preference_topic") or "")
    return {
        "memory_id": str(row.get("memory_id") or ""),
        "type": memory_type,
        "subtype": subtype,
        "tier": str(row.get("tier") or "long_term"),
        "scope": str(row.get("scope") or "user"),
        "preference_topic": topic,
        "preference_value": str(row.get("preference_value") or ""),
        "summary_for_user": memory_summary_for_user(row),
        "why_extracted": _why_extracted(memory_type, subtype, topic, result),
        "where_it_will_apply": _where_it_applies(row),
        "actions": ["view", "test_effect", "forget"],
    }


def memory_summary_for_user(row: Mapping[str, Any]) -> str:
    topic = str(row.get("preference_topic") or "")
    value = str(row.get("preference_value") or "")
    parsed_scope = ""
    if not topic or not value:
        raw_summary = " ".join(str(row.get("summary") or row.get("content") or "").split())
        legacy_preference = re.search(
            r"\b(?P<topic>[a-z][a-z0-9_]*)\s*=\s*(?P<value>[^()\s，。,；;]+)"
            r"(?:\s*\((?P<scope>[a-z_]+)\))?",
            raw_summary,
            flags=re.IGNORECASE,
        )
        known_topics = {
            "code_search_tool",
            "test_runner_command",
            "service_log_tool",
            "response_order",
            "response_language",
            "response_verbosity",
            "high_risk_confirmation",
        }
        if legacy_preference and legacy_preference.group("topic").lower() in known_topics:
            topic = legacy_preference.group("topic").lower()
            value = legacy_preference.group("value")
            parsed_scope = str(legacy_preference.group("scope") or "")
    if topic and value:
        topic_label = {
            "code_search_tool": "代码搜索工具",
            "test_runner_command": "测试运行命令",
            "service_log_tool": "服务日志工具",
            "response_order": "回答顺序",
            "response_language": "回答语言",
            "response_verbosity": "回答详细度",
            "high_risk_confirmation": "高风险操作确认",
        }.get(topic, "交互偏好")
        value_label = {
            "command_then_explanation": "先给命令或结论，再说明原因",
            "required": "操作前必须确认",
            "zh-CN": "中文",
            "concise": "简洁回答",
        }.get(value, value)
        scope = str(parsed_scope or row.get("preference_scope") or row.get("scope") or "global")
        scope_label = {
            "global": "全部会话",
            "agent": "当前 Agent",
            "project": "当前项目",
            "task_type": "同类任务",
            "session": "当前会话",
            "user": "当前用户",
        }.get(scope, "相关任务")
        return "用户偏好：%s为 %s，适用于%s。" % (topic_label, value_label, scope_label)
    summary = str(row.get("summary") or row.get("content") or "").strip()
    return " ".join(summary.split())[:240] or "已形成一条受控记忆。"


def _why_extracted(memory_type: str, subtype: str, topic: str, result: Mapping[str, Any]) -> str:
    extraction = result.get("extraction")
    if isinstance(extraction, Mapping) and extraction.get("reason"):
        return str(extraction["reason"])
    if topic:
        return "检测到稳定的 Agent 交互偏好，已按作用域保存为可复用偏好。"
    if memory_type == "procedure" or subtype == "procedural":
        return "内容包含状态、动作和结果，适合在类似任务中作为操作经验复用。"
    if memory_type == "sensitive_policy":
        return "内容被识别为安全策略，按受控规则保存。"
    return "内容通过质量和敏感性检查，并被抽取为可检索的记忆。"


def _where_it_applies(row: Mapping[str, Any]) -> list[str]:
    topic = str(row.get("preference_topic") or "")
    scope = str(row.get("preference_scope") or row.get("scope") or "global")
    if topic:
        return ["Agent 规划", "工具选择", "回答生成"] if scope in {"global", "agent", "project", "task_type", "session"} else ["相关任务"]
    return ["相关检索任务"]


def _sensitivity_dict(value: Any) -> dict[str, Any]:
    if value is None:
        return {}
    if hasattr(value, "__dict__"):
        return dict(value.__dict__)
    return dict(value) if isinstance(value, Mapping) else {}


def _verification_statuses(result: Mapping[str, Any], *, confirmed: bool, dry_run: bool) -> list[dict[str, str]]:
    raw = result.get("verification")
    verification_map = raw if isinstance(raw, Mapping) else {}
    by_name: dict[str, dict[str, Any]] = {}
    if isinstance(raw, list):
        for item in raw:
            if isinstance(item, Mapping):
                memory_id = str(item.get("memory_id") or "")
                if memory_id:
                    by_name["main_record"] = item
                    if item.get("irreversible_ok"):
                        by_name["plaintext_probe"] = item
    if isinstance(raw, Mapping):
        by_name.update({str(key): value for key, value in raw.items() if isinstance(value, Mapping)})
    result_steps: list[dict[str, str]] = []
    for name in _VERIFICATION_NAMES:
        if dry_run:
            result_steps.append({"name": name, "status": "skipped", "detail": "确认遗忘后才执行此项验证。"})
            continue
        status = "skipped"
        detail = "当前结果未提供该项独立证明。"
        if name == "main_record" and result.get("forgotten_memory_ids", result.get("forgotten")) is not None:
            status = "passed" if result.get("forgotten_memory_ids", result.get("forgotten", [])) else "skipped"
            detail = "受管主记录已写入遗忘结果。" if status == "passed" else "没有需要遗忘的主记录。"
        elif name == "agent_event_payload" and result.get("scrubbed_event_count") is not None:
            status = "passed" if int(result.get("scrubbed_event_count") or 0) >= 0 else "failed"
            detail = "Agent 事件载荷已按结果处理。"
        elif name in {"sqlite_fts", "graph_relations", "search_cache"} and result.get("confirmed"):
            # 真实回查每个检索面，而不是凭 confirmed 标志直接判定通过。
            status, detail = _retrieval_surface_status(name, result)
        elif name == "mmap_segment_compact" and result.get("compact") is not None:
            status = "passed" if result.get("compact") else "skipped"
            detail = "段文件整理已执行。" if status == "passed" else "未要求整理段文件。"
        elif name == "plaintext_probe" and by_name.get(name, {}).get("irreversible_ok"):
            status = "passed"
            detail = "未发现受管段文件明文残留。"
        elif name == "agent_context_absence" and verification_map.get("all_stages_absent") is not None:
            status = "passed" if verification_map["all_stages_absent"] else "failed"
            detail = "遗忘内容未再进入 Agent 上下文。" if status == "passed" else "仍有阶段返回遗忘内容。"
        elif name == "audit_chain" and result.get("audit_chain_ok") is not None:
            status = "passed" if result.get("audit_chain_ok") else "failed"
            detail = "操作记录完整性校验通过。" if status == "passed" else "操作记录完整性需要复核。"
        result_steps.append({"name": name, "status": status, "detail": detail})
    return result_steps


_SURFACE_FIELDS = {
    "sqlite_fts": ("sqlite_fts_recallable", "文本检索索引"),
    "graph_relations": ("graph_relations_recallable", "关联关系索引"),
    "search_cache": ("search_cache_recallable", "检索缓存"),
}


def _retrieval_surface_status(name: str, result: Mapping[str, Any]) -> tuple[str, str]:
    """Report a retrieval surface from the post-delete probe, not from a flag."""

    field, label = _SURFACE_FIELDS[name]
    probes: list[Mapping[str, Any]] = []
    raw = result.get("verification")
    if isinstance(raw, list):
        for item in raw:
            surfaces = item.get("retrieval_surfaces") if isinstance(item, Mapping) else None
            if isinstance(surfaces, Mapping):
                probes.append(surfaces)
    elif isinstance(raw, Mapping):
        for item in raw.get("retrieval_surfaces") or []:
            if isinstance(item, Mapping):
                probes.append(item)
    if not probes:
        return "skipped", "%s未提供复查结果，请在管理员视图重新校验。" % label
    recallable = [item for item in probes if item.get(field)]
    if recallable:
        return "failed", "%s仍可召回已遗忘内容，需要管理员处理。" % label
    if name == "search_cache":
        dropped = result.get("search_cache_entries_dropped")
        if dropped is None and isinstance(raw, Mapping):
            dropped = raw.get("search_cache_entries_dropped")
        suffix = "（已清理 %s 条缓存）" % dropped if dropped is not None else ""
        return "passed", "%s复查未命中已遗忘内容%s。" % (label, suffix)
    return "passed", "%s复查未命中已遗忘内容。" % label


def _notification_mapping(event_type: str) -> tuple[str, str, str, str] | None:
    mapping = {
        "ingest": ("info", "memory_added", "新增记忆", "查看记忆"),
        "interaction_ingest": ("info", "agent_activity", "Agent 记忆更新", "查看 Agent 工作台"),
        "interaction_preference_extract": ("info", "agent_activity", "Agent 偏好已更新", "查看 Agent 工作台"),
        "agent_task_completed": ("info", "agent_activity", "Agent 任务已完成", "查看 Agent 工作台"),
        "sensitive_block": ("critical", "sensitive_block", "敏感内容已保护", "查看内容保护"),
        "tool_policy_block": ("critical", "tool_block", "高风险操作已拦截", "查看操作记录"),
        "policy_block": ("critical", "tool_block", "高风险操作已拦截", "查看操作记录"),
        "forget": ("info", "forget", "遗忘已完成", "查看遗忘验证"),
        "forget_memory_ids": ("info", "forget", "遗忘已完成", "查看遗忘验证"),
        "agent_forget": ("info", "forget", "Agent 记忆已遗忘", "查看遗忘验证"),
        "forget_failed": ("critical", "forget", "遗忘操作需要处理", "查看遗忘验证"),
        "agent_forget_failed": ("critical", "forget", "Agent 记忆遗忘需要处理", "查看遗忘验证"),
        "verify_audit": ("info", "audit", "操作记录已校验", "查看系统维护"),
        "audit_chain_error": ("critical", "audit_error", "操作记录完整性异常", "查看系统维护"),
        "audit_integrity_error": ("critical", "audit_error", "操作记录完整性异常", "查看系统维护"),
        "api_internal_error": ("critical", "service_error", "服务处理异常", "查看系统维护"),
        "daemon_stopped": ("critical", "service_error", "记忆服务已停止", "查看系统维护"),
        "service_stopped": ("critical", "service_error", "记忆服务已停止", "查看系统维护"),
        "peony_import_partial": ("warning", "peony_import", "文件导入部分完成", "查看 Agent 工作台"),
        "peony_import": ("info", "peony_import", "文件导入已完成", "查看 Agent 工作台"),
        "retrieval_status": ("warning", "sdk_status", "本机检索状态已更新", "查看本机智能检索"),
        "sdk_fallback": ("warning", "sdk_status", "本机智能检索已降级", "查看本机智能检索"),
        "resource_warning": ("warning", "resource", "系统资源接近限制", "查看系统维护"),
    }
    return mapping.get(event_type)


def _notification_summary(event_type: str, detail: Mapping[str, Any]) -> str:
    if event_type == "ingest":
        kind = {
            "preference": "偏好记忆",
            "knowledge": "知识记忆",
            "procedure": "操作经验",
            "event": "事件记忆",
            "memory": "记忆",
        }.get(str(detail.get("type") or "memory").lower(), "记忆")
        return "系统已保存一条%s，稍后可在 Agent 工作台查看它的适用范围。" % kind
    if event_type == "sensitive_block":
        labels = ", ".join(str(item) for item in detail.get("labels") or [])
        return "高风险内容未进入长期记忆%s。" % (("（" + labels + "）") if labels else "")
    if event_type in {"tool_policy_block", "policy_block"}:
        return "高风险工具调用已被拦截，Agent 需要先获得明确确认。"
    if event_type in {"forget", "forget_memory_ids", "agent_forget"}:
        return "本次遗忘已返回受管记录、索引和上下文复核结果。"
    if event_type in {"forget_failed", "agent_forget_failed"}:
        return "遗忘验证没有全部通过，请检查未通过步骤和受管范围。"
    if event_type in {"daemon_stopped", "service_stopped"}:
        return "记忆服务已停止，Agent 暂时不能读写受管记忆。"
    if event_type in {"peony_import", "peony_import_partial"}:
        return "文件导入结果已记录，可在 Agent 工作台查看新增记忆和适用范围。"
    if event_type in {"sdk_fallback", "retrieval_status"}:
        return "本机智能检索状态已更新，普通检索仍可继续使用。"
    if event_type == "api_internal_error":
        return "一次操作没有完成，请进入系统维护查看处理建议。"
    return "系统状态已更新，可从对应页面查看详情。"


def _parse_detail(value: Any) -> dict[str, Any]:
    if isinstance(value, Mapping):
        return dict(value)
    try:
        parsed = json.loads(str(value or "{}"))
    except (TypeError, ValueError):
        return {}
    return dict(parsed) if isinstance(parsed, Mapping) else {}


def _unique(values: Iterable[str]) -> list[str]:
    output: list[str] = []
    for value in values:
        if value and value not in output:
            output.append(value)
    return output
