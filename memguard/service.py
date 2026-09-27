from pathlib import Path
from collections import OrderedDict
from concurrent.futures import ThreadPoolExecutor, as_completed
from time import perf_counter
from typing import Dict, List, Optional, Tuple, Union
import json
import math
import os
import re

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
from memguard.extraction.fact_slot import parse_fact_slot
from memguard.feedback import (
    build_forget_verification_report,
    build_memory_change_summary,
    build_preference_effect_proof,
    build_sensitive_block_notice,
    memory_summary_for_user,
    merge_memory_change_summaries,
    notifications_from_audit,
)
from memguard.extraction.semantic import HybridMemoryExtractor
from memguard.forgetting.natural_intent import ErasureIntentCompiler
from memguard.forgetting.plan import ErasurePlan
from memguard.forgetting.planner import ForgetPlanner
from memguard.governance.trust import assess_memory_source
from memguard.ingestion.behavior import BehaviorEvent, load_shell_history
from memguard.ingestion.config import config_entry_to_memory_text, load_config_entries, validate_config_entry
from memguard.ingestion.quality import assess_text_quality
from memguard.ingestion.tool_preference import ToolPreferenceMiner
from memguard.ingestion.tool_result import ToolResult
from memguard.integrations.events import AgentInteractionEvent
from memguard.integrations.evidence_spans import exact_source_span, preference_source_span
from memguard.integrations.adherence import (
    final_visible_output,
    response_starts_with_command_or_conclusion,
    successful_terminal_commands,
)
from memguard.integrations.context_injector import (
    stage_allows_memory,
    stage_allows_preference,
    validate_agent_context_stage,
)
from memguard.integrations.habit_aggregator import PreferenceAggregate
from memguard.integrations.interaction_extractor import (
    InteractionExtractionResult,
    PreferenceObservation,
    extract_interaction_preferences,
)
from memguard.integrations.preference_enhancer import PreferenceEnhancer
from memguard.eval.live_comparison import STAGES as LIVE_COMPARISON_STAGES, build_comparison as build_live_comparison
from memguard.integrations.normalizer import normalize_event, normalize_events
from memguard.models import AuditInfo, MemoryRecord, MemorySource, VersionInfo, make_memory, new_id, stable_hash, utc_now
from memguard.natural_language import explicitly_requests_no_store, infer_retention_tier, looks_like_question
from memguard.os_guard.events import parse_os_event
from memguard.os_guard.cgroup import cgroup_status
from memguard.os_guard.journald import JournaldLogger
from memguard.retrieval.conflict import ConflictResolver, preference_topic as inferred_preference_topic
from memguard.retrieval.fusion import QueryIntent, classify_query_intent, rrf_fuse
from memguard.retrieval.hybrid import HybridRetriever, cosine, expand_query_aliases, normalize_vector, tokenize_literal
from memguard.retrieval.vector_cache import vector_content_hash
from memguard.sensitivity.custom_patterns import PatternValidationError, load_patterns, save_pattern, save_pattern_from_instruction, suggest_pattern_from_instruction
from memguard.sensitivity.detector import SensitiveDetector
from memguard.store.mmap_store import AppendOnlyMemoryStore
from memguard.store.sqlite_meta import FactSlotConflict, SQLiteMetaStore, fact_slot_scope


