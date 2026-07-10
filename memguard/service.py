from pathlib import Path
from collections import OrderedDict
from concurrent.futures import ThreadPoolExecutor, as_completed
from time import perf_counter
from typing import Dict, List, Optional, Tuple, Union
import json

from memguard.config import (
    DEFAULT_NAMESPACE,
    custom_patterns_path,
    default_db_path,
    default_store_path,
    get_home,
    list_namespaces,
    normalize_namespace,
)
from memguard.extraction.entity import extract_entities, extract_relations
from memguard.feedback import (
    build_forget_verification_report,
    build_memory_change_summary,
    build_preference_effect_proof,
    build_sensitive_block_notice,
    merge_memory_change_summaries,
    notifications_from_audit,
)
from memguard.extraction.semantic import HybridMemoryExtractor
from memguard.forgetting.natural_intent import ErasureIntentCompiler
from memguard.forgetting.planner import ForgetPlanner
from memguard.governance.trust import assess_memory_source
from memguard.ingestion.behavior import BehaviorEvent, load_shell_history
from memguard.ingestion.config import config_entry_to_memory_text, load_config_entries, validate_config_entry
from memguard.ingestion.quality import assess_text_quality
from memguard.ingestion.tool_preference import ToolPreferenceMiner
from memguard.ingestion.tool_result import ToolResult
from memguard.integrations.events import AgentInteractionEvent
from memguard.integrations.context_injector import (
    stage_allows_memory,
    stage_allows_preference,
    validate_agent_context_stage,
)
from memguard.integrations.habit_aggregator import PreferenceAggregate
from memguard.integrations.interaction_extractor import PreferenceObservation, extract_interaction_preferences
from memguard.integrations.normalizer import normalize_event, normalize_events
from memguard.models import AuditInfo, MemoryRecord, MemorySource, VersionInfo, make_memory, new_id, stable_hash, utc_now
from memguard.os_guard.events import parse_os_event
from memguard.os_guard.cgroup import cgroup_status
from memguard.os_guard.journald import JournaldLogger
from memguard.retrieval.conflict import ConflictResolver
from memguard.retrieval.hybrid import HybridRetriever
from memguard.sensitivity.custom_patterns import PatternValidationError, load_patterns, save_pattern, save_pattern_from_instruction, suggest_pattern_from_instruction
from memguard.sensitivity.detector import SensitiveDetector
from memguard.store.mmap_store import AppendOnlyMemoryStore
from memguard.store.sqlite_meta import SQLiteMetaStore


