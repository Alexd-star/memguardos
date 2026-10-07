"""Read-only lifecycle projections for governed Agent memories.

The storage model deliberately keeps the authoritative record, Agent events,
and audit chain separate.  This module gives clients one stable vocabulary for
explaining that model without introducing a second source of truth:

* ``atom`` is one independently governable memory record;
* ``episode`` is a derived task/session view over supporting Agent events;
* ``support`` is a bounded provenance link or source span;
* ``operation`` is an audit-backed lifecycle operation.

These projections never return raw Agent payloads.  Callers can ask for
bounded event summaries, while the actual event body remains behind the
existing evidence and authorization paths.
"""

from __future__ import annotations

from collections import defaultdict
from dataclasses import asdict, dataclass, field
import json
from typing import Any, Iterable, Mapping

from memguard.models import stable_hash


_OPERATION_LABELS = {
    "ingest": "写入",
    "ingest_duplicate": "复用已有记忆",
    "update": "版本更新",
    "rollback": "版本回滚",
    "forget": "精准遗忘",
    "agent_forget": "Agent 交互遗忘",
    "memory_version_committed": "版本提交",
    "fact_conflict_requires_review": "事实冲突待核对",
    "fact_conflict_resolved": "事实核对完成",
    "fact_conflict_write_cancelled": "事实更新取消",
    "sensitive_block": "敏感内容阻断",
    "agent_context_build": "Agent 上下文使用",
    "preference_effect_proof": "偏好生效核验",
}


def _text(value: Any, limit: int = 240) -> str:
    return " ".join(str(value or "").split())[:limit]


def _int_or_default(value: Any, default: int = -1) -> int:
    try:
        return int(value) if value is not None and str(value) != "" else default
    except (TypeError, ValueError):
        return default


def _memory_kind(row: Mapping[str, Any]) -> str:
    memory_type = str(row.get("type") or "knowledge")
    subtype = str(row.get("subtype") or "general")
    if memory_type in {"preference", "sensitive_policy"}:
        return "preference" if memory_type == "preference" else "policy"
    if subtype in {"tool_trace", "procedural_trace", "tool_failure_case", "workflow"}:
        return "procedure"
    if subtype in {"case", "behavior_trace", "os_event"}:
        return "episode_fact"
    return "fact"


def _lifecycle_state(row: Mapping[str, Any]) -> str:
    if bool(row.get("deleted")):
        return "forgotten"
    governance = str(row.get("governance_state") or "active")
    if governance == "quarantined":
        return "quarantined"
    if governance != "active":
        return governance
    if row.get("valid_to") or row.get("superseded_by"):
        return "superseded"
    return "active"


