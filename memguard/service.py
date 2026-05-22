from pathlib import Path
from typing import Dict, List, Optional

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
from memguard.forgetting.planner import ForgetPlanner
from memguard.ingestion.behavior import BehaviorEvent, load_shell_history
from memguard.ingestion.config import config_entry_to_memory_text, load_config_entries, validate_config_entry
from memguard.ingestion.quality import assess_text_quality
from memguard.ingestion.tool_result import ToolResult
from memguard.models import AuditInfo, MemoryRecord, MemorySource, VersionInfo, make_memory, new_id, stable_hash, utc_now
from memguard.os_guard.events import parse_os_event
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
        self.retriever = HybridRetriever()
        self.conflicts = ConflictResolver()
        self.forget_planner = ForgetPlanner()
        self.journald = JournaldLogger()

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
            self._audit("quality_reject", None, detail)
            return {"stored": False, "reason": "quality_reject", "quality": quality.to_dict()}

        sensitivity = self.detector.classify(clean_text, source_uri)
        if sensitivity.level == "high":
            detail = {
                "source_kind": source_kind,
                "source_uri": source_uri,
                "labels": sensitivity.labels,
                "quality": quality.to_dict(),
                "reason": "high risk sensitive content is not stored in long-term memory",
            }
            self._audit("sensitive_block", None, detail)
            return {"stored": False, "reason": "sensitive_block", "sensitivity": sensitivity, "quality": quality.to_dict()}

        stored_text = clean_text
        if sensitivity.level in {"medium", "low"} and sensitivity.labels:
            stored_text = self.detector.redact(clean_text, source_uri)
            sensitivity.redacted = True

        memory_type = self._classify_memory(stored_text)
        subtype = self._classify_subtype(stored_text, memory_type, source_kind)
        record = make_memory(
            content=stored_text,
            memory_type=memory_type,
            source=MemorySource(kind=source_kind, uri=source_uri),
            summary=self._summarize(stored_text, memory_type),
            subtype=subtype,
            scope=self.namespace,
            tier=tier or self._default_tier(memory_type, source_kind),
            sensitivity=sensitivity,
        )
        record.confidence = min(record.confidence, quality.score)
        self._enrich_graph(record)
        self.store.append(record)
        self.meta.upsert_memory(record)
        self._audit(
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
            },
        )
        return {"stored": True, "record": record, "quality": quality.to_dict()}

    def ingest_tool_result(self, tool_result: ToolResult, source_uri: str = "tool_result") -> Dict[str, object]:
        return self.ingest_text(
            tool_result.to_memory_text(),
            source_kind="tool_result",
            source_uri=source_uri,
            tier="session" if tool_result.exit_code == 0 else "working",
        )

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

        sensitivity = self.detector.classify(text, previous.source.uri)
        if sensitivity.level == "high":
            self._audit(
                "sensitive_block",
                memory_id,
                {
                    "labels": sensitivity.labels,
                    "reason": "high risk update is not stored",
                    "update_reason": reason,
                },
            )
            return {"updated": False, "reason": "sensitive_block", "sensitivity": sensitivity}

        stored_text = text
        if sensitivity.level in {"medium", "low"} and sensitivity.labels:
            stored_text = self.detector.redact(text, previous.source.uri)
            sensitivity.redacted = True

        record = MemoryRecord(
            memory_id=previous.memory_id,
            type=self._classify_memory(stored_text),
            subtype=self._classify_subtype(stored_text, self._classify_memory(stored_text), "update"),
            scope=previous.scope,
            tier=previous.tier,
            content=stored_text,
            summary=self._summarize(stored_text, self._classify_memory(stored_text)),
            source=MemorySource(kind="update", uri=previous.memory_id),
            evidence=[stored_text[:240]],
            confidence=previous.confidence,
            importance=previous.importance,
            language=previous.language,
            entities=previous.entities,
            relations=previous.relations,
            sensitivity=sensitivity,
            version=VersionInfo(version_id=new_id("ver"), parent_version_id=previous.version.version_id, op="update"),
            audit=AuditInfo(created_by="memguard", created_at=utc_now()),
        ).finalize_hash()
        self._enrich_graph(record)
        self.store.append(record)
        self.meta.upsert_memory(record)
        self._audit("update", memory_id, {"reason": reason, "version_id": record.version.version_id})
        return {"updated": True, "record": record}

    def list_memories(self, include_deleted: bool = False, tier: Optional[str] = None) -> List[Dict[str, object]]:
        return self.meta.list_memories(include_deleted=include_deleted, tier=tier)

    def promote_memory(self, memory_id: str, tier: str) -> Dict[str, object]:
        if tier not in {"working", "session", "long_term"}:
            return {"promoted": False, "reason": "invalid_tier", "tier": tier}
        record = self.store.get_latest(memory_id)
        if record is None:
            return {"promoted": False, "reason": "memory_not_found", "memory_id": memory_id}
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
            version=VersionInfo(version_id=new_id("ver"), parent_version_id=record.version.version_id, op="promote"),
            audit=AuditInfo(created_by="memguard", created_at=utc_now()),
        ).finalize_hash()
        self.store.append(promoted)
        self.meta.upsert_memory(promoted)
        self._audit("promote", memory_id, {"tier": tier, "version_id": promoted.version.version_id})
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
            self.store.tombstone(record, "tier eviction: " + tier)
            self.meta.mark_deleted(record.memory_id)
            evicted.append(record.memory_id)
        self._audit("tier_evict", None, {"tier": tier, "evicted": evicted})
        return {"evicted": True, "tier": tier, "memory_ids": evicted}

    def versions(self, memory_id: str) -> List[Dict[str, object]]:
        versions = []
        for record in self.store.get_versions(memory_id):
            versions.append(
                {
                    "memory_id": record.memory_id,
                    "version_id": record.version.version_id,
                    "parent_version_id": record.version.parent_version_id,
                    "op": record.version.op,
                    "summary": record.summary,
                    "deleted": record.deleted,
                    "created_at": record.audit.created_at,
                    "hash": record.audit.hash,
                }
            )
        self._audit("versions", memory_id, {"count": len(versions)})
        return versions

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
            version=VersionInfo(version_id=new_id("ver"), parent_version_id=latest.version.version_id, op="rollback"),
            audit=AuditInfo(created_by="memguard", created_at=utc_now(), hash=stable_hash(target.memory_id + version_id + utc_now())),
            deleted=False,
        )
        self.store.append(record)
        self.meta.upsert_memory(record)
        self._audit("rollback", memory_id, {"target_version_id": version_id, "new_version_id": record.version.version_id})
        return {"rolled_back": True, "record": record}

    def search(self, query: str, limit: int = 5) -> List[Dict[str, object]]:
        candidates = self._search_candidates(query, limit=max(limit * 8, 80))
        ranked = self.retriever.rank(query, candidates, limit=limit)
        results = []
        seen = set()
        for score, record in ranked:
            item = dict(record)
            item["score"] = round(score, 4)
            item["match_source"] = "hybrid"
            item["vector_backend"] = item.pop("_vector_backend", self.retriever.last_backend_used)
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
        self._audit("search", None, {"query": query, "count": len(results)})
        return results[:limit]

    def build_context(self, query: str, limit: int = 5, max_chars: int = 2000, token_budget: Optional[int] = None) -> Dict[str, object]:
        effective_max_chars = max_chars
        if token_budget and token_budget > 0:
            # Conservative cap for mixed Chinese/English contexts; exact tokenizers
            # are provider-specific, so the package reports estimates instead.
            effective_max_chars = min(max_chars, max(256, int(token_budget / 0.65)))
        hits = self.search(query, limit=limit)
        memories = []
        lines = []
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
                break
            if len(line) > remaining:
                line = line[: max(0, remaining - 16)] + "\n[truncated]"
                truncated = True
            used += len(line)
            lines.append(line)
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
            },
        )
        return result

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
        seen = set()
        candidates: List[Dict[str, object]] = []
        for source in (
            self.meta.search_text(query, limit=limit),
            self.meta.graph_search(query, limit=max(10, limit // 4)),
            self.meta.list_memories(include_deleted=False)[:limit],
        ):
            for record in source:
                memory_id = record.get("memory_id")
                if memory_id in seen:
                    continue
                seen.add(memory_id)
                candidates.append(record)
        if not candidates:
            return self.meta.list_memories(include_deleted=False)
        return candidates

    def graph_search(self, entity_query: str, limit: int = 10) -> List[Dict[str, object]]:
        results = self.meta.graph_search(entity_query, limit=limit)
        self._audit("graph_search", None, {"entity_query": entity_query, "count": len(results)})
        return results

    def related(self, memory_id: str, limit: int = 10) -> Dict[str, object]:
        graph = self.meta.memory_graph(memory_id)
        related = self.meta.related_memories(memory_id, limit=limit)
        result = {"graph": graph, "related": related}
        self._audit("related", memory_id, {"related_count": len(related), "entity_count": len(graph["entities"])})
        return result

    def forget(self, request: str, confirm: bool = False, compact: bool = False) -> Dict[str, object]:
        records = self.meta.list_memories(include_deleted=False)
        candidates = self.forget_planner.candidates(request, records)
        if not confirm:
            self._audit("forget_dry_run", None, {"request": request, "candidate_count": len(candidates)})
            return {"confirmed": False, "request": request, "candidates": candidates}

        forgotten = []
        for candidate in candidates:
            memory_id = str(candidate["memory_id"])
            record = self.store.get_latest(memory_id)
            if record is None:
                continue
            self.store.tombstone(record, request)
            self.meta.mark_deleted(memory_id)
            forgotten.append(memory_id)
        if compact:
            self.store.compact()
        self._audit(
            "forget",
            None,
            {"request": request, "forgotten": forgotten, "compact": compact},
        )
        return {"confirmed": True, "request": request, "forgotten": forgotten}

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
            "default_backend": "hash",
            "kylin_sdk_role": "optional_rerank_backend",
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
        status["record_count"] = len(self.meta.list_memories(include_deleted=True))
        status["live_record_count"] = len(self.meta.list_memories(include_deleted=False))
        status["audit_event_count"] = len(self.meta.list_audit(limit=1000000))
        self._audit(
            "namespace_status",
            None,
            {
                "namespace_home": status["namespace_home"],
                "record_count": status["record_count"],
                "live_record_count": status["live_record_count"],
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

    def _audit(self, event_type: str, memory_id: Optional[str], detail: Dict[str, object]) -> None:
        detail = dict(detail)
        detail.setdefault("namespace", self.namespace)
        self.meta.add_audit(event_type, memory_id, detail)
        self.journald.log(event_type, memory_id, detail)

    @staticmethod
    def _enrich_graph(record: MemoryRecord) -> None:
        record.entities = extract_entities(record.content + " " + record.summary)
        record.relations = extract_relations(record.entities)

    @staticmethod
    def _classify_memory(text: str) -> str:
        lowered = text.lower()
        preference_markers = ["以后", "偏好", "喜欢", "不要", "请总是", "请先", "请优先", "prefer", "style", "答辩材料"]
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
            if any(marker in lowered for marker in ["简洁", "详细", "中文", "英文", "style", "回答", "最短", "风险提醒", "答辩", "材料", "证据边界"]):
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