class MemGuardService:
    def __init__(self, store_path: Optional[Path] = None, db_path: Optional[Path] = None, namespace: Optional[str] = None):
        self.namespace = normalize_namespace(namespace)
        self.store = AppendOnlyMemoryStore(store_path or default_store_path(self.namespace))
        self.meta = SQLiteMetaStore(db_path or default_db_path(self.namespace))
        if store_path is not None or db_path is not None:
            self.pattern_path = (Path(db_path) if db_path is not None else Path(store_path)).parent / "sensitive_patterns.json"
        else:
            self.pattern_path = custom_patterns_path(self.namespace)
        self.detector = SensitiveDetector(load_patterns(self.pattern_path))
        self.extractor = HybridMemoryExtractor()
        self.retriever = HybridRetriever()
        self.conflicts = ConflictResolver()
        self.forget_planner = ForgetPlanner()
        self.erasure_intent_compiler = ErasureIntentCompiler()
        self.tool_preference_miner = ToolPreferenceMiner()
        self.journald = JournaldLogger()
        self._search_cache: OrderedDict[Tuple[str, int, str], List[Dict[str, object]]] = OrderedDict()
        self._search_cache_limit = 128

    def close(self) -> None:
        self.meta.close()

    def ingest_text(self, text: str, source_kind: str = "manual", source_uri: str = "", tier: Optional[str] = None) -> Dict[str, object]:
        clean_text, quality = assess_text_quality(text, source_kind=source_kind)
        if not quality.accepted:
            detail = {
                "source_kind": source_kind,
                "source_uri": source_uri,
                "quality": quality.to_dict(),
                "reason": "input quality check rejected the memory",
            }
            audit = self._audit("quality_reject", None, detail)
            result = {"stored": False, "reason": "quality_reject", "quality": quality.to_dict()}
            result["audit_event_id"] = _audit_event_id(audit)
            result["feedback"] = build_memory_change_summary(
                result,
                namespace=self.namespace,
                source=source_kind,
                session_id=source_uri,
                audit_event_ids=[result["audit_event_id"]],
            ).to_dict()
            return result

        sensitivity = self.detector.classify(clean_text, source_uri)
        if sensitivity.level == "high":
            detail = {
                "source_kind": source_kind,
                "source_uri": source_uri,
                "labels": sensitivity.labels,
                "quality": quality.to_dict(),
                "reason": "high risk sensitive content is not stored in long-term memory",
            }
            audit = self._audit("sensitive_block", None, detail)
            result = {"stored": False, "reason": "sensitive_block", "sensitivity": sensitivity, "quality": quality.to_dict()}
            result["audit_event_id"] = _audit_event_id(audit)
            result["sensitive_notice"] = build_sensitive_block_notice(
                result,
                source=source_kind,
                audit_event_id=result["audit_event_id"],
            ).to_dict()
            result["feedback"] = build_memory_change_summary(
                result,
                namespace=self.namespace,
                source=source_kind,
                session_id=source_uri,
                audit_event_ids=[result["audit_event_id"]],
            ).to_dict()
            return result

        stored_text = clean_text
        if sensitivity.level in {"medium", "low"} and sensitivity.labels:
            stored_text = self.detector.redact(clean_text, source_uri)
            sensitivity.redacted = True

        extraction = self.extractor.extract(stored_text, source_kind)
        governance = assess_memory_source(stored_text, source_kind, source_uri)
        memory_type = extraction.memory_type
        subtype = extraction.subtype
        record = make_memory(
            content=stored_text,
            memory_type=memory_type,
            source=MemorySource(kind=source_kind, uri=source_uri),
            summary=extraction.summary,
            subtype=subtype,
            scope=self.namespace,
            tier=tier or self._default_tier(memory_type, source_kind),
            sensitivity=sensitivity,
            trust_level=governance.trust_level,
            trust_score=governance.trust_score,
            governance_state=governance.governance_state,
            governance_reason=governance.reason,
        )
        record.confidence = min(record.confidence, quality.score, extraction.confidence)
        self._enrich_graph(record)
        self.store.append(record)
        self.meta.upsert_memory(record)
        audit = self._audit(
            "ingest",
            record.memory_id,
            {
                "type": record.type,
                "subtype": record.subtype,
                "tier": record.tier,
                "source_kind": source_kind,
                "source_uri": source_uri,
                "sensitivity": record.sensitivity.level,
                "labels": record.sensitivity.labels,
                "quality": quality.to_dict(),
                "extraction": extraction.to_dict(),
                "governance": governance.to_dict(),
            },
        )
        result = {
            "stored": True,
            "quarantined": governance.quarantined,
            "record": record,
            "quality": quality.to_dict(),
            "extraction": extraction.to_dict(),
            "governance": governance.to_dict(),
        }
        result["audit_event_id"] = _audit_event_id(audit)
        result["feedback"] = build_memory_change_summary(
            result,
            namespace=self.namespace,
            source=source_kind,
            session_id=source_uri,
            audit_event_ids=[result["audit_event_id"]],
        ).to_dict()
        return result

    def ingest_tool_result(self, tool_result: ToolResult, source_uri: str = "tool_result") -> Dict[str, object]:
        return self.ingest_text(
            tool_result.to_memory_text(),
            source_kind="tool_result",
            source_uri=source_uri,
            tier="session" if tool_result.exit_code == 0 else "working",
        )

    def ingest_tool_results(self, tool_results: List[ToolResult], source_uri: str = "tool_results") -> Dict[str, object]:
        if len(tool_results) > 200:
            raise ValueError("tool_results must contain at most 200 items")
        stored_results = [
            self.ingest_tool_result(tool_result, source_uri="%s#%s" % (source_uri, index + 1))
            for index, tool_result in enumerate(tool_results)
        ]
        preference_results = []
        for candidate in self.tool_preference_miner.mine(tool_results):
            preference_results.append(
                self.ingest_text(
                    candidate.to_memory_text(),
                    source_kind="tool_result",
                    source_uri="%s#tool-preference:%s:%s" % (source_uri, candidate.domain, candidate.preferred_tool),
                    tier="profile",
                )
            )
        return {
            "stored_count": sum(1 for item in stored_results if item.get("stored")),
            "preference_count": sum(1 for item in preference_results if item.get("stored")),
            "results": stored_results,
            "preferences": preference_results,
        }

    def ingest_interaction(self, event: Union[AgentInteractionEvent, Dict[str, object]]) -> Dict[str, object]:
        parsed = event if isinstance(event, AgentInteractionEvent) else AgentInteractionEvent.from_dict(event)
        normalized = normalize_event(parsed)
        extraction = extract_interaction_preferences(normalized)
        safe_event, sensitivity = self._sanitize_interaction_event(
            normalized,
            remember_allowed=extraction.remember_allowed,
        )
        result = self.meta.insert_agent_event(safe_event)
        audit_type = "interaction_ingest" if result["stored"] else "interaction_duplicate"
        audit = self._audit(
            audit_type,
            None,
            {
                "event_id": result["event_id"],
                "provenance_hash": safe_event.provenance_hash,
                "agent_name": safe_event.agent_name,
                "session_id": safe_event.session_id,
                "turn_id": safe_event.turn_id,
                "sequence": safe_event.sequence,
                "event_type": safe_event.event_type,
                "role": safe_event.role,
                "remember_allowed": extraction.remember_allowed,
                "sensitivity_level": sensitivity.level,
                "sensitivity_labels": sensitivity.labels,
                "policy_decision": str((safe_event.tool_args or {}).get("decision") or ""),
                "policy_category": str((safe_event.tool_args or {}).get("policy_category") or ""),
            },
        )
        memory_ids = []
        if result["stored"] and extraction.remember_allowed and sensitivity.level != "high":
            for observation in extraction.observations:
                self.meta.add_preference_observation(observation, safe_event)
                aggregate_row = self._preference_aggregate(observation, safe_event)
                if aggregate_row and aggregate_row["stable"] and not aggregate_row.get("promoted_memory_id"):
                    memory = self._store_preference_observation(observation, safe_event)
                    for event_id in self.meta.preference_observation_event_ids(aggregate_row):
                        relation = "confirmed_by" if event_id == safe_event.event_id and observation.explicit else "extracted_from"
                        self.link_memory_event(memory.memory_id, event_id, relation)
                    self.meta.mark_preference_promoted(aggregate_row, memory.memory_id)
                    memory_ids.append(memory.memory_id)
        feedback_parts = []
        for memory_id in memory_ids:
            row = self.meta.get_memory(memory_id)
            if row:
                feedback_parts.append(
                    build_memory_change_summary(
                        {"stored": True, "record": row},
                        namespace=self.namespace,
                        source="agent",
                        session_id=safe_event.session_id,
                        audit_event_ids=[_audit_event_id(audit)],
                    ).to_dict()
                )
        if feedback_parts:
            feedback = merge_memory_change_summaries(
                feedback_parts,
                namespace=self.namespace,
                source="agent",
                session_id=safe_event.session_id,
            )
        else:
            feedback = build_memory_change_summary(
                {
                    "stored": False,
                    "reason": "sensitive_block" if sensitivity.level == "high" else "interaction_not_promoted",
                    "sensitivity": sensitivity,
                },
                namespace=self.namespace,
                source="agent",
                session_id=safe_event.session_id,
                audit_event_ids=[_audit_event_id(audit)],
            ).to_dict()
        return {
            **result,
            "agent_name": safe_event.agent_name,
            "session_id": safe_event.session_id,
            "turn_id": safe_event.turn_id,
            "sequence": safe_event.sequence,
            "event_type": safe_event.event_type,
            "remember_allowed": extraction.remember_allowed,
            "remember_reason": extraction.reason,
            "sensitivity": sensitivity.__dict__,
            "memory_ids": memory_ids,
            "audit_event_id": _audit_event_id(audit),
            "feedback": feedback,
            "sensitive_notice": (
                build_sensitive_block_notice(
                    {"stored": False, "reason": "sensitive_block", "sensitivity": sensitivity},
                    source="agent",
                    audit_event_id=_audit_event_id(audit),
                ).to_dict()
                if sensitivity.level == "high"
                else None
            ),
        }

    def ingest_interactions(
        self,
        events: List[Union[AgentInteractionEvent, Dict[str, object]]],
    ) -> Dict[str, object]:
        if len(events) > 500:
            raise ValueError("events must contain at most 500 items")
        parsed = [
            event if isinstance(event, AgentInteractionEvent) else AgentInteractionEvent.from_dict(event)
            for event in events
        ]
        results = [self.ingest_interaction(event) for event in normalize_events(parsed)]
        return {
            "event_count": len(results),
            "stored_count": sum(1 for result in results if result["stored"]),
            "duplicate_count": sum(1 for result in results if result["duplicate"]),
            "blocked_memory_count": sum(
                1 for result in results if result["sensitivity"]["level"] == "high" or not result["remember_allowed"]
            ),
            "memory_count": sum(len(result["memory_ids"]) for result in results),
            "memory_ids": [
                memory_id
                for result in results
                for memory_id in result["memory_ids"]
            ],
            "memory_change_summary": merge_memory_change_summaries(
                [result.get("feedback", {}) for result in results],
                namespace=self.namespace,
                source="agent",
                session_id=str(results[0].get("session_id") if results else ""),
            ),
            "results": results,
        }

    def interaction_status(self) -> Dict[str, object]:
        aggregates = []
        for row in self.meta.list_preference_aggregates():
            aggregate = PreferenceAggregate(
                evidence_count=int(row["evidence_count"]),
                distinct_sessions=int(row["distinct_sessions"]),
                positive_ratio=float(row["positive_ratio"]),
                contradiction_count=int(row["contradiction_count"]),
                explicit_confirmation=bool(row["explicit_confirmation"]),
            )
            item = dict(row)
            item["stable"] = aggregate.stable
            aggregates.append(item)
        result = {
            "namespace": self.namespace,
            "event_count": len(self.meta.list_agent_events(limit=1000000)),
            "preference_aggregates": aggregates,
        }
        self._audit(
            "interaction_status",
            None,
            {
                "event_count": result["event_count"],
                "aggregate_count": len(aggregates),
                "stable_count": sum(1 for item in aggregates if item["stable"]),
            },
        )
        return result

    def agent_dashboard_snapshot(
        self,
        session_id: str = "",
        task: str = "查看当前 Agent 记忆状态",
        agent_name: str = "openhands",
        project_root: str = "",
        task_type: str = "",
        limit: int = 100,
    ) -> Dict[str, object]:
        events = self.meta.list_agent_events(
            session_id=str(session_id).strip() or None,
            limit=max(1, min(int(limit), 500)),
        )
        timeline = []
        for event in events:
            timeline.append(
                {
                    "event_id": event["event_id"],
                    "agent_name": event["agent_name"],
                    "session_id": event["session_id"],
                    "turn_id": event["turn_id"],
                    "sequence": event["sequence"],
                    "event_type": event["event_type"],
                    "role": event["role"],
                    "content": event["content"],
                    "tool_name": event["tool_name"],
                    "tool_args": event["tool_args"],
                    "tool_result": event["tool_result"],
                    "exit_code": event["exit_code"],
                    "timestamp": event["timestamp"],
                    "forgotten": bool(event.get("forgotten")),
                    "provenance_hash": event["provenance_hash"],
                }
            )
        status = self.interaction_status()
        clean_task = str(task).strip() or "查看当前 Agent 记忆状态"
        effective_session = str(session_id).strip() or "dashboard-preview"
        contexts = {
            stage: self.build_agent_context(
                task=clean_task,
                stage=stage,
                agent_name=agent_name,
                session_id=effective_session,
                project_root=project_root,
                task_type=task_type,
            )
            for stage in ("planning", "tool_selection", "response_generation")
        }
        effect_proofs = []
        for stage, context in contexts.items():
            for memory in context.get("memories", []):
                if not memory.get("preference_topic"):
                    continue
                actual: Dict[str, object] = {}
                if stage == "tool_selection":
                    for event in timeline:
                        if event["event_type"] != "tool_call":
                            continue
                        args = event.get("tool_args") if isinstance(event.get("tool_args"), dict) else {}
                        actual = {
                            "tool_name": event.get("tool_name", ""),
                            "command": args.get("command", ""),
                            "event_id": event.get("event_id", ""),
                        }
                        break
                elif stage == "response_generation":
                    for event in timeline:
                        if event["event_type"] in {"assistant_message", "task_complete"}:
                            actual = {"response": event.get("content", ""), "event_id": event.get("event_id", "")}
                            break
                proof = build_preference_effect_proof(
                    task=clean_task,
                    agent_name=agent_name,
                    session_id=effective_session,
                    stage=stage,
                    preference=memory,
                    actual_behavior=actual,
                    source_event_ids=memory.get("source_event_ids", []),
                    evidence_links=["/api/agent-evidence?memory_id=%s" % str(memory.get("memory_id") or "")],
                )
                effect_proofs.append(proof.to_dict())
        return {
            "summary": {
                "event_count": len(timeline),
                "session_count": len({event["session_id"] for event in timeline}),
                "tool_call_count": sum(1 for event in timeline if event["event_type"] == "tool_call"),
                "tool_result_count": sum(1 for event in timeline if event["event_type"] == "tool_result"),
                "forgotten_event_count": sum(1 for event in timeline if event["forgotten"]),
                "stable_preference_count": sum(
                    1 for item in status["preference_aggregates"] if item["stable"]
                ),
            },
            "timeline": timeline,
            "preference_aggregates": status["preference_aggregates"],
            "contexts": contexts,
            "preference_effect_proofs": effect_proofs,
        }

    def _preference_aggregate(
        self,
        observation: PreferenceObservation,
        event: AgentInteractionEvent,
    ) -> Optional[Dict[str, object]]:
        expected = {
            "topic": observation.topic,
            "value": observation.value,
            "preference_scope": observation.scope,
            "project_root": event.project_root if observation.scope == "project" else "",
            "task_type": event.task_type if observation.scope == "task_type" else "",
            "scope_agent_name": event.agent_name if observation.scope == "agent" else "",
            "scope_session_id": event.session_id if observation.scope == "session" else "",
        }
        for row in self.meta.list_preference_aggregates():
            if any(row.get(key) != value for key, value in expected.items()):
                continue
            aggregate = PreferenceAggregate(
                evidence_count=int(row["evidence_count"]),
                distinct_sessions=int(row["distinct_sessions"]),
                positive_ratio=float(row["positive_ratio"]),
                contradiction_count=int(row["contradiction_count"]),
                explicit_confirmation=bool(row["explicit_confirmation"]),
            )
            result = dict(row)
            result["stable"] = aggregate.stable
            return result
        return None

    def select_preferences(
        self,
        agent_name: str,
        session_id: str,
        project_root: str = "",
        task_type: str = "",
    ) -> Dict[str, object]:
        scope_rank = {
            "session": 5,
            "project": 4,
            "task_type": 3,
            "agent": 2,
            "global": 1,
        }
        candidates = [
            row
            for row in self.meta.list_memories(include_deleted=False)
            if row.get("type") in {"preference", "sensitive_policy"} and row.get("preference_topic")
        ]
        event_order_cache = {}
        applicable = []
        excluded = []
        for row in candidates:
            scope = str(row.get("preference_scope") or "global")
            matches = {
                "global": True,
                "agent": str(row.get("agent_name") or "") == agent_name,
                "project": bool(project_root) and str(row.get("project_root") or "") == project_root,
                "task_type": bool(task_type) and str(row.get("task_type") or "") == task_type,
                "session": str(row.get("preference_session_id") or "") == session_id,
            }.get(scope, False)
            item = dict(row)
            item["preference_explicit"] = bool(item.get("preference_explicit"))
            item["topic"] = item.get("preference_topic")
            item["value"] = item.get("preference_value")
            memory_id = str(item.get("memory_id") or "")
            if memory_id not in event_order_cache:
                evidence = self.meta.memory_event_evidence(memory_id)
                event_order_cache[memory_id] = max(
                    (
                        (
                            str(event.get("timestamp") or ""),
                            int(event.get("ingest_order") or 0),
                            int(event.get("sequence") or 0),
                            str(event.get("event_id") or ""),
                        )
                        for event in evidence
                    ),
                    default=("", 0, 0, ""),
                )
            item["_event_order"] = event_order_cache[memory_id]
            if not matches:
                item["reason"] = "scope_not_applicable"
                item.pop("_event_order", None)
                excluded.append(item)
                continue
            item["_scope_rank"] = scope_rank.get(scope, 0)
            applicable.append(item)

        selected = []
        by_topic: Dict[str, List[Dict[str, object]]] = {}
        for row in applicable:
            by_topic.setdefault(str(row["preference_topic"]), []).append(row)
        for topic, rows in sorted(by_topic.items()):
            ordered = sorted(
                rows,
                key=lambda row: (
                    int(row["_scope_rank"]),
                    1 if row["preference_explicit"] else 0,
                    row["_event_order"],
                    str(row.get("updated_at") or ""),
                ),
                reverse=True,
            )
            chosen = ordered[0]
            chosen.pop("_scope_rank", None)
            chosen.pop("_event_order", None)
            chosen["selection_reason"] = "highest applicable scope, then explicit evidence, then latest version"
            selected.append(chosen)
            for row in ordered[1:]:
                if int(row["_scope_rank"]) < scope_rank.get(str(chosen.get("preference_scope")), 0):
                    reason = "lower_scope_priority"
                elif bool(row["preference_explicit"]) != bool(chosen["preference_explicit"]):
                    reason = "lower_evidence_priority"
                else:
                    reason = "older_version"
                row.pop("_scope_rank", None)
                row.pop("_event_order", None)
                row["reason"] = reason
                excluded.append(row)

        result = {
            "agent_name": agent_name,
            "session_id": session_id,
            "project_root": project_root,
            "task_type": task_type,
            "selected": selected,
            "excluded": excluded,
        }
        self._audit(
            "preference_select",
            None,
            {
                "agent_name": agent_name,
                "session_id": session_id,
                "project_root": project_root,
                "task_type": task_type,
                "selected_memory_ids": [row["memory_id"] for row in selected],
                "excluded_count": len(excluded),
            },
        )
        return result

    def link_memory_event(self, memory_id: str, event_id: str, relation: str = "extracted_from") -> Dict[str, object]:
        allowed_relations = {"extracted_from", "confirmed_by", "contradicted_by", "derived_from", "used_by"}
        if relation not in allowed_relations:
            return {
                "linked": False,
                "reason": "unsupported_relation",
                "memory_id": memory_id,
                "event_id": event_id,
                "relation": relation,
            }
        result = self.meta.link_memory_event(memory_id, event_id, relation)
        if result.get("linked"):
            self._audit(
                "memory_event_link",
                memory_id,
                {
                    "event_id": event_id,
                    "relation": relation,
                    "created": result.get("created", False),
                },
            )
        return result

    def preference_evidence(self, memory_id: str) -> Dict[str, object]:
        memory = self.meta.get_memory(memory_id)
        if memory is None:
            return {"found": False, "reason": "memory_not_found", "memory_id": memory_id, "events": []}
        deleted = bool(memory.get("deleted"))
        events = self.meta.memory_event_evidence(memory_id)
        if deleted:
            for event in events:
                event["content"] = ""
                event["tool_args"] = {}
                event["tool_result"] = ""
                event["state_before"] = ""
                event["state_after"] = ""
                event["user_feedback"] = ""
        result = {
            "found": True,
            "memory_id": memory_id,
            "deleted": deleted,
            "type": memory.get("type"),
            "subtype": memory.get("subtype"),
            "events": events,
        }
        self._audit(
            "preference_evidence",
            memory_id,
            {
                "deleted": deleted,
                "event_count": len(events),
            },
        )
        return result

    def memory_change_summary(self, result: Dict[str, object], source: str = "manual", session_id: str = "") -> Dict[str, object]:
        return build_memory_change_summary(
            result,
            namespace=self.namespace,
            source=source,
            session_id=session_id,
            audit_event_ids=[str(result.get("audit_event_id") or "")],
        ).to_dict()

    def preference_effect_proof(
        self,
        memory_id: str,
        task: str = "验证当前偏好是否生效",
        stage: str = "tool_selection",
        agent_name: str = "openhands",
        session_id: str = "",
    ) -> Dict[str, object]:
        memory = self.meta.get_memory(str(memory_id))
        if memory is None:
            return {"found": False, "memory_id": memory_id, "reason": "memory_not_found"}
        evidence = self.preference_evidence(str(memory_id))
        observed_events = list(evidence.get("events", []))
        source_sessions = {str(item.get("session_id") or "") for item in observed_events if item.get("session_id")}
        for source_session in source_sessions:
            for event in self.meta.list_agent_events(session_id=source_session, limit=500):
                if not any(str(item.get("event_id")) == str(event.get("event_id")) for item in observed_events):
                    observed_events.append(event)
        actual: Dict[str, object] = {}
        for event in observed_events:
            event_type = str(event.get("event_type") or "")
            if stage == "tool_selection" and event_type == "tool_call":
                tool_args = event.get("tool_args") if isinstance(event.get("tool_args"), dict) else {}
                actual = {
                    "tool_name": event.get("tool_name", ""),
                    "command": tool_args.get("command", ""),
                    "event_id": event.get("event_id", ""),
                }
                break
            if stage == "response_generation" and event_type in {"assistant_message", "task_complete"}:
                actual = {
                    "response": event.get("content", ""),
                    "event_id": event.get("event_id", ""),
                }
                break
            if stage == "planning" and event_type in {"user_message", "assistant_message"}:
                actual = {"response": event.get("content", ""), "event_id": event.get("event_id", "")}
                break
        proof = build_preference_effect_proof(
            task=task,
            agent_name=agent_name,
            session_id=session_id,
            stage=stage,
            preference=memory,
            actual_behavior=actual,
            source_event_ids=[str(item.get("event_id") or "") for item in evidence.get("events", [])],
            evidence_links=["/api/agent-evidence?memory_id=%s" % str(memory_id)],
        ).to_dict()
        return {"found": True, "memory": memory, "proof": proof}

    def memory_detail(
        self,
        memory_id: str,
        task: str = "验证这条记忆的适用范围",
        agent_name: str = "openhands",
        session_id: str = "detail-preview",
    ) -> Dict[str, object]:
        memory = self.meta.get_memory(str(memory_id))
        if memory is None:
            return {"found": False, "memory_id": memory_id, "reason": "memory_not_found"}
        evidence = self.preference_evidence(str(memory_id))
        observed_events = list(evidence.get("events", []))
        source_sessions = {str(item.get("session_id") or "") for item in observed_events if item.get("session_id")}
        for source_session in source_sessions:
            for event in self.meta.list_agent_events(session_id=source_session, limit=500):
                if not any(str(item.get("event_id")) == str(event.get("event_id")) for item in observed_events):
                    observed_events.append(event)
        detail = {
            "found": True,
            "memory": memory,
            "source_events": evidence.get("events", []),
            "versions": self.versions(str(memory_id)),
            "graph": self.meta.memory_graph(str(memory_id)),
            "related": self.meta.related_memories(str(memory_id), limit=8),
            "agent_usage": {
                "event_count": len(observed_events),
                "last_stage": "tool_selection" if any(item.get("event_type") == "tool_call" for item in observed_events) else "暂无证据",
            },
        }
        if memory.get("preference_topic"):
            detail["preference_proof"] = self.preference_effect_proof(
                str(memory_id), task=task, agent_name=agent_name, session_id=session_id
            )
        return detail

    def forget_verification_report(
        self,
        result: Dict[str, object],
        request: str = "",
        dry_run: Optional[bool] = None,
    ) -> Dict[str, object]:
        return build_forget_verification_report(result, request=request, dry_run=dry_run).to_dict()

    def recent_notifications(self, limit: int = 30) -> List[Dict[str, object]]:
        return notifications_from_audit(self.audit(limit=max(1, min(int(limit), 200))))

    def feedback_home_snapshot(self, limit: int = 8) -> Dict[str, object]:
        recent = self.list_memories(include_deleted=False)[: max(1, min(int(limit), 30))]
        recent_memory_changes = []
        for row in recent:
            recent_memory_changes.append(
                {
                    "memory_id": row.get("memory_id"),
                    "type": row.get("type"),
                    "summary_for_user": " ".join(str(row.get("summary") or row.get("content") or "").split())[:240],
                    "scope": row.get("scope"),
                    "tier": row.get("tier"),
                    "actions": ["view", "test_effect", "forget"],
                }
            )
        notifications = self.recent_notifications(limit=max(10, limit * 3))
        interaction = self.interaction_status()
        preferences = [
            {
                "topic": item.get("topic"),
                "value": item.get("value"),
                "scope": item.get("preference_scope"),
                "stable": item.get("stable"),
                "memory_id": item.get("promoted_memory_id"),
            }
            for item in interaction.get("preference_aggregates", [])
            if item.get("stable")
        ][: max(1, min(int(limit), 30))]
        return {
            "schema": "memguardos-feedback-home-v1",
            "namespace": self.namespace,
            "recent_memories": recent_memory_changes,
            "current_preferences": preferences,
            "recent_agent_tasks": [
                item for item in notifications if item.get("kind") == "agent_activity"
            ][:limit],
            "recent_sensitive_blocks": [
                item for item in notifications if item.get("kind") == "sensitive_block"
            ][:limit],
            "recent_forgetting": [
                item for item in notifications if item.get("kind") == "forget"
            ][:limit],
            "notifications": notifications[:limit],
            "audit": self.verify_audit(),
        }

    def run_feedback_replay(
        self,
        task: str,
        *,
        mode: str = "replay",
        session_id: str = "",
    ) -> Dict[str, object]:
        """Run a deterministic local replay for UI verification.

        This is intentionally separate from a real model-driven OpenHands run:
        the response labels the mode so a replay is never presented as live model
        evidence.  Real OpenHands runs continue to use OpenHandsMemoryRunner.
        """
        clean_task = str(task or "").strip()
        if not clean_task:
            raise ValueError("task is required")
        clean_mode = str(mode or "replay").strip().lower()
        if clean_mode not in {"replay", "dry-run", "openhands"}:
            raise ValueError("mode must be replay, dry-run or openhands")
        effective_session = str(session_id or "web-replay-%s" % new_id("session"))
        if clean_mode == "openhands":
            return {
                "ok": False,
                "execution_mode": "openhands",
                "truthful_note": "真实 OpenHands 运行请使用 Agent 桥接入口，并提供可用模型和工作区；本地控制台不会把回放结果冒充真实模型结果。",
                "next_action": "在终端运行真实 OpenHands 入口，或先选择本地回放验证记忆链。",
            }
        if clean_mode == "dry-run":
            contexts = {
                stage: self.build_agent_context(
                    task=clean_task,
                    stage=stage,
                    agent_name="openhands",
                    session_id=effective_session,
                    task_type="code_assistance",
                )
                for stage in ("planning", "tool_selection", "response_generation")
            }
            return {
                "ok": True,
                "execution_mode": "dry-run",
                "session_id": effective_session,
                "context": contexts,
                "event_summary": {"stored": 0, "blocked": 0, "tool_calls": 0},
                "truthful_note": "这是只读预览，没有写入 Agent 事件，也没有声称模型已经执行任务。",
            }
        events = [
            AgentInteractionEvent(
                agent_name="openhands",
                agent_version="replay",
                session_id=effective_session,
                turn_id="turn-1",
                sequence=1,
                event_type="user_message",
                role="user",
                content="以后查代码优先使用 rg。%s" % clean_task,
                task_type="code_search",
                source_uri="web_replay",
            ),
            AgentInteractionEvent(
                agent_name="openhands",
                agent_version="replay",
                session_id=effective_session,
                turn_id="turn-1",
                sequence=2,
                event_type="tool_call",
                role="assistant",
                tool_name="terminal",
                tool_args={"command": 'rg -n "MemGuardService" memguard'},
                state_before="需要查找记忆服务实现位置",
                task_type="code_search",
                source_uri="web_replay",
            ),
            AgentInteractionEvent(
                agent_name="openhands",
                agent_version="replay",
                session_id=effective_session,
                turn_id="turn-1",
                sequence=3,
                event_type="tool_result",
                role="tool",
                tool_name="terminal",
                tool_result="memguard/service.py:48:class MemGuardService",
                exit_code=0,
                state_after="已找到目标实现",
                task_type="code_search",
                source_uri="web_replay",
            ),
            AgentInteractionEvent(
                agent_name="openhands",
                agent_version="replay",
                session_id=effective_session,
                turn_id="turn-1",
                sequence=4,
                event_type="assistant_message",
                role="assistant",
                content="rg -n \"MemGuardService\" memguard\n已找到记忆服务实现。",
                task_type="code_search",
                source_uri="web_replay",
            ),
        ]
        ingestion = self.ingest_interactions([event.to_dict() for event in events])
        snapshot = self.agent_dashboard_snapshot(session_id=effective_session, task=clean_task)
        memory_changes = []
        for memory_id in ingestion.get("memory_ids", []):
            row = self.meta.get_memory(str(memory_id))
            if row:
                memory_changes.append(
                    {
                        "memory_id": memory_id,
                        "summary_for_user": " ".join(str(row.get("summary") or row.get("content") or "").split())[:240],
                        "type": row.get("type"),
                        "scope": row.get("scope"),
                        "tier": row.get("tier"),
                        "actions": ["view", "test_effect", "forget"],
                    }
                )
        if not memory_changes:
            for preference in snapshot.get("preference_aggregates", []):
                memory_id = str(preference.get("promoted_memory_id") or "")
                row = self.meta.get_memory(memory_id) if memory_id else None
                if not row:
                    continue
                memory_changes.append(
                    {
                        "memory_id": memory_id,
                        "summary_for_user": "已复用偏好：%s = %s" % (
                            preference.get("topic", ""),
                            preference.get("value", ""),
                        ),
                        "type": row.get("type"),
                        "scope": row.get("scope"),
                        "tier": row.get("tier"),
                        "reused": True,
                        "actions": ["view", "test_effect", "forget"],
                    }
                )
        return {
            "ok": True,
            "execution_mode": "replay",
            "session_id": effective_session,
            "task": clean_task,
            "context": snapshot.get("contexts", {}),
            "timeline": snapshot.get("timeline", []),
            "preference_effect_proofs": snapshot.get("preference_effect_proofs", []),
            "memory_changes": memory_changes,
            "memory_change_summary": ingestion.get("memory_change_summary", {}),
            "sensitive_blocks": [],
            "event_summary": ingestion,
            "truthful_note": "这是可复现的本地事件回放，用于核验记忆链，不代表外部模型已经独立完成任意任务。",
        }

    def _sanitize_interaction_event(
        self,
        event: AgentInteractionEvent,
        remember_allowed: bool,
    ) -> Tuple[AgentInteractionEvent, object]:
        combined = " ".join(
            part
            for part in (
                event.content,
                event.tool_result,
                event.state_before,
                event.state_after,
                event.user_feedback,
            )
            if part
        )
        sensitivity = self.detector.classify(combined, event.source_uri)
        payload = event.to_dict()
        payload["provenance_hash"] = ""
        if not remember_allowed or sensitivity.level == "high":
            for field in ("content", "tool_result", "state_before", "state_after", "user_feedback"):
                payload[field] = ""
            payload["tool_args"] = {}
        elif sensitivity.labels:
            for field in ("content", "tool_result", "state_before", "state_after", "user_feedback"):
                payload[field] = self.detector.redact(str(payload[field]), event.source_uri)
        return AgentInteractionEvent.from_dict(payload), sensitivity

    def _store_preference_observation(
        self,
        observation: PreferenceObservation,
        event: AgentInteractionEvent,
    ) -> MemoryRecord:
        subtype = {
            "code_search_tool": "tool_preference",
            "service_log_tool": "tool_preference",
            "test_runner_command": "tool_preference",
            "response_order": "output_style",
            "response_language": "output_style",
            "response_verbosity": "output_style",
            "high_risk_confirmation": "security_policy",
        }.get(observation.topic, "user_habit")
        memory_type = "sensitive_policy" if subtype == "security_policy" else "preference"
        evidence_kind = "显式偏好" if observation.explicit else "多轮交互习惯"
        content = (
            "用户与 Agent 的%s：topic=%s value=%s preference_scope=%s "
            "agent=%s session=%s turn=%s"
            % (
                evidence_kind,
                observation.topic,
                observation.value,
                observation.scope,
                event.agent_name,
                event.session_id,
                event.turn_id,
            )
        )
        summary = "用户交互偏好：%s=%s（%s）" % (
            observation.topic,
            observation.value,
            observation.scope,
        )
        record = make_memory(
            content=content,
            memory_type=memory_type,
            source=MemorySource(kind="agent_interaction", uri=event.source_uri or event.event_id),
            summary=summary,
            subtype=subtype,
            scope=self.namespace,
            tier="long_term" if observation.scope != "session" else "session",
            preference_topic=observation.topic,
            preference_value=observation.value,
            preference_scope=observation.scope,
            preference_explicit=observation.explicit,
            agent_name=event.agent_name if observation.scope == "agent" else "",
            preference_session_id=event.session_id if observation.scope == "session" else "",
            project_root=event.project_root if observation.scope == "project" else "",
            task_type=event.task_type if observation.scope == "task_type" else "",
        )
        record.confidence = observation.confidence
        record.evidence = ["agent_event:%s" % event.event_id]
        self._enrich_graph(record)
        self.store.append(record)
        self.meta.upsert_memory(record)
        self._audit(
            "interaction_preference_extract",
            record.memory_id,
            {
                "event_id": event.event_id,
                "agent_name": event.agent_name,
                "session_id": event.session_id,
                "turn_id": event.turn_id,
                "topic": observation.topic,
                "value": observation.value,
                "preference_scope": observation.scope,
                "confidence": observation.confidence,
            },
        )
        return record

    def ingest_config(self, path: Path) -> Dict[str, object]:
        entries = load_config_entries(path)
        ingested = []
        blocked = []
        skipped = []
        for entry in entries:
            issue = validate_config_entry(entry)
            if issue is not None:
                skipped.append({"path": issue.path, "reason": issue.reason})
                continue
            result = self.ingest_text(
                config_entry_to_memory_text(entry),
                source_kind="manual_config",
                source_uri="%s#%s" % (path, entry.path),
                tier="long_term",
            )
            if result.get("stored"):
                record = result["record"]
                ingested.append(
                    {
                        "path": entry.path,
                        "memory_id": record.memory_id,
                        "type": record.type,
                        "subtype": record.subtype,
                        "tier": record.tier,
                    }
                )
            else:
                blocked.append({"path": entry.path, "reason": result.get("reason")})
        self._audit(
            "config_ingest",
            None,
            {
                "source_uri": str(path),
                "entries": len(entries),
                "stored": len(ingested),
                "blocked": len(blocked),
                "skipped": len(skipped),
            },
        )
        return {
            "source_uri": str(path),
            "entries": len(entries),
            "stored_count": len(ingested),
            "blocked_count": len(blocked),
            "skipped_count": len(skipped),
            "records": ingested,
            "blocked": blocked,
            "skipped": skipped,
        }

    def ingest_behavior_event(self, event: BehaviorEvent) -> Dict[str, object]:
        return self.ingest_text(
            event.to_memory_text(),
            source_kind="user_behavior",
            source_uri="%s#%s" % (event.source, event.index),
            tier="session",
        )

    def ingest_shell_history(self, path: Path, limit: int = 200) -> Dict[str, object]:
        events = load_shell_history(path, limit=limit)
        records = []
        blocked = []
        for event in events:
            result = self.ingest_behavior_event(event)
            if result.get("stored"):
                record = result["record"]
                records.append(
                    {
                        "command": event.command,
                        "memory_id": record.memory_id,
                        "type": record.type,
                        "subtype": record.subtype,
                        "tier": record.tier,
                    }
                )
            else:
                blocked.append({"command": event.command, "reason": result.get("reason")})
        self._audit(
            "behavior_ingest",
            None,
            {"source_uri": str(path), "events": len(events), "stored": len(records), "blocked": len(blocked)},
        )
        return {
            "source_uri": str(path),
            "events": len(events),
            "stored_count": len(records),
            "blocked_count": len(blocked),
            "records": records,
            "blocked": blocked,
        }

    def ingest_os_event(self, raw_event: str) -> Dict[str, object]:
        event = parse_os_event(raw_event)
        detail = {
            "event_type": event.event_type,
            "path": event.path,
            "process": event.process,
            "action": event.action,
        }
        self._audit("os_event", None, detail)
        return self.ingest_text(event.to_memory_text(), source_kind="os_event", source_uri=event.path, tier="working")

    def update_memory(self, memory_id: str, text: str, reason: str = "manual update") -> Dict[str, object]:
        previous = self.store.get_latest(memory_id)
        if previous is None:
            return {"updated": False, "reason": "memory_not_found", "memory_id": memory_id}

        clean_text, quality = assess_text_quality(text, source_kind="update")
        if not quality.accepted:
            self._audit(
                "quality_reject",
                memory_id,
                {
                    "source_kind": "update",
                    "source_uri": previous.memory_id,
                    "quality": quality.to_dict(),
                    "reason": "updated memory failed input quality check",
                    "update_reason": reason,
                },
            )
            return {"updated": False, "reason": "quality_reject", "quality": quality.to_dict()}

        sensitivity = self.detector.classify(clean_text, previous.source.uri)
        if sensitivity.level == "high":
            self._audit(
                "sensitive_block",
                memory_id,
                {
                    "labels": sensitivity.labels,
                    "quality": quality.to_dict(),
                    "reason": "high risk update is not stored",
                    "update_reason": reason,
                },
            )
            return {"updated": False, "reason": "sensitive_block", "sensitivity": sensitivity, "quality": quality.to_dict()}

        stored_text = clean_text
        if sensitivity.level in {"medium", "low"} and sensitivity.labels:
            stored_text = self.detector.redact(clean_text, previous.source.uri)
            sensitivity.redacted = True

        extraction = self.extractor.extract(stored_text, "update")
        memory_type = extraction.memory_type
        new_version_id = new_id("ver")
        previous.invalidate(superseded_by=new_version_id)
        self.store.append(previous)
        self.meta.invalidate_memory(memory_id, superseded_by=new_version_id, valid_to=previous.temporal.valid_to)

        record = MemoryRecord(
            memory_id=previous.memory_id,
            type=memory_type,
            subtype=extraction.subtype,
            scope=previous.scope,
            tier=previous.tier,
            content=stored_text,
            summary=extraction.summary,
            source=MemorySource(kind="update", uri=previous.memory_id),
            evidence=[stored_text[:240]],
            confidence=min(previous.confidence, quality.score, extraction.confidence),
            importance=previous.importance,
            language=previous.language,
            entities=previous.entities,
            relations=previous.relations,
            sensitivity=sensitivity,
            version=VersionInfo(version_id=new_version_id, parent_version_id=previous.version.version_id, op="update"),
            audit=AuditInfo(created_by="memguard", created_at=utc_now()),
            preference_topic=previous.preference_topic,
            preference_value=previous.preference_value,
            preference_scope=previous.preference_scope,
            preference_explicit=previous.preference_explicit,
            agent_name=previous.agent_name,
            preference_session_id=previous.preference_session_id,
            project_root=previous.project_root,
            task_type=previous.task_type,
            trust_level=previous.trust_level,
            trust_score=previous.trust_score,
            governance_state=previous.governance_state,
            governance_reason=previous.governance_reason,
        ).finalize_hash()
        self._enrich_graph(record)
        self.store.append(record)
        self.meta.upsert_memory(record)
        self._audit(
            "update",
            memory_id,
            {
                "reason": reason,
                "superseded_version_id": previous.version.version_id,
                "version_id": record.version.version_id,
                "valid_from": record.temporal.valid_from,
                "previous_valid_to": previous.temporal.valid_to,
                "quality": quality.to_dict(),
                "extraction": extraction.to_dict(),
            },
        )
        return {"updated": True, "record": record, "quality": quality.to_dict(), "extraction": extraction.to_dict()}

    def list_memories(
        self,
        include_deleted: bool = False,
        tier: Optional[str] = None,
        governance_state: Optional[str] = "active",
    ) -> List[Dict[str, object]]:
        return self.meta.list_memories(
            include_deleted=include_deleted,
            tier=tier,
            governance_state=governance_state,
        )

    def list_quarantined_memories(self, limit: int = 100) -> List[Dict[str, object]]:
        records = self.meta.list_memories(
            include_deleted=False,
            limit=max(1, min(int(limit), 1000)),
            governance_state="quarantined",
        )
        self._audit("governance_list_quarantine", None, {"count": len(records), "limit": limit})
        return records

    def review_quarantined_memory(
        self,
        memory_id: str,
        decision: str,
        reviewer: str,
        reason: str,
    ) -> Dict[str, object]:
        decision = str(decision or "").strip().lower()
        reviewer = str(reviewer or "").strip()
        reason = str(reason or "").strip()
        if decision not in {"approve", "reject"}:
            raise ValueError("governance decision must be approve or reject")
        if not reviewer:
            raise ValueError("governance reviewer is required")
        if not reason:
            raise ValueError("governance review reason is required")
        row = self.meta.get_memory(memory_id)
        if row is None or row.get("deleted"):
            return {"reviewed": False, "reason": "memory_not_found", "memory_id": memory_id}
        if row.get("governance_state") != "quarantined":
            return {
                "reviewed": False,
                "reason": "memory_not_quarantined",
                "memory_id": memory_id,
                "governance_state": row.get("governance_state"),
            }
        previous = self.store.get_latest(memory_id)
        if previous is None:
            return {"reviewed": False, "reason": "segment_record_not_found", "memory_id": memory_id}

        if decision == "reject":
            previous.governance_state = "rejected"
            previous.governance_reason = reason
            previous.trust_level = "rejected"
            previous.trust_score = 0.0
            tombstone = self.store.tombstone(previous, "governance rejection: " + reason)
            self.meta.mark_deleted(memory_id, superseded_by=tombstone.version.version_id)
            self.meta.set_governance_state(memory_id, "rejected", "rejected", 0.0, reason)
            self._audit(
                "governance_reject",
                memory_id,
                {"reviewer": reviewer, "reason": reason, "source_uri": row.get("source_uri")},
            )
            return {"reviewed": True, "memory_id": memory_id, "governance_state": "rejected"}

        new_version_id = new_id("ver")
        previous.invalidate(superseded_by=new_version_id)
        self.store.append(previous)
        self.meta.invalidate_memory(memory_id, superseded_by=new_version_id, valid_to=previous.temporal.valid_to)
        payload = previous.to_dict()
        now = utc_now()
        payload["version"] = {
            "version_id": new_version_id,
            "parent_version_id": previous.version.version_id,
            "op": "governance_approve",
        }
        payload["audit"] = {"created_by": reviewer, "created_at": now, "hash": ""}
        payload["temporal"] = {"valid_from": now, "valid_to": None, "superseded_by": None}
        payload["trust_level"] = "reviewed"
        payload["trust_score"] = 0.85
        payload["governance_state"] = "active"
        payload["governance_reason"] = reason
        payload["deleted"] = False
        approved = MemoryRecord.from_dict(payload).finalize_hash()
        self.store.append(approved)
        self.meta.upsert_memory(approved)
        self._audit(
            "governance_approve",
            memory_id,
            {
                "reviewer": reviewer,
                "reason": reason,
                "source_uri": row.get("source_uri"),
                "version_id": new_version_id,
            },
        )
        return {
            "reviewed": True,
            "memory_id": memory_id,
            "governance_state": "active",
            "record": approved,
        }

    def promote_memory(self, memory_id: str, tier: str) -> Dict[str, object]:
        if tier not in {"working", "session", "long_term"}:
            return {"promoted": False, "reason": "invalid_tier", "tier": tier}
        record = self.store.get_latest(memory_id)
        if record is None:
            return {"promoted": False, "reason": "memory_not_found", "memory_id": memory_id}
        new_version_id = new_id("ver")
        record.invalidate(superseded_by=new_version_id)
        self.store.append(record)
        self.meta.invalidate_memory(memory_id, superseded_by=new_version_id, valid_to=record.temporal.valid_to)

        promoted = MemoryRecord(
            memory_id=record.memory_id,
            type=record.type,
            subtype=record.subtype,
            scope=record.scope,
            tier=tier,
            content=record.content,
            summary=record.summary,
            source=MemorySource(kind="promote", uri=record.memory_id),
            evidence=record.evidence,
            confidence=record.confidence,
            importance=record.importance,
            language=record.language,
            entities=record.entities,
            relations=record.relations,
            sensitivity=record.sensitivity,
            version=VersionInfo(version_id=new_version_id, parent_version_id=record.version.version_id, op="promote"),
            audit=AuditInfo(created_by="memguard", created_at=utc_now()),
            preference_topic=record.preference_topic,
            preference_value=record.preference_value,
            preference_scope=record.preference_scope,
            preference_explicit=record.preference_explicit,
            agent_name=record.agent_name,
            preference_session_id=record.preference_session_id,
            project_root=record.project_root,
            task_type=record.task_type,
            trust_level=record.trust_level,
            trust_score=record.trust_score,
            governance_state=record.governance_state,
            governance_reason=record.governance_reason,
        ).finalize_hash()
        self.store.append(promoted)
        self.meta.upsert_memory(promoted)
        self._audit(
            "promote",
            memory_id,
            {
                "tier": tier,
                "superseded_version_id": record.version.version_id,
                "version_id": promoted.version.version_id,
                "previous_valid_to": record.temporal.valid_to,
            },
        )
        return {"promoted": True, "record": promoted}

    def evict_tier(self, tier: str, confirm: bool = False) -> Dict[str, object]:
        if tier not in {"working", "session"}:
            return {"evicted": False, "reason": "only working/session tiers can be evicted by policy", "tier": tier}
        records = self.meta.list_memories(include_deleted=False, tier=tier)
        if not confirm:
            return {"evicted": False, "tier": tier, "candidate_count": len(records), "candidates": records}
        evicted = []
        for row in records:
            record = self.store.get_latest(row["memory_id"])
            if record is None:
                continue
            tombstone = self.store.tombstone(record, "tier eviction: " + tier)
            self.meta.mark_deleted(record.memory_id, superseded_by=tombstone.version.version_id)
            evicted.append(record.memory_id)
        self._audit("tier_evict", None, {"tier": tier, "evicted": evicted})
        return {"evicted": True, "tier": tier, "memory_ids": evicted}

    def memory_policy_status(self, eviction_limit: int = 10) -> Dict[str, object]:
        live_records = self.meta.list_memories(include_deleted=False)
        meta_records = self.meta.list_memories(include_deleted=True, governance_state=None)
        segment_records = self.store.scan()
        tier_order = ["working", "session", "long_term"]
        tiers: Dict[str, Dict[str, object]] = {
            tier: {
                "count": 0,
                "content_chars": 0,
                "summary_chars": 0,
                "approx_payload_bytes": 0,
                "sensitivity": {},
                "sources": {},
                "oldest_updated_at": "",
                "newest_updated_at": "",
            }
            for tier in tier_order
        }

        for row in live_records:
            tier = str(row.get("tier") or "long_term")
            if tier not in tiers:
                tiers[tier] = {
                    "count": 0,
                    "content_chars": 0,
                    "summary_chars": 0,
                    "approx_payload_bytes": 0,
                    "sensitivity": {},
                    "sources": {},
                    "oldest_updated_at": "",
                    "newest_updated_at": "",
                }
            bucket = tiers[tier]
            content = str(row.get("content") or "")
            summary = str(row.get("summary") or "")
            source_kind = str(row.get("source_kind") or "unknown")
            sensitivity_level = str(row.get("sensitivity_level") or "none")
            updated_at = str(row.get("updated_at") or "")
            bucket["count"] = int(bucket["count"]) + 1
            bucket["content_chars"] = int(bucket["content_chars"]) + len(content)
            bucket["summary_chars"] = int(bucket["summary_chars"]) + len(summary)
            bucket["approx_payload_bytes"] = int(bucket["approx_payload_bytes"]) + len((content + summary).encode("utf-8"))
            sensitivities = dict(bucket["sensitivity"])
            sensitivities[sensitivity_level] = int(sensitivities.get(sensitivity_level, 0)) + 1
            bucket["sensitivity"] = sensitivities
            sources = dict(bucket["sources"])
            sources[source_kind] = int(sources.get(source_kind, 0)) + 1
            bucket["sources"] = sources
            if updated_at:
                if not bucket["oldest_updated_at"] or updated_at < str(bucket["oldest_updated_at"]):
                    bucket["oldest_updated_at"] = updated_at
                if not bucket["newest_updated_at"] or updated_at > str(bucket["newest_updated_at"]):
                    bucket["newest_updated_at"] = updated_at

        stale_count = max(0, len(segment_records) - len(live_records))
        eviction_candidates = [
            {
                "memory_id": row.get("memory_id"),
                "tier": row.get("tier"),
                "type": row.get("type"),
                "subtype": row.get("subtype"),
                "source_kind": row.get("source_kind"),
                "updated_at": row.get("updated_at"),
                "summary": row.get("summary"),
            }
            for row in sorted(
                [row for row in live_records if row.get("tier") in {"working", "session"}],
                key=lambda item: str(item.get("updated_at") or ""),
            )[: max(0, eviction_limit)]
        ]
        store_bytes = self._path_size(self.store.path)
        sqlite_bytes = sum(
            self._path_size(path)
            for path in (
                self.meta.path,
                Path(str(self.meta.path) + "-wal"),
                Path(str(self.meta.path) + "-shm"),
            )
        )
        result = {
            "namespace": self.namespace,
            "policy": {
                "working": "当前任务临时上下文，优先淘汰，不进入长期沉淀。",
                "session": "本轮会话/阶段性任务记忆，可在任务结束后批量淘汰或挑选沉淀。",
                "long_term": "稳定偏好、知识、安全策略和可复用经验，默认保留并参与 RAG。",
                "eviction_order": ["working", "session"],
                "promotion_path": "working/session -> long_term",
                "compaction": "遗忘或层级淘汰后可执行 compact，清理追加段文件中的旧明文。",
            },
            "tiers": tiers,
            "counts": {
                "live": len(live_records),
                "metadata_records": len(meta_records),
                "segment_records": len(segment_records),
                "stale_or_deleted": stale_count,
                "eviction_candidates": len(eviction_candidates),
            },
            "storage": {
                "segment_path": str(self.store.path),
                "segment_bytes": store_bytes,
                "sqlite_path": str(self.meta.path),
                "sqlite_total_bytes": sqlite_bytes,
                "approx_total_bytes": store_bytes + sqlite_bytes,
                "bytes_per_live_memory": round((store_bytes + sqlite_bytes) / float(len(live_records) or 1), 3),
            },
            "eviction_candidates": eviction_candidates,
            "recommendations": self._memory_policy_recommendations(tiers, stale_count, store_bytes + sqlite_bytes),
            "cgroup": cgroup_status(),
        }
        self._audit(
            "memory_policy_status",
            None,
            {
                "live": len(live_records),
                "stale_or_deleted": stale_count,
                "eviction_candidates": len(eviction_candidates),
                "approx_total_bytes": store_bytes + sqlite_bytes,
            },
        )
        return result

    def versions(self, memory_id: str) -> List[Dict[str, object]]:
        by_version_id: Dict[str, Dict[str, object]] = {}
        for record in self.store.get_versions(memory_id):
            by_version_id[record.version.version_id] = {
                "memory_id": record.memory_id,
                "version_id": record.version.version_id,
                "parent_version_id": record.version.parent_version_id,
                "op": record.version.op,
                "summary": record.summary,
                "deleted": record.deleted,
                "created_at": record.audit.created_at,
                "valid_from": record.temporal.valid_from,
                "valid_to": record.temporal.valid_to,
                "superseded_by": record.temporal.superseded_by,
                "active": not record.deleted and record.temporal.valid_to is None,
                "hash": record.audit.hash,
            }
        versions = list(by_version_id.values())
        self._audit("versions", memory_id, {"count": len(versions)})
        return versions

    def temporal_status(self, limit: int = 20) -> Dict[str, object]:
        live = self.meta.list_memories(include_deleted=False, limit=limit)
        segment_records = self.store.scan()
        inactive = [record for record in segment_records if record.deleted or record.temporal.valid_to]
        status = {
            "active_count": len(self.meta.list_memories(include_deleted=False)),
            "sample_active": [
                {
                    "memory_id": row["memory_id"],
                    "type": row["type"],
                    "subtype": row["subtype"],
                    "tier": row["tier"],
                    "version_id": row["version_id"],
                    "valid_from": row.get("valid_from") or row.get("updated_at"),
                    "valid_to": row.get("valid_to"),
                    "superseded_by": row.get("superseded_by"),
                    "summary": row["summary"],
                }
                for row in live[:limit]
            ],
            "inactive_sample": [
                {
                    "memory_id": record.memory_id,
                    "version_id": record.version.version_id,
                    "op": record.version.op,
                    "valid_from": record.temporal.valid_from,
                    "valid_to": record.temporal.valid_to,
                    "superseded_by": record.temporal.superseded_by,
                    "deleted": record.deleted,
                    "summary": record.summary,
                }
                for record in inactive[-limit:]
            ],
            "policy": {
                "meaning": "valid_from/valid_to describe when a memory fact is active for retrieval and generation.",
                "update_behavior": "updates, promotions, rollbacks and forgetting close the previous fact window before writing the new state or tombstone.",
                "retrieval_behavior": "normal search only reads non-deleted active metadata rows; historical versions remain available through versions/audit.",
            },
        }
        self._audit(
            "temporal_status",
            None,
            {
                "active_count": status["active_count"],
                "inactive_sample_count": len(status["inactive_sample"]),
            },
        )
        return status

    def rollback(self, memory_id: str, version_id: str) -> Dict[str, object]:
        versions = self.store.get_versions(memory_id)
        target = None
        latest = None
        for record in versions:
            if record.version.version_id == version_id:
                target = record
            latest = record
        if target is None or latest is None:
            return {"rolled_back": False, "reason": "version_not_found", "memory_id": memory_id}
        if target.deleted:
            return {"rolled_back": False, "reason": "cannot_rollback_to_tombstone", "memory_id": memory_id}

        new_version_id = new_id("ver")
        latest.invalidate(superseded_by=new_version_id)
        self.store.append(latest)
        self.meta.invalidate_memory(memory_id, superseded_by=new_version_id, valid_to=latest.temporal.valid_to)

        record = MemoryRecord(
            memory_id=target.memory_id,
            type=target.type,
            subtype=target.subtype,
            scope=target.scope,
            tier=target.tier,
            content=target.content,
            summary=target.summary,
            source=MemorySource(kind="rollback", uri=version_id),
            evidence=target.evidence,
            confidence=target.confidence,
            importance=target.importance,
            language=target.language,
            entities=target.entities,
            relations=target.relations,
            sensitivity=target.sensitivity,
            version=VersionInfo(version_id=new_version_id, parent_version_id=latest.version.version_id, op="rollback"),
            audit=AuditInfo(created_by="memguard", created_at=utc_now(), hash=stable_hash(target.memory_id + version_id + utc_now())),
            preference_topic=target.preference_topic,
            preference_value=target.preference_value,
            preference_scope=target.preference_scope,
            preference_explicit=target.preference_explicit,
            agent_name=target.agent_name,
            preference_session_id=target.preference_session_id,
            project_root=target.project_root,
            task_type=target.task_type,
            trust_level=target.trust_level,
            trust_score=target.trust_score,
            governance_state=target.governance_state,
            governance_reason=target.governance_reason,
            deleted=False,
        )
        self.store.append(record)
        self.meta.upsert_memory(record)
        self._audit(
            "rollback",
            memory_id,
            {
                "target_version_id": version_id,
                "superseded_version_id": latest.version.version_id,
                "new_version_id": record.version.version_id,
                "previous_valid_to": latest.temporal.valid_to,
            },
        )
        return {"rolled_back": True, "record": record}

    def search(self, query: str, limit: int = 5, audit: bool = True) -> List[Dict[str, object]]:
        clean_query = query.strip()
        if not clean_query:
            return []
        cache_key = (clean_query, max(1, int(limit)), self.meta.memory_revision())
        cached = self._search_cache.get(cache_key)
        if cached is not None:
            self._search_cache.move_to_end(cache_key)
            results = [dict(item) for item in cached]
            if audit:
                self._audit("search", None, {"query": clean_query, "count": len(results), "cache": "hit"})
            return results

        candidates = self._search_candidates(query, limit=max(limit * 8, 64))
        ranked = self.retriever.rank(query, candidates, limit=limit)
        results = []
        seen = set()
        for score, record in ranked:
            item = dict(record)
            item["score"] = round(score, 4)
            item["match_source"] = "hybrid"
            item["vector_backend"] = item.pop("_vector_backend", self.retriever.last_backend_used)
            if "_score_breakdown" in item:
                item["score_breakdown"] = item.pop("_score_breakdown")
            if "_expansion_trace" in item:
                item["expansion_trace"] = item.pop("_expansion_trace")
            results.append(item)
            seen.add(item["memory_id"])
        for record in self.meta.graph_search(query, limit=limit):
            if record["memory_id"] in seen:
                continue
            record["score"] = 0.22
            record["match_source"] = "graph"
            results.append(record)
            seen.add(record["memory_id"])
        if not results:
            for record in self.meta.search_text(query, limit=limit):
                record["score"] = 0.1
                record["match_source"] = "text"
                results.append(record)
        if audit:
            self._audit("search", None, {"query": clean_query, "count": len(results), "cache": "miss"})
        results = results[:limit]
        self._remember_search(cache_key, results)
        return [dict(item) for item in results]

    def batch_search(self, queries: List[str], limit: int = 5, workers: int = 4) -> Dict[str, object]:
        clean_queries = [query.strip() for query in queries if query and query.strip()]
        workers = max(1, min(int(workers), max(1, len(clean_queries)), 16))
        if not clean_queries:
            result = {
                "query_count": 0,
                "workers": workers,
                "read_path_audit": "single_batch_audit_event",
                "results": [],
                "latency": {"total_ms": 0.0, "p50_ms": 0.0, "p95_ms": 0.0, "p99_ms": 0.0},
                "errors": [],
                "ok": True,
            }
            self._audit("batch_search", None, {"query_count": 0, "workers": workers, "errors": 0})
            return result

        started = perf_counter()
        results: List[Optional[Dict[str, object]]] = [None] * len(clean_queries)
        latencies: List[float] = []
        errors: List[Dict[str, object]] = []
        partitions: List[List[Tuple[int, str]]] = [[] for _ in range(workers)]
        for index, query in enumerate(clean_queries):
            partitions[index % workers].append((index, query))

        def run_partition(items: List[Tuple[int, str]]) -> Dict[str, object]:
            worker_service = MemGuardService(store_path=self.store.path, db_path=self.meta.path, namespace=self.namespace)
            partition_results: List[Dict[str, object]] = []
            partition_errors: List[Dict[str, object]] = []
            try:
                for index, query in items:
                    try:
                        query_started = perf_counter()
                        hits = worker_service.search(query, limit=limit, audit=False)
                        elapsed_ms = (perf_counter() - query_started) * 1000
                        partition_results.append(
                            {
                                "index": index,
                                "query": query,
                                "hits": hits,
                                "latency_ms": round(elapsed_ms, 3),
                            }
                        )
                    except Exception as exc:
                        partition_errors.append({"index": index, "query": query, "error": repr(exc)})
            finally:
                worker_service.close()
            return {"results": partition_results, "errors": partition_errors}

        with ThreadPoolExecutor(max_workers=workers) as executor:
            future_map = {
                executor.submit(run_partition, partition): partition_index
                for partition_index, partition in enumerate(partitions)
                if partition
            }
            for future in as_completed(future_map):
                try:
                    partition_result = future.result()
                    errors.extend(partition_result["errors"])
                    for item in partition_result["results"]:
                        index = int(item["index"])
                        results[index] = item
                        latencies.append(float(item["latency_ms"]))
                except Exception as exc:
                    partition_index = future_map[future]
                    for index, query in partitions[partition_index]:
                        errors.append({"index": index, "query": query, "error": repr(exc)})

        total_ms = (perf_counter() - started) * 1000
        ordered_results = [item for item in results if item is not None]
        result = {
            "query_count": len(clean_queries),
            "workers": workers,
            "worker_service_count": len(future_map),
            "read_path_audit": "single_batch_audit_event",
            "results": ordered_results,
            "latency": {
                "total_ms": round(total_ms, 3),
                "p50_ms": round(self._percentile(latencies, 0.50), 3),
                "p95_ms": round(self._percentile(latencies, 0.95), 3),
                "p99_ms": round(self._percentile(latencies, 0.99), 3),
            },
            "errors": errors,
            "ok": not errors and len(ordered_results) == len(clean_queries),
        }
        self._audit(
            "batch_search",
            None,
            {
                "query_count": len(clean_queries),
                "workers": workers,
                "errors": len(errors),
                "total_ms": result["latency"]["total_ms"],
                "p95_ms": result["latency"]["p95_ms"],
            },
        )
        return result

    def build_context(self, query: str, limit: int = 5, max_chars: int = 2000, token_budget: Optional[int] = None) -> Dict[str, object]:
        effective_max_chars = max_chars
        if token_budget and token_budget > 0:
            # Conservative cap for mixed Chinese/English contexts; exact tokenizers
            # are provider-specific, so the package reports estimates instead.
            effective_max_chars = min(max_chars, max(256, int(token_budget / 0.65)))
        hits = self.search(query, limit=limit)
        memories = []
        lines = []
        selection_trace = []
        source_distribution: Dict[str, int] = {}
        type_distribution: Dict[str, int] = {}
        tier_distribution: Dict[str, int] = {}
        sensitivity_distribution: Dict[str, int] = {}
        used = 0
        truncated = False
        for idx, hit in enumerate(hits, start=1):
            content = " ".join(str(hit.get("content", "")).split())
            summary = " ".join(str(hit.get("summary", "")).split())
            source = hit.get("source") or {}
            source_kind = hit.get("source_kind", "")
            if not source_kind and isinstance(source, dict):
                source_kind = source.get("kind", "")
            sensitivity = self._row_sensitivity(hit)
            source_distribution[source_kind or "unknown"] = source_distribution.get(source_kind or "unknown", 0) + 1
            type_name = str(hit.get("type") or "unknown")
            tier_name = str(hit.get("tier") or "unknown")
            sensitivity_name = str(sensitivity.get("level") or "none")
            type_distribution[type_name] = type_distribution.get(type_name, 0) + 1
            tier_distribution[tier_name] = tier_distribution.get(tier_name, 0) + 1
            sensitivity_distribution[sensitivity_name] = sensitivity_distribution.get(sensitivity_name, 0) + 1
            line = (
                "[%d] type=%s subtype=%s tier=%s score=%s source=%s\n"
                "summary: %s\n"
                "evidence: %s"
                % (
                    idx,
                    hit.get("type", ""),
                    hit.get("subtype", ""),
                    hit.get("tier", ""),
                    hit.get("score", ""),
                    source_kind,
                    summary[:240],
                    content[:360],
                )
            )
            remaining = effective_max_chars - used
            if remaining <= 0:
                truncated = True
                selection_trace.append(
                    {
                        "memory_id": hit.get("memory_id"),
                        "rank": idx,
                        "included": False,
                        "reason": "character_budget_exhausted",
                        "type": hit.get("type"),
                        "subtype": hit.get("subtype"),
                        "tier": hit.get("tier"),
                        "source_kind": source_kind,
                        "sensitivity_level": sensitivity.get("level"),
                    }
                )
                break
            original_line_length = len(line)
            if len(line) > remaining:
                line = line[: max(0, remaining - 16)] + "\n[truncated]"
                truncated = True
            used += len(line)
            lines.append(line)
            selection_trace.append(
                {
                    "memory_id": hit.get("memory_id"),
                    "rank": idx,
                    "included": True,
                    "reason": "selected_by_hybrid_graph_or_text_search",
                    "truncated": len(line) < original_line_length,
                    "score": hit.get("score"),
                    "match_source": hit.get("match_source"),
                    "type": hit.get("type"),
                    "subtype": hit.get("subtype"),
                    "tier": hit.get("tier"),
                    "source_kind": source_kind,
                    "sensitivity_level": sensitivity.get("level"),
                    "vector_backend": hit.get("vector_backend"),
                }
            )
            memories.append(
                {
                    "memory_id": hit.get("memory_id"),
                    "type": hit.get("type"),
                    "subtype": hit.get("subtype"),
                    "tier": hit.get("tier"),
                    "score": hit.get("score"),
                    "source_kind": source_kind,
                    "summary": summary,
                    "sensitivity": sensitivity,
                }
            )
        context = "\n\n".join(lines)
        result = {
            "query": query,
            "memory_count": len(memories),
            "max_chars": max_chars,
            "effective_max_chars": effective_max_chars,
            "context": context,
            "memories": memories,
            "explainability": {
                "source_distribution": source_distribution,
                "type_distribution": type_distribution,
                "tier_distribution": tier_distribution,
                "sensitivity_distribution": sensitivity_distribution,
                "selection_trace": selection_trace,
                "filtering_policy": [
                    "仅使用 live 且未被精准遗忘的记忆",
                    "高风险敏感内容在写入阶段阻断，中低风险内容进入上下文前保持脱敏占位符",
                    "按 token_budget/max_chars 控制上下文长度，超出预算时截断并记录 selection_trace",
                ],
            },
            "generation_policy": "仅使用已返回且已脱敏的记忆作为 OS Agent 生成上下文；不得输出被精准遗忘或被敏感策略阻断的明文。",
        }
        context_tokens = self.estimate_tokens(context)
        json_tokens = self.estimate_tokens(str(result))
        result["budget"] = {
            "context_chars": len(context),
            "estimated_context_tokens": context_tokens,
            "estimated_package_tokens": json_tokens,
            "token_budget": token_budget,
            "over_token_budget": bool(token_budget and context_tokens > token_budget),
            "truncated": truncated,
            "estimator": "cjk_chars_plus_latin_chars_div_4",
        }
        self._audit(
            "context_build",
            None,
            {
                "query": query,
                "memory_count": len(memories),
                "max_chars": max_chars,
                "effective_max_chars": effective_max_chars,
                "estimated_context_tokens": context_tokens,
                "token_budget": token_budget,
                "truncated": truncated,
                "source_distribution": source_distribution,
                "type_distribution": type_distribution,
                "tier_distribution": tier_distribution,
            },
        )
        return result

    def build_agent_context(
        self,
        task: str,
        stage: str,
        agent_name: str,
        session_id: str,
        project_root: str = "",
        task_type: str = "",
        limit: int = 8,
        max_chars: int = 4000,
        token_budget: int = 800,
    ) -> Dict[str, object]:
        clean_stage = validate_agent_context_stage(stage)
        clean_task = str(task).strip()
        if not clean_task:
            raise ValueError("task is required")
        search_hits = self.search(clean_task, limit=max(limit * 3, limit), audit=False)
        preference_selection = self.select_preferences(
            agent_name=agent_name,
            session_id=session_id,
            project_root=project_root,
            task_type=task_type,
        )

        candidates = []
        filtered = []
        seen = set()
        for preference in preference_selection["selected"]:
            memory_id = str(preference.get("memory_id") or "")
            if stage_allows_preference(clean_stage, preference):
                candidate = dict(preference)
                candidate["selection_reason"] = "applicable scoped preference for %s" % clean_stage
                candidates.append(candidate)
                seen.add(memory_id)
            else:
                filtered.append(
                    {
                        "memory_id": memory_id,
                        "type": preference.get("type"),
                        "subtype": preference.get("subtype"),
                        "reason": "preference_topic_not_used_in_stage",
                    }
                )
        for preference in preference_selection["excluded"]:
            filtered.append(
                {
                    "memory_id": preference.get("memory_id"),
                    "type": preference.get("type"),
                    "subtype": preference.get("subtype"),
                    "reason": preference.get("reason", "preference_not_selected"),
                }
            )

        for hit in search_hits:
            memory_id = str(hit.get("memory_id") or "")
            if not memory_id or memory_id in seen:
                continue
            if hit.get("type") in {"preference", "sensitive_policy"}:
                filtered.append(
                    {
                        "memory_id": memory_id,
                        "type": hit.get("type"),
                        "subtype": hit.get("subtype"),
                        "reason": "preferences_must_pass_scope_selection",
                    }
                )
                continue
            if not stage_allows_memory(clean_stage, hit):
                filtered.append(
                    {
                        "memory_id": memory_id,
                        "type": hit.get("type"),
                        "subtype": hit.get("subtype"),
                        "reason": "memory_subtype_not_used_in_stage",
                    }
                )
                continue
            candidate = dict(hit)
            candidate["selection_reason"] = "hybrid retrieval result relevant to %s" % clean_stage
            candidates.append(candidate)
            seen.add(memory_id)

        memories = []
        context_lines = []
        used_chars = 0
        used_tokens = 0
        for candidate in candidates:
            if len(memories) >= limit:
                filtered.append(
                    {
                        "memory_id": candidate.get("memory_id"),
                        "type": candidate.get("type"),
                        "subtype": candidate.get("subtype"),
                        "reason": "stage_limit_reached",
                    }
                )
                continue
            memory_id = str(candidate.get("memory_id") or "")
            summary = " ".join(str(candidate.get("summary") or "").split())
            content = " ".join(str(candidate.get("content") or "").split())
            line = (
                "[%s] type=%s subtype=%s reason=%s\nsummary: %s\nevidence: %s"
                % (
                    memory_id,
                    candidate.get("type", ""),
                    candidate.get("subtype", ""),
                    candidate.get("selection_reason", ""),
                    summary[:300],
                    content[:500],
                )
            )
            remaining_chars = max_chars - used_chars
            remaining_tokens = token_budget - used_tokens
            line = self._fit_context_line(line, remaining_chars, remaining_tokens)
            if not line:
                filtered.append(
                    {
                        "memory_id": memory_id,
                        "type": candidate.get("type"),
                        "subtype": candidate.get("subtype"),
                        "reason": "context_budget_exhausted",
                    }
                )
                continue
            source_event_ids = []
            if candidate.get("preference_topic"):
                evidence = self.preference_evidence(memory_id)
                source_event_ids = [str(item["event_id"]) for item in evidence.get("events", [])]
            line_tokens = self.estimate_tokens(line)
            used_chars += len(line)
            used_tokens += line_tokens
            context_lines.append(line)
            memories.append(
                {
                    "memory_id": memory_id,
                    "type": candidate.get("type"),
                    "subtype": candidate.get("subtype"),
                    "tier": candidate.get("tier"),
                    "score": candidate.get("score"),
                    "preference_topic": candidate.get("preference_topic", ""),
                    "preference_value": candidate.get("preference_value", ""),
                    "preference_scope": candidate.get("preference_scope", ""),
                    "selection_reason": candidate.get("selection_reason"),
                    "source_event_ids": source_event_ids,
                    "estimated_tokens": line_tokens,
                }
            )

        context = "\n\n".join(context_lines)
        result = {
            "task": clean_task,
            "stage": clean_stage,
            "agent_name": agent_name,
            "session_id": session_id,
            "project_root": project_root,
            "task_type": task_type,
            "retrieval_passes": 1,
            "memory_count": len(memories),
            "context": context,
            "memories": memories,
            "explainability": {
                "selected": [
                    {
                        "memory_id": item["memory_id"],
                        "reason": item["selection_reason"],
                        "source_event_ids": item["source_event_ids"],
                    }
                    for item in memories
                ],
                "filtered": filtered,
                "stage_policy": clean_stage,
                "preference_scope_order": ["session", "project", "task_type", "agent", "global"],
            },
            "budget": {
                "max_chars": max_chars,
                "token_budget": token_budget,
                "context_chars": len(context),
                "estimated_context_tokens": self.estimate_tokens(context),
                "truncated": any(item["reason"] in {"context_budget_exhausted", "stage_limit_reached"} for item in filtered),
                "estimator": "cjk_chars_plus_latin_chars_div_4",
            },
        }
        self._audit(
            "agent_context_build",
            None,
            {
                "task": clean_task,
                "stage": clean_stage,
                "agent_name": agent_name,
                "session_id": session_id,
                "project_root": project_root,
                "task_type": task_type,
                "memory_ids": [item["memory_id"] for item in memories],
                "filtered_count": len(filtered),
                "estimated_context_tokens": result["budget"]["estimated_context_tokens"],
            },
        )
        return result

    def _fit_context_line(self, line: str, remaining_chars: int, remaining_tokens: int) -> str:
        if remaining_chars <= 0 or remaining_tokens <= 0:
            return ""
        candidate = line[:remaining_chars]
        if self.estimate_tokens(candidate) <= remaining_tokens:
            return candidate
        low = 0
        high = len(candidate)
        while low < high:
            middle = (low + high + 1) // 2
            if self.estimate_tokens(candidate[:middle]) <= remaining_tokens:
                low = middle
            else:
                high = middle - 1
        if low < 24:
            return ""
        return candidate[:low]

    @staticmethod
    def estimate_tokens(text: str) -> int:
        if not text:
            return 0
        cjk = 0
        latin_like = 0
        other = 0
        for char in text:
            code = ord(char)
            if 0x4E00 <= code <= 0x9FFF or 0x3400 <= code <= 0x4DBF:
                cjk += 1
            elif char.isascii() and (char.isalnum() or char in "-_/.:%"):
                latin_like += 1
            elif char.isspace():
                continue
            else:
                other += 1
        return cjk + ((latin_like + 3) // 4) + ((other + 1) // 2)

    def _search_candidates(self, query: str, limit: int = 80) -> List[Dict[str, object]]:
        candidate_limit = max(1, int(limit))
        seen = set()
        candidates: List[Dict[str, object]] = []

        def add_unique(source: List[Dict[str, object]]) -> None:
            for record in source:
                memory_id = record.get("memory_id")
                if memory_id in seen:
                    continue
                seen.add(memory_id)
                candidates.append(record)
                if len(candidates) >= candidate_limit:
                    return

        add_unique(self.meta.search_text(query, limit=candidate_limit))
        if len(candidates) < candidate_limit:
            remaining = candidate_limit - len(candidates)
            add_unique(self.meta.graph_search(query, limit=max(10, remaining)))
        if len(candidates) < candidate_limit:
            remaining = candidate_limit - len(candidates)
            add_unique(self.meta.list_recent_memories(remaining, exclude_ids=list(seen)))
        return candidates[:candidate_limit]

    def graph_search(self, entity_query: str, limit: int = 10) -> List[Dict[str, object]]:
        results = self.meta.graph_search(entity_query, limit=limit)
        self._audit("graph_search", None, {"entity_query": entity_query, "count": len(results)})
        return results

    def hierarchical_graph_search(self, entity_query: str, limit: int = 10) -> Dict[str, object]:
        summary_rows = self.meta.graph_search(entity_query, limit=limit)
        if not summary_rows:
            summary_rows = self.search(entity_query, limit=limit, audit=False)

        summary_layer = []
        entity_relation_layer = []
        chunk_layer = []
        seen_relations = set()
        for rank, row in enumerate(summary_rows[:limit], start=1):
            memory_id = str(row.get("memory_id", ""))
            summary_layer.append(
                {
                    "layer": "summary",
                    "rank": rank,
                    "memory_id": memory_id,
                    "summary": row.get("summary", ""),
                    "type": row.get("type", ""),
                    "subtype": row.get("subtype", ""),
                    "tier": row.get("tier", ""),
                    "source_kind": row.get("source_kind", ""),
                    "matched_entity": row.get("matched_entity", entity_query),
                }
            )
            graph = self.meta.memory_graph(memory_id)
            for entity in graph.get("entities", []):
                key = (memory_id, entity, "entity", "")
                if key in seen_relations:
                    continue
                seen_relations.add(key)
                entity_relation_layer.append(
                    {
                        "layer": "entity_relation",
                        "memory_id": memory_id,
                        "source": entity,
                        "relation": "mentions",
                        "target": memory_id,
                    }
                )
            for relation in graph.get("relations", []):
                key = (
                    memory_id,
                    relation.get("source", ""),
                    relation.get("relation", ""),
                    relation.get("target", ""),
                )
                if key in seen_relations:
                    continue
                seen_relations.add(key)
                entity_relation_layer.append(
                    {
                        "layer": "entity_relation",
                        "memory_id": memory_id,
                        "source": relation.get("source", ""),
                        "relation": relation.get("relation", ""),
                        "target": relation.get("target", ""),
                    }
                )
            chunk_layer.append(
                {
                    "layer": "chunk",
                    "rank": rank,
                    "memory_id": memory_id,
                    "source_uri": row.get("source_uri", ""),
                    "content_preview": " ".join(str(row.get("content", "")).split())[:360],
                    "valid_from": row.get("valid_from", ""),
                    "valid_to": row.get("valid_to", ""),
                }
            )

        result = {
            "query": entity_query,
            "layers": {
                "summary": summary_layer,
                "entity_relation": entity_relation_layer[: max(limit * 4, 12)],
                "chunk": chunk_layer,
            },
            "explainability": {
                "retrieval_path": ["summary", "entity_relation", "chunk"],
                "summary_count": len(summary_layer),
                "entity_relation_count": len(entity_relation_layer[: max(limit * 4, 12)]),
                "chunk_count": len(chunk_layer),
                "purpose": "先用摘要层定位相关记忆，再用实体关系层解释关联，最后回到原文 chunk 层提供可追溯证据。",
            },
        }
        self._audit(
            "hierarchical_graph_search",
            None,
            {
                "entity_query": entity_query,
                "summary_count": len(summary_layer),
                "entity_relation_count": len(result["layers"]["entity_relation"]),
                "chunk_count": len(chunk_layer),
            },
        )
        return result

    def related(self, memory_id: str, limit: int = 10) -> Dict[str, object]:
        graph = self.meta.memory_graph(memory_id)
        related = self.meta.related_memories(memory_id, limit=limit)
        result = {"graph": graph, "related": related}
        self._audit("related", memory_id, {"related_count": len(related), "entity_count": len(graph["entities"])})
        return result

    def forget(self, request: str, confirm: bool = False, compact: bool = False) -> Dict[str, object]:
        records = self.meta.list_memories(include_deleted=False)
        candidates = self.forget_planner.candidates(request, records)
        external_residue = self._managed_forget_residue(confirm=confirm)
        if not confirm:
            self._audit("forget_dry_run", None, {"request": request, "candidate_count": len(candidates)})
            result = {
                "confirmed": False,
                "request": request,
                "candidates": candidates,
                "external_residue": external_residue,
            }
            result["verification_report"] = self.forget_verification_report(result, request=request, dry_run=True)
            return result

        forgotten = []
        probes: Dict[str, str] = {}
        for candidate in candidates:
            memory_id = str(candidate["memory_id"])
            record = self.store.get_latest(memory_id)
            if record is None:
                continue
            probes[memory_id] = record.content
            tombstone = self.store.tombstone(record, request)
            self.meta.mark_deleted(memory_id, superseded_by=tombstone.version.version_id)
            forgotten.append(memory_id)
        if compact:
            self.store.compact()
        verification = [
            self.verify_forget_probe(memory_id, probe_text=probe)
            for memory_id, probe in probes.items()
        ]
        audit = self._audit(
            "forget",
            None,
            {"request": request, "forgotten": forgotten, "compact": compact},
        )
        result = {
            "confirmed": True,
            "request": request,
            "forgotten": forgotten,
            "external_residue": external_residue,
            "compact": compact,
            "verification": verification,
            "audit_chain_ok": bool(self.meta.verify_audit_chain().get("ok")),
        }
        result["verification_report"] = self.forget_verification_report(result, request=request, dry_run=False)
        result["audit_event_id"] = _audit_event_id(audit)
        return result

    def compile_erasure_intent(self, request: str, context: Optional[Dict[str, object]] = None) -> Dict[str, object]:
        compiled = self.erasure_intent_compiler.compile(request, context=context or {})
        self._audit(
            "compile_erasure_intent",
            None,
            {
                "confidence": compiled["confidence"],
                "memory_types": compiled["memory_types"],
                "scope": compiled["scope"],
                "requires_confirm": compiled["requires_confirm"],
            },
        )
        return compiled

    def forget_by_intent(
        self,
        request: str,
        context: Optional[Dict[str, object]] = None,
        confirm: bool = False,
        compact: bool = True,
    ) -> Dict[str, object]:
        compiled = self.compile_erasure_intent(request, context=context or {})
        agent_filters = self.erasure_intent_compiler.to_agent_filters(compiled)
        use_agent_route = bool(
            agent_filters
            and (
                compiled.get("agent_name")
                or compiled.get("session_id")
                or (compiled.get("scope") or {}).get("level") in {"agent", "session"}
                or compiled.get("preference_topic")
            )
        )
        if use_agent_route:
            result = self.forget_agent_interactions(agent_filters, confirm=confirm, compact=compact)
            route = "agent_interactions"
        else:
            result = self.forget(
                self.erasure_intent_compiler.to_forget_request(compiled),
                confirm=confirm,
                compact=compact,
            )
            route = "managed_memory"
        return {"compiled_intent": compiled, "route": route, "result": result}

    def forget_memory_ids(self, memory_ids: List[str], reason: str, compact: bool = False) -> Dict[str, object]:
        unique_ids: List[str] = []
        for memory_id in memory_ids:
            normalized = str(memory_id).strip()
            if normalized and normalized not in unique_ids:
                unique_ids.append(normalized)

        forgotten: List[str] = []
        missing: List[str] = []
        already_deleted: List[str] = []
        verification: List[Dict[str, object]] = []

        for memory_id in unique_ids:
            row = self.meta.get_memory(memory_id)
            record = self.store.get_latest(memory_id)
            if row is None and record is None:
                missing.append(memory_id)
                continue
            if (row is not None and row.get("deleted")) or (record is not None and record.deleted):
                already_deleted.append(memory_id)
                verification.append(self.verify_forget_probe(memory_id))
                continue
            if record is None:
                missing.append(memory_id)
                continue
            tombstone = self.store.tombstone(record, reason)
            self.meta.mark_deleted(memory_id, superseded_by=tombstone.version.version_id)
            forgotten.append(memory_id)

        if compact and forgotten:
            self.store.compact()

        for memory_id in forgotten:
            verification.append(self.verify_forget_probe(memory_id))

        result = {
            "ok": bool(forgotten) and not missing,
            "confirmed": True,
            "reason": reason,
            "requested_memory_ids": unique_ids,
            "forgotten_memory_ids": forgotten,
            "missing_memory_ids": missing,
            "already_deleted_memory_ids": already_deleted,
            "summary": {
                "requested": len(unique_ids),
                "forgotten": len(forgotten),
                "missing": len(missing),
                "already_deleted": len(already_deleted),
                "compact": compact,
            },
            "verification": verification,
            "external_residue": self._managed_forget_residue(confirm=True),
        }
        audit = self._audit("forget_memory_ids", None, result["summary"] | {"reason": reason, "memory_ids": forgotten})
        result["audit_event_id"] = _audit_event_id(audit)
        result["audit_chain_ok"] = bool(self.meta.verify_audit_chain().get("ok"))
        return result

    def _managed_forget_residue(self, confirm: bool) -> Dict[str, object]:
        return {
            "scope": "confirmed_boundary_notice" if confirm else "dry_run_boundary_notice",
            "managed_store_boundary": True,
            "covered": [
                "memguardos_sqlite_metadata",
                "memguardos_fts_index",
                "memguardos_graph_index",
                "memguardos_mmap_segment_after_compact",
                "memguardos_search_cache",
            ],
            "not_covered": [
                "external_agent_logs",
                "llm_provider_logs",
                "terminal_history",
                "vm_snapshots",
                "filesystem_backups",
                "block_device_forensic_recovery",
            ],
            "requires_separate_cleanup": True,
            "explanation": (
                "MemGuardOS forget removes governed records from MemGuardOS-managed metadata, indexes, graph "
                "and compacted segment storage. External Agent persistence, provider logs, VM snapshots, backups "
                "and media-level sanitization are outside this logical forgetting boundary."
            ),
        }

    def forget_agent_interactions(
        self,
        filters: Dict[str, object],
        confirm: bool = False,
        compact: bool = True,
        external_persistence: bool = True,
    ) -> Dict[str, object]:
        allowed = {
            "agent_name",
            "session_id",
            "turn_id",
            "preference_topic",
            "start_time",
            "end_time",
        }
        clean_filters = {
            key: str(value).strip()
            for key, value in filters.items()
            if key in allowed and str(value).strip()
        }
        if not clean_filters:
            raise ValueError("at least one Agent forgetting scope filter is required")
        impact = self.meta.agent_forget_impact(clean_filters)
        residue = {
            "risk": "external_agent_persistence" if external_persistence else "none_declared",
            "requires_separate_cleanup": bool(external_persistence),
            "explanation": (
                "OpenHands conversation persistence is outside the MemGuardOS managed store "
                "and must be deleted separately when enabled."
                if external_persistence
                else "The caller declared that no external Agent persistence copy is retained."
            ),
        }
        plan = {
            "confirmed": False,
            "filters": clean_filters,
            "affected": impact,
            "external_residue": residue,
        }
        if not confirm:
            audit = self._audit(
                "agent_forget_dry_run",
                None,
                {
                    "filters": clean_filters,
                    "event_count": len(impact["event_ids"]),
                    "memory_count": len(impact["memory_ids"]),
                    "observation_count": impact["observation_count"],
                    "context_usage_count": impact["context_usage_count"],
                    "external_persistence": external_persistence,
                },
            )
            plan["audit_event_id"] = _audit_event_id(audit)
            plan["verification_report"] = self.forget_verification_report(
                plan,
                request="按 Agent 交互范围遗忘",
                dry_run=True,
            )
            return plan

        probes = []
        forgotten_memory_ids = []
        for memory_id in impact["memory_ids"]:
            record = self.store.get_latest(memory_id)
            if record is None:
                continue
            probes.append(record.content)
            tombstone = self.store.tombstone(
                record,
                "agent interaction forget: %s" % json.dumps(clean_filters, ensure_ascii=False),
            )
            self.meta.mark_deleted(memory_id, superseded_by=tombstone.version.version_id)
            forgotten_memory_ids.append(memory_id)
        deleted_observations = self.meta.delete_preference_observations(
            impact["event_ids"],
            str(clean_filters.get("preference_topic") or ""),
        )
        scrubbed_events = self.meta.forget_agent_event_payloads(impact["event_ids"])
        if compact:
            self.store.compact()

        verification_session = "forget-verify-%s" % new_id("session")
        contexts = {
            stage: self.build_agent_context(
                task="验证精准遗忘结果 %s" % str(clean_filters.get("preference_topic") or ""),
                stage=stage,
                agent_name=str(clean_filters.get("agent_name") or "openhands"),
                session_id=verification_session,
            )
            for stage in ("planning", "tool_selection", "response_generation")
        }
        selected_after = {
            stage: [str(item["memory_id"]) for item in result["memories"]]
            for stage, result in contexts.items()
        }
        all_stages_absent = all(
            memory_id not in selected
            for memory_id in forgotten_memory_ids
            for selected in selected_after.values()
        )
        remaining_events = {
            str(item["event_id"]): item
            for item in self.meta.list_agent_events(limit=1000000)
            if str(item["event_id"]) in impact["event_ids"]
        }
        event_payload_scrubbed = all(
            item.get("forgotten")
            and not item.get("content")
            and not item.get("tool_result")
            and not item.get("tool_args")
            for item in remaining_events.values()
        ) and len(remaining_events) == len(impact["event_ids"])
        segment_plaintext_absent = all(
            not probe or not self.store.raw_contains(probe)
            for probe in probes
        )
        verification = {
            "verification_session_id": verification_session,
            "selected_memory_ids_by_stage": selected_after,
            "all_stages_absent": all_stages_absent,
            "event_payload_scrubbed": event_payload_scrubbed,
            "segment_plaintext_absent": segment_plaintext_absent,
        }
        audit = self._audit(
            "agent_forget",
            None,
            {
                "filters": clean_filters,
                "forgotten_memory_ids": forgotten_memory_ids,
                "scrubbed_event_count": scrubbed_events,
                "deleted_observation_count": deleted_observations,
                "compact": compact,
                "verification": verification,
                "external_persistence": external_persistence,
            },
        )
        result = {
            **plan,
            "confirmed": True,
            "forgotten_memory_ids": forgotten_memory_ids,
            "scrubbed_event_count": scrubbed_events,
            "deleted_observation_count": deleted_observations,
            "compact": compact,
            "verification": verification,
            "audit_chain_ok": bool(self.meta.verify_audit_chain().get("ok")),
        }
        result["audit_event_id"] = _audit_event_id(audit)
        result["verification_report"] = self.forget_verification_report(
            result,
            request="按 Agent 交互范围遗忘",
            dry_run=False,
        )
        return result

    def verify_forget(self, memory_id: str) -> Dict[str, object]:
        return self.verify_forget_probe(memory_id)

    def verify_forget_probe(self, memory_id: str, probe_text: str = "") -> Dict[str, object]:
        latest = self.store.get_latest(memory_id, include_deleted=True)
        meta = self.meta.get_memory(memory_id)
        deleted = bool(latest and latest.deleted) or bool(meta and meta.get("deleted"))
        leaked = False
        if meta and meta.get("content"):
            leaked = True
        segment_leak = self.store.raw_contains(probe_text) if probe_text else False
        return {
            "memory_id": memory_id,
            "deleted": deleted,
            "metadata_plaintext_leak": leaked,
            "segment_plaintext_leak": segment_leak,
            "irreversible_ok": deleted and not leaked and not segment_leak,
        }

    def compact(self) -> Dict[str, object]:
        live_count = self.store.compact()
        self._audit("compact", None, {"live_count": live_count})
        return {"compacted": True, "live_count": live_count}

    def audit(self, limit: int = 50, event_type: Optional[str] = None) -> List[Dict[str, object]]:
        return self.meta.list_audit(limit=limit, event_type=event_type)

    def verify_audit(self) -> Dict[str, object]:
        result = self.meta.verify_audit_chain()
        self.journald.log("audit_verify", None, result)
        return result

    def retrieval_status(self) -> Dict[str, object]:
        status = self.retriever.status()
        status["policy"] = {
            "default_backend": self.retriever.requested_backend,
            "kylin_sdk_role": "openkylin_auto_rerank_with_hash_fallback",
            "fallback_behavior": "If Kylin embedding inference fails or times out, retrieval keeps using stable SQLite FTS + hash-vector ranking.",
        }
        self._audit(
            "retrieval_status",
            None,
            {
                "requested_backend": status.get("requested_backend"),
                "last_backend_used": status.get("last_backend_used"),
            },
        )
        return status

    def explain_extraction(self, text: str, source_kind: str = "manual") -> Dict[str, object]:
        clean_text, quality = assess_text_quality(text, source_kind=source_kind)
        if not quality.accepted:
            return {
                "accepted": False,
                "quality": quality.to_dict(),
                "reason": "input quality check rejected the memory",
            }
        sensitivity = self.detector.classify(clean_text, "")
        redacted = self.detector.redact(clean_text, "") if sensitivity.labels else clean_text
        extraction = self.extractor.extract(redacted, source_kind)
        return {
            "accepted": True,
            "source_kind": source_kind,
            "quality": quality.to_dict(),
            "sensitivity": sensitivity.__dict__,
            "redacted_for_extraction": sensitivity.redacted or bool(sensitivity.labels),
            "extraction": extraction.to_dict(),
        }

    def namespace_status(self) -> Dict[str, object]:
        store_path = self.store.path
        db_path = self.meta.path
        patterns_path = self.pattern_path
        configured_home = get_home()
        expected_root = configured_home if self.namespace == DEFAULT_NAMESPACE else configured_home / "namespaces" / self.namespace
        expected_store = expected_root / "memory.segment"
        expected_db = expected_root / "memguard.sqlite3"
        status: Dict[str, object] = {
            "namespace": self.namespace,
            "is_default": self.namespace == DEFAULT_NAMESPACE,
            "home": str(configured_home),
            "namespace_home": str(store_path.parent),
            "storage_root": str(store_path.parent),
            "store_path": str(store_path),
            "db_path": str(db_path),
            "custom_patterns_path": str(patterns_path),
            "store_exists": store_path.exists(),
            "db_exists": db_path.exists(),
            "custom_patterns_exists": patterns_path.exists(),
            "store_bytes": store_path.stat().st_size if store_path.exists() else 0,
            "db_bytes": db_path.stat().st_size if db_path.exists() else 0,
            "uses_custom_paths": store_path != expected_store or db_path != expected_db,
        }
        status["record_count"] = len(
            self.meta.list_memories(include_deleted=True, governance_state=None)
        )
        status["live_record_count"] = len(self.meta.list_memories(include_deleted=False))
        status["quarantined_record_count"] = len(
            self.meta.list_memories(
                include_deleted=False,
                governance_state="quarantined",
            )
        )
        status["audit_event_count"] = len(self.meta.list_audit(limit=1000000))
        self._audit(
            "namespace_status",
            None,
            {
                "namespace_home": status["namespace_home"],
                "record_count": status["record_count"],
                "live_record_count": status["live_record_count"],
                "quarantined_record_count": status["quarantined_record_count"],
            },
        )
        return status

    @staticmethod
    def list_namespaces() -> List[Dict[str, object]]:
        return list_namespaces()

    def resolve_conflicts(self) -> Dict[str, object]:
        result = self.conflicts.resolve_preferences(self.meta.list_memories(include_deleted=False))
        self._audit("conflict_resolve", None, result)
        return result

    def list_patterns(self) -> Dict[str, str]:
        return load_patterns(self.pattern_path)

    def add_pattern(self, name: str, regex: str) -> Dict[str, str]:
        patterns = save_pattern(self.pattern_path, name, regex)
        self.detector = SensitiveDetector(patterns)
        self._audit("pattern_add", None, {"name": name, "regex": regex})
        return patterns

    def try_add_pattern(self, name: str, regex: str) -> Dict[str, object]:
        try:
            patterns = self.add_pattern(name, regex)
            return {"ok": True, "patterns": patterns}
        except PatternValidationError as exc:
            self._audit("pattern_reject", None, {"name": name, "reason": str(exc)})
            return {"ok": False, "reason": str(exc)}

    def suggest_pattern_from_instruction(self, instruction: str, name: Optional[str] = None) -> Dict[str, object]:
        try:
            suggestion = suggest_pattern_from_instruction(instruction, name=name)
            self._audit("pattern_suggest", None, {"name": suggestion["name"], "regex": suggestion["regex"], "instruction": instruction})
            return {"ok": True, "suggestion": suggestion}
        except PatternValidationError as exc:
            self._audit("pattern_suggest_reject", None, {"name": name, "instruction": instruction, "reason": str(exc)})
            return {"ok": False, "reason": str(exc)}

    def add_pattern_from_instruction(self, instruction: str, name: Optional[str] = None) -> Dict[str, object]:
        try:
            suggestion = save_pattern_from_instruction(self.pattern_path, instruction, name=name)
            self.detector = SensitiveDetector(load_patterns(self.pattern_path))
            self._audit("pattern_add_natural", None, {"name": suggestion["name"], "regex": suggestion["regex"], "instruction": instruction})
            return {"ok": True, "suggestion": suggestion}
        except PatternValidationError as exc:
            self._audit("pattern_reject", None, {"name": name, "instruction": instruction, "reason": str(exc)})
            return {"ok": False, "reason": str(exc)}

    def _audit(self, event_type: str, memory_id: Optional[str], detail: Dict[str, object]) -> Dict[str, object]:
        detail = dict(detail)
        detail.setdefault("namespace", self.namespace)
        result = self.meta.add_audit(event_type, memory_id, detail)
        self.journald.log(event_type, memory_id, detail)
        return result

    def _remember_search(self, cache_key: Tuple[str, int, str], results: List[Dict[str, object]]) -> None:
        self._search_cache[cache_key] = [dict(item) for item in results]
        self._search_cache.move_to_end(cache_key)
        while len(self._search_cache) > self._search_cache_limit:
            self._search_cache.popitem(last=False)

    @staticmethod
    def _enrich_graph(record: MemoryRecord) -> None:
        record.entities = extract_entities(record.content + " " + record.summary)
        record.relations = extract_relations(record.entities)

    @staticmethod
    def _classify_memory(text: str) -> str:
        lowered = text.lower()
        preference_markers = ["以后", "偏好", "喜欢", "不要", "请总是", "请先", "请优先", "prefer", "style", "输出口径"]
        security_markers = ["安全策略", "不要保存", "脱敏", "secret", "password", "api key", "私钥", "密钥", "访问令牌"]
        if "tool result:" in lowered:
            return "knowledge"
        if any(marker in lowered for marker in security_markers):
            return "sensitive_policy"
        if any(marker in text or marker in lowered for marker in preference_markers):
            return "preference"
        return "knowledge"

    @staticmethod
    def _classify_subtype(text: str, memory_type: str, source_kind: str) -> str:
        lowered = text.lower()
        if source_kind == "tool_result" or "tool result:" in lowered:
            if "procedural_memory=state_action_result" in lowered:
                return "procedural_trace"
            if "status=failure" in lowered or "exit_code=1" in lowered:
                return "tool_failure_case"
            return "tool_trace"
        if source_kind == "os_event":
            return "os_event"
        if source_kind == "user_behavior":
            if any(marker in lowered for marker in ["rm ", "sudo", "chmod", "chown", "dd ", "mkfs", "git reset"]):
                return "behavior_risk"
            return "behavior_trace"
        if memory_type == "sensitive_policy":
            return "security_policy"
        if memory_type == "preference":
            if any(marker in lowered for marker in ["linux", "rg", "grep", "pytest", "tool", "工具"]):
                return "tool_preference"
            if any(marker in lowered for marker in ["简洁", "详细", "中文", "英文", "style", "回答", "最短", "风险提醒", "口径", "材料", "证据边界"]):
                return "output_style"
            if any(marker in lowered for marker in ["命令"]):
                return "tool_preference"
            return "user_habit"
        if any(marker in lowered for marker in ["template", "模板"]):
            return "template"
        if any(marker in lowered for marker in ["案例", "case", "error", "failure", "修复", "bug", "stderr"]):
            return "case"
        if any(marker in lowered for marker in ["workflow", "流程", "步骤", "systemd", "memorymax", "smoke test", "验证"]):
            return "workflow"
        return "fact"

    @staticmethod
    def _summarize(text: str, memory_type: str) -> str:
        prefix = {
            "preference": "用户偏好",
            "knowledge": "知识记忆",
            "sensitive_policy": "安全策略",
        }.get(memory_type, "记忆")
        clean = " ".join(text.strip().split())
        return "%s：%s" % (prefix, clean[:140])

    @staticmethod
    def _default_tier(memory_type: str, source_kind: str) -> str:
        if source_kind == "os_event":
            return "working"
        if source_kind == "user_behavior":
            return "session"
        if memory_type in {"preference", "sensitive_policy", "knowledge"}:
            return "long_term"
        return "session"

    @staticmethod
    def _row_sensitivity(row: Dict[str, object]) -> Dict[str, object]:
        raw = row.get("sensitivity")
        if isinstance(raw, dict):
            return raw
        labels = row.get("sensitivity_labels", [])
        if isinstance(labels, str):
            try:
                import json

                labels = json.loads(labels)
            except Exception:
                labels = [labels] if labels else []
        return {
            "level": row.get("sensitivity_level", "none"),
            "labels": labels if isinstance(labels, list) else [],
            "redacted": bool(row.get("sensitivity_redacted", False)),
        }

    @staticmethod
    def _path_size(path: Path) -> int:
        try:
            return path.stat().st_size if path.exists() else 0
        except OSError:
            return 0

    @staticmethod
    def _percentile(values: List[float], q: float) -> float:
        if not values:
            return 0.0
        ordered = sorted(values)
        index = min(len(ordered) - 1, max(0, int(round((len(ordered) - 1) * q))))
        return ordered[index]

    @staticmethod
    def _memory_policy_recommendations(tiers: Dict[str, Dict[str, object]], stale_count: int, total_bytes: int) -> List[str]:
        recommendations = []
        working_count = int(tiers.get("working", {}).get("count", 0))
        session_count = int(tiers.get("session", {}).get("count", 0))
        long_term_count = int(tiers.get("long_term", {}).get("count", 0))
        if working_count:
            recommendations.append("working 层存在临时记忆，任务结束后优先执行 evict-tier working 或挑选 promote 到 long_term。")
        if session_count:
            recommendations.append("session 层存在阶段性记忆，建议在任务结束后按项目、会话或时间窗口进行淘汰或沉淀。")
        if stale_count:
            recommendations.append("存在已删除或旧版本记录，建议执行 compact 以压缩 mmap 段文件并清理旧明文。")
        if total_bytes > 64 * 1024 * 1024:
            recommendations.append("当前存储超过 64MB，建议增加 namespace 拆分、压缩和更严格的 working/session 淘汰策略。")
        if long_term_count == 0:
            recommendations.append("long_term 层为空，RAG 复用能力不足，建议沉淀稳定偏好、知识和安全策略。")
        if not recommendations:
            recommendations.append("当前分层、存储和淘汰状态健康，可继续按默认策略运行。")
        return recommendations


def _audit_event_id(audit: object) -> str:
    if isinstance(audit, dict) and audit.get("audit_id") is not None:
        return "audit-%s" % audit["audit_id"]
    return ""
