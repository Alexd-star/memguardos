from __future__ import annotations

from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional
import hashlib
import json
import uuid


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def new_id(prefix: str) -> str:
    return "%s_%s" % (prefix, uuid.uuid4().hex[:16])


def stable_hash(payload: str) -> str:
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


@dataclass
class MemorySource:
    kind: str
    uri: str = ""
    timestamp: str = field(default_factory=utc_now)


@dataclass
class Sensitivity:
    level: str = "none"
    labels: List[str] = field(default_factory=list)
    redacted: bool = False


@dataclass
class VersionInfo:
    version_id: str = field(default_factory=lambda: new_id("ver"))
    parent_version_id: Optional[str] = None
    op: str = "create"


@dataclass
class AuditInfo:
    created_by: str = "memguard"
    created_at: str = field(default_factory=utc_now)
    hash: str = ""


@dataclass
class TemporalInfo:
    valid_from: str = field(default_factory=utc_now)
    valid_to: Optional[str] = None
    superseded_by: Optional[str] = None


@dataclass
class MemoryRecord:
    memory_id: str
    type: str
    subtype: str
    scope: str
    tier: str
    content: str
    summary: str
    source: MemorySource
    evidence: List[str] = field(default_factory=list)
    confidence: float = 1.0
    importance: float = 0.5
    language: str = "mixed"
    entities: List[str] = field(default_factory=list)
    relations: List[Dict[str, Any]] = field(default_factory=list)
    sensitivity: Sensitivity = field(default_factory=Sensitivity)
    version: VersionInfo = field(default_factory=VersionInfo)
    audit: AuditInfo = field(default_factory=AuditInfo)
    temporal: TemporalInfo = field(default_factory=TemporalInfo)
    preference_topic: str = ""
    preference_value: str = ""
    preference_scope: str = "global"
    preference_explicit: bool = True
    agent_name: str = ""
    preference_session_id: str = ""
    project_root: str = ""
    task_type: str = ""
    deleted: bool = False

    def finalize_hash(self) -> "MemoryRecord":
        payload = self.content + self.summary + self.memory_id + self.version.version_id + self.temporal.valid_from
        self.audit.hash = stable_hash(payload)
        return self

    def invalidate(self, valid_to: Optional[str] = None, superseded_by: Optional[str] = None) -> "MemoryRecord":
        self.temporal.valid_to = valid_to or utc_now()
        self.temporal.superseded_by = superseded_by
        return self

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)

    def to_json(self) -> str:
        return json.dumps(self.to_dict(), ensure_ascii=False, sort_keys=True)

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "MemoryRecord":
        source = MemorySource(**data.get("source", {}))
        sensitivity = Sensitivity(**data.get("sensitivity", {}))
        version = VersionInfo(**data.get("version", {}))
        audit = AuditInfo(**data.get("audit", {}))
        temporal = TemporalInfo(**data.get("temporal", {}))
        return cls(
            memory_id=data["memory_id"],
            type=data["type"],
            subtype=data.get("subtype", "general"),
            scope=data["scope"],
            tier=data.get("tier", "long_term"),
            content=data.get("content", ""),
            summary=data.get("summary", ""),
            source=source,
            evidence=list(data.get("evidence", [])),
            confidence=float(data.get("confidence", 1.0)),
            importance=float(data.get("importance", 0.5)),
            language=data.get("language", "mixed"),
            entities=list(data.get("entities", [])),
            relations=list(data.get("relations", [])),
            sensitivity=sensitivity,
            version=version,
            audit=audit,
            temporal=temporal,
            preference_topic=data.get("preference_topic", ""),
            preference_value=data.get("preference_value", ""),
            preference_scope=data.get("preference_scope", "global"),
            preference_explicit=bool(data.get("preference_explicit", True)),
            agent_name=data.get("agent_name", ""),
            preference_session_id=data.get("preference_session_id", ""),
            project_root=data.get("project_root", ""),
            task_type=data.get("task_type", ""),
            deleted=bool(data.get("deleted", False)),
        )

    @classmethod
    def from_json(cls, line: str) -> "MemoryRecord":
        return cls.from_dict(json.loads(line))


def make_memory(
    content: str,
    memory_type: str,
    source: MemorySource,
    summary: Optional[str] = None,
    subtype: str = "general",
    scope: str = "user",
    tier: str = "long_term",
    sensitivity: Optional[Sensitivity] = None,
    preference_topic: str = "",
    preference_value: str = "",
    preference_scope: str = "global",
    preference_explicit: bool = True,
    agent_name: str = "",
    preference_session_id: str = "",
    project_root: str = "",
    task_type: str = "",
) -> MemoryRecord:
    clean_summary = summary or content[:160]
    record = MemoryRecord(
        memory_id=new_id("mem"),
        type=memory_type,
        subtype=subtype,
        scope=scope,
        tier=tier,
        content=content,
        summary=clean_summary,
        source=source,
        evidence=[content[:240]],
        sensitivity=sensitivity or Sensitivity(),
        preference_topic=preference_topic,
        preference_value=preference_value,
        preference_scope=preference_scope,
        preference_explicit=preference_explicit,
        agent_name=agent_name,
        preference_session_id=preference_session_id,
        project_root=project_root,
        task_type=task_type,
    )
    return record.finalize_hash()