@dataclass(frozen=True)
class AtomView:
    object_type: str
    object_id: str
    memory_id: str
    kind: str
    subtype: str
    lifecycle_state: str
    tier: str
    scope: str
    preference_scope: str
    project_root: str
    task_type: str
    summary: str
    source_kind: str
    source_uri: str
    version_id: str
    parent_version_id: str
    valid_from: str
    valid_to: str
    superseded_by: str
    retention_session_id: str
    governance_state: str
    confidence: float | None
    importance: float | None
    language: str
    version_op: str
    version_count: int
    sensitivity_level: str

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class SupportView:
    object_type: str
    support_id: str
    memory_id: str
    event_id: str
    relation: str
    source_field: str
    source_start: int
    source_end: int
    excerpt: str
    event_type: str
    agent_name: str
    session_id: str
    timestamp: str
    redacted: bool = False

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class EpisodeView:
    object_type: str
    episode_id: str
    memory_id: str
    agent_name: str
    session_id: str
    project_root: str
    task_type: str
    event_count: int
    event_types: list[str] = field(default_factory=list)
    first_timestamp: str = ""
    last_timestamp: str = ""
    support_ids: list[str] = field(default_factory=list)
    event_summaries: list[dict[str, Any]] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class OperationView:
    object_type: str
    operation_id: str
    audit_id: int | str
    memory_id: str
    operation: str
    label: str
    status: str
    created_at: str
    event_hash: str

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def atom_view(row: Mapping[str, Any]) -> dict[str, Any]:
    """Convert one metadata row into the public atom contract."""

    return AtomView(
        object_type="atom",
        object_id=str(row.get("memory_id") or ""),
        memory_id=str(row.get("memory_id") or ""),
        kind=_memory_kind(row),
        subtype=str(row.get("subtype") or "general"),
        lifecycle_state=_lifecycle_state(row),
        tier=str(row.get("tier") or "long_term"),
        scope=str(row.get("scope") or ""),
        preference_scope=str(row.get("preference_scope") or "global"),
        project_root=str(row.get("project_root") or ""),
        task_type=str(row.get("task_type") or ""),
        summary=_text(row.get("summary") or row.get("content")),
        source_kind=str(row.get("source_kind") or ""),
        source_uri=str(row.get("source_uri") or ""),
        version_id=str(row.get("version_id") or ""),
        parent_version_id=str(row.get("parent_version_id") or ""),
        valid_from=str(row.get("valid_from") or row.get("updated_at") or ""),
        valid_to=str(row.get("valid_to") or ""),
        superseded_by=str(row.get("superseded_by") or ""),
        retention_session_id=str(row.get("retention_session_id") or ""),
        governance_state=str(row.get("governance_state") or "active"),
        confidence=(
            round(float(row["confidence"]), 4)
            if row.get("confidence") is not None
            else None
        ),
        importance=(
            round(float(row["importance"]), 4)
            if row.get("importance") is not None
            else None
        ),
        language=str(row.get("language") or ""),
        version_op=str(row.get("version_op") or ""),
        version_count=max(0, _int_or_default(row.get("version_count"), 0)),
        sensitivity_level=str(row.get("sensitivity_level") or "none"),
    ).to_dict()


def support_views(memory_id: str, events: Iterable[Mapping[str, Any]], *, redacted: bool = False) -> list[dict[str, Any]]:
    result: list[dict[str, Any]] = []
    for event in events:
        event_id = str(event.get("event_id") or "")
        if not event_id:
            continue
        excerpt = "" if redacted or bool(event.get("forgotten")) else _text(event.get("source_excerpt"), 500)
        support = SupportView(
            object_type="support",
            support_id="support:" + stable_hash("%s|%s|%s" % (memory_id, event_id, event.get("relation") or ""))[:20],
            memory_id=memory_id,
            event_id=event_id,
            relation=str(event.get("relation") or "supports"),
            source_field=str(event.get("source_field") or ""),
            source_start=_int_or_default(event.get("source_start")),
            source_end=_int_or_default(event.get("source_end")),
            excerpt=excerpt,
            event_type=str(event.get("event_type") or ""),
            agent_name=str(event.get("agent_name") or ""),
            session_id=str(event.get("session_id") or ""),
            timestamp=str(event.get("timestamp") or event.get("linked_at") or ""),
            redacted=bool(redacted or event.get("forgotten")),
        )
        result.append(support.to_dict())
    return result


