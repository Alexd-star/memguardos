from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Dict, Tuple


@dataclass(frozen=True)
class ErasurePlan:
    """Immutable execution boundary for a compiled forgetting request.

    Natural-language parsing is deliberately kept outside the destructive path.
    Preview and confirmation both consume this object, so time, type and scope
    constraints cannot disappear during a text round-trip.
    """

    source_text: str
    memory_types: Tuple[str, ...]
    topic: str
    target_phrase: str
    topic_aliases: Tuple[str, ...]
    normalized_preference_topic: str
    preference_topic: str
    scope_level: str
    agent_name: str
    session_id: str
    project_root: str
    start_time: str
    end_time: str
    time_source: str
    sensitive_labels: Tuple[str, ...]
    retention_constraints: Tuple[Dict[str, Any], ...]
    confidence: float
    unresolved_slots: Tuple[str, ...]

    @classmethod
    def from_intent(cls, intent: Dict[str, Any]) -> "ErasurePlan":
        scope = intent.get("scope") if isinstance(intent.get("scope"), dict) else {}
        time_range = (
            intent.get("time_range")
            if isinstance(intent.get("time_range"), dict)
            else {}
        )
        constraints = tuple(
            dict(item)
            for item in (intent.get("retention_constraints") or [])
            if isinstance(item, dict)
        )
        return cls(
            source_text=str(intent.get("source_text") or "").strip(),
            memory_types=tuple(str(item) for item in (intent.get("memory_types") or ["all"])),
            topic=str(intent.get("topic") or "").strip(),
            target_phrase=str(intent.get("target_phrase") or "").strip(),
            topic_aliases=tuple(
                str(item).strip()
                for item in (intent.get("topic_aliases") or [])
                if str(item).strip()
            ),
            normalized_preference_topic=str(
                intent.get("normalized_preference_topic") or ""
            ).strip(),
            preference_topic=str(intent.get("preference_topic") or "").strip(),
            scope_level=str(scope.get("level") or "global").strip().lower(),
            agent_name=str(scope.get("agent_name") or intent.get("agent_name") or "").strip(),
            session_id=str(scope.get("session_id") or intent.get("session_id") or "").strip(),
            project_root=str(scope.get("project_root") or "").strip(),
            start_time=str(time_range.get("start_time") or "").strip(),
            end_time=str(time_range.get("end_time") or "").strip(),
            time_source=str(time_range.get("source_text") or "").strip(),
            sensitive_labels=tuple(
                str(item).strip()
                for item in (intent.get("sensitive_labels") or [])
                if str(item).strip()
            ),
            retention_constraints=constraints,
            confidence=float(intent.get("confidence") or 0.0),
            unresolved_slots=tuple(str(item) for item in (intent.get("unresolved_slots") or [])),
        )

    @property
    def canonical_preference_topic(self) -> str:
        return self.normalized_preference_topic or self.preference_topic

    @property
    def has_time_window(self) -> bool:
        return bool(self.start_time or self.end_time)

    @property
    def unresolved_scope(self) -> bool:
        return bool(
            (self.scope_level == "agent" and not self.agent_name)
            or (self.scope_level == "session" and not self.session_id)
            or (self.scope_level == "project" and not self.project_root)
        )

    def as_intent(self) -> Dict[str, Any]:
        scope: Dict[str, str] = {"level": self.scope_level}
        if self.agent_name:
            scope["agent_name"] = self.agent_name
        if self.session_id:
            scope["session_id"] = self.session_id
        if self.project_root:
            scope["project_root"] = self.project_root
        return {
            "source_text": self.source_text,
            "memory_types": list(self.memory_types),
            "topic": self.topic,
            "target_phrase": self.target_phrase,
            "topic_aliases": list(self.topic_aliases),
            "normalized_preference_topic": self.normalized_preference_topic,
            "preference_topic": self.preference_topic,
            "scope": scope,
            "agent_name": self.agent_name,
            "session_id": self.session_id,
            "time_range": {
                "start_time": self.start_time,
                "end_time": self.end_time,
                "source_text": self.time_source,
            },
            "sensitive_labels": list(self.sensitive_labels),
            "retention_constraints": [dict(item) for item in self.retention_constraints],
            "confidence": self.confidence,
            "unresolved_slots": list(self.unresolved_slots),
        }

    def as_dict(self) -> Dict[str, Any]:
        result = self.as_intent()
        result.update(
            {
                "schema": "memguardos-erasure-plan-v1",
                "requires_confirm": True,
                "unresolved_scope": self.unresolved_scope,
            }
        )
        return result