class MemGuardService:
    EMBEDDING_BACKEND = "kylin_sdk"
    HIT_FLUSH_THRESHOLD = 512
    EMBEDDING_MODEL_REVISION = "kylin-sdk-v1"

    def __init__(self, store_path: Optional[Path] = None, db_path: Optional[Path] = None, namespace: Optional[str] = None):
        self.namespace = normalize_namespace(namespace)
        self.store = AppendOnlyMemoryStore(store_path or default_store_path(self.namespace))
        self.meta = SQLiteMetaStore(
            db_path or default_db_path(self.namespace), expected_namespace=self.namespace,
        )
        try:
            self.meta.bind_namespace(self.namespace)
            self.store.bind_namespace(self.namespace)
        except Exception:
            self.meta.close()
            raise
        if store_path is not None or db_path is not None:
            self.pattern_path = (Path(db_path) if db_path is not None else Path(store_path)).parent / "sensitive_patterns.json"
        else:
            self.pattern_path = custom_patterns_path(self.namespace)
        self.detector = SensitiveDetector(load_patterns(self.pattern_path))
        self.extractor = HybridMemoryExtractor()
        self.preference_enhancer = PreferenceEnhancer()
        self.retriever = HybridRetriever()
        self.dense_candidates_enabled = os.environ.get("MEMGUARD_DENSE_CANDIDATES", "").strip() == "1"
        self.conflicts = ConflictResolver()
        self.forget_planner = ForgetPlanner()
        self.erasure_intent_compiler = ErasureIntentCompiler()
        self.tool_preference_miner = ToolPreferenceMiner()
        self.journald = JournaldLogger()
        self._search_cache: OrderedDict[Tuple[str, int, str, str, str], List[Dict[str, object]]] = OrderedDict()
        self._search_cache_limit = 128
        self._last_search_timings: Dict[str, float] = {}
        self._pending_hits: List[str] = []
        self._recover_pending_erasures()
        self._recover_pending_memory_writes()
        permission_corrections = [
            *getattr(self.store, "permission_corrections", []),
            *getattr(self.meta, "permission_corrections", []),
        ]
        if permission_corrections:
            self._audit(
                "storage_permissions_corrected",
                None,
                {"corrections": permission_corrections},
            )

    def close(self) -> None:
        self.flush_memory_hits()
        self.meta.close()

    def _recover_pending_erasures(self) -> Dict[str, object]:
        """Finish SQLite-committed erasures before exposing service operations."""

        pending = self.meta.pending_erasures()
        if not pending:
            return {"recovered": 0, "storage_cleanup": None}
        physical_ids = [
            str(row["memory_id"]) for row in pending if row["compact_required"]
        ]
        for row in pending:
            if row["compact_required"]:
                continue
            memory_id = str(row["memory_id"])
            record = self.store.get_latest(memory_id, include_deleted=True)
            if record is not None and not record.deleted:
                self.store.tombstone(
                    record, "resume interrupted erasure",
                    version_id=str(row["tombstone_version_id"]),
                )
        if physical_ids:
            self.store.purge_ids(physical_ids)
        cleanup = self.meta.secure_cleanup(vacuum=bool(physical_ids))
        self.meta.finish_memory_erasures([str(row["memory_id"]) for row in pending])
        self._invalidate_search_cache()
        return {"recovered": len(pending), "storage_cleanup": cleanup}

    def _begin_memory_erasure(
        self, record: MemoryRecord, *, compact: bool,
    ) -> Dict[str, int]:
        return self.meta.begin_memory_erasure(
            record.memory_id, new_id("ver"), compact=compact,
            plaintext=record.content,
            expected_version_id=record.version.version_id,
        )

    def _begin_memory_erasures(
        self, records: List[MemoryRecord], *, compact: bool,
    ) -> Dict[str, int]:
        return self.meta.begin_memory_erasures([
            {
                "memory_id": record.memory_id,
                "tombstone_version_id": new_id("ver"),
                "compact": compact,
                "plaintext": record.content,
                "expected_version_id": record.version.version_id,
            }
            for record in records
        ])

    def _recover_pending_memory_writes(self) -> int:
        pending = self.meta.pending_memory_writes()
        for item in pending:
            try:
                if item["expected_version_id"]:
                    self.meta.commit_staged_existing_memory_write(
                        str(item["version_id"]), self.store.append_version_once,
                    )
                else:
                    self.meta.commit_staged_new_memory_write(
                        str(item["version_id"]), self._append_staged_record_once,
                    )
            except FactSlotConflict:
                continue
            except ValueError:
                current = self.meta.get_memory(str(item["memory_id"]))
                if current is None or current["version_id"] != item["version_id"]:
                    raise
                if any(
                    row["version_id"] == item["version_id"]
                    for row in self.meta.pending_memory_writes()
                ):
                    raise
        return len(pending)

    def _append_staged_record_once(self, record: MemoryRecord) -> MemoryRecord:
        latest = self.store.get_latest(record.memory_id, include_deleted=True)
        if latest is not None and latest.version.version_id == record.version.version_id:
            if latest.to_json() != record.to_json():
                raise ValueError("staged memory payload differs from segment")
            return latest
        if latest is not None:
            raise ValueError("segment memory id differs from staged new write")
        return self.store.append(record)

    def _peer_committed_record(self, record: MemoryRecord) -> bool:
        current = self.meta.get_memory(record.memory_id)
        if current is None or current["version_id"] != record.version.version_id:
            return False
        if any(
            row["version_id"] == record.version.version_id
            for row in self.meta.pending_memory_writes()
        ):
            return False
        latest = self.store.get_latest(record.memory_id, include_deleted=True)
        return latest is not None and latest.to_json() == record.to_json()

    def _persist_new_memory(
        self, record: MemoryRecord,
        source_link: Optional[Dict[str, object]] = None,
    ) -> MemoryRecord:
        content_hash = vector_content_hash(record.summary, record.content)
        self.meta.stage_new_memory_write(
            record, self.EMBEDDING_BACKEND, self.EMBEDDING_MODEL_REVISION,
            content_hash, source_link=source_link,
        )
        try:
            return self.meta.commit_staged_new_memory_write(
                record.version.version_id, self._append_staged_record_once,
            )
        except ValueError:
            if self._peer_committed_record(record):
                return record
            raise

    def _persist_existing_memory(
        self, previous: MemoryRecord, record: MemoryRecord,
        source_link: Optional[Dict[str, object]] = None,
    ) -> MemoryRecord:
        self.meta.stage_existing_memory_write(
            previous, record, self.EMBEDDING_BACKEND,
            self.EMBEDDING_MODEL_REVISION,
            vector_content_hash(record.summary, record.content),
            source_link=source_link,
        )
        try:
            return self.meta.commit_staged_existing_memory_write(
                record.version.version_id, self.store.append_version_once,
            )
        except ValueError:
            if self._peer_committed_record(record):
                return record
            raise

    def enqueue_missing_embedding_jobs(
        self, scan_limit: int = 100
    ) -> Dict[str, object]:
        return self.meta.enqueue_missing_embedding_jobs(
            self.EMBEDDING_BACKEND,
            self.EMBEDDING_MODEL_REVISION,
            scan_limit,
        )

    def embedding_cache_status(self) -> Dict[str, object]:
        status = self.meta.embedding_cache_status(
            self.EMBEDDING_BACKEND,
            self.EMBEDDING_MODEL_REVISION,
        )
        return {
            "backend": self.EMBEDDING_BACKEND,
            "model_revision": self.EMBEDDING_MODEL_REVISION,
            **status,
        }

    def backfill_embeddings(
        self,
        limit: int = 100,
        timeout_seconds: float = 30.0,
    ) -> Dict[str, object]:
        started = perf_counter()
        deadline = started + max(0.05, float(timeout_seconds))
        effective_limit = max(1, min(int(limit), 1000))
        scan_report = self.meta.enqueue_missing_embedding_jobs(
            self.EMBEDDING_BACKEND,
            self.EMBEDDING_MODEL_REVISION,
            effective_limit,
        )
        rows = self.meta.list_pending_embeddings(
            self.EMBEDDING_BACKEND,
            self.EMBEDDING_MODEL_REVISION,
            effective_limit,
        )
        provider = self.retriever.kylin_provider
        ready = 0
        failed = 0
        memory_ids: List[str] = []
        failures: List[Dict[str, str]] = []

        for row in rows:
            remaining = deadline - perf_counter()
            if remaining <= 0:
                break
            text = "%s\n%s" % (row["summary"], row["content"])
            vector = None
            failure_reason = "Kylin SDK 未在时间预算内返回有效向量"
            if provider is None:
                failure_reason = "Kylin SDK 向量提供器未启用"
            else:
                try:
                    vector = provider.vector(text, timeout_seconds=remaining)
                except Exception as exc:  # SDK boundary: isolate one failed job.
                    failure_reason = "Kylin SDK 回填失败：%s" % str(exc)[:240]

            completed = False
            if vector:
                try:
                    completed = self.meta.complete_memory_embedding(
                        row["memory_id"],
                        row["content_hash"],
                        self.EMBEDDING_BACKEND,
                        self.EMBEDDING_MODEL_REVISION,
                        vector,
                        expected_attempt_count=row["attempt_count"],
                        expected_job_token=row["job_token"],
                    )
                except (TypeError, ValueError) as exc:
                    failure_reason = "Kylin SDK 返回非法向量：%s" % str(exc)[:240]
            if completed:
                ready += 1
                memory_ids.append(row["memory_id"])
                continue

            marked_failed = self.meta.mark_embedding_failed(
                row["memory_id"],
                row["content_hash"],
                self.EMBEDDING_BACKEND,
                self.EMBEDDING_MODEL_REVISION,
                failure_reason,
                expected_attempt_count=row["attempt_count"],
                expected_job_token=row["job_token"],
            )
            if marked_failed:
                failed += 1
                failures.append(
                    {
                        "memory_id": row["memory_id"],
                        "reason": failure_reason,
                    }
                )

        status = self.embedding_cache_status()
        queue_remaining = int(status["pending"]) + int(status["failed"])
        due_remaining = int(status["due"])
        scan_complete = bool(scan_report["wrapped"])
        return {
            "backend": self.EMBEDDING_BACKEND,
            "model_revision": self.EMBEDDING_MODEL_REVISION,
            "processed": ready + failed,
            "ready": ready,
            "failed": failed,
            "queue_remaining": queue_remaining,
            "due_remaining": due_remaining,
            "retry_deferred": int(status["retry_deferred"]),
            "next_retry_at": status["next_retry_at"],
            "scan_complete": scan_complete,
            "remaining": queue_remaining if scan_complete else None,
            "has_more": (not scan_complete) or due_remaining > 0,
            "scan": scan_report,
            "elapsed_ms": round((perf_counter() - started) * 1000.0, 3),
            "memory_ids": memory_ids,
            "failures": failures,
        }

    def ingest_text(
        self, text: str, source_kind: str = "manual", source_uri: str = "",
        tier: Optional[str] = None,
        source_link: Optional[Dict[str, object]] = None,
        retention_session_id: str = "",
    ) -> Dict[str, object]:
        self._recover_pending_memory_writes()
        if source_link and source_link.get("relation") != "source_clause":
            raise ValueError("unsupported source relation")
        safe_source_uri = self._safe_provenance_uri(source_uri)
        if explicitly_requests_no_store(text):
            audit = self._audit(
                "user_no_store",
                None,
                {
                    "source_kind": source_kind,
                    "source_uri_hash": stable_hash(source_uri) if source_uri else "",
                    "reason": "user explicitly requested that this content not be stored",
                },
            )
            result = {
                "stored": False,
                "reason": "user_requested_no_memory",
                "audit_event_id": _audit_event_id(audit),
            }
            result["feedback"] = build_memory_change_summary(
                result,
                namespace=self.namespace,
                source=source_kind,
                session_id=safe_source_uri,
                audit_event_ids=[result["audit_event_id"]],
            ).to_dict()
            return result
        clean_text, quality = assess_text_quality(text, source_kind=source_kind)
        if not quality.accepted:
            detail = {
                "source_kind": source_kind,
                "source_uri_hash": stable_hash(source_uri) if source_uri else "",
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
                session_id=safe_source_uri,
                audit_event_ids=[result["audit_event_id"]],
            ).to_dict()
            return result

        sensitivity = self.detector.classify(clean_text, source_uri)
        if sensitivity.level == "high":
            detail = {
                "source_kind": source_kind,
                "source_uri": safe_source_uri,
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
                session_id=safe_source_uri,
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
        effective_tier = tier or infer_retention_tier(clean_text) or self._default_tier(memory_type, source_kind)
        if effective_tier not in {"working", "session", "long_term"}:
            raise ValueError("invalid memory tier")
        session_scope = str(retention_session_id or "").strip() if effective_tier in {"working", "session"} else ""
        if effective_tier in {"working", "session"} and not session_scope:
            return {"stored": False, "reason": "session_required", "tier": effective_tier}
        existing = self.meta.find_active_memory_by_content(
            stored_text, self.namespace, tier=effective_tier, retention_session_id=session_scope,
        )
        fact = parse_fact_slot(stored_text) if memory_type == "knowledge" and not sensitivity.labels else None
        if fact is not None:
            candidates = self.meta.find_active_fact_candidates(
                fact_slot_scope(self.namespace, session_scope), fact.subject, fact.predicate,
                tier=effective_tier,
            )
            if len(candidates) > 1:
                return self._fact_review_required(
                    fact.predicate, candidates, source_kind, safe_source_uri,
                )
            if candidates:
                current = parse_fact_slot(str(candidates[0]["content"]))
                if current is not None and current.value == fact.value:
                    existing = candidates[0]
                elif (
                    fact.explicit_update
                    and source_kind in {"manual", "user_cli", "api"}
                    and governance.governance_state == "active"
                ):
                    updated = self.update_memory(
                        str(candidates[0]["memory_id"]), fact.canonical_content,
                        reason="explicit fact correction", source_link=source_link,
                    )
                    if updated.get("updated"):
                        result = {
                            **updated, "stored": True, "fact_update": True,
                            "quality": quality.to_dict(),
                            "extraction": extraction.to_dict(),
                        }
                        result["feedback"] = build_memory_change_summary(
                            result, namespace=self.namespace, source=source_kind,
                            session_id=safe_source_uri,
                            audit_event_ids=[str(updated.get("audit_event_id") or "")],
                        ).to_dict()
                        return result
                    return {"stored": False, **updated}
                else:
                    return self._fact_review_required(
                        fact.predicate, candidates, source_kind, safe_source_uri,
                    )
        if existing is not None:
            existing_record = self.store.get_latest(str(existing["memory_id"]))
            if existing_record is not None:
                if source_link:
                    linked = self.meta.link_memory_event(
                        existing_record.memory_id,
                        str(source_link.get("event_id") or ""),
                        str(source_link.get("relation") or ""),
                        source=source_link.get("source"),
                    )
                    if not linked["linked"]:
                        raise ValueError("source event is unavailable")
                audit = self._audit(
                    "ingest_duplicate",
                    existing_record.memory_id,
                    {
                        "source_kind": source_kind,
                        "source_uri": safe_source_uri,
                        "existing_source_kind": existing.get("source_kind", ""),
                        "existing_source_uri": existing.get("source_uri", ""),
                        "reason": "identical active memory already exists",
                    },
                )
                result = {
                    "stored": False,
                    "duplicate": True,
                    "reason": "duplicate_memory_reused",
                    "record": existing_record,
                    "quality": quality.to_dict(),
                    "extraction": extraction.to_dict(),
                    "governance": governance.to_dict(),
                    "audit_event_id": _audit_event_id(audit),
                }
                result["feedback"] = build_memory_change_summary(
                    result,
                    namespace=self.namespace,
                    source=source_kind,
                    session_id=safe_source_uri,
                    audit_event_ids=[result["audit_event_id"]],
                ).to_dict()
                return result
        record = make_memory(
            content=stored_text,
            memory_type=memory_type,
            source=MemorySource(kind=source_kind, uri=safe_source_uri),
            summary=extraction.summary,
            subtype=subtype,
            scope=self.namespace,
            tier=effective_tier,
            retention_session_id=session_scope,
            sensitivity=sensitivity,
            trust_level=governance.trust_level,
            trust_score=governance.trust_score,
            governance_state=governance.governance_state,
            governance_reason=governance.reason,
        )
        record.confidence = min(record.confidence, quality.score, extraction.confidence)
        self._enrich_graph(record)
        try:
            record = self._persist_new_memory(record, source_link=source_link)
        except (FactSlotConflict, ValueError) as exc:
            if fact is None or (
                not isinstance(exc, FactSlotConflict)
                and str(exc) != "staged memory write not found"
            ):
                raise
            candidates = self.meta.find_active_fact_candidates(
                fact_slot_scope(self.namespace, session_scope), fact.subject, fact.predicate,
                tier=effective_tier,
            )
            if not candidates:
                raise
            return self._fact_review_required(
                fact.predicate, candidates, source_kind, safe_source_uri,
            )
        audit = self._audit(
            "ingest",
            record.memory_id,
            {
                "type": record.type,
                "subtype": record.subtype,
                "tier": record.tier,
                "source_kind": source_kind,
                "source_uri": safe_source_uri,
                "sensitivity": record.sensitivity.level,
                "labels": record.sensitivity.labels,
                "quality": quality.to_dict(),
                "extraction": {
                    key: value
                    for key, value in extraction.to_dict().items()
                    if key != "summary"
                },
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
            session_id=safe_source_uri,
            audit_event_ids=[result["audit_event_id"]],
        ).to_dict()
        return result

    def _fact_review_required(
        self, predicate: str, candidates: List[Dict[str, object]],
        source_kind: str, safe_source_uri: str,
    ) -> Dict[str, object]:
        ids = [str(item["memory_id"]) for item in candidates]
        audit = self._audit(
            "fact_conflict_requires_review", None,
            {"predicate": predicate, "candidate_memory_ids": ids, "source_kind": source_kind},
        )
        result = {
            "stored": False, "reason": "fact_conflict_requires_review",
            "candidate_memory_ids": ids,
            "audit_event_id": _audit_event_id(audit),
        }
        result["feedback"] = build_memory_change_summary(
            result, namespace=self.namespace, source=source_kind,
            session_id=safe_source_uri, audit_event_ids=[result["audit_event_id"]],
        ).to_dict()
        return result

    def ingest_natural_memory(
        self,
        text: str,
        *,
        source_kind: str = "user_cli",
        source_uri: str = "memguard",
        session_id: str = "",
        tier: Optional[str] = None,
    ) -> Dict[str, object]:
        """Ingest natural language through one semantic and safety decision path.

        Actionable user preferences become structured Agent preferences rather
        than generic text records.  Knowledge, cases and workflows continue
        through the general extractor.  Both paths use the same sensitivity,
        provenance and feedback contracts.
        """

        requested_tier = tier or infer_retention_tier(text)
        if requested_tier in {"working", "session"} and not str(session_id or "").strip():
            return {"stored": False, "reason": "session_required", "tier": requested_tier}
        effective_session_id = str(session_id or new_id("session"))
        event_text = str(text or "")
        if requested_tier in {"working", "session"} and not any(
            marker in event_text for marker in ("本轮", "本次会话", "当前会话", "这个会话")
        ):
            event_text = "%s（仅在本次会话生效）" % event_text
        event = AgentInteractionEvent(
            agent_name="memguardos",
            agent_version="product",
            session_id=effective_session_id,
            turn_id=new_id("turn"),
            sequence=1,
            event_type="user_message",
            role="user",
            content=event_text,
            source_uri=str(source_uri or source_kind),
            event_id=new_id("evt"),
        )
        extraction = extract_interaction_preferences(event)
        if extraction.observations or not extraction.remember_allowed:
            interaction = self.ingest_interaction(event)
            safe_event, _ = self._sanitize_interaction_event(
                normalize_event(event), extraction.remember_allowed,
            )
            memory_ids = [str(item) for item in interaction.get("memory_ids") or []]
            sensitivity = dict(interaction.get("sensitivity") or {})
            knowledge_results = []
            if extraction.remember_allowed and sensitivity.get("level") != "high":
                clauses = [part.strip() for part in re.split(r"[，,。；;\n]+", event_text) if part.strip()]
                if len(clauses) > 1:
                    for clause in clauses:
                        clause_event = AgentInteractionEvent.from_dict(
                            {**event.to_dict(), "content": clause, "provenance_hash": ""}
                        )
                        if extract_interaction_preferences(clause_event).observations:
                            continue
                        if not _independent_fact_clause(clause):
                            continue
                        span = exact_source_span(safe_event, clause)
                        source = (
                            span.to_dict()
                            if span and not self.detector.classify(clause, source_uri).labels
                            else None
                        )
                        knowledge = self.ingest_text(
                            clause, source_kind=source_kind, source_uri=source_uri, tier=requested_tier,
                            retention_session_id=effective_session_id,
                            source_link={
                                "event_id": event.event_id,
                                "relation": "source_clause",
                                "source": source,
                            },
                        )
                        knowledge_results.append(knowledge)
                        if knowledge.get("stored"):
                            memory_id = knowledge["record"].memory_id
                            memory_ids.append(memory_id)
            fact_conflict_ids = list(dict.fromkeys(
                str(memory_id)
                for item in knowledge_results
                for memory_id in item.get("candidate_memory_ids") or []
            ))
            records = [self.meta.get_memory(memory_id) for memory_id in memory_ids]
            records = [record for record in records if record]
            reason = ""
            if sensitivity.get("level") == "high":
                reason = "sensitive_block"
            elif not interaction.get("remember_allowed"):
                reason = "user_requested_no_memory"
            elif fact_conflict_ids and not memory_ids:
                reason = "fact_conflict_requires_review"
            elif not memory_ids:
                reason = "interaction_not_promoted"
            feedback_parts = [interaction.get("feedback") or {}]
            feedback_parts.extend(item.get("feedback") or {} for item in knowledge_results)
            return {
                "stored": bool(memory_ids),
                "updated": any(item.get("updated") for item in knowledge_results),
                "partial_conflict": bool(fact_conflict_ids),
                "candidate_memory_ids": fact_conflict_ids,
                "event_stored": bool(interaction.get("stored")),
                "reason": reason,
                "record": records[0] if records else None,
                "records": records,
                "memory_ids": memory_ids,
                "feedback": merge_memory_change_summaries(
                    feedback_parts, namespace=self.namespace, source=source_kind, session_id=effective_session_id
                ),
                "sensitive_notice": interaction.get("sensitive_notice"),
                "sensitivity": sensitivity,
                "audit_event_id": interaction.get("audit_event_id", ""),
                "preference_updates": interaction.get("preference_updates") or [],
                "preference_withdrawals": interaction.get("preference_withdrawals") or [],
                "natural_intent": "mixed_preference_knowledge" if knowledge_results else "structured_preference",
                "knowledge_results": knowledge_results,
            }
        result = self.ingest_text(
            text,
            source_kind=source_kind,
            source_uri=source_uri,
            tier=requested_tier,
            retention_session_id=effective_session_id,
        )
        sensitivity = result.get("sensitivity")
        if sensitivity is not None and not isinstance(sensitivity, dict):
            result["sensitivity"] = dict(getattr(sensitivity, "__dict__", {}))
        result["natural_intent"] = "memory_content"
        return result

    def ingest_tool_result(
        self, tool_result: ToolResult, source_uri: str = "tool_result", session_id: str = "",
    ) -> Dict[str, object]:
        return self.ingest_text(
            tool_result.to_memory_text(),
            source_kind="tool_result",
            source_uri=source_uri,
            tier="session" if tool_result.exit_code == 0 else "working",
            retention_session_id=session_id,
        )

    def ingest_tool_results(
        self, tool_results: List[ToolResult], source_uri: str = "tool_results", session_id: str = "",
    ) -> Dict[str, object]:
        if len(tool_results) > 200:
            raise ValueError("tool_results must contain at most 200 items")
        if not str(session_id or "").strip():
            return {
                "stored_count": 0, "preference_count": 0, "experience_count": 0,
                "results": [], "preferences": [], "experience_candidates": [],
                "reason": "session_required",
            }
        stored_results = [
            self.ingest_tool_result(tool_result, source_uri="%s#%s" % (source_uri, index + 1), session_id=session_id)
            for index, tool_result in enumerate(tool_results)
        ]
        experience_results = []
        for candidate in self.tool_preference_miner.mine(tool_results):
            experience_results.append(
                self.ingest_text(
                    candidate.to_memory_text(),
                    source_kind="tool_result",
                    source_uri="%s#tool-experience:%s:%s" % (source_uri, candidate.domain, candidate.preferred_tool),
                    tier="long_term",
                )
            )
        return {
            "stored_count": sum(1 for item in stored_results if item.get("stored")),
            "preference_count": 0,
            "experience_count": sum(1 for item in experience_results if item.get("stored")),
            "results": stored_results,
            "preferences": [],
            "experience_candidates": experience_results,
        }

    def ingest_interaction(self, event: Union[AgentInteractionEvent, Dict[str, object]]) -> Dict[str, object]:
        self._recover_pending_memory_writes()
        parsed = event if isinstance(event, AgentInteractionEvent) else AgentInteractionEvent.from_dict(event)
        normalized = normalize_event(parsed)
        # 偏好提取用原文，避免低风险打码（例如 systemd 单元名被邮箱规则命中）
        # 破坏正常抽取；写入记忆时再对偏好值单独脱敏，见 _store_preference_observation。
        extraction = extract_interaction_preferences(normalized)
        safe_event, sensitivity = self._sanitize_interaction_event(
            normalized,
            remember_allowed=extraction.remember_allowed,
        )
        # Optional semantic enhancement may invoke an external process/model.
        # It must never receive an unclassified secret. High-risk content is
        # blocked locally; medium/low findings are redacted before enhancement.
        if sensitivity.level != "high":
            extraction = self._augment_with_enhancer(extraction, safe_event)
        if extraction.observations:
            safe_observations = [
                item for item in extraction.observations
                if not self.detector.classify(item.value).labels
            ]
            if len(safe_observations) != len(extraction.observations):
                extraction = InteractionExtractionResult(
                    remember_allowed=extraction.remember_allowed,
                    reason=extraction.reason,
                    observations=safe_observations,
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
        memory_observations: Dict[str, PreferenceObservation] = {}
        preference_updates = []
        preference_withdrawals = []
        if result["stored"] and extraction.remember_allowed and sensitivity.level != "high":
            for observation in extraction.observations:
                span = preference_source_span(safe_event, observation)
                self.meta.add_preference_observation(
                    observation, safe_event,
                    source=span.to_dict() if span else None,
                )
                if observation.polarity == "negative" and observation.explicit:
                    withdrawal = self._adjudicate_preference_withdrawal(observation, safe_event)
                    if withdrawal:
                        preference_withdrawals.append(withdrawal)
                    continue
                aggregate_row = self._preference_aggregate(observation, safe_event)
                if aggregate_row and aggregate_row["stable"] and not aggregate_row.get("promoted_memory_id"):
                    memory = self._store_preference_observation(observation, safe_event)
                    memory_ids.append(memory.memory_id)
                    memory_observations[memory.memory_id] = observation
                    update = self._adjudicate_preference_update(observation, memory)
                    if update:
                        preference_updates.append(update)
        feedback_parts = []
        for memory_id in memory_ids:
            row = self.meta.get_memory(memory_id)
            if row:
                observation = memory_observations[memory_id]
                update = next(
                    (
                        item
                        for item in preference_updates
                        if str(item.get("new_memory_id") or "") == memory_id
                    ),
                    None,
                )
                feedback_parts.append(
                    build_memory_change_summary(
                        {
                            "stored": update is None,
                            "updated": update is not None,
                            "record": row,
                            "extraction": {
                                "reason": (
                                    "本轮用户明确要求"
                                    if observation.explicit
                                    else "多轮行为证据达到稳定阈值"
                                ),
                                "explicit": observation.explicit,
                                "confidence": observation.confidence,
                                "polarity": observation.polarity,
                            },
                        },
                        namespace=self.namespace,
                        source="agent",
                        session_id=safe_event.session_id,
                        audit_event_ids=[_audit_event_id(audit)],
                    ).to_dict()
                )
        for withdrawal in preference_withdrawals:
            feedback_parts.append(
                build_memory_change_summary(
                    {"preference_withdrawal": withdrawal},
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
            skipped_reason = (
                "sensitive_block"
                if sensitivity.level == "high"
                else (
                    "user_requested_no_memory"
                    if not extraction.remember_allowed
                    else "interaction_not_promoted"
                )
            )
            feedback = build_memory_change_summary(
                {
                    "stored": False,
                    "reason": skipped_reason,
                    "sensitivity": sensitivity,
                },
                namespace=self.namespace,
                source="agent",
                session_id=safe_event.session_id,
                audit_event_ids=[_audit_event_id(audit)],
            ).to_dict()
        observed_preferences = []
        if extraction.remember_allowed and sensitivity.level != "high":
            for observation in extraction.observations:
                aggregate = self._preference_aggregate(observation, safe_event) or {}
                created = next(
                    (
                        memory_id
                        for memory_id, item in memory_observations.items()
                        if item.topic == observation.topic
                        and item.value == observation.value
                        and item.scope == observation.scope
                    ),
                    "",
                )
                withdrawn = any(
                    item.get("topic") == observation.topic
                    and item.get("value") == observation.value
                    and item.get("scope") == observation.scope
                    for item in preference_withdrawals
                )
                if created:
                    status = "updated" if any(
                        str(item.get("new_memory_id") or "") == created
                        for item in preference_updates
                    ) else "saved"
                elif withdrawn:
                    status = "withdrawn"
                elif aggregate.get("stable") and aggregate.get("promoted_memory_id"):
                    status = "reinforced"
                elif observation.polarity == "negative":
                    status = "negative_observation"
                else:
                    status = "candidate"
                observed_preferences.append(
                    {
                        "topic": observation.topic,
                        "value": observation.value,
                        "scope": observation.scope,
                        "polarity": observation.polarity,
                        "explicit": observation.explicit,
                        "confidence": round(float(observation.confidence), 3),
                        "evidence_count": int(aggregate.get("evidence_count") or 1),
                        "distinct_sessions": int(aggregate.get("distinct_sessions") or 1),
                        "status": status,
                        "status_label": {
                            "saved": "已保存",
                            "updated": "已更新",
                            "withdrawn": "已停止使用",
                            "reinforced": "已加强依据",
                            "negative_observation": "已记录撤回意向",
                            "candidate": "正在观察",
                        }[status],
                        "category_label": _preference_category_label(observation.topic),
                        "summary_for_user": memory_summary_for_user(
                            {
                                "preference_topic": observation.topic,
                                "preference_value": observation.value,
                                "preference_scope": observation.scope,
                            }
                        ),
                        "memory_id": created or str(aggregate.get("promoted_memory_id") or ""),
                    }
                )
        feedback["observed_preferences"] = observed_preferences
        return {
            **result,
            "agent_name": safe_event.agent_name,
            "session_id": safe_event.session_id,
            "turn_id": safe_event.turn_id,
            "sequence": safe_event.sequence,
            "event_type": safe_event.event_type,
            "preference_updates": preference_updates,
            "preference_withdrawals": preference_withdrawals,
            "observed_preferences": observed_preferences,
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
        successful_commands = successful_terminal_commands(timeline)
        final_response = final_visible_output(timeline)
        for stage, context in contexts.items():
            for memory in context.get("memories", []):
                if not memory.get("preference_topic"):
                    continue
                actual: Dict[str, object] = {}
                if stage == "tool_selection":
                    if successful_commands:
                        actual = {"tool_name": "terminal", "command": successful_commands[0]}
                elif stage == "response_generation":
                    if final_response:
                        actual = {
                            "response": final_response,
                            "command_first": response_starts_with_command_or_conclusion(
                                final_response,
                                successful_commands,
                            ),
                        }
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
            matches = str(row.get("retention_session_id") or "") in {"", session_id} and {
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
        successful_commands = successful_terminal_commands(observed_events)
        final_response = final_visible_output(observed_events)
        if stage == "tool_selection" and successful_commands:
            actual = {"tool_name": "terminal", "command": successful_commands[0]}
        elif stage == "response_generation" and final_response:
            actual = {
                "response": final_response,
                "command_first": response_starts_with_command_or_conclusion(
                    final_response,
                    successful_commands,
                ),
            }
        elif stage == "planning":
            for event in observed_events:
                if str(event.get("event_type") or "") in {"user_message", "assistant_message"}:
                    actual = {
                        "response": event.get("content", ""),
                        "event_id": event.get("event_id", ""),
                    }
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
        light: bool = False,
    ) -> Dict[str, object]:
        memory = self.meta.get_memory(str(memory_id))
        if memory is None:
            return {"found": False, "memory_id": memory_id, "reason": "memory_not_found"}
        if memory.get("deleted"):
            # 已遗忘的记忆只回执状态，不再返回正文与来源，避免"遗忘后仍能查看详情"。
            return {"found": False, "memory_id": memory_id, "reason": "memory_forgotten"}
        evidence = self.preference_evidence(str(memory_id))
        observed_events = list(evidence.get("events", []))
        if not light:
            observed_ids = {str(item.get("event_id")) for item in observed_events}
            source_sessions = {str(item.get("session_id") or "") for item in observed_events if item.get("session_id")}
            for source_session in source_sessions:
                for event in self.meta.list_agent_events(session_id=source_session, limit=500):
                    if str(event.get("event_id")) not in observed_ids:
                        observed_ids.add(str(event.get("event_id")))
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
        if memory.get("preference_topic") and not light:
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
        effective_limit = max(1, min(int(limit), 30))
        recent_candidates = self.list_memories(include_deleted=False, limit=500)
        recent_groups: Dict[Tuple[str, ...], Dict[str, object]] = {}
        for row in recent_candidates:
            preference_identity = self._preference_identity(row)
            if preference_identity:
                key = ("preference",) + preference_identity
            else:
                summary_for_user = " ".join(memory_summary_for_user(row).casefold().split())
                key = (
                    str(row.get("type") or ""),
                    str(row.get("subtype") or ""),
                    str(row.get("scope") or ""),
                    summary_for_user,
                )
            existing = recent_groups.get(key)
            if existing is None:
                item = dict(row)
                item["duplicate_count"] = 1
                item["duplicate_memory_ids"] = [str(row.get("memory_id") or "")]
                recent_groups[key] = item
                continue
            ids = list(existing.get("duplicate_memory_ids") or [])
            memory_id = str(row.get("memory_id") or "")
            if memory_id and memory_id not in ids:
                ids.append(memory_id)
            existing["duplicate_memory_ids"] = ids
            existing["duplicate_count"] = len(ids)
        recent = list(recent_groups.values())[:effective_limit]
        recent_memory_changes = []
        for row in recent:
            recent_memory_changes.append(
                {
                    "memory_id": row.get("memory_id"),
                    "type": row.get("type"),
                    "subtype": row.get("subtype"),
                    "summary_for_user": memory_summary_for_user(row),
                    "preference_topic": row.get("preference_topic"),
                    "preference_value": row.get("preference_value"),
                    "scope": row.get("scope"),
                    "tier": row.get("tier"),
                    "source_kind": row.get("source_kind"),
                    "sensitivity_level": row.get("sensitivity_level"),
                    "governance_state": row.get("governance_state"),
                    "duplicate_count": row.get("duplicate_count", 1),
                    "duplicate_memory_ids": row.get("duplicate_memory_ids", [row.get("memory_id")]),
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

    def memory_value_comparison(
        self,
        task: str,
        *,
        agent_name: str = "openhands",
        session_id: str = "",
        task_type: str = "code_assistance",
    ) -> Dict[str, object]:
        """Run the same task with and without memory and report the real difference.

        Both arms are built from live state: the baseline injects nothing, the
        memory arm injects whatever actually matches. The cost of the naive
        "paste the whole history" alternative is measured from the stored
        interaction events rather than assumed.
        """

        clean_task = str(task or "").strip()
        if not clean_task:
            raise ValueError("task is required")
        probe_session = str(session_id or "comparison-%s" % new_id("session"))

        contexts = {
            stage: self.build_agent_context(
                task=clean_task,
                stage=stage,
                agent_name=agent_name,
                session_id=probe_session,
                task_type=task_type,
            )
            for stage in LIVE_COMPARISON_STAGES
        }
        selection = self.select_preferences(
            agent_name=agent_name,
            session_id=probe_session,
            task_type=task_type,
        )
        result = build_live_comparison(
            task=clean_task,
            contexts=contexts,
            selected_preferences=list(selection.get("selected") or []),
            full_history_tokens=self._full_history_token_estimate(agent_name=agent_name),
        )
        result["session_id"] = probe_session
        result["execution_mode"] = "deterministic_policy_comparison"
        result["value_evidence_reference"] = self.memory_value_evidence()
        self._audit(
            "memory_value_comparison",
            None,
            {
                "task": clean_task,
                "applied_preference_count": result["measured"]["applied_preference_count"],
                "injected_memory_count": result["measured"]["injected_memory_count"],
                "token_saving_vs_full_history": result["measured"]["token_saving_vs_full_history"],
            },
        )
        return result

    def _full_history_token_estimate(self, agent_name: str = "openhands", limit: int = 200) -> int:
        """Token cost of replaying raw interaction history instead of memories."""

        parts: List[str] = []
        for event in self.meta.list_agent_events(limit=limit):
            if agent_name and str(event.get("agent_name") or "") != agent_name:
                continue
            for field in ("content", "tool_result", "state_before", "state_after", "user_feedback"):
                value = str(event.get(field) or "")
                if value:
                    parts.append(value)
        return self.estimate_tokens(" ".join(parts))

    def memory_value_evidence(self) -> Dict[str, object]:
        """Read optional benchmark evidence explicitly configured by the operator.

        Product behavior never depends on repository documentation.  A deployment
        that wants to expose archived benchmark numbers can point to a verified
        metrics file with ``MEMGUARD_MEMORY_VALUE_EVIDENCE``.
        """

        configured = os.environ.get("MEMGUARD_MEMORY_VALUE_EVIDENCE", "").strip()
        if not configured:
            return {
                "available": False,
                "reason": "benchmark evidence is not configured",
                "configuration": "MEMGUARD_MEMORY_VALUE_EVIDENCE",
            }
        path = Path(configured).expanduser()
        if not path.exists():
            return {
                "available": False,
                "reason": "configured benchmark evidence file was not found",
                "path": str(path),
            }
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return {"available": False, "reason": "benchmark evidence file unreadable"}
        aggregate = payload.get("aggregate") or {}
        return {
            "available": True,
            "case_count": payload.get("case_count") or aggregate.get("case_count"),
            "groups": aggregate.get("groups"),
            "mean_deltas": aggregate.get("mean_deltas"),
            "inference": aggregate.get("inference"),
            "evidence_class": payload.get("evidence_class") or "deterministic_authored_fixture",
            "evidence_note": payload.get("evidence_note") or (
                "结果来自人工编写的固定场景，用于可复现机制回归；"
                "不能替代真实 Agent 独立运行的因果证据。"
            ),
            "source": str(path),
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
        # tool_args 的值同样可能藏有凭据（例如 curl -H "Authorization: Bearer ..."），
        # 必须一起参与判定和脱敏，否则按键名脱敏会被值级内容绕过。
        combined = " ".join(
            part
            for part in (
                event.content,
                event.tool_result,
                event.state_before,
                event.state_after,
                event.user_feedback,
                _flatten_tool_args_text(event.tool_args),
            )
            if part
        )
        sensitivity = self.detector.classify(combined, event.source_uri)
        payload = event.to_dict()
        payload["provenance_hash"] = ""
        payload["source_uri"] = self._safe_provenance_uri(event.source_uri)
        payload["project_root"] = self._safe_provenance_uri(event.project_root)
        if not remember_allowed or sensitivity.level == "high":
            for field in ("content", "tool_result", "state_before", "state_after", "user_feedback"):
                payload[field] = ""
            payload["tool_args"] = {}
        elif sensitivity.labels:
            for field in ("content", "tool_result", "state_before", "state_after", "user_feedback"):
                payload[field] = self.detector.redact(str(payload[field]), event.source_uri)
            payload["tool_args"] = self._redact_tool_args(payload.get("tool_args"), event.source_uri)
        return AgentInteractionEvent.from_dict(payload), sensitivity

    def _safe_provenance_uri(self, source_uri: str) -> str:
        uri = str(source_uri or "")
        if not uri:
            return ""
        finding = self.detector.classify(uri)
        if finding.level == "high" or self.detector._sensitive_path(uri):
            return "redacted:sha256:%s" % stable_hash(uri)[:16]
        return self.detector.redact(uri) if finding.labels else uri

    def _redact_tool_args(self, value: object, source_uri: str) -> object:
        if isinstance(value, dict):
            return {key: self._redact_tool_args(item, source_uri) for key, item in value.items()}
        if isinstance(value, list):
            return [self._redact_tool_args(item, source_uri) for item in value]
        if isinstance(value, str):
            return self.detector.redact(value, source_uri)
        return value

    def _augment_with_enhancer(
        self,
        extraction: InteractionExtractionResult,
        event: AgentInteractionEvent,
    ) -> InteractionExtractionResult:
        """Let an optional local model add candidates; rules keep the final say.

        Suggestions are schema-validated, topic-whitelisted and always recorded
        as implicit evidence, so an enhancer can never promote a preference on
        its own and a failing enhancer never blocks ingest.
        """

        if not extraction.remember_allowed or not self.preference_enhancer.enabled:
            return extraction
        try:
            extra, error = self.preference_enhancer.suggest(event, list(extraction.observations))
        except Exception as exc:  # noqa: BLE001 - enhancer must never break ingest
            extra, error = [], "preference enhancer raised: %s" % str(exc)[:120]
        if error:
            self._audit("preference_enhancer_skipped", None, {"reason": error, "event_id": event.event_id})
            return extraction
        if not extra:
            return extraction
        self._audit(
            "preference_enhancer_suggested",
            None,
            {
                "event_id": event.event_id,
                "accepted": [{"topic": item.topic, "value": item.value} for item in extra],
                "decided_by": "deterministic_rules_with_optional_model_suggestion",
            },
        )
        return InteractionExtractionResult(
            remember_allowed=extraction.remember_allowed,
            reason=extraction.reason,
            observations=list(extraction.observations) + extra,
        )

    def _adjudicate_preference_update(
        self,
        observation: PreferenceObservation,
        memory: MemoryRecord,
    ) -> Optional[Dict[str, object]]:
        """Report preference supersession committed with the new memory."""

        topic = str(observation.topic or "").strip()
        if not topic:
            return None
        superseded = self.meta.superseded_preference_ids(memory.memory_id)
        if not superseded:
            return None
        self._invalidate_search_cache()
        update = {
            "topic": topic,
            "scope": observation.scope,
            "new_value": observation.value,
            "new_memory_id": memory.memory_id,
            "superseded_memory_ids": superseded,
            "explicit": observation.explicit,
            "decided_by": "deterministic_rule",
            "reason": "same topic and scope resolved to a newer value",
        }
        self._audit("preference_update", memory.memory_id, update)
        return update

    def _adjudicate_preference_withdrawal(
        self,
        observation: PreferenceObservation,
        event: AgentInteractionEvent,
    ) -> Optional[Dict[str, object]]:
        """Deactivate an explicitly withdrawn preference without erasing history.

        ``不要再用 grep`` changes future Agent behavior immediately, while
        ``忘掉 grep 的习惯`` remains the separate, irreversible erasure flow.
        Keeping those operations distinct gives users natural control without
        silently destroying audit evidence.
        """

        topic = str(observation.topic or "").strip()
        value = str(observation.value or "").strip()
        scope = str(observation.scope or "global").strip()
        if not topic or not value:
            return None
        retired: List[str] = []
        for row in self.meta.list_memories(include_deleted=False, governance_state="active"):
            memory_id = str(row.get("memory_id") or "")
            if not memory_id:
                continue
            if str(row.get("preference_topic") or "") != topic:
                continue
            if str(row.get("preference_value") or "") != value:
                continue
            if str(row.get("preference_scope") or "global") != scope:
                continue
            self.meta.set_governance_state(
                memory_id,
                "superseded",
                str(row.get("trust_level") or "normal"),
                float(row.get("trust_score") or 0.0),
                "withdrawn_by_user:%s" % event.event_id,
            )
            self.link_memory_event(memory_id, event.event_id, "withdrawn_by")
            retired.append(memory_id)
        if not retired:
            return None
        self._invalidate_search_cache()
        result = {
            "topic": topic,
            "value": value,
            "scope": scope,
            "retired_memory_ids": retired,
            "explicit": True,
            "decided_by": "deterministic_rule",
            "reason": "user explicitly withdrew this preference",
        }
        self._audit("preference_withdrawal", retired[0], result)
        return result

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
        # 偏好正文与摘要在落盘前再过一次敏感检测，避免抽取阶段读原文时把敏感片段带入记忆。
        content = self.detector.redact(content, event.source_uri)
        summary = self.detector.redact(summary, event.source_uri)
        record = make_memory(
            content=content,
            memory_type=memory_type,
            source=MemorySource(kind="agent_interaction", uri=event.source_uri or event.event_id),
            summary=summary,
            subtype=subtype,
            scope=self.namespace,
            tier="long_term" if observation.scope != "session" else "session",
            retention_session_id=event.session_id if observation.scope == "session" else "",
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
        record = self._persist_new_memory(record)
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

    def ingest_behavior_event(self, event: BehaviorEvent, session_id: str = "") -> Dict[str, object]:
        return self.ingest_text(
            event.to_memory_text(),
            source_kind="user_behavior",
            source_uri="%s#%s" % (event.source, event.index),
            tier="session",
            retention_session_id=session_id,
        )

    def ingest_shell_history(self, path: Path, limit: int = 200, session_id: str = "") -> Dict[str, object]:
        if not str(session_id or "").strip():
            return {
                "source_uri": str(path), "events": 0, "stored_count": 0,
                "blocked_count": 0, "records": [], "blocked": [],
                "reason": "session_required",
            }
        events = load_shell_history(path, limit=limit)
        records = []
        blocked = []
        for event in events:
            result = self.ingest_behavior_event(event, session_id=session_id)
            if result.get("stored"):
                record = result["record"]
                records.append(
                    {
                        "command": self.detector.redact(event.command),
                        "memory_id": record.memory_id,
                        "type": record.type,
                        "subtype": record.subtype,
                        "tier": record.tier,
                    }
                )
            else:
                blocked.append({
                    "command": self.detector.redact(event.command),
                    "reason": result.get("reason"),
                })
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

    def ingest_os_event(self, raw_event: str, session_id: str = "") -> Dict[str, object]:
        event = parse_os_event(raw_event)
        detail = {
            "event_type": event.event_type,
            "path": event.path,
            "process": event.process,
            "action": event.action,
        }
        audit = self._audit("os_event", None, detail)
        result = self.ingest_text(
            event.to_memory_text(), source_kind="os_event", source_uri=event.path,
            tier="working", retention_session_id=session_id,
        )
        result["os_audit_event_id"] = _audit_event_id(audit)
        if result.get("reason") == "session_required":
            result["reason"] = "audit_only"
        return result

    def update_memory(
        self, memory_id: str, text: str, reason: str = "manual update",
        source_link: Optional[Dict[str, object]] = None,
    ) -> Dict[str, object]:
        self._recover_pending_memory_writes()
        row = self.meta.get_memory(memory_id)
        if row is None or row.get("deleted"):
            return {"updated": False, "reason": "memory_not_found", "memory_id": memory_id}
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
        if stored_text == previous.content:
            return {"updated": False, "reason": "unchanged", "record": previous}

        replacement_observation = None
        if previous.preference_topic:
            update_event = AgentInteractionEvent(
                agent_name="memguardos", agent_version="product",
                session_id="memory-update", turn_id=new_id("turn"), sequence=1,
                event_type="user_message", role="user", content=stored_text,
            )
            matching = [
                item for item in extract_interaction_preferences(update_event).observations
                if item.topic == previous.preference_topic and item.polarity == "positive"
            ]
            if len(matching) != 1:
                return {"updated": False, "reason": "preference_update_requires_structured_value"}
            replacement_observation = matching[0]
        extraction = self.extractor.extract(stored_text, "update")
        memory_type = previous.type if replacement_observation else extraction.memory_type
        new_version_id = new_id("ver")
        previous.invalidate(superseded_by=new_version_id)

        record = MemoryRecord(
            memory_id=previous.memory_id,
            type=memory_type,
            subtype=previous.subtype if replacement_observation else extraction.subtype,
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
            preference_value=(replacement_observation.value if replacement_observation else previous.preference_value),
            preference_scope=previous.preference_scope,
            preference_explicit=previous.preference_explicit,
            agent_name=previous.agent_name,
            preference_session_id=previous.preference_session_id,
            retention_session_id=previous.retention_session_id,
            project_root=previous.project_root,
            task_type=previous.task_type,
            trust_level=previous.trust_level,
            trust_score=previous.trust_score,
            governance_state=previous.governance_state,
            governance_reason=previous.governance_reason,
        ).finalize_hash()
        self._enrich_graph(record)
        record = self._persist_existing_memory(previous, record, source_link=source_link)
        audit = self._audit(
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
        return {
            "updated": True, "record": record,
            "quality": quality.to_dict(), "extraction": extraction.to_dict(),
            "audit_event_id": _audit_event_id(audit),
        }

    def list_memories(
        self,
        include_deleted: bool = False,
        tier: Optional[str] = None,
        governance_state: Optional[str] = "active",
        limit: Optional[int] = None,
    ) -> List[Dict[str, object]]:
        return self.meta.list_memories(
            include_deleted=include_deleted,
            tier=tier,
            governance_state=governance_state,
            limit=limit,
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
        self._recover_pending_memory_writes()
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
            self._begin_memory_erasure(previous, compact=True)
            self._recover_pending_erasures()
            self.meta.set_governance_state(memory_id, "rejected", "rejected", 0.0, reason)
            self._audit(
                "governance_reject",
                memory_id,
                {"reviewer": reviewer, "reason": reason, "source_uri": row.get("source_uri")},
            )
            return {"reviewed": True, "memory_id": memory_id, "governance_state": "rejected"}

        new_version_id = new_id("ver")
        previous.invalidate(superseded_by=new_version_id)
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
        approved = self._persist_existing_memory(previous, approved)
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

    def promote_memory(self, memory_id: str, tier: str, session_id: str = "") -> Dict[str, object]:
        self._recover_pending_memory_writes()
        if tier not in {"working", "session", "long_term"}:
            return {"promoted": False, "reason": "invalid_tier", "tier": tier}
        row = self.meta.get_memory(memory_id)
        if row is None or row.get("deleted"):
            return {"promoted": False, "reason": "memory_not_found", "memory_id": memory_id}
        record = self.store.get_latest(memory_id)
        if record is None:
            return {"promoted": False, "reason": "memory_not_found", "memory_id": memory_id}
        if record.tier == tier:
            return {"promoted": False, "reason": "already_in_tier", "record": record}
        if record.retention_session_id and session_id and record.retention_session_id != str(session_id).strip():
            return {"promoted": False, "reason": "session_mismatch", "tier": tier}
        target_session = (str(session_id or "").strip() or record.retention_session_id) if tier != "long_term" else ""
        if tier != "long_term" and not target_session:
            return {"promoted": False, "reason": "session_required", "tier": tier}
        new_version_id = new_id("ver")
        record.invalidate(superseded_by=new_version_id)

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
            retention_session_id=target_session,
            project_root=record.project_root,
            task_type=record.task_type,
            trust_level=record.trust_level,
            trust_score=record.trust_score,
            governance_state=record.governance_state,
            governance_reason=record.governance_reason,
        ).finalize_hash()
        promoted = self._persist_existing_memory(record, promoted)
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
        selected_records = []
        latest = self.store.get_latest_many(str(row["memory_id"]) for row in records)
        for row in records:
            record = latest.get(str(row["memory_id"]))
            if record is None:
                continue
            selected_records.append(record)
        self._begin_memory_erasures(selected_records, compact=False)
        evicted = [record.memory_id for record in selected_records]
        if evicted:
            self._recover_pending_erasures()
        self._audit("tier_evict", None, {"tier": tier, "evicted": evicted})
        return {"evicted": True, "tier": tier, "memory_ids": evicted}

    def evict_session(
        self, session_id: str, confirm: bool = False,
        expected_versions: Optional[Dict[str, str]] = None,
    ) -> Dict[str, object]:
        scoped_id = str(session_id or "").strip()
        if not scoped_id:
            return {"evicted": False, "reason": "session_required", "memory_ids": []}
        rows = self.meta.list_session_memories(scoped_id)
        memory_ids = [str(row["memory_id"]) for row in rows]
        current_versions = {str(row["memory_id"]): str(row["version_id"]) for row in rows}
        if not confirm:
            return {
                "evicted": False, "session_id": scoped_id,
                "candidate_count": len(rows), "memory_ids": memory_ids,
                "expected_versions": current_versions,
            }
        if expected_versions is not None and expected_versions != current_versions:
            return {"evicted": False, "reason": "stale_preview", "session_id": scoped_id, "memory_ids": []}
        if not memory_ids:
            return {"evicted": True, "session_id": scoped_id, "memory_ids": []}
        result = self.forget_memory_ids(
            memory_ids, reason="session ended", compact=True,
            expected_versions=current_versions,
        )
        return {
            "evicted": bool(result.get("ok", False)), "session_id": scoped_id,
            "memory_ids": result.get("forgotten_memory_ids", []),
            "reason": result.get("reason", ""), "verification": result.get("verification", []),
        }

    def memory_policy_status(self, eviction_limit: int = 10) -> Dict[str, object]:
        self.flush_memory_hits()
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
        # 淘汰按"冷度优先"：既看多久没更新，也看是否真的被检索命中过。
        eviction_candidates = [
            {
                "memory_id": row.get("memory_id"),
                "tier": row.get("tier"),
                "type": row.get("type"),
                "subtype": row.get("subtype"),
                "source_kind": row.get("source_kind"),
                "updated_at": row.get("updated_at"),
                "hit_count": int(row.get("hit_count") or 0),
                "last_hit_at": row.get("last_hit_at") or "",
                "heat_score": _memory_heat_score(row),
                "summary": row.get("summary"),
            }
            for row in sorted(
                [row for row in live_records if row.get("tier") in {"working", "session"}],
                key=lambda item: (_memory_heat_score(item), str(item.get("updated_at") or "")),
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
        row = self.meta.get_memory(memory_id)
        if row is None:
            return []
        if row.get("deleted"):
            predecessor = {
                "memory_id": memory_id,
                "version_id": str(row.get("version_id") or ""),
                "parent_version_id": None,
                "op": "superseded",
                "summary": "[REDACTED]",
                "deleted": False,
                "created_at": "",
                "valid_from": str(row.get("valid_from") or ""),
                "valid_to": str(row.get("valid_to") or ""),
                "superseded_by": str(row.get("superseded_by") or ""),
                "active": False,
                "hash": "",
            }
            tombstone = {
                "memory_id": memory_id,
                "version_id": str(row.get("superseded_by") or ""),
                "parent_version_id": str(row.get("version_id") or ""),
                "op": "forget",
                "summary": "FORGOTTEN",
                "deleted": True,
                "created_at": str(row.get("updated_at") or ""),
                "valid_from": str(row.get("valid_to") or ""),
                "valid_to": str(row.get("valid_to") or ""),
                "superseded_by": None,
                "active": False,
                "hash": "",
            }
            return [predecessor, tombstone]
        by_version_id: Dict[str, Dict[str, object]] = {}
        committed_version_found = False
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
            if record.version.version_id == row["version_id"]:
                committed_version_found = True
                break
        if not committed_version_found:
            return []
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
        self._recover_pending_memory_writes()
        row = self.meta.get_memory(memory_id)
        if row is None or row.get("deleted"):
            return {"rolled_back": False, "reason": "memory_not_found", "memory_id": memory_id}
        if row["version_id"] == version_id:
            return {"rolled_back": False, "reason": "already_current", "memory_id": memory_id}
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
        if latest.version.op == "rollback" and latest.source.uri == version_id:
            return {"rolled_back": False, "reason": "already_rolled_back", "record": latest}

        new_version_id = new_id("ver")
        latest.invalidate(superseded_by=new_version_id)

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
            audit=AuditInfo(created_by="memguard", created_at=utc_now()),
            preference_topic=target.preference_topic,
            preference_value=target.preference_value,
            preference_scope=target.preference_scope,
            preference_explicit=target.preference_explicit,
            agent_name=target.agent_name,
            preference_session_id=target.preference_session_id,
            retention_session_id=target.retention_session_id,
            project_root=target.project_root,
            task_type=target.task_type,
            trust_level=target.trust_level,
            trust_score=target.trust_score,
            governance_state=target.governance_state,
            governance_reason=target.governance_reason,
            deleted=False,
        ).finalize_hash()
        record = self._persist_existing_memory(latest, record)
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

    def search(
        self, query: str, limit: int = 5, audit: bool = True, session_id: str = "",
    ) -> List[Dict[str, object]]:
        clean_query = query.strip()
        if not clean_query:
            return []
        cache_key = (
            clean_query, max(1, int(limit)), self.meta.memory_revision(),
            self.meta.embedding_revision(self.EMBEDDING_BACKEND, self.EMBEDDING_MODEL_REVISION),
            session_id,
        )
        cached = self._search_cache.get(cache_key)
        if cached is not None:
            self._search_cache.move_to_end(cache_key)
            results = [dict(item) for item in cached]
            if audit:
                self._audit("search", None, {"query": clean_query, "count": len(results), "cache": "hit"})
            return results

        started_at = perf_counter()
        intent = classify_query_intent(clean_query)
        candidates = self._search_candidates(
            query, limit=max(limit * 8, 64), include_graph=False, session_id=session_id,
        )
        dense_ids, dense_query_vector, dense_status = self._dense_candidate_ids(
            clean_query, limit=max(limit * 4, 20), session_id=session_id,
        )
        recall_done_at = perf_counter()
        exact_ids: List[str] = []
        if intent == QueryIntent.EXACT_LOOKUP:
            exact_ids = re.findall(r"\bmem[-_][a-zA-Z0-9-]{8,}\b", clean_query)
            known_ids = {str(item.get("memory_id") or "") for item in candidates}
            for memory_id in exact_ids:
                record = self.meta.get_memory(memory_id)
                if record and self._retrieval_record_is_eligible(record, session_id):
                    if memory_id not in known_ids:
                        candidates.insert(0, record)
                        known_ids.add(memory_id)
        memory_hashes = {
            str(item["memory_id"]): vector_content_hash(
                str(item.get("summary") or ""), str(item.get("content") or "")
            )
            for item in candidates
        }
        persisted_vectors = self.meta.get_memory_embeddings(
            memory_hashes,
            self.EMBEDDING_BACKEND,
            self.EMBEDDING_MODEL_REVISION,
        )
        base_pairs = self.retriever.rank(
            query,
            candidates,
            # RRF channels must be built from the same complete candidate set.
            # Truncating by the blended semantic score here can discard a strong
            # lexical hit before the lexical ranking is even constructed.
            limit=max(len(candidates), limit * 4, 20),
            persisted_vectors=persisted_vectors,
            kylin_query_vector=dense_query_vector,
            allow_kylin_rerank=dense_status not in {
                "latency_budget_exceeded", "query_vector_unavailable",
            },
            allow_ungrounded_kylin_candidates=not self.dense_candidates_enabled,
        )
        base_ranked = []
        for base_score, record in base_pairs:
            item = dict(record)
            item["_base_score"] = float(base_score)
            base_ranked.append(item)

        lexical_ids = [
            item["memory_id"]
            for item in sorted(
                base_ranked,
                key=lambda row: (
                    -float(
                        row.get("_score_breakdown", {}).get(
                            "exact_phrase_score", 0.0
                        )
                    ),
                    -float(
                        row.get("_score_breakdown", {}).get("routing_score", 0.0)
                    ),
                    -float(
                        row.get("_score_breakdown", {}).get(
                            "original_lexical_hit_count", 0.0
                        )
                    ),
                    -float(
                        row.get("_score_breakdown", {}).get(
                            "original_lexical_precision", 0.0
                        )
                    ),
                    -float(
                        row.get("_score_breakdown", {}).get(
                            "lexical_hit_count", 0.0
                        )
                    ),
                    -float(
                        row.get("_score_breakdown", {}).get(
                            "lexical_precision", 0.0
                        )
                    ),
                    -float(
                        row.get("_score_breakdown", {}).get("topic_score", 0.0)
                    ),
                    -float(
                        row.get("_score_breakdown", {}).get("subtype_score", 0.0)
                    ),
                    -float(
                        row.get("_score_breakdown", {}).get("entity_score", 0.0)
                    ),
                    -float(
                        row.get("_score_breakdown", {}).get("summary_score", 0.0)
                    )
                    - float(
                        row.get("_score_breakdown", {}).get("content_score", 0.0)
                    ),
                    -float(row.get("_hash_score", 0.0)),
                    str(row.get("memory_id") or ""),
                ),
            )
            if float(
                item.get("_score_breakdown", {}).get("exact_phrase_score", 0.0)
            )
            > 0
            or float(
                item.get("_score_breakdown", {}).get("lexical_hit_count", 0.0)
            )
            > 0
        ]
        hash_ids = [
            item["memory_id"]
            for item in sorted(
                base_ranked,
                key=lambda row: (
                    -float(row.get("_hash_score", 0.0)),
                    str(row.get("memory_id") or ""),
                ),
            )
        ]
        kylin_ids = [
            item["memory_id"]
            for item in sorted(
                (
                    row
                    for row in base_ranked
                    if float(row.get("_kylin_score", 0.0)) > 0.0
                ),
                key=lambda row: (
                    -float(row.get("_kylin_score", 0.0)),
                    str(row.get("memory_id") or ""),
                ),
            )
        ]
        # Both Kylin rankings use the same embedding evidence. When the
        # independent dense lane is ready, voting again through the prefilter
        # rerank lane would over-weight weak vector matches in RRF.
        rankings = {
            "lexical": lexical_ids,
            "hash": hash_ids,
            "kylin": [] if dense_ids else kylin_ids,
        }
        by_id = {str(item["memory_id"]): item for item in base_ranked}
        if dense_ids:
            verified_dense_ids = []
            for memory_id in dense_ids:
                record = self.meta.get_memory(memory_id)
                if record is None or not self._retrieval_record_is_eligible(record, session_id):
                    continue
                verified_dense_ids.append(memory_id)
                if memory_id not in by_id:
                    item = dict(record)
                    item["_base_score"] = 0.0
                    item["_vector_backend"] = "kylin_sdk_dense"
                    by_id[memory_id] = item
                elif by_id[memory_id].get("_vector_backend") == "hash":
                    by_id[memory_id]["_vector_backend"] = "kylin_sdk_dense"
            rankings["dense"] = verified_dense_ids

        if intent == QueryIntent.RELATIONAL:
            graph_result = (
                self.hierarchical_graph_search(
                    clean_query, limit=max(limit * 3, 10), session_id=session_id,
                ) if session_id else self.hierarchical_graph_search(
                    clean_query, limit=max(limit * 3, 10),
                )
            )
            summary_rows = graph_result.get("layers", {}).get("summary", [])
            graph_ids = []
            for summary_row in summary_rows:
                memory_id = str(summary_row.get("memory_id") or "")
                if not memory_id or memory_id in graph_ids:
                    continue
                graph_ids.append(memory_id)
                if memory_id in by_id:
                    continue
                record = self.meta.get_memory(memory_id)
                if record and self._retrieval_record_is_eligible(record, session_id):
                    item = dict(record)
                    item["_base_score"] = 0.0
                    item["_vector_backend"] = "graph"
                    by_id[memory_id] = item
            rankings["graph"] = graph_ids

        weights = {
            "lexical": 1.25
            if intent
            in {
                QueryIntent.EXACT_LOOKUP,
                QueryIntent.PREFERENCE,
                QueryIntent.PROCEDURAL,
                QueryIntent.TEMPORAL,
                QueryIntent.RELATIONAL,
            }
            else 1.0,
            "hash": 1.0,
            "kylin": 1.15,
            "dense": 1.15 if dense_ids else 0.0,
            "graph": 1.25 if intent == QueryIntent.RELATIONAL else 0.0,
        }
        exact_hits = set()
        distinctive_hit_ids = {
            str(item.get("memory_id") or "")
            for item in base_ranked
            if int(
                item.get("_score_breakdown", {}).get(
                    "distinctive_latin_token_count", 0
                )
            )
            == 1
            and int(
                item.get("_score_breakdown", {}).get(
                    "distinctive_term_hit_count", 0
                )
            )
            == 1
        }
        rare_distinctive_hit = len(distinctive_hit_ids) <= 2
        command_signature_hit_ids = {
            str(item.get("memory_id") or "")
            for item in base_ranked
            if int(
                item.get("_score_breakdown", {}).get(
                    "distinctive_latin_token_count", 0
                )
            )
            >= 2
            and float(
                item.get("_score_breakdown", {}).get(
                    "distinctive_term_coverage", 0.0
                )
            )
            >= 0.75
        }
        rare_command_signature_hit = len(command_signature_hit_ids) <= 2
        for item in base_ranked:
            memory_id = str(item.get("memory_id") or "")
            phrase_hit = bool(clean_query) and (
                clean_query in str(item.get("summary") or "")
                or clean_query in str(item.get("content") or "")
            )
            id_hit = intent == QueryIntent.EXACT_LOOKUP and memory_id in exact_ids
            distinctive_hit = (
                rare_distinctive_hit and memory_id in distinctive_hit_ids
            )
            command_signature_hit = (
                rare_command_signature_hit
                and memory_id in command_signature_hit_ids
            )
            if phrase_hit or id_hit or distinctive_hit or command_signature_hit:
                exact_hits.add(memory_id)
        scoring_done_at = perf_counter()
        fused = rrf_fuse(rankings, weights=weights, exact_hits=exact_hits)
        fusion_done_at = perf_counter()
        ranked = []
        for fused_item in fused:
            item = by_id.get(fused_item.memory_id)
            if item is None:
                continue
            item["score"] = fused_item.score
            item["retrieval_explanation"] = {
                "query_intent": intent.value,
                "fusion_sources": list(fused_item.sources),
                "protected_exact_hit": fused_item.protected,
                "kylin_vector_cached": (
                    fused_item.memory_id in persisted_vectors
                    or fused_item.memory_id in dense_ids
                ),
                "dense_candidate_status": dense_status,
            }
            ranked.append((fused_item.score, item))
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
        if not results:
            for record in self.meta.search_text(query, limit=limit):
                record["score"] = 0.1
                record["match_source"] = "text"
                results.append(record)
        results = self._collapse_search_duplicates(results)
        for rank, item in enumerate(results, start=1):
            item["rank"] = rank
            item["match_reason"] = self._search_match_reason(item)
        results = results[:limit]
        finished_at = perf_counter()
        timings = {
            "candidate_recall_ms": round((recall_done_at - started_at) * 1000, 2),
            "scoring_ms": round((scoring_done_at - recall_done_at) * 1000, 2),
            "fusion_ms": round((fusion_done_at - scoring_done_at) * 1000, 2),
            "postprocess_ms": round((finished_at - fusion_done_at) * 1000, 2),
            "total_ms": round((finished_at - started_at) * 1000, 2),
        }
        self._last_search_timings = dict(timings)
        for item in results:
            explanation = item.get("retrieval_explanation")
            if isinstance(explanation, dict):
                explanation["timings"] = dict(timings)
        # 命中反馈：被真正召回的记忆计入热度，供分层淘汰按"冷度优先"排序。
        # 热度只是统计量，按批落盘；每次检索都 fsync 会让 P50 从 8ms 劣化到 110ms。
        self._buffer_memory_hits([str(item.get("memory_id") or "") for item in results])
        if audit:
            self._audit(
                "search",
                None,
                {"query": clean_query, "count": len(results), "cache": "miss", "timings": timings},
            )
        if not self.dense_candidates_enabled or dense_status in {"ready", "disabled"}:
            self._remember_search(cache_key, results)
        return [dict(item) for item in results]

    def _dense_candidate_ids(
        self, query: str, limit: int, session_id: str = "",
    ) -> Tuple[List[str], Optional[List[float]], str]:
        if not self.dense_candidates_enabled:
            return [], None, "disabled"
        provider = self.retriever.kylin_provider
        if provider is None:
            return [], None, "provider_unavailable"
        try:
            cap = max(1, min(int(os.environ.get("MEMGUARD_DENSE_SCAN_LIMIT", "2000")), 5000))
        except ValueError:
            cap = 2000
        started = perf_counter()
        vectors, complete = self.meta.list_dense_candidate_vectors(
            self.EMBEDDING_BACKEND, self.EMBEDDING_MODEL_REVISION,
            self.namespace, cap, session_id=session_id,
        )
        if not complete:
            return [], None, "scan_limit_exceeded"
        if not vectors:
            return [], None, "no_ready_vectors"
        budget_ms = self.retriever.kylin_rerank_budget_ms
        remaining = budget_ms / 1000 - (perf_counter() - started) if budget_ms > 0 else 0
        if remaining <= 0:
            return [], None, "latency_budget_exceeded"
        try:
            query_vector = provider.vector(
                str(expand_query_aliases(query)["expanded_query"]),
                timeout_seconds=remaining,
            )
        except Exception:
            return [], None, "query_vector_unavailable"
        if not query_vector or not all(math.isfinite(value) for value in query_vector):
            return [], None, "query_vector_unavailable"
        normalized_query = normalize_vector(query_vector)
        scored = []
        for memory_id, vector in vectors:
            if len(vector) != len(query_vector):
                continue
            similarity = cosine(normalized_query, normalize_vector(vector))
            if similarity >= 0.45:
                scored.append((similarity, memory_id))
        if (perf_counter() - started) * 1000 > budget_ms:
            return [], query_vector, "latency_budget_exceeded"
        scored.sort(key=lambda item: (-item[0], item[1]))
        if not scored:
            return [], query_vector, "ready"
        # RRF ignores cosine magnitude, so admitting the whole dense tail can
        # promote weak semantic matches above stronger lexical evidence.
        leader_score = scored[0][0]
        close_candidates = [
            memory_id for score, memory_id in scored[:min(limit, 3)]
            if leader_score - score <= 0.05
        ]
        return close_candidates, query_vector, "ready"

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

    def build_context(
        self, query: str, limit: int = 5, max_chars: int = 2000,
        token_budget: Optional[int] = None, session_id: str = "",
    ) -> Dict[str, object]:
        effective_max_chars = max_chars
        if token_budget and token_budget > 0:
            # Conservative cap for mixed Chinese/English contexts; exact tokenizers
            # are provider-specific, so the package reports estimates instead.
            effective_max_chars = min(max_chars, max(256, int(token_budget / 0.65)))
        hits = self.search(query, limit=limit, session_id=session_id)
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
        search_hits = self.search(clean_task, limit=max(limit * 3, limit), audit=False, session_id=session_id)
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
            if hit.get("type") in {"preference", "sensitive_policy"} and hit.get("preference_topic"):
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
                    "version_id": candidate.get("version_id", ""),
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

    def _search_candidates(
        self,
        query: str,
        limit: int = 80,
        *,
        include_graph: bool = True,
        include_recent: bool = True,
        session_id: str = "",
    ) -> List[Dict[str, object]]:
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

        expanded = expand_query_aliases(query)
        text_queries = [str(query or "").strip()]
        text_queries.extend(str(alias).strip() for alias in expanded.get("aliases", []))
        text_queries = list(dict.fromkeys(item for item in text_queries if item))[:12]
        for text_query in text_queries:
            if len(candidates) >= candidate_limit:
                break
            add_unique(
                self.meta.search_text(
                    text_query,
                    limit=candidate_limit - len(candidates),
                    session_id=session_id,
                )
            )
        if include_graph and len(candidates) < candidate_limit:
            remaining = candidate_limit - len(candidates)
            graph_query = str(expanded.get("expanded_query") or query)
            add_unique(self.meta.graph_search(graph_query, limit=max(10, remaining), session_id=session_id))
        if include_recent and len(candidates) < candidate_limit:
            remaining = candidate_limit - len(candidates)
            add_unique(self.meta.list_recent_memories(remaining, exclude_ids=list(seen), session_id=session_id))
        return candidates[:candidate_limit]

    @staticmethod
    def _retrieval_record_is_eligible(record: Dict[str, object], session_id: str = "") -> bool:
        return (
            not bool(record.get("deleted"))
            and str(record.get("retention_session_id") or "") in {"", session_id}
            and str(record.get("governance_state") or "active") == "active"
            and str(record.get("trust_level") or "trusted")
            in {"trusted", "verified", "reviewed"}
        )

    def graph_search(self, entity_query: str, limit: int = 10, session_id: str = "") -> List[Dict[str, object]]:
        results = self.meta.graph_search(entity_query, limit=limit, session_id=session_id)
        self._audit("graph_search", None, {"entity_query": entity_query, "count": len(results)})
        return results

    def hierarchical_graph_search(
        self, entity_query: str, limit: int = 10, session_id: str = "",
    ) -> Dict[str, object]:
        summary_rows = self.meta.graph_search(entity_query, limit=limit, session_id=session_id)
        if not summary_rows:
            summary_rows = self._search_candidates(
                entity_query,
                limit=limit,
                include_graph=False,
                include_recent=False,
                session_id=session_id,
            )

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

    def related(self, memory_id: str, limit: int = 10, session_id: str = "") -> Dict[str, object]:
        seed = self.meta.get_memory(memory_id)
        if seed is None or not self._retrieval_record_is_eligible(seed, session_id):
            return {"graph": {"entities": [], "relations": []}, "related": []}
        graph = self.meta.memory_graph(memory_id)
        related = self.meta.related_memories(memory_id, limit=limit, session_id=session_id)
        result = {"graph": graph, "related": related}
        self._audit("related", memory_id, {"related_count": len(related), "entity_count": len(graph["entities"])})
        return result

    def forget(self, request: str, confirm: bool = False, compact: bool = False) -> Dict[str, object]:
        intent = self.compile_erasure_intent(request)
        plan = ErasurePlan.from_intent(intent)
        return self._forget_with_plan(plan, confirm=confirm, compact=compact)

    def _forget_with_plan(
        self,
        plan: ErasurePlan,
        confirm: bool = False,
        compact: bool = False,
    ) -> Dict[str, object]:
        self._recover_pending_memory_writes()
        all_records = self.meta.list_memories(include_deleted=False)
        _, time_window = _apply_forget_time_window(all_records, plan.as_intent())
        records = self.meta.select_forget_candidates(plan)
        time_window = dict(time_window)
        time_window["structured_records_after"] = len(records)
        if plan.unresolved_scope or plan.unresolved_slots:
            time_window["scope_blocked"] = True
            time_window["note"] = "遗忘条件未能完整解析，已安全停止；请补充目标或作用范围。"
        candidates = [] if (plan.unresolved_scope or plan.unresolved_slots) else self.forget_planner.candidates(
            plan.source_text, records, intent=plan.as_intent()
        )
        external_residue = self._managed_forget_residue(confirm=confirm)
        if not confirm:
            self._audit(
                "forget_dry_run",
                None,
                _forget_audit_detail(plan, candidate_count=len(candidates)),
            )
            result = {
                "confirmed": False,
                "request": plan.source_text,
                "erasure_plan": plan.as_dict(),
                "candidates": candidates,
                "time_window": time_window,
                "external_residue": external_residue,
            }
            result["verification_report"] = self.forget_verification_report(
                result,
                request=plan.source_text,
                dry_run=True,
            )
            return result

        forgotten = []
        probes: Dict[str, str] = {}
        selected_records: Dict[str, MemoryRecord] = {}
        latest = self.store.get_latest_many(str(item["memory_id"]) for item in candidates)
        for candidate in candidates:
            memory_id = str(candidate["memory_id"])
            record = latest.get(memory_id)
            if record is None:
                continue
            probes[memory_id] = record.content
            selected_records[memory_id] = record
        effects = self._begin_memory_erasures(list(selected_records.values()), compact=compact)
        forgotten = list(selected_records)
        deleted_preference_observations = effects["deleted_observations"]
        scrubbed_event_count = effects["scrubbed_events"]
        audit_payloads_redacted = effects["redacted_audit"]
        storage_cleanup = (
            self._recover_pending_erasures()["storage_cleanup"]
            if forgotten else {"checkpoint": [], "vacuum": False}
        )
        cache_entries_dropped = self._invalidate_search_cache() if forgotten else 0
        verification = [
            self.verify_forget_probe(memory_id, probe_text=probe)
            for memory_id, probe in probes.items()
        ]
        audit = self._audit(
            "forget",
            None,
            {
                **_forget_audit_detail(plan, candidate_count=len(candidates)),
                "forgotten": forgotten,
                "deleted_preference_observation_count": deleted_preference_observations,
                "compact": compact,
            },
        )
        result = {
            "confirmed": True,
            "request": plan.source_text,
            "erasure_plan": plan.as_dict(),
            "forgotten": forgotten,
            "deleted_preference_observation_count": deleted_preference_observations,
            "scrubbed_event_count": scrubbed_event_count,
            "time_window": time_window,
            "external_residue": external_residue,
            "compact": compact,
            "storage_cleanup": storage_cleanup,
            "audit_payloads_redacted": audit_payloads_redacted,
            "verification": verification,
            "search_cache_entries_dropped": cache_entries_dropped,
            "audit_chain_ok": bool(self.meta.verify_audit_chain().get("ok")),
        }
        result["audit_event_id"] = _audit_event_id(audit)
        result["ok"] = bool(forgotten) and result["audit_chain_ok"] and all(
            item["irreversible_ok"] for item in verification
        )
        result["verification_report"] = self.forget_verification_report(
            result, request=plan.source_text, dry_run=False
        )
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
        plan = ErasurePlan.from_intent(compiled)
        agent_filters = self.erasure_intent_compiler.to_agent_filters(compiled)
        use_agent_route = bool(
            not plan.unresolved_scope
            and not plan.unresolved_slots
            and plan.scope_level in {"agent", "session"}
            and (agent_filters.get("agent_name") or agent_filters.get("session_id"))
            and not plan.sensitive_labels
            and not plan.retention_constraints
            and (
                not plan.topic
                or (
                    plan.canonical_preference_topic
                    and plan.topic == plan.preference_topic
                )
            )
        )
        if use_agent_route:
            result = self.forget_agent_interactions(agent_filters, confirm=confirm, compact=compact)
            route = "agent_interactions"
        else:
            result = self._forget_with_plan(plan, confirm=confirm, compact=compact)
            route = "managed_memory"
        return {"compiled_intent": compiled, "route": route, "result": result}

    def forget_memory_ids(
        self, memory_ids: List[str], reason: str, compact: bool = True,
        expected_versions: Optional[Dict[str, str]] = None,
    ) -> Dict[str, object]:
        self._recover_pending_memory_writes()
        unique_ids: List[str] = []
        for memory_id in memory_ids:
            normalized = str(memory_id).strip()
            if normalized and normalized not in unique_ids:
                unique_ids.append(normalized)

        if expected_versions is not None:
            stale_ids = [
                memory_id for memory_id in unique_ids
                if not expected_versions.get(memory_id)
                or str((self.meta.get_memory(memory_id) or {}).get("version_id") or "")
                != str(expected_versions[memory_id])
            ]
            if stale_ids:
                return {
                    "ok": False, "confirmed": False, "reason": "stale_preview",
                    "requested_memory_ids": unique_ids, "stale_memory_ids": stale_ids,
                    "forgotten_memory_ids": [],
                }

        forgotten: List[str] = []
        missing: List[str] = []
        already_deleted: List[str] = []
        verification: List[Dict[str, object]] = []
        probe_texts: Dict[str, str] = {}
        selected_records: List[MemoryRecord] = []
        latest = self.store.get_latest_many(unique_ids, include_deleted=True)

        for memory_id in unique_ids:
            row = self.meta.get_memory(memory_id)
            record = latest.get(memory_id)
            if row is None and record is None:
                missing.append(memory_id)
                continue
            if (row is not None and row.get("deleted")) or (record is not None and record.deleted):
                already_deleted.append(memory_id)
                verification.append(self.verify_forget_probe(memory_id, probe_texts.get(memory_id, "")))
                continue
            if record is None:
                missing.append(memory_id)
                continue
            # 先留存原文片段，删除后才能用它真实回查各检索面。
            probe_texts[memory_id] = str(getattr(record, "content", "") or getattr(record, "summary", "") or "")
            selected_records.append(record)

        effects = self._begin_memory_erasures(selected_records, compact=compact)
        forgotten = [record.memory_id for record in selected_records]
        deleted_preference_observations = effects["deleted_observations"]
        scrubbed_event_count = effects["scrubbed_events"]
        audit_payloads_redacted = effects["redacted_audit"]

        storage_cleanup = (
            self._recover_pending_erasures()["storage_cleanup"]
            if forgotten else {"checkpoint": [], "vacuum": False}
        )
        cache_entries_dropped = self._invalidate_search_cache() if forgotten else 0

        for memory_id in forgotten:
            verification.append(self.verify_forget_probe(memory_id, probe_texts.get(memory_id, "")))

        result = {
            "ok": False,
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
            "deleted_preference_observation_count": deleted_preference_observations,
            "scrubbed_event_count": scrubbed_event_count,
            "storage_cleanup": storage_cleanup,
            "audit_payloads_redacted": audit_payloads_redacted,
            "search_cache_entries_dropped": cache_entries_dropped,
            "external_residue": self._managed_forget_residue(confirm=True),
        }
        audit = self._audit(
            "forget_memory_ids",
            None,
            result["summary"] | {"reason_hash": stable_hash(reason), "memory_ids": forgotten},
        )
        result["audit_event_id"] = _audit_event_id(audit)
        result["audit_chain_ok"] = bool(self.meta.verify_audit_chain().get("ok"))
        result["ok"] = bool(forgotten) and not missing and result["audit_chain_ok"] and all(
            item["irreversible_ok"] for item in verification
        )
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
        self._recover_pending_memory_writes()
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
            "user_explanation": (
                "OpenHands 自身保存的会话副本不在 MemGuardOS 管理范围内；"
                "如果已启用该功能，还需要在 OpenHands 中单独删除。"
                if external_persistence
                else "当前调用方声明未保留外部 Agent 会话副本。"
            ),
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
        selected_records = []
        latest = self.store.get_latest_many(str(memory_id) for memory_id in impact["memory_ids"])
        for memory_id in impact["memory_ids"]:
            record = latest.get(str(memory_id))
            if record is None:
                continue
            probes.append(record.content)
            selected_records.append(record)
        effects = self._begin_memory_erasures(selected_records, compact=compact)
        forgotten_memory_ids = [record.memory_id for record in selected_records]
        deleted_memory_observations = effects["deleted_observations"]
        scrubbed_linked_events = effects["scrubbed_events"]
        audit_payloads_redacted = effects["redacted_audit"]
        deleted_observations = self.meta.delete_preference_observations(
            impact["event_ids"],
            str(clean_filters.get("preference_topic") or ""),
        ) + deleted_memory_observations
        scrubbed_events = max(
            scrubbed_linked_events,
            self.meta.forget_agent_event_payloads(impact["event_ids"]),
        )
        storage_cleanup = (
            self._recover_pending_erasures()["storage_cleanup"]
            if forgotten_memory_ids else self.meta.secure_cleanup(vacuum=compact)
        )
        cache_entries_dropped = self._invalidate_search_cache()

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
            and not item.get("state_before")
            and not item.get("state_after")
            and not item.get("user_feedback")
            for item in remaining_events.values()
        ) and len(remaining_events) == len(impact["event_ids"])
        segment_plaintext_absent = all(
            not probe or not self.store.raw_contains(probe)
            for probe in probes
        )
        retrieval_surfaces = [
            self.probe_retrieval_surfaces(memory_id, probe)
            for memory_id, probe in zip(forgotten_memory_ids, list(probes) + [""] * len(forgotten_memory_ids))
        ]
        verification = {
            "verification_session_id": verification_session,
            "selected_memory_ids_by_stage": selected_after,
            "all_stages_absent": all_stages_absent,
            "event_payload_scrubbed": event_payload_scrubbed,
            "segment_plaintext_absent": segment_plaintext_absent,
            "retrieval_surfaces": retrieval_surfaces,
            "retrieval_surfaces_clean": all(
                not item.get("any_surface_recallable") for item in retrieval_surfaces
            ),
            "search_cache_entries_dropped": cache_entries_dropped,
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
            "storage_cleanup": storage_cleanup,
            "audit_payloads_redacted": audit_payloads_redacted,
            "verification": verification,
            "audit_chain_ok": bool(self.meta.verify_audit_chain().get("ok")),
        }
        result["ok"] = bool(impact["event_ids"] or forgotten_memory_ids) and all((
            event_payload_scrubbed,
            segment_plaintext_absent,
            verification["retrieval_surfaces_clean"],
            all_stages_absent,
            result["audit_chain_ok"],
        ))
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
        probe = str(probe_text or "").strip()
        segment_verified = False
        segment_leak = False
        if probe:
            try:
                segment_leak = self.store.raw_contains(probe)
                segment_verified = True
            except Exception:
                pass
        surfaces = self.probe_retrieval_surfaces(memory_id, probe_text)
        try:
            evidence = self.meta.memory_event_evidence(memory_id)
            event_payload_scrubbed = all(
                item.get("forgotten")
                and not any(item.get(key) for key in (
                    "content", "tool_result", "state_before", "state_after",
                    "user_feedback", "source_excerpt",
                ))
                and not item.get("tool_args")
                for item in evidence
            )
        except Exception:
            event_payload_scrubbed = False
        verified = bool(probe and segment_verified and surfaces["verified"] and event_payload_scrubbed)
        clean = bool(deleted and not leaked and not segment_leak and not surfaces["any_surface_recallable"])
        return {
            "memory_id": memory_id,
            "deleted": deleted,
            "metadata_plaintext_leak": leaked,
            "segment_plaintext_leak": segment_leak,
            "segment_plaintext_verified": segment_verified,
            "event_payload_scrubbed": event_payload_scrubbed,
            "retrieval_surfaces": surfaces,
            "verification_status": "passed" if verified and clean else "failed" if not clean else "unknown",
            "irreversible_ok": verified and clean,
        }

    def probe_retrieval_surfaces(self, memory_id: str, probe_text: str = "") -> Dict[str, object]:
        """Re-query each retrieval surface and report whether the id is still recallable.

        This turns the FTS / graph / cache verification steps from an assertion
        into a measurement: we actually search again instead of trusting that a
        confirmed delete cleaned every path.
        """

        target = str(memory_id).strip()
        probe = str(probe_text or "").strip()
        fts_hit = False
        graph_hit = False
        fact_hit = False
        cache_hit = False
        errors: List[str] = []
        try:
            if self.meta._fts_enabled:
                fts_hit = self.meta.conn.execute(
                    "SELECT 1 FROM memory_fts WHERE memory_id = ? LIMIT 1", (target,)
                ).fetchone() is not None
        except Exception as exc:
            errors.append("sqlite_fts: %s" % type(exc).__name__)
        try:
            graph_hit = any(
                self.meta.conn.execute(
                    "SELECT 1 FROM %s WHERE memory_id = ? LIMIT 1" % table, (target,)
                ).fetchone() is not None
                for table in ("memory_entity", "relation")
            )
        except Exception as exc:
            errors.append("graph_relations: %s" % type(exc).__name__)
        try:
            fact_hit = self.meta.conn.execute(
                "SELECT 1 FROM fact_slot WHERE memory_id = ? LIMIT 1", (target,)
            ).fetchone() is not None
        except Exception as exc:
            errors.append("fact_slot: %s" % type(exc).__name__)
        for cached in self._search_cache.values():
            if any(str(item.get("memory_id") or "") == target for item in cached):
                cache_hit = True
                break
        return {
            "probe_text_used": bool(probe),
            "sqlite_fts_recallable": fts_hit,
            "graph_relations_recallable": graph_hit,
            "fact_slot_recallable": fact_hit,
            "search_cache_recallable": cache_hit,
            "verified": not errors,
            "verification_errors": errors,
            "any_surface_recallable": bool(fts_hit or graph_hit or fact_hit or cache_hit),
        }

    def _buffer_memory_hits(self, memory_ids: List[str]) -> None:
        """Queue retrieval hits; flush in batches so search stays fsync-free."""

        for memory_id in memory_ids:
            normalized = str(memory_id).strip()
            if normalized:
                self._pending_hits.append(normalized)
        if len(self._pending_hits) >= self.HIT_FLUSH_THRESHOLD:
            self.flush_memory_hits()

    def flush_memory_hits(self) -> int:
        """Persist buffered retrieval hits. Safe to call at any time."""

        if not self._pending_hits:
            return 0
        pending, self._pending_hits = self._pending_hits, []
        try:
            return self.meta.record_memory_hits(pending)
        except Exception:
            # 热度是统计量，落盘失败不得影响检索或遗忘主链路。
            return 0

    def _invalidate_search_cache(self) -> int:
        """Drop in-process retrieval caches so forgotten text cannot be served again."""

        self.flush_memory_hits()
        dropped = len(self._search_cache)
        self._search_cache.clear()
        self._last_search_timings = {}
        provider = getattr(self.retriever, "embedding_provider", None)
        cache = getattr(provider, "cache", None)
        if hasattr(cache, "clear"):
            try:
                cache.clear()
            except Exception:
                pass
        return dropped

    def compact(self) -> Dict[str, object]:
        self._recover_pending_memory_writes()
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
        status["last_search_timings"] = dict(self._last_search_timings)
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

    def list_patterns(self) -> Dict[str, object]:
        return load_patterns(self.pattern_path)

    def add_pattern(self, name: str, regex: str, level: str = "") -> Dict[str, object]:
        patterns = save_pattern(self.pattern_path, name, regex, level=level)
        self.detector = SensitiveDetector(patterns)
        self._audit("pattern_add", None, {"name": name, "regex": regex, "level": level or "default"})
        return patterns

    def try_add_pattern(self, name: str, regex: str, level: str = "") -> Dict[str, object]:
        try:
            patterns = self.add_pattern(name, regex, level=level)
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
        detail = self._sanitize_audit_detail(detail)
        detail.setdefault("namespace", self.namespace)
        result = self.meta.add_audit(event_type, memory_id, detail)
        self.journald.log(event_type, memory_id, detail)
        return result

    def _sanitize_audit_detail(self, value: object) -> object:
        if isinstance(value, dict):
            return {key: self._sanitize_audit_detail(item) for key, item in value.items()}
        if isinstance(value, (list, tuple)):
            return [self._sanitize_audit_detail(item) for item in value]
        if isinstance(value, str):
            finding = self.detector.classify(value)
            if finding.level == "high" or self.detector._sensitive_path(value):
                return "redacted:sha256:%s" % stable_hash(value)[:16]
            return self.detector.redact(value) if finding.labels else value
        return value

    def _remember_search(self, cache_key: Tuple[str, int, str, str, str], results: List[Dict[str, object]]) -> None:
        self._search_cache[cache_key] = [dict(item) for item in results]
        self._search_cache.move_to_end(cache_key)
        while len(self._search_cache) > self._search_cache_limit:
            self._search_cache.popitem(last=False)

    @classmethod
    def _collapse_search_duplicates(cls, results: List[Dict[str, object]]) -> List[Dict[str, object]]:
        collapsed: List[Dict[str, object]] = []
        for raw in results:
            item = dict(raw)
            item.setdefault("duplicate_count", 1)
            item.setdefault("duplicate_memory_ids", [str(item.get("memory_id") or "")])
            duplicate_of: Optional[Dict[str, object]] = None
            for existing in collapsed:
                if cls._search_items_are_duplicates(existing, item):
                    duplicate_of = existing
                    break
            if duplicate_of is None:
                collapsed.append(item)
                continue
            ids = list(duplicate_of.get("duplicate_memory_ids") or [])
            memory_id = str(item.get("memory_id") or "")
            if memory_id and memory_id not in ids:
                ids.append(memory_id)
            duplicate_of["duplicate_memory_ids"] = ids
            duplicate_of["duplicate_count"] = len(ids)
        return collapsed

    @classmethod
    def _search_items_are_duplicates(cls, left: Dict[str, object], right: Dict[str, object]) -> bool:
        if str(left.get("memory_id") or "") == str(right.get("memory_id") or ""):
            return True
        left_identity = cls._preference_identity(left)
        right_identity = cls._preference_identity(right)
        if left_identity and left_identity == right_identity:
            return True
        left_topic = str(left.get("preference_topic") or "").strip()
        right_topic = str(right.get("preference_topic") or "").strip()
        if left_topic and left_topic == right_topic:
            return (
                str(left.get("preference_value") or "").strip().casefold()
                == str(right.get("preference_value") or "").strip().casefold()
                and str(left.get("preference_scope") or "global")
                == str(right.get("preference_scope") or "global")
            )
        if str(left.get("type") or "") != str(right.get("type") or ""):
            return False
        if str(left.get("subtype") or "") != str(right.get("subtype") or ""):
            return False
        left_text = " ".join(str(left.get("content") or "").casefold().split())
        right_text = " ".join(str(right.get("content") or "").casefold().split())
        if left_text == right_text and left_text:
            return True
        left_tokens = set(tokenize_literal(left_text))
        right_tokens = set(tokenize_literal(right_text))
        if not left_tokens or not right_tokens:
            return False
        similarity = len(left_tokens & right_tokens) / float(len(left_tokens | right_tokens))
        return similarity >= 0.92

    @staticmethod
    def _preference_identity(item: Dict[str, object]) -> Tuple[str, ...]:
        if str(item.get("type") or "") not in {"preference", "sensitive_policy"}:
            return ()
        topic = str(item.get("preference_topic") or "").strip()
        if not topic:
            topic = inferred_preference_topic(item)
        value = str(item.get("preference_value") or "").strip().casefold()
        text = " ".join(
            [
                str(item.get("summary") or ""),
                str(item.get("summary_for_user") or ""),
                str(item.get("content") or ""),
            ]
        ).casefold()
        if topic == "code_search_tool":
            if value in {"rg", "ripgrep"} or re.search(r"(?:^|\W)(?:rg|ripgrep)(?:$|\W)", text):
                value = "rg"
            elif value == "grep" or re.search(r"(?:^|\W)grep(?:$|\W)", text):
                value = "grep"
        if not topic or not value:
            return ()
        scope = str(item.get("preference_scope") or "global").strip() or "global"
        return topic, value, scope

    @staticmethod
    def _search_match_reason(item: Dict[str, object]) -> str:
        if item.get("match_source") == "graph":
            return "与相关实体或关系直接关联"
        if item.get("match_source") == "text":
            return "正文或摘要包含查询关键词"
        breakdown = item.get("score_breakdown") if isinstance(item.get("score_breakdown"), dict) else {}
        if float(breakdown.get("exact_phrase_score", 0.0)) > 0:
            return "包含完整查询内容"
        if float(breakdown.get("topic_score", 0.0)) > 0 or float(breakdown.get("subtype_score", 0.0)) > 0:
            return "主题和记忆类型与当前任务一致"
        if float(breakdown.get("entity_score", 0.0)) > 0:
            return "命中查询中的关键实体"
        if float(breakdown.get("summary_score", 0.0)) > 0:
            return "摘要关键词与查询匹配"
        if float(breakdown.get("content_score", 0.0)) > 0:
            return "正文关键词与查询匹配"
        explanation = item.get("retrieval_explanation")
        if isinstance(explanation, dict) and "dense" in explanation.get("fusion_sources", []):
            return "已保存的语义向量与查询匹配"
        return "综合语义相关度较高"

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


def _preference_category_label(topic: str) -> str:
    if topic in {"code_search_tool", "test_runner_command", "service_log_tool"}:
        return "工具偏好"
    if topic in {"response_order", "response_language", "response_verbosity"}:
        return "回答偏好"
    if topic == "high_risk_confirmation":
        return "安全策略"
    return "用户偏好"


def _memory_heat_score(row: Dict[str, object]) -> float:
    """Heat = retrieval hits with a recency bonus for the last hit.

    Used only to order eviction candidates, so an unreferenced working-tier
    memory is retired before one the Agent keeps recalling.
    """

    hits = int(row.get("hit_count") or 0)
    score = float(hits)
    last_hit = str(row.get("last_hit_at") or "")
    if last_hit:
        score += 0.5
    return score


def _apply_forget_time_window(
    records: List[Dict[str, object]],
    intent: Dict[str, object],
) -> Tuple[List[Dict[str, object]], Dict[str, object]]:
    """Restrict forget candidates to the time window the request actually asked for.

    Returns the filtered records plus a description of the applied window so the
    preview can show the user which boundary was enforced.
    """

    raw = intent.get("time_range") if isinstance(intent, dict) else None
    if not isinstance(raw, dict):
        return records, {"applied": False, "reason": "no_time_range_in_request"}
    start = str(raw.get("start_time") or "").strip()
    end = str(raw.get("end_time") or "").strip()
    if not start and not end:
        return records, {"applied": False, "reason": "empty_time_range"}

    kept: List[Dict[str, object]] = []
    for record in records:
        stamp = str(record.get("valid_from") or record.get("updated_at") or "").strip()
        if not stamp:
            # 没有时间戳的记忆不因时间条件被删除，避免误伤。
            continue
        if start and stamp < start:
            continue
        if end and stamp > end:
            continue
        kept.append(record)
    return kept, {
        "applied": True,
        "start_time": start,
        "end_time": end,
        "source_text": str(raw.get("source_text") or ""),
        "records_before": len(records),
        "records_after": len(kept),
    }


def _forget_audit_detail(plan: ErasurePlan, candidate_count: int) -> Dict[str, object]:
    """Keep a verifiable audit trail without retaining the forgotten phrase."""

    return {
        "request_hash": stable_hash(plan.source_text),
        "candidate_count": int(candidate_count),
        "memory_types": list(plan.memory_types),
        "scope_level": plan.scope_level,
        "has_time_window": plan.has_time_window,
        "start_time": plan.start_time,
        "end_time": plan.end_time,
        "plan_schema": "memguardos-erasure-plan-v1",
    }


def _flatten_tool_args_text(value: object) -> str:
    """Collect every string leaf of tool_args so value-level secrets are inspected too."""

    if isinstance(value, dict):
        return " ".join(_flatten_tool_args_text(item) for item in value.values())
    if isinstance(value, (list, tuple)):
        return " ".join(_flatten_tool_args_text(item) for item in value)
    if isinstance(value, str):
        return value
    return ""


def _independent_fact_clause(text: str) -> bool:
    """Only persist a second clause when it expresses an independent fact."""

    clause = str(text or "").strip()
    return bool(
        len(clause) >= 6
        and not looks_like_question(clause)
        and re.search(r"(?:是|为|位于|定于|属于|发布日|版本|日期|截止日|负责人|路径)", clause)
    )


def _audit_event_id(audit: object) -> str:
    if isinstance(audit, dict) and audit.get("audit_id") is not None:
        return "audit-%s" % audit["audit_id"]
    return ""