def episode_views(
    memory_id: str,
    events: Iterable[Mapping[str, Any]],
    supports: Iterable[Mapping[str, Any]],
    *,
    include_event_summaries: bool = False,
    redacted: bool = False,
) -> list[dict[str, Any]]:
    grouped: dict[tuple[str, str, str, str], list[Mapping[str, Any]]] = defaultdict(list)
    for event in events:
        key = (
            str(event.get("agent_name") or "unknown"),
            str(event.get("session_id") or "unknown"),
            str(event.get("project_root") or ""),
            str(event.get("task_type") or ""),
        )
        grouped[key].append(event)
    support_by_event = defaultdict(list)
    for support in supports:
        support_by_event[str(support.get("event_id") or "")].append(str(support.get("support_id") or ""))
    result: list[dict[str, Any]] = []
    for key, group in sorted(grouped.items(), key=lambda item: (item[0][1], item[0][0])):
        timestamps = sorted(str(item.get("timestamp") or item.get("linked_at") or "") for item in group)
        event_types = list(dict.fromkeys(str(item.get("event_type") or "") for item in group if item.get("event_type")))
        event_summaries: list[dict[str, Any]] = []
        if include_event_summaries:
            for event in sorted(group, key=lambda item: (str(item.get("timestamp") or ""), str(item.get("event_id") or ""))):
                event_summaries.append(
                    {
                        "event_id": str(event.get("event_id") or ""),
                        "event_type": str(event.get("event_type") or ""),
                        "role": str(event.get("role") or ""),
                        "tool_name": str(event.get("tool_name") or ""),
                        "exit_code": event.get("exit_code"),
                        "summary": "" if redacted or event.get("forgotten") else _text(
                            event.get("content") or event.get("tool_result") or event.get("state_after")
                        ),
                    }
                )
        event_ids = [str(item.get("event_id") or "") for item in group if item.get("event_id")]
        support_ids = [support_id for event_id in event_ids for support_id in support_by_event.get(event_id, [])]
        identity = "|".join([memory_id, *key])
        result.append(
            EpisodeView(
                object_type="episode",
                episode_id="episode:" + stable_hash(identity)[:20],
                memory_id=memory_id,
                agent_name=key[0],
                session_id=key[1],
                project_root=key[2],
                task_type=key[3],
                event_count=len(group),
                event_types=event_types,
                first_timestamp=timestamps[0] if timestamps else "",
                last_timestamp=timestamps[-1] if timestamps else "",
                support_ids=support_ids,
                event_summaries=event_summaries,
            ).to_dict()
        )
    return result


def operation_views(memory_id: str, audits: Iterable[Mapping[str, Any]]) -> list[dict[str, Any]]:
    result: list[dict[str, Any]] = []
    for audit in audits:
        event_type = str(audit.get("event_type") or "")
        status = "completed"
        if event_type in {"fact_conflict_requires_review", "sensitive_block"}:
            status = "review_required" if event_type.startswith("fact_") else "blocked"
        elif event_type == "fact_conflict_write_cancelled":
            status = "cancelled"
        audit_id = audit.get("audit_id")
        result.append(
            OperationView(
                object_type="operation",
                operation_id="audit:%s" % str(audit_id or audit.get("event_hash") or ""),
                audit_id=audit_id or "",
                memory_id=memory_id,
                operation=event_type,
                label=_OPERATION_LABELS.get(event_type, event_type or "系统操作"),
                status=status,
                created_at=str(audit.get("created_at") or ""),
                event_hash=str(audit.get("event_hash") or ""),
            ).to_dict()
        )
    return result


def lifecycle_contract() -> dict[str, Any]:
    return {
        "schema": "memguardos-lifecycle-v1",
        "authority": "SQLite metadata transaction plus append-only segment",
        "objects": {
            "atom": "可独立检索、更新和遗忘的原子记忆",
            "episode": "按 Agent、会话、项目和任务类型聚合的交互情节视图",
            "support": "支撑原子记忆的来源事件或精确文本跨度",
            "operation": "由审计哈希链记录的写入、更新、使用、冲突和遗忘操作",
        },
        "retrieval_rule": "只使用当前授权范围内、未删除且有效的 atom；episode/support 负责解释来源，不绕过授权读取正文。",
        "forgetting_rule": "遗忘 atom 时同步清理其受管派生索引、支持链接和可验证的运行态上下文；外部 Agent 日志与备份仍由对应系统管理。",
    }


def parse_audit_detail(audit: Mapping[str, Any]) -> dict[str, Any]:
    """Return only safe audit keys for diagnostics and lifecycle summaries."""

    raw = audit.get("detail")
    try:
        detail = json.loads(raw) if isinstance(raw, str) else raw
    except (TypeError, ValueError, json.JSONDecodeError):
        detail = {}
    if not isinstance(detail, dict):
        return {}
    return {"keys": sorted(str(key) for key in detail)}
