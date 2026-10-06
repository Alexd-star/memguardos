from pathlib import Path
from typing import Any, Callable, Dict, List, Optional
import json
import sqlite3
from datetime import datetime, timedelta

from memguard.models import MemoryRecord, new_id, stable_hash, utc_now
from memguard.extraction.fact_slot import parse_fact_slot
from memguard.forgetting.plan import ErasurePlan
from memguard.store.permissions import ensure_private_directory, ensure_private_file
from memguard.store.dependencies import DependencyStoreMixin
from memguard.retrieval.vector_cache import (
    PersistedVector,
    decode_vector,
    encode_vector,
    vector_content_hash,
)


def fact_slot_scope(namespace: str, retention_session_id: str = "") -> str:
    return (
        "%s::session:%s" % (namespace, retention_session_id)
        if retention_session_id else namespace
    )


_ALLOWED_COLUMN_MIGRATIONS = {
    ("memory", "subtype", "TEXT NOT NULL DEFAULT 'general'"),
    ("memory", "tier", "TEXT NOT NULL DEFAULT 'long_term'"),
    ("memory", "sensitivity_redacted", "INTEGER NOT NULL DEFAULT 0"),
    ("memory", "valid_from", "TEXT NOT NULL DEFAULT ''"),
    ("memory", "valid_to", "TEXT"),
    ("memory", "superseded_by", "TEXT"),
    ("memory", "preference_topic", "TEXT NOT NULL DEFAULT ''"),
    ("memory", "preference_value", "TEXT NOT NULL DEFAULT ''"),
    ("memory", "preference_scope", "TEXT NOT NULL DEFAULT 'global'"),
    ("memory", "preference_explicit", "INTEGER NOT NULL DEFAULT 1"),
    ("memory", "agent_name", "TEXT NOT NULL DEFAULT ''"),
    ("memory", "preference_session_id", "TEXT NOT NULL DEFAULT ''"),
    ("memory", "retention_session_id", "TEXT NOT NULL DEFAULT ''"),
    ("memory", "project_root", "TEXT NOT NULL DEFAULT ''"),
    ("memory", "task_type", "TEXT NOT NULL DEFAULT ''"),
    ("memory", "trust_level", "TEXT NOT NULL DEFAULT 'trusted'"),
    ("memory", "trust_score", "REAL NOT NULL DEFAULT 1.0"),
    ("memory", "governance_state", "TEXT NOT NULL DEFAULT 'active'"),
    ("memory", "governance_reason", "TEXT NOT NULL DEFAULT ''"),
    ("memory", "hit_count", "INTEGER NOT NULL DEFAULT 0"),
    ("memory", "last_hit_at", "TEXT NOT NULL DEFAULT ''"),
    ("audit", "prev_hash", "TEXT NOT NULL DEFAULT ''"),
    ("audit", "event_hash", "TEXT NOT NULL DEFAULT ''"),
    ("agent_events", "project_root", "TEXT NOT NULL DEFAULT ''"),
    ("agent_events", "task_type", "TEXT NOT NULL DEFAULT ''"),
    ("agent_events", "forgotten", "INTEGER NOT NULL DEFAULT 0"),
    ("agent_events", "forgotten_at", "TEXT"),
    ("preference_observations", "project_root", "TEXT NOT NULL DEFAULT ''"),
    ("preference_observations", "task_type", "TEXT NOT NULL DEFAULT ''"),
    ("preference_observations", "scope_agent_name", "TEXT NOT NULL DEFAULT ''"),
    ("preference_observations", "scope_session_id", "TEXT NOT NULL DEFAULT ''"),
    ("preference_observations", "source_field", "TEXT NOT NULL DEFAULT ''"),
    ("preference_observations", "source_start", "INTEGER NOT NULL DEFAULT -1"),
    ("preference_observations", "source_end", "INTEGER NOT NULL DEFAULT -1"),
    ("preference_observations", "source_excerpt", "TEXT NOT NULL DEFAULT ''"),
    ("memory_event_links", "source_field", "TEXT NOT NULL DEFAULT ''"),
    ("memory_event_links", "source_start", "INTEGER NOT NULL DEFAULT -1"),
    ("memory_event_links", "source_end", "INTEGER NOT NULL DEFAULT -1"),
    ("memory_event_links", "source_excerpt", "TEXT NOT NULL DEFAULT ''"),
    ("memory_embedding", "attempt_count", "INTEGER NOT NULL DEFAULT 0"),
    ("memory_embedding", "next_attempt_at", "TEXT NOT NULL DEFAULT ''"),
    ("memory_embedding", "job_token", "TEXT NOT NULL DEFAULT ''"),
    ("pending_memory_write", "expected_version_id", "TEXT NOT NULL DEFAULT ''"),
    ("pending_memory_write", "previous_record_json", "TEXT NOT NULL DEFAULT ''"),
    ("pending_memory_write", "source_link_json", "TEXT NOT NULL DEFAULT ''"),
    ("pending_memory_write", "fact_conflict_id", "TEXT NOT NULL DEFAULT ''"),
    ("fact_conflict", "project_root", "TEXT NOT NULL DEFAULT ''"),
    ("fact_conflict", "candidate_version_ids_json", "TEXT NOT NULL DEFAULT '{}'"),
    ("fact_conflict", "source_link_json", "TEXT NOT NULL DEFAULT ''"),
}


class FactSlotConflict(ValueError):
    def __init__(self, candidate_memory_ids: List[str]):
        super().__init__("fact slot already has an active memory")
        self.candidate_memory_ids = candidate_memory_ids


class FactConflictChanged(ValueError):
    def __init__(self, reason: str):
        super().__init__(reason)
        self.reason = reason


class SQLiteMetaStore(DependencyStoreMixin):
    def __init__(self, path: Path, expected_namespace: Optional[str] = None):
        self.path = Path(path)
        self.permission_corrections = []
        correction = ensure_private_directory(self.path.parent)
        if correction:
            self.permission_corrections.append(correction)
        self.conn = sqlite3.connect(str(self.path), timeout=30.0)
        correction = ensure_private_file(self.path)
        if correction:
            self.permission_corrections.append(correction)
        self.conn.row_factory = sqlite3.Row
        self.conn.create_function(
            "memguard_content_hash", 2, vector_content_hash, deterministic=True
        )
        self.conn.execute("PRAGMA busy_timeout = 30000")
        self.conn.execute("PRAGMA temp_store = MEMORY")
        self.conn.execute("PRAGMA cache_size = -32768")
        self.conn.execute("PRAGMA mmap_size = 268435456")
        self.conn.execute("PRAGMA secure_delete = ON")
        try:
            self.conn.execute("PRAGMA journal_mode = WAL")
            self.conn.execute("PRAGMA synchronous = FULL")
        except sqlite3.OperationalError:
            pass
        self._fts_enabled = False
        try:
            if expected_namespace is not None:
                self._check_namespace_owner(expected_namespace)
            self.init_schema()
        except Exception:
            self.conn.close()
            raise

    def _check_namespace_owner(self, namespace: str) -> None:
        tables = {
            str(row["name"])
            for row in self.conn.execute(
                """SELECT name FROM sqlite_master WHERE type = 'table'
                   AND name IN ('storage_identity', 'memory', 'pending_memory_write', 'agent_events')"""
            )
        }
        if "storage_identity" in tables:
            existing = self.conn.execute(
                "SELECT namespace FROM storage_identity WHERE singleton = 1"
            ).fetchone()
            if existing is not None:
                if existing["namespace"] != namespace:
                    raise ValueError("SQLite metadata belongs to another namespace")
                return
        else:
            existing = None
        scopes = set()
        if "memory" in tables:
            scopes.update(str(row["scope"]) for row in self.conn.execute("SELECT DISTINCT scope FROM memory"))
        if "pending_memory_write" in tables:
            scopes.update(
                str(row["scope"] or "")
                for row in self.conn.execute(
                    """SELECT DISTINCT json_extract(record_json, '$.scope') AS scope
                       FROM pending_memory_write"""
                )
            )
        allowed_scopes = {namespace, "user"} if namespace == "default" else {namespace}
        if scopes - allowed_scopes:
            raise ValueError("SQLite metadata contains records from another namespace")
        if existing is None and not scopes and namespace != "default" and "agent_events" in tables:
            if self.conn.execute("SELECT 1 FROM agent_events LIMIT 1").fetchone() is not None:
                raise ValueError("unbound event history has no verifiable namespace")

    def init_schema(self) -> None:
        self.conn.execute(
            """
            CREATE TABLE IF NOT EXISTS memory (
                memory_id TEXT PRIMARY KEY,
                type TEXT NOT NULL,
                subtype TEXT NOT NULL DEFAULT 'general',
                scope TEXT NOT NULL,
                tier TEXT NOT NULL DEFAULT 'long_term',
                summary TEXT NOT NULL,
                content TEXT NOT NULL,
                source_kind TEXT NOT NULL,
                source_uri TEXT NOT NULL,
                sensitivity_level TEXT NOT NULL,
                sensitivity_labels TEXT NOT NULL,
                sensitivity_redacted INTEGER NOT NULL DEFAULT 0,
                version_id TEXT NOT NULL,
                valid_from TEXT NOT NULL DEFAULT '',
                valid_to TEXT,
                superseded_by TEXT,
                preference_topic TEXT NOT NULL DEFAULT '',
                preference_value TEXT NOT NULL DEFAULT '',
                preference_scope TEXT NOT NULL DEFAULT 'global',
                preference_explicit INTEGER NOT NULL DEFAULT 1,
                agent_name TEXT NOT NULL DEFAULT '',
                preference_session_id TEXT NOT NULL DEFAULT '',
                retention_session_id TEXT NOT NULL DEFAULT '',
                project_root TEXT NOT NULL DEFAULT '',
                task_type TEXT NOT NULL DEFAULT '',
                trust_level TEXT NOT NULL DEFAULT 'trusted',
                trust_score REAL NOT NULL DEFAULT 1.0,
                governance_state TEXT NOT NULL DEFAULT 'active',
                governance_reason TEXT NOT NULL DEFAULT '',
                deleted INTEGER NOT NULL DEFAULT 0,
                updated_at TEXT NOT NULL
            )
            """
        )
        self.conn.execute(
            """
            CREATE TABLE IF NOT EXISTS pending_erasure (
                memory_id TEXT PRIMARY KEY,
                tombstone_version_id TEXT NOT NULL,
                compact_required INTEGER NOT NULL,
                created_at TEXT NOT NULL
            )
            """
        )
        self.conn.execute(
            """
            CREATE TABLE IF NOT EXISTS pending_memory_write (
                version_id TEXT PRIMARY KEY,
                memory_id TEXT NOT NULL,
                record_json TEXT NOT NULL,
                backend TEXT NOT NULL,
                model_revision TEXT NOT NULL,
                content_hash TEXT NOT NULL,
                expected_version_id TEXT NOT NULL DEFAULT '',
                previous_record_json TEXT NOT NULL DEFAULT '',
                source_link_json TEXT NOT NULL DEFAULT '',
                fact_conflict_id TEXT NOT NULL DEFAULT '',
                created_at TEXT NOT NULL
            )
            """
        )
        self._ensure_column("pending_memory_write", "expected_version_id", "TEXT NOT NULL DEFAULT ''")
        self._ensure_column("pending_memory_write", "previous_record_json", "TEXT NOT NULL DEFAULT ''")
        self._ensure_column("pending_memory_write", "source_link_json", "TEXT NOT NULL DEFAULT ''")
        self._ensure_column("pending_memory_write", "fact_conflict_id", "TEXT NOT NULL DEFAULT ''")
        self.conn.execute("""CREATE TABLE IF NOT EXISTS pending_segment_cleanup (
            memory_id TEXT NOT NULL, version_id TEXT NOT NULL,
            previous_version_id TEXT NOT NULL, created_at TEXT NOT NULL,
            PRIMARY KEY(memory_id, version_id)
        )""")
        # Cancellation may follow a durable append and a rolled-back metadata
        # commit. Keep its physical cleanup durable without retaining plaintext.
        self.conn.execute("""CREATE TRIGGER IF NOT EXISTS queue_cancelled_derived_segment
            AFTER DELETE ON pending_memory_write
            WHEN json_array_length(OLD.record_json, '$.dependencies') > 0
                AND NOT EXISTS (SELECT 1 FROM memory
                    WHERE memory_id = OLD.memory_id AND version_id = OLD.version_id)
            BEGIN
                INSERT OR IGNORE INTO pending_segment_cleanup
                    (memory_id, version_id, previous_version_id, created_at)
                VALUES (OLD.memory_id, OLD.version_id, OLD.expected_version_id, OLD.created_at);
            END""")
        self._ensure_column("memory", "subtype", "TEXT NOT NULL DEFAULT 'general'")
        self._ensure_column("memory", "tier", "TEXT NOT NULL DEFAULT 'long_term'")
        self._ensure_column("memory", "sensitivity_redacted", "INTEGER NOT NULL DEFAULT 0")
        self._ensure_column("memory", "valid_from", "TEXT NOT NULL DEFAULT ''")
        self._ensure_column("memory", "valid_to", "TEXT")
        self._ensure_column("memory", "superseded_by", "TEXT")
        self._ensure_column("memory", "preference_topic", "TEXT NOT NULL DEFAULT ''")
        self._ensure_column("memory", "preference_value", "TEXT NOT NULL DEFAULT ''")
        self._ensure_column("memory", "preference_scope", "TEXT NOT NULL DEFAULT 'global'")
        self._ensure_column("memory", "preference_explicit", "INTEGER NOT NULL DEFAULT 1")
        self._ensure_column("memory", "agent_name", "TEXT NOT NULL DEFAULT ''")
        self._ensure_column("memory", "preference_session_id", "TEXT NOT NULL DEFAULT ''")
        self._ensure_column("memory", "retention_session_id", "TEXT NOT NULL DEFAULT ''")
        self._ensure_column("memory", "project_root", "TEXT NOT NULL DEFAULT ''")
        self._ensure_column("memory", "task_type", "TEXT NOT NULL DEFAULT ''")
        self._ensure_column("memory", "trust_level", "TEXT NOT NULL DEFAULT 'trusted'")
        self._ensure_column("memory", "trust_score", "REAL NOT NULL DEFAULT 1.0")
        self._ensure_column("memory", "governance_state", "TEXT NOT NULL DEFAULT 'active'")
        self._ensure_column("memory", "governance_reason", "TEXT NOT NULL DEFAULT ''")
        self._ensure_column("memory", "hit_count", "INTEGER NOT NULL DEFAULT 0")
        self._ensure_column("memory", "last_hit_at", "TEXT NOT NULL DEFAULT ''")
        self.conn.execute(
            """
            CREATE TABLE IF NOT EXISTS audit (
                audit_id INTEGER PRIMARY KEY AUTOINCREMENT,
                event_type TEXT NOT NULL,
                memory_id TEXT,
                detail TEXT NOT NULL,
                created_at TEXT NOT NULL,
                prev_hash TEXT NOT NULL DEFAULT '',
                event_hash TEXT NOT NULL DEFAULT ''
            )
            """
        )
        self._ensure_column("audit", "prev_hash", "TEXT NOT NULL DEFAULT ''")
        self._ensure_column("audit", "event_hash", "TEXT NOT NULL DEFAULT ''")
        self.conn.execute(
            """
            CREATE TABLE IF NOT EXISTS entity (
                entity TEXT PRIMARY KEY,
                updated_at TEXT NOT NULL
            )
            """
        )
        self.conn.execute(
            """
            CREATE TABLE IF NOT EXISTS memory_entity (
                memory_id TEXT NOT NULL,
                entity TEXT NOT NULL,
                PRIMARY KEY(memory_id, entity)
            )
            """
        )
        self.conn.execute(
            """
            CREATE TABLE IF NOT EXISTS relation (
                relation_id INTEGER PRIMARY KEY AUTOINCREMENT,
                memory_id TEXT NOT NULL,
                source TEXT NOT NULL,
                relation TEXT NOT NULL,
                target TEXT NOT NULL
            )
            """
        )
        self.conn.execute(
            """
            CREATE TABLE IF NOT EXISTS agent_events (
                event_id TEXT PRIMARY KEY,
                provenance_hash TEXT NOT NULL UNIQUE,
                schema_version TEXT NOT NULL,
                agent_name TEXT NOT NULL,
                agent_version TEXT NOT NULL,
                session_id TEXT NOT NULL,
                turn_id TEXT NOT NULL,
                sequence INTEGER NOT NULL,
                event_type TEXT NOT NULL,
                role TEXT NOT NULL,
                content TEXT NOT NULL,
                tool_name TEXT NOT NULL,
                tool_args_json TEXT NOT NULL,
                tool_result TEXT NOT NULL,
                exit_code INTEGER,
                state_before TEXT NOT NULL DEFAULT '',
                state_after TEXT NOT NULL DEFAULT '',
                user_feedback TEXT NOT NULL DEFAULT '',
                project_root TEXT NOT NULL DEFAULT '',
                task_type TEXT NOT NULL DEFAULT '',
                timestamp TEXT NOT NULL,
                source_uri TEXT NOT NULL,
                forgotten INTEGER NOT NULL DEFAULT 0,
                forgotten_at TEXT,
                created_at TEXT NOT NULL
            )
            """
        )
        self._ensure_column("agent_events", "project_root", "TEXT NOT NULL DEFAULT ''")
        self._ensure_column("agent_events", "task_type", "TEXT NOT NULL DEFAULT ''")
        self._ensure_column("agent_events", "forgotten", "INTEGER NOT NULL DEFAULT 0")
        self._ensure_column("agent_events", "forgotten_at", "TEXT")
        self.conn.execute(
            """
            CREATE TABLE IF NOT EXISTS memory_event_links (
                memory_id TEXT NOT NULL,
                event_id TEXT NOT NULL,
                relation TEXT NOT NULL,
                source_field TEXT NOT NULL DEFAULT '',
                source_start INTEGER NOT NULL DEFAULT -1,
                source_end INTEGER NOT NULL DEFAULT -1,
                source_excerpt TEXT NOT NULL DEFAULT '',
                created_at TEXT NOT NULL,
                PRIMARY KEY (memory_id, event_id, relation)
            )
            """
        )
        self._ensure_column("memory_event_links", "source_field", "TEXT NOT NULL DEFAULT ''")
        self._ensure_column("memory_event_links", "source_start", "INTEGER NOT NULL DEFAULT -1")
        self._ensure_column("memory_event_links", "source_end", "INTEGER NOT NULL DEFAULT -1")
        self._ensure_column("memory_event_links", "source_excerpt", "TEXT NOT NULL DEFAULT ''")
        self.conn.execute(
            """
            CREATE TABLE IF NOT EXISTS preference_observations (
                observation_id INTEGER PRIMARY KEY AUTOINCREMENT,
                event_id TEXT NOT NULL,
                agent_name TEXT NOT NULL,
                session_id TEXT NOT NULL,
                topic TEXT NOT NULL,
                value TEXT NOT NULL,
                polarity TEXT NOT NULL,
                preference_scope TEXT NOT NULL,
                explicit INTEGER NOT NULL,
                confidence REAL NOT NULL,
                project_root TEXT NOT NULL DEFAULT '',
                task_type TEXT NOT NULL DEFAULT '',
                scope_agent_name TEXT NOT NULL DEFAULT '',
                scope_session_id TEXT NOT NULL DEFAULT '',
                source_field TEXT NOT NULL DEFAULT '',
                source_start INTEGER NOT NULL DEFAULT -1,
                source_end INTEGER NOT NULL DEFAULT -1,
                source_excerpt TEXT NOT NULL DEFAULT '',
                promoted_memory_id TEXT,
                created_at TEXT NOT NULL,
                UNIQUE(event_id, topic, value, polarity, preference_scope)
            )
            """
        )
        self._ensure_column("preference_observations", "project_root", "TEXT NOT NULL DEFAULT ''")
        self._ensure_column("preference_observations", "task_type", "TEXT NOT NULL DEFAULT ''")
        self._ensure_column("preference_observations", "scope_agent_name", "TEXT NOT NULL DEFAULT ''")
        self._ensure_column("preference_observations", "scope_session_id", "TEXT NOT NULL DEFAULT ''")
        self._ensure_column("preference_observations", "source_field", "TEXT NOT NULL DEFAULT ''")
        self._ensure_column("preference_observations", "source_start", "INTEGER NOT NULL DEFAULT -1")
        self._ensure_column("preference_observations", "source_end", "INTEGER NOT NULL DEFAULT -1")
        self._ensure_column("preference_observations", "source_excerpt", "TEXT NOT NULL DEFAULT ''")
        self._init_memory_embedding_schema()
        self.conn.execute(
            """
            CREATE TABLE IF NOT EXISTS embedding_scan_cursor (
                backend TEXT NOT NULL,
                model_revision TEXT NOT NULL,
                last_memory_id TEXT NOT NULL DEFAULT '',
                updated_at TEXT NOT NULL,
                PRIMARY KEY(backend, model_revision)
            )
            """
        )
        try:
            self.conn.execute(
                """
                CREATE VIRTUAL TABLE IF NOT EXISTS memory_fts
                USING fts5(memory_id UNINDEXED, content, summary, type)
                """
            )
            self._fts_enabled = True
        except sqlite3.OperationalError:
            self._fts_enabled = False
        self.conn.execute(
            """CREATE TABLE IF NOT EXISTS schema_migrations (
                name TEXT PRIMARY KEY, applied_at TEXT NOT NULL
            )"""
        )
        self.conn.execute(
            """CREATE TABLE IF NOT EXISTS fact_slot (
                memory_id TEXT PRIMARY KEY,
                scope TEXT NOT NULL,
                subject TEXT NOT NULL,
                predicate TEXT NOT NULL
            )"""
        )
        self.conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_fact_slot_key ON fact_slot(scope, subject, predicate)"
        )
        self.conn.execute(
            """CREATE TABLE IF NOT EXISTS fact_conflict (
                conflict_id TEXT PRIMARY KEY,
                scope TEXT NOT NULL,
                tier TEXT NOT NULL,
                project_root TEXT NOT NULL DEFAULT '',
                subject TEXT NOT NULL,
                predicate TEXT NOT NULL,
                incoming_value TEXT NOT NULL,
                incoming_content TEXT NOT NULL,
                source_kind TEXT NOT NULL,
                source_uri TEXT NOT NULL,
                candidate_memory_ids_json TEXT NOT NULL,
                candidate_version_ids_json TEXT NOT NULL DEFAULT '{}',
                source_link_json TEXT NOT NULL DEFAULT '',
                status TEXT NOT NULL CHECK(status IN ('pending', 'resolved', 'dismissed')),
                resolution TEXT NOT NULL DEFAULT '',
                created_at TEXT NOT NULL,
                resolved_at TEXT NOT NULL DEFAULT '',
                resolved_memory_id TEXT NOT NULL DEFAULT ''
            )"""
        )
        self._ensure_column("fact_conflict", "project_root", "TEXT NOT NULL DEFAULT ''")
        self._ensure_column("fact_conflict", "candidate_version_ids_json", "TEXT NOT NULL DEFAULT '{}'")
        self._ensure_column("fact_conflict", "source_link_json", "TEXT NOT NULL DEFAULT ''")
        self.conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_fact_conflict_status "
            "ON fact_conflict(status, created_at DESC)"
        )
        self.conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_fact_conflict_slot "
            "ON fact_conflict(scope, subject, predicate, status, created_at DESC)"
        )
        self.conn.execute(
            """CREATE TABLE IF NOT EXISTS verified_tool_outcome (
                scope TEXT NOT NULL,
                project_root TEXT NOT NULL,
                session_id TEXT NOT NULL,
                domain TEXT NOT NULL,
                tool TEXT NOT NULL,
                success_seen INTEGER NOT NULL DEFAULT 0,
                failure_seen INTEGER NOT NULL DEFAULT 0,
                updated_at TEXT NOT NULL,
                PRIMARY KEY (scope, project_root, session_id, domain, tool)
            )"""
        )
        self.conn.execute(
            """CREATE TABLE IF NOT EXISTS verified_tool_experience (
                scope TEXT NOT NULL,
                project_root TEXT NOT NULL,
                domain TEXT NOT NULL,
                tool TEXT NOT NULL,
                memory_id TEXT NOT NULL,
                created_at TEXT NOT NULL,
                PRIMARY KEY (scope, project_root, domain, tool)
            )"""
        )
        if self.conn.execute(
            "SELECT 1 FROM schema_migrations WHERE name = 'fact_slot_backfill_v1'"
        ).fetchone() is None:
            for row in self.conn.execute(
                "SELECT memory_id, scope, retention_session_id, content FROM memory WHERE deleted = 0 AND type = 'knowledge'"
            ):
                fact = parse_fact_slot(str(row["content"]))
                if fact is not None:
                    self.conn.execute(
                        "INSERT OR IGNORE INTO fact_slot(memory_id, scope, subject, predicate) VALUES (?, ?, ?, ?)",
                        (row["memory_id"], fact_slot_scope(row["scope"], row["retention_session_id"]), fact.subject, fact.predicate),
                    )
            self.conn.execute(
                "INSERT OR IGNORE INTO schema_migrations(name, applied_at) VALUES ('fact_slot_backfill_v1', ?)",
                (utc_now(),),
            )
        self.conn.execute("CREATE INDEX IF NOT EXISTS idx_memory_live_updated ON memory(deleted, updated_at DESC)")
        self.conn.execute("CREATE INDEX IF NOT EXISTS idx_memory_tier_live ON memory(tier, deleted, updated_at DESC)")
        self.conn.execute("CREATE INDEX IF NOT EXISTS idx_memory_retention_session ON memory(retention_session_id, tier, deleted)")
        self.conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_verified_tool_outcome_key "
            "ON verified_tool_outcome(scope, project_root, domain, tool)"
        )
        self.conn.execute("CREATE INDEX IF NOT EXISTS idx_memory_governance ON memory(governance_state, deleted, updated_at DESC)")
        self.conn.execute("CREATE INDEX IF NOT EXISTS idx_memory_entity_entity ON memory_entity(entity, memory_id)")
        self.conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_agent_events_session_sequence "
            "ON agent_events(agent_name, session_id, sequence)"
        )
        self.conn.execute("CREATE INDEX IF NOT EXISTS idx_agent_events_type ON agent_events(event_type, created_at DESC)")
        self.conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_memory_event_links_event "
            "ON memory_event_links(event_id, memory_id)"
        )
        self.conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_preference_observations_topic "
            "ON preference_observations(topic, value, created_at)"
        )
        self.conn.execute(
            """
            CREATE INDEX IF NOT EXISTS idx_memory_embedding_cache_status
            ON memory_embedding(backend, model_revision, status, updated_at)
            """
        )
        self._repair_preference_observation_owners_once()
        self._init_dependency_schema()
        self.conn.commit()

    def _repair_preference_observation_owners_once(self) -> None:
        migration = "preference_observation_owner_v1"
        if self.conn.execute(
            "SELECT 1 FROM schema_migrations WHERE name = ?", (migration,)
        ).fetchone() is not None:
            return
        mismatched = self.conn.execute(
            """SELECT p.observation_id, p.event_id, p.topic, p.value,
                      p.preference_scope, p.project_root, p.task_type,
                      p.scope_agent_name, p.scope_session_id
               FROM preference_observations p
               LEFT JOIN memory current ON current.memory_id = p.promoted_memory_id
               WHERE p.promoted_memory_id IS NOT NULL
                 AND (current.memory_id IS NULL OR current.preference_topic != p.topic
                      OR current.preference_value != p.value
                      OR current.preference_scope != p.preference_scope
                      OR current.project_root != p.project_root
                      OR current.task_type != p.task_type
                      OR current.agent_name != p.scope_agent_name
                      OR current.preference_session_id != p.scope_session_id)"""
        ).fetchall()
        repaired = 0
        for observation in mismatched:
            matches = self.conn.execute(
                """SELECT DISTINCT m.memory_id FROM memory_event_links l
                   JOIN memory m ON m.memory_id = l.memory_id
                   WHERE l.event_id = ? AND l.relation IN ('confirmed_by', 'extracted_from')
                     AND m.deleted = 0
                     AND m.preference_topic = ? AND m.preference_value = ?
                     AND m.preference_scope = ? AND m.project_root = ?
                     AND m.task_type = ? AND m.agent_name = ?
                     AND m.preference_session_id = ? LIMIT 2""",
                (
                    observation["event_id"], observation["topic"], observation["value"],
                    observation["preference_scope"], observation["project_root"],
                    observation["task_type"], observation["scope_agent_name"],
                    observation["scope_session_id"],
                ),
            ).fetchall()
            if len(matches) == 1:
                self.conn.execute(
                    "UPDATE preference_observations SET promoted_memory_id = ? WHERE observation_id = ?",
                    (matches[0]["memory_id"], observation["observation_id"]),
                )
                repaired += 1
        unresolved = len(mismatched) - repaired
        if mismatched:
            self._add_audit_no_commit(
                "preference_observation_owner_repair", None,
                {"repaired": repaired, "unresolved": unresolved},
            )
        self.conn.execute(
            "INSERT OR IGNORE INTO schema_migrations(name, applied_at) VALUES (?, ?)",
            (migration, utc_now()),
        )

    def close(self) -> None:
        self.conn.close()

    def bind_namespace(self, namespace: str) -> None:
        """Fail closed when a database path is reused by another namespace."""

        self.conn.execute("BEGIN IMMEDIATE")
        try:
            self.conn.execute(
                """CREATE TABLE IF NOT EXISTS storage_identity (
                    singleton INTEGER PRIMARY KEY CHECK(singleton = 1),
                    namespace TEXT NOT NULL
                )"""
            )
            self._check_namespace_owner(namespace)
            existing = self.conn.execute(
                "SELECT namespace FROM storage_identity WHERE singleton = 1"
            ).fetchone()
            if existing is None:
                self.conn.execute(
                    "INSERT INTO storage_identity(singleton, namespace) VALUES (1, ?)",
                    (namespace,),
                )
            self.conn.commit()
        except Exception:
            self.conn.rollback()
            raise

    def _check_segment_cleanup_no_commit(self, record: MemoryRecord) -> None:
        if self.conn.execute(
            "SELECT 1 FROM pending_segment_cleanup WHERE memory_id = ? AND version_id = ?",
            (record.memory_id, record.version.version_id),
        ).fetchone() is not None:
            raise ValueError("cancelled segment version requires cleanup")

    def _upsert_memory_no_commit(self, record: MemoryRecord) -> None:
        self._check_segment_cleanup_no_commit(record)
        self._validate_dependencies_no_commit(record)
        cursor = self.conn.execute(
            """
            INSERT INTO memory (
                memory_id, type, scope, tier, summary, content, source_kind, source_uri,
                sensitivity_level, sensitivity_labels, sensitivity_redacted, version_id,
                valid_from, valid_to, superseded_by, preference_topic, preference_value,
                preference_scope, preference_explicit, agent_name, preference_session_id,
                retention_session_id,
                project_root, task_type, trust_level, trust_score, governance_state,
                governance_reason, deleted, updated_at, subtype
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(memory_id) DO UPDATE SET
                type=excluded.type,
                subtype=excluded.subtype,
                scope=excluded.scope,
                tier=excluded.tier,
                summary=excluded.summary,
                content=excluded.content,
                source_kind=excluded.source_kind,
                source_uri=excluded.source_uri,
                sensitivity_level=excluded.sensitivity_level,
                sensitivity_labels=excluded.sensitivity_labels,
                sensitivity_redacted=excluded.sensitivity_redacted,
                version_id=excluded.version_id,
                valid_from=excluded.valid_from,
                valid_to=excluded.valid_to,
                superseded_by=excluded.superseded_by,
                preference_topic=excluded.preference_topic,
                preference_value=excluded.preference_value,
                preference_scope=excluded.preference_scope,
                preference_explicit=excluded.preference_explicit,
                agent_name=excluded.agent_name,
                preference_session_id=excluded.preference_session_id,
                retention_session_id=excluded.retention_session_id,
                project_root=excluded.project_root,
                task_type=excluded.task_type,
                trust_level=excluded.trust_level,
                trust_score=excluded.trust_score,
                governance_state=excluded.governance_state,
                governance_reason=excluded.governance_reason,
                deleted=excluded.deleted,
                updated_at=excluded.updated_at
            WHERE memory.deleted = 0 OR excluded.deleted = 1
            """,
            (
                record.memory_id,
                record.type,
                record.scope,
                record.tier,
                record.summary,
                record.content,
                record.source.kind,
                record.source.uri,
                record.sensitivity.level,
                json.dumps(record.sensitivity.labels, ensure_ascii=False),
                1 if record.sensitivity.redacted else 0,
                record.version.version_id,
                record.temporal.valid_from,
                record.temporal.valid_to,
                record.temporal.superseded_by,
                record.preference_topic,
                record.preference_value,
                record.preference_scope,
                1 if record.preference_explicit else 0,
                record.agent_name,
                record.preference_session_id,
                record.retention_session_id,
                record.project_root,
                record.task_type,
                record.trust_level,
                record.trust_score,
                record.governance_state,
                record.governance_reason,
                1 if record.deleted else 0,
                utc_now(),
                record.subtype,
            ),
        )
        if cursor.rowcount != 1:
            raise ValueError("forgotten memory id cannot be reactivated")
        self._register_dependencies_no_commit(record)
        if self._fts_enabled:
            self.conn.execute("DELETE FROM memory_fts WHERE memory_id = ?", (record.memory_id,))
            if not record.deleted and record.governance_state == "active":
                self.conn.execute(
                    "INSERT INTO memory_fts(memory_id, content, summary, type) VALUES (?, ?, ?, ?)",
                    (record.memory_id, record.content, record.summary + " " + record.subtype, record.type),
                )
        if not record.deleted and record.governance_state == "active":
            self._upsert_graph(record)
        else:
            self.conn.execute("DELETE FROM memory_entity WHERE memory_id = ?", (record.memory_id,))
            self.conn.execute("DELETE FROM relation WHERE memory_id = ?", (record.memory_id,))
        fact = parse_fact_slot(record.content) if record.type == "knowledge" and not record.deleted else None
        if fact is None:
            self.conn.execute("DELETE FROM fact_slot WHERE memory_id = ?", (record.memory_id,))
        else:
            self.conn.execute(
                """INSERT INTO fact_slot(memory_id, scope, subject, predicate)
                   VALUES (?, ?, ?, ?)
                   ON CONFLICT(memory_id) DO UPDATE SET scope = excluded.scope,
                       subject = excluded.subject, predicate = excluded.predicate""",
                (record.memory_id, fact_slot_scope(record.scope, record.retention_session_id), fact.subject, fact.predicate),
            )

    def upsert_memory(self, record: MemoryRecord) -> None:
        with self.conn:
            self._upsert_memory_no_commit(record)

    def upsert_memory_with_embedding_job(
        self,
        record: MemoryRecord,
        backend: str,
        model_revision: str,
        content_hash: str,
    ) -> None:
        with self.conn:
            self._upsert_memory_with_embedding_job_no_commit(
                record, backend, model_revision, content_hash,
            )

    def _upsert_memory_with_embedding_job_no_commit(
        self, record: MemoryRecord, backend: str, model_revision: str,
        content_hash: str,
    ) -> None:
        self._upsert_memory_no_commit(record)
        if (
            not record.deleted
            and record.governance_state == "active"
            and record.trust_level in {"trusted", "verified", "reviewed"}
        ):
            self.conn.execute(
                """
                DELETE FROM memory_embedding
                WHERE memory_id = ?
                  AND NOT (
                      content_hash = ? AND backend = ? AND model_revision = ?
                  )
                """,
                (record.memory_id, content_hash, backend, model_revision),
            )
            self._enqueue_memory_embedding_no_commit(
                record.memory_id, content_hash, backend, model_revision, now=utc_now(),
            )
        else:
            self.conn.execute(
                "DELETE FROM memory_embedding WHERE memory_id = ?",
                (record.memory_id,),
            )

    def stage_new_memory_write(
        self, record: MemoryRecord, backend: str, model_revision: str,
        content_hash: str, source_link: Optional[Dict[str, Any]] = None,
    ) -> None:
        with self.conn:
            self.conn.execute("BEGIN IMMEDIATE")
            self._check_segment_cleanup_no_commit(record)
            existing = self.conn.execute(
                "SELECT 1 FROM memory WHERE memory_id = ?", (record.memory_id,)
            ).fetchone()
            if existing is not None:
                raise ValueError("memory id already exists")
            if self.conn.execute(
                "SELECT 1 FROM pending_memory_write WHERE memory_id = ?", (record.memory_id,)
            ).fetchone() is not None:
                raise ValueError("memory id already has a pending write")
            source_link_json = self._staged_source_link_json(source_link)
            self._validate_dependencies_no_commit(record)
            self.conn.execute(
                """
                INSERT INTO pending_memory_write(
                    version_id, memory_id, record_json, backend,
                    model_revision, content_hash, source_link_json, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    record.version.version_id, record.memory_id, record.to_json(),
                    backend, model_revision, content_hash, source_link_json, utc_now(),
                ),
            )

    def stage_existing_memory_write(
        self, previous: MemoryRecord, record: MemoryRecord,
        backend: str, model_revision: str, content_hash: str,
        source_link: Optional[Dict[str, Any]] = None,
        fact_conflict_id: str = "",
    ) -> None:
        if (
            previous.memory_id != record.memory_id
            or previous.version.version_id != record.version.parent_version_id
            or previous.temporal.superseded_by != record.version.version_id
            or previous.temporal.valid_to is None
            or record.temporal.valid_to is not None
            or record.deleted
        ):
            raise ValueError("invalid staged memory version transition")
        with self.conn:
            self.conn.execute("BEGIN IMMEDIATE")
            self._check_segment_cleanup_no_commit(record)
            conflict_source_json = ""
            if fact_conflict_id:
                conflict_source_json = self._validate_fact_conflict_write_no_commit(
                    fact_conflict_id, previous, record,
                )
            row = self.conn.execute(
                "SELECT version_id, deleted FROM memory WHERE memory_id = ?",
                (record.memory_id,),
            ).fetchone()
            if row is None or row["deleted"] or row["version_id"] != previous.version.version_id:
                raise ValueError("stale memory version")
            if self.conn.execute(
                "SELECT 1 FROM pending_memory_write WHERE memory_id = ?", (record.memory_id,)
            ).fetchone() is not None:
                raise ValueError("memory id already has a pending write")
            source_link_json = self._staged_source_link_json(source_link)
            if fact_conflict_id:
                if source_link is not None and source_link_json != conflict_source_json:
                    raise FactConflictChanged("candidate_version_changed")
                source_link_json = conflict_source_json
            self._validate_dependencies_no_commit(record)
            self.conn.execute(
                """
                INSERT INTO pending_memory_write(
                    version_id, memory_id, record_json, backend, model_revision,
                    content_hash, expected_version_id, previous_record_json,
                    source_link_json, fact_conflict_id, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    record.version.version_id, record.memory_id, record.to_json(),
                    backend, model_revision, content_hash,
                    previous.version.version_id, previous.to_json(), source_link_json,
                    fact_conflict_id, utc_now(),
                ),
            )

    def _staged_source_link_json(self, source_link: Optional[Dict[str, Any]]) -> str:
        if not source_link:
            return ""
        if source_link.get("relation") != "source_clause":
            raise ValueError("unsupported staged source relation")
        event = self.conn.execute(
            """SELECT content, user_feedback, forgotten FROM agent_events
               WHERE event_id = ?""",
            (str(source_link.get("event_id") or ""),),
        ).fetchone()
        if event is None or event["forgotten"]:
            raise ValueError("source event is unavailable")
        self._validated_source_fields(source_link.get("source"), event)
        return json.dumps(source_link, ensure_ascii=False, sort_keys=True)

    def _attach_staged_source_link_no_commit(self, record: MemoryRecord, source_link_json: str) -> None:
        if not source_link_json:
            return
        source_link = json.loads(source_link_json)
        if source_link.get("relation") != "source_clause":
            raise ValueError("invalid staged source relation")
        linked = self._insert_memory_event_link_no_commit(
            record.memory_id, str(source_link.get("event_id") or ""),
            "source_clause", source_link.get("source"),
        )
        if linked is None:
            raise ValueError("source event is unavailable")

    def pending_memory_writes(self) -> List[Dict[str, Any]]:
        return [dict(row) for row in self.conn.execute(
            """SELECT version_id, memory_id, expected_version_id, fact_conflict_id
               FROM pending_memory_write ORDER BY created_at, version_id"""
        )]

    def pending_segment_cleanups(self) -> List[Dict[str, Any]]:
        return [dict(row) for row in self.conn.execute(
            "SELECT * FROM pending_segment_cleanup ORDER BY created_at, memory_id, version_id"
        )]

    def recover_pending_segment_cleanups(
        self, cleanup_versions: Callable[[List[Dict[str, Any]]], None],
    ) -> int:
        with self.conn:
            self.conn.execute("BEGIN IMMEDIATE")
            pending = [dict(row) for row in self.conn.execute(
                """SELECT c.*, m.version_id AS committed_version_id,
                    m.deleted AS committed_deleted FROM pending_segment_cleanup c
                    LEFT JOIN memory m ON m.memory_id = c.memory_id
                    ORDER BY c.created_at, c.memory_id, c.version_id"""
            )]
            if any(row["version_id"] == row["committed_version_id"] for row in pending):
                raise ValueError("cancelled segment version is committed")
            if pending:
                cleanup_versions(pending)
        if pending:
            self.secure_cleanup()
            with self.conn:
                self.conn.execute("BEGIN IMMEDIATE")
                self.conn.executemany(
                    "DELETE FROM pending_segment_cleanup WHERE memory_id = ? AND version_id = ?",
                    [(row["memory_id"], row["version_id"]) for row in pending],
                )
        return len(pending)

    def commit_staged_new_memory_write(
        self, version_id: str, append_record: Callable[[MemoryRecord], MemoryRecord],
    ) -> MemoryRecord:
        self.conn.execute("BEGIN IMMEDIATE")
        try:
            row = self.conn.execute(
                "SELECT * FROM pending_memory_write WHERE version_id = ?", (version_id,)
            ).fetchone()
            if row is None:
                raise ValueError("staged memory write not found")
            record = MemoryRecord.from_json(str(row["record_json"]))
            self._check_segment_cleanup_no_commit(record)
            if record.memory_id != row["memory_id"] or record.version.version_id != version_id:
                raise ValueError("staged memory write identity mismatch")
            if row["expected_version_id"] or row["previous_record_json"]:
                raise ValueError("staged write is not a new memory")
            existing = self.conn.execute(
                "SELECT 1 FROM memory WHERE memory_id = ?", (record.memory_id,)
            ).fetchone()
            if existing is not None:
                raise ValueError("staged memory id was claimed by another write")
            fact = parse_fact_slot(record.content) if record.type == "knowledge" else None
            if fact is not None:
                candidates = self.find_active_fact_candidates(
                    fact_slot_scope(record.scope, record.retention_session_id), fact.subject, fact.predicate,
                    tier=record.tier, project_root=record.project_root,
                )
                if candidates:
                    ids = [str(item["memory_id"]) for item in candidates]
                    self.conn.execute(
                        "DELETE FROM pending_memory_write WHERE version_id = ?", (version_id,)
                    )
                    self._add_audit_no_commit(
                        "fact_write_race_blocked", None,
                        {"candidate_memory_ids": ids, "predicate": fact.predicate},
                    )
                    self.conn.commit()
                    raise FactSlotConflict(ids)
            self._validate_staged_dependencies_no_commit(record)
            append_record(record)
            self._upsert_memory_with_embedding_job_no_commit(
                record, str(row["backend"]), str(row["model_revision"]),
                str(row["content_hash"]),
            )
            self._attach_preference_evidence_no_commit(record)
            self._attach_staged_source_link_no_commit(record, str(row["source_link_json"]))
            self._add_audit_no_commit(
                "memory_version_committed", record.memory_id,
                self._version_audit_detail(record),
            )
            self.conn.execute(
                "DELETE FROM pending_memory_write WHERE version_id = ?", (version_id,)
            )
            self.conn.commit()
            return record
        except FactSlotConflict:
            raise
        except Exception:
            self.conn.rollback()
            raise

    def commit_staged_existing_memory_write(
        self, version_id: str,
        append_version: Callable[[MemoryRecord, MemoryRecord], MemoryRecord],
    ) -> MemoryRecord:
        self.conn.execute("BEGIN IMMEDIATE")
        try:
            row = self.conn.execute(
                "SELECT * FROM pending_memory_write WHERE version_id = ?", (version_id,)
            ).fetchone()
            if row is None:
                raise ValueError("staged memory write not found")
            if not row["expected_version_id"] or not row["previous_record_json"]:
                raise ValueError("staged write is not a version update")
            previous = MemoryRecord.from_json(str(row["previous_record_json"]))
            record = MemoryRecord.from_json(str(row["record_json"]))
            self._check_segment_cleanup_no_commit(record)
            if (
                previous.memory_id != row["memory_id"]
                or record.memory_id != previous.memory_id
                or previous.version.version_id != row["expected_version_id"]
                or record.version.parent_version_id != row["expected_version_id"]
                or record.version.version_id != version_id
                or previous.temporal.superseded_by != version_id
                or previous.temporal.valid_to is None
                or record.temporal.valid_to is not None
                or record.deleted
            ):
                raise ValueError("staged memory version identity mismatch")
            current = self.conn.execute(
                "SELECT version_id, deleted FROM memory WHERE memory_id = ?",
                (record.memory_id,),
            ).fetchone()
            fact_conflict_id = str(row["fact_conflict_id"] or "")
            if fact_conflict_id:
                try:
                    conflict_source_json = self._validate_fact_conflict_write_no_commit(
                        fact_conflict_id, previous, record,
                    )
                    if conflict_source_json != str(row["source_link_json"] or ""):
                        raise FactConflictChanged("candidate_version_changed")
                except FactConflictChanged as exc:
                    # Cancellation is durable before recovery sees the error;
                    # neither the segment nor committed memory has changed.
                    self.conn.execute(
                        "DELETE FROM pending_memory_write WHERE version_id = ?", (version_id,),
                    )
                    self._add_audit_no_commit(
                        "fact_conflict_write_cancelled", record.memory_id,
                        {"conflict_id": fact_conflict_id, "version_id": version_id, "reason": exc.reason},
                    )
                    self.conn.commit()
                    raise
            if current is None or current["deleted"] or current["version_id"] != row["expected_version_id"]:
                raise ValueError("stale memory version")
            self._validate_staged_dependencies_no_commit(record)
            append_version(previous, record)
            self._upsert_memory_with_embedding_job_no_commit(
                record, str(row["backend"]), str(row["model_revision"]),
                str(row["content_hash"]),
            )
            self._attach_staged_source_link_no_commit(record, str(row["source_link_json"]))
            self._add_audit_no_commit(
                "memory_version_committed", record.memory_id,
                self._version_audit_detail(record),
            )
            if fact_conflict_id:
                self.conn.execute(
                    """UPDATE fact_conflict
                       SET status = 'resolved', resolution = 'accept_incoming',
                           resolved_at = ?, resolved_memory_id = ?
                       WHERE conflict_id = ? AND status = 'pending'""",
                    (utc_now(), record.memory_id, fact_conflict_id),
                )
                self._add_audit_no_commit(
                    "fact_conflict_resolved", record.memory_id,
                    {"conflict_id": fact_conflict_id, "resolution": "accept_incoming",
                     "version_id": record.version.version_id},
                )
            self.conn.execute(
                "DELETE FROM pending_memory_write WHERE version_id = ?", (version_id,)
            )
            self.conn.commit()
            return record
        except FactConflictChanged:
            self.conn.rollback()
            raise
        except Exception:
            self.conn.rollback()
            raise

    @staticmethod
    def _version_audit_detail(record: MemoryRecord) -> Dict[str, Any]:
        return {
            "version_id": record.version.version_id,
            "parent_version_id": record.version.parent_version_id,
            "operation": record.version.op,
        }

    def _attach_preference_evidence_no_commit(self, record: MemoryRecord) -> None:
        if record.source.kind != "agent_interaction" or not record.preference_topic:
            return
        origin_ids = {
            evidence.removeprefix("agent_event:")
            for evidence in record.evidence if evidence.startswith("agent_event:")
        }
        rows = self.conn.execute(
            """
            SELECT observation_id, event_id, explicit, source_field, source_start,
                   source_end, source_excerpt FROM preference_observations
            WHERE topic = ? AND value = ? AND preference_scope = ?
              AND project_root = ? AND task_type = ?
              AND scope_agent_name = ? AND scope_session_id = ?
              AND promoted_memory_id IS NULL
            """,
            (
                record.preference_topic, record.preference_value,
                record.preference_scope, record.project_root, record.task_type,
                record.agent_name, record.preference_session_id,
            ),
        ).fetchall()
        if not rows:
            raise ValueError("preference write has no eligible source observations")
        now = utc_now()
        for row in rows:
            event_id = str(row["event_id"])
            relation = "confirmed_by" if event_id in origin_ids and row["explicit"] else "extracted_from"
            self.conn.execute(
                """
                INSERT OR IGNORE INTO memory_event_links(
                    memory_id, event_id, relation, source_field,
                    source_start, source_end, source_excerpt, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    record.memory_id, event_id, relation,
                    row["source_field"], row["source_start"], row["source_end"],
                    row["source_excerpt"], now,
                ),
            )
        self.conn.executemany(
            """UPDATE preference_observations SET promoted_memory_id = ?
               WHERE observation_id = ? AND promoted_memory_id IS NULL""",
            [(record.memory_id, int(row["observation_id"])) for row in rows],
        )
        old_rows = self.conn.execute(
            """
            SELECT memory_id FROM memory
            WHERE memory_id != ? AND deleted = 0 AND governance_state = 'active'
              AND preference_topic = ? AND preference_value != ?
              AND preference_scope = ? AND agent_name = ?
              AND preference_session_id = ? AND project_root = ? AND task_type = ?
            """,
            (
                record.memory_id, record.preference_topic, record.preference_value,
                record.preference_scope, record.agent_name,
                record.preference_session_id, record.project_root, record.task_type,
            ),
        ).fetchall()
        reason = "superseded_by_preference_update:%s" % record.memory_id
        for old in old_rows:
            old_id = str(old["memory_id"])
            self.conn.execute(
                """
                UPDATE memory
                SET governance_state = 'superseded', governance_reason = ?,
                    valid_to = ?, superseded_by = ?, updated_at = ?
                WHERE memory_id = ? AND governance_state = 'active' AND deleted = 0
                """,
                (reason, now, record.version.version_id, now, old_id),
            )
            for event_id in origin_ids:
                self.conn.execute(
                    """
                    INSERT OR IGNORE INTO memory_event_links(memory_id, event_id, relation, created_at)
                    VALUES (?, ?, 'contradicted_by', ?)
                    """,
                    (old_id, event_id, now),
                )

    def superseded_preference_ids(self, new_memory_id: str) -> List[str]:
        rows = self.conn.execute(
            "SELECT memory_id FROM memory WHERE governance_reason = ? ORDER BY memory_id",
            ("superseded_by_preference_update:%s" % new_memory_id,),
        ).fetchall()
        return [str(row["memory_id"]) for row in rows]

    def update_tier(self, memory_id: str, tier: str) -> None:
        self.conn.execute(
            "UPDATE memory SET tier = ?, updated_at = ? WHERE memory_id = ? AND deleted = 0",
            (tier, utc_now(), memory_id),
        )
        self.conn.commit()

    def invalidate_memory(self, memory_id: str, superseded_by: Optional[str] = None, valid_to: Optional[str] = None) -> None:
        self.conn.execute(
            "UPDATE memory SET valid_to = ?, superseded_by = ?, updated_at = ? WHERE memory_id = ? AND deleted = 0",
            (valid_to or utc_now(), superseded_by, utc_now(), memory_id),
        )
        self.conn.commit()

    def mark_deleted(self, memory_id: str, superseded_by: Optional[str] = None) -> None:
        with self.conn:
            self._mark_deleted_no_commit(memory_id, superseded_by)

    def _mark_deleted_no_commit(
        self, memory_id: str, superseded_by: Optional[str],
        expected_version_id: Optional[str] = None,
    ) -> int:
        now = utc_now()
        version_guard = " AND deleted = 0 AND version_id = ?" if expected_version_id else ""
        params = (now, superseded_by, now, memory_id)
        if expected_version_id:
            params += (expected_version_id,)
        cursor = self.conn.execute(
            """
            UPDATE memory
            SET deleted = 1,
                content = '', summary = 'FORGOTTEN', source_kind = 'forgotten', source_uri = '',
                sensitivity_level = 'none', sensitivity_labels = '[]', sensitivity_redacted = 0,
                preference_topic = '', preference_value = '', preference_scope = 'global',
                preference_explicit = 0, agent_name = '', preference_session_id = '',
                retention_session_id = '',
                project_root = '', task_type = '', governance_state = 'forgotten',
                governance_reason = '',
                valid_to = ?, superseded_by = ?, updated_at = ?
            WHERE memory_id = ?
            """ + version_guard,
            params,
        )
        if cursor.rowcount == 0:
            return 0
        if self._fts_enabled:
            self.conn.execute("DELETE FROM memory_fts WHERE memory_id = ?", (memory_id,))
        self.conn.execute("DELETE FROM memory_entity WHERE memory_id = ?", (memory_id,))
        self.conn.execute("DELETE FROM relation WHERE memory_id = ?", (memory_id,))
        self.conn.execute("DELETE FROM memory_embedding WHERE memory_id = ?", (memory_id,))
        self.conn.execute("DELETE FROM fact_slot WHERE memory_id = ?", (memory_id,))
        self.conn.execute(
            "DELETE FROM entity WHERE NOT EXISTS "
            "(SELECT 1 FROM memory_entity WHERE memory_entity.entity = entity.entity)"
        )
        return int(cursor.rowcount)

    def begin_memory_erasure(
        self, memory_id: str, tombstone_version_id: str, *, compact: bool,
        plaintext: str = "", expected_version_id: str,
    ) -> Dict[str, int]:
        """Hide a memory and its linked evidence in one durable SQLite commit."""

        return self.begin_memory_erasures([{
            "memory_id": memory_id,
            "tombstone_version_id": tombstone_version_id,
            "compact": compact,
            "plaintext": plaintext,
            "expected_version_id": expected_version_id,
        }])

    def begin_memory_erasures(self, items: List[Dict[str, Any]]) -> Dict[str, int]:
        """Apply a previewed batch atomically or leave every target untouched."""

        totals = {"scrubbed_events": 0, "deleted_observations": 0,
                  "deleted_tool_outcomes": 0, "redacted_audit": 0,
                  "redacted_fact_conflicts": 0}
        memory_ids = []
        plaintexts = []
        linked_event_ids = set()
        target_links = []
        with self.conn:
            self.conn.execute("BEGIN IMMEDIATE")
            self._check_erasure_dependency_closure_no_commit(items)
            self._cancel_revoked_derived_writes_no_commit(items)
            pending_sources = self.conn.execute(
                "SELECT source_link_json FROM pending_memory_write WHERE source_link_json <> ''"
            ).fetchall()
            if pending_sources and items:
                target_ids = [str(item["memory_id"]) for item in items]
                placeholders = ",".join("?" for _ in target_ids)
                affected_events = {
                    str(row["event_id"])
                    for row in self.conn.execute(
                        "SELECT DISTINCT event_id FROM memory_event_links WHERE memory_id IN (%s)"
                        % placeholders,  # nosec B608 - only parameter placeholders are interpolated
                        target_ids,
                    )
                }
                for row in pending_sources:
                    try:
                        source_link = json.loads(str(row["source_link_json"]))
                        event_id = str(source_link["event_id"])
                    except (ValueError, TypeError, KeyError) as exc:
                        raise ValueError("invalid pending source write") from exc
                    if event_id in affected_events:
                        raise ValueError("pending source write must be recovered before erasure")
            for item in items:
                memory_id = str(item["memory_id"])
                if self.conn.execute(
                    "SELECT 1 FROM pending_memory_write WHERE memory_id = ?", (memory_id,)
                ).fetchone() is not None:
                    raise ValueError("pending memory write must be recovered before erasure: %s" % memory_id)
                tombstone_version_id = str(item["tombstone_version_id"])
                self.conn.execute(
                    """
                    INSERT INTO pending_erasure(memory_id, tombstone_version_id, compact_required, created_at)
                    VALUES (?, ?, ?, ?)
                    ON CONFLICT(memory_id) DO UPDATE SET
                        compact_required = MAX(compact_required, excluded.compact_required)
                    """,
                    (memory_id, tombstone_version_id, int(item["compact"]), utc_now()),
                )
                changed = self._mark_deleted_no_commit(
                    memory_id, tombstone_version_id,
                    expected_version_id=str(item["expected_version_id"]),
                )
                if changed != 1:
                    raise ValueError("stale memory version: %s" % memory_id)
                memory_ids.append(memory_id)
                plaintexts.append(str(item.get("plaintext") or ""))
                links = self.conn.execute(
                    """SELECT event_id, source_field, source_start, source_end
                       FROM memory_event_links WHERE memory_id = ?""",
                    (memory_id,),
                ).fetchall()
                linked_event_ids.update(str(link["event_id"]) for link in links)
                target_links.extend(links)
                totals["deleted_observations"] += self.conn.execute(
                    "DELETE FROM preference_observations WHERE promoted_memory_id = ?",
                    (memory_id,),
                ).rowcount
                promotion = self.conn.execute(
                    """SELECT scope, project_root, domain, tool
                       FROM verified_tool_experience WHERE memory_id = ?""",
                    (memory_id,),
                ).fetchone()
                if promotion is not None:
                    totals["deleted_tool_outcomes"] += self.conn.execute(
                        """DELETE FROM verified_tool_outcome
                           WHERE scope = ? AND project_root = ? AND domain = ? AND tool = ?""",
                        tuple(promotion),
                    ).rowcount
            if memory_ids:
                self._erase_dependency_metadata_no_commit(memory_ids)
                placeholders = ",".join("?" for _ in memory_ids)
                self.conn.execute(
                    """UPDATE memory_event_links
                       SET source_field = '', source_start = -1,
                           source_end = -1, source_excerpt = ''
                       WHERE memory_id IN (%s)""" % placeholders,  # nosec B608
                    memory_ids,
                )
                # Keep a surviving atom's excerpt only when its source span is
                # known and disjoint from every revoked span in that event.
                for link in target_links:
                    field = str(link["source_field"] or "")
                    start, end = int(link["source_start"]), int(link["source_end"])
                    overlap = (
                        " AND source_field = ? AND source_start < ? AND source_end > ?"
                        if field in {"content", "user_feedback"} and 0 <= start < end
                        else ""
                    )
                    params: List[Any] = [str(link["event_id"]), *memory_ids]
                    if overlap:
                        params.extend((field, end, start))
                    self.conn.execute(
                        """UPDATE memory_event_links SET source_field = '',
                                  source_start = -1, source_end = -1, source_excerpt = ''
                           WHERE event_id = ? AND memory_id NOT IN (%s)%s"""
                        % (placeholders, overlap),  # nosec B608
                        params,
                    )
            if linked_event_ids:
                placeholders = ",".join("?" for _ in linked_event_ids)
                self.conn.execute(
                    """UPDATE preference_observations
                       SET source_field = '', source_start = -1,
                           source_end = -1, source_excerpt = ''
                       WHERE event_id IN (%s)""" % placeholders,  # nosec B608
                    sorted(linked_event_ids),
                )
            totals["scrubbed_events"] = self._forget_agent_event_payloads_no_commit(
                sorted(linked_event_ids)
            )
            if memory_ids:
                totals["redacted_fact_conflicts"] = self._redact_fact_conflicts_no_commit(memory_ids)
                totals["redacted_audit"] = self._redact_audit_payloads_no_commit(
                    memory_ids, plaintexts
                )
        return totals

    def pending_erasures(self) -> List[Dict[str, Any]]:
        return [dict(row) for row in self.conn.execute(
            "SELECT * FROM pending_erasure ORDER BY created_at, memory_id"
        )]

    def finish_memory_erasures(self, memory_ids: List[str]) -> None:
        if not memory_ids:
            return
        with self.conn:
            self.conn.executemany(
                "DELETE FROM pending_erasure WHERE memory_id = ?",
                [(memory_id,) for memory_id in memory_ids],
            )

    def select_forget_candidates(self, plan: ErasurePlan) -> List[Dict[str, Any]]:
        """Apply non-negotiable erasure boundaries before semantic ranking."""

        if plan.unresolved_scope:
            return []

        requested = set(plan.memory_types)
        if requested and "all" not in requested:
            supported_types = {"preference", "security_policy", "knowledge", "tool_trace"}
            if not requested & supported_types:
                return []

        filter_types = bool(requested and "all" not in requested)
        preference_types = bool(requested & {"preference", "security_policy"})
        knowledge_type = "knowledge" in requested
        tool_trace_type = "tool_trace" in requested
        scope_sql = ""
        scope_params: List[Any] = []
        if plan.scope_level in {"agent", "session"}:
            event_conditions = ["e.forgotten = 0"]
            record_conditions = []
            for field, value in (("agent_name", plan.agent_name), ("session_id", plan.session_id)):
                if not value:
                    continue
                event_conditions.append("e.%s = ?" % field)
                if field == "session_id":
                    record_conditions.append("(memory.preference_session_id = ? OR memory.retention_session_id = ?)")
                    scope_params.extend([value, value])
                else:
                    record_conditions.append("memory.agent_name = ?")
                    scope_params.append(value)
            # Apply agent/session to the same source event, never two unrelated
            # links. Applicability scope and the origin of a memory are distinct.
            scope_sql = " AND ((%s) OR EXISTS (SELECT 1 FROM memory_event_links l " \
                "JOIN agent_events e ON e.event_id = l.event_id " \
                "WHERE l.memory_id = memory.memory_id AND %s))" % (
                    " AND ".join(record_conditions), " AND ".join(event_conditions),
                )
            scope_params.extend(value for value in (plan.agent_name, plan.session_id) if value)
        elif plan.scope_level == "project":
            scope_sql = " AND project_root = ?"
            scope_params.append(plan.project_root)
        rows = self.conn.execute(
            """
            SELECT * FROM memory
            WHERE deleted = 0 AND governance_state = 'active'
              AND (
                    ? = 0
                    OR (? = 1 AND type IN ('preference', 'sensitive_policy'))
                    OR (? = 1 AND type = 'knowledge')
                    OR (
                        ? = 1 AND type = 'knowledge'
                        AND subtype IN ('tool_trace', 'procedural_trace', 'tool_failure_case')
                    )
                  )
              AND (? = '' OR COALESCE(NULLIF(valid_from, ''), updated_at) >= ?)
              AND (? = '' OR COALESCE(NULLIF(valid_from, ''), updated_at) <= ?)
            """ + scope_sql + " ORDER BY updated_at DESC",
            (
                int(filter_types),
                int(preference_types),
                int(knowledge_type),
                int(tool_trace_type),
                plan.start_time,
                plan.start_time,
                plan.end_time,
                plan.end_time,
                *scope_params,
            ),
        ).fetchall()
        return [dict(row) for row in rows]

    def secure_cleanup(self, vacuum: bool = False) -> Dict[str, Any]:
        """Flush deleted plaintext from WAL; optionally rewrite free database pages."""

        self.conn.commit()
        checkpoint = list(self.conn.execute("PRAGMA wal_checkpoint(TRUNCATE)").fetchone() or ())
        if checkpoint and checkpoint[0] != 0:
            raise RuntimeError("SQLite WAL checkpoint is busy; erasure remains pending")
        if vacuum:
            self.conn.execute("VACUUM")
        ensure_private_file(self.path)
        for suffix in ("-wal", "-shm"):
            ensure_private_file(Path(str(self.path) + suffix))
        return {"checkpoint": checkpoint, "vacuum": bool(vacuum)}

    def redact_audit_payloads(
        self,
        memory_ids: List[str],
        plaintexts: Optional[List[str]] = None,
    ) -> int:
        """Replace forgotten audit payloads with hashes and rebuild the chain."""

        with self.conn:
            return self._redact_audit_payloads_no_commit(memory_ids, plaintexts)

    def _redact_audit_payloads_no_commit(
        self, memory_ids: List[str], plaintexts: Optional[List[str]] = None,
    ) -> int:
        targets = {str(item) for item in memory_ids if str(item)}
        probes = [str(item) for item in (plaintexts or []) if str(item)]
        rows = self.conn.execute("SELECT * FROM audit ORDER BY audit_id ASC").fetchall()
        redacted = 0
        for row in rows:
            detail = str(row["detail"] or "")
            matches = str(row["memory_id"] or "") in targets or any(
                probe in detail for probe in probes
            ) or any(target in detail for target in targets)
            if not matches:
                continue
            try:
                existing = json.loads(detail)
            except (TypeError, ValueError):
                existing = {}
            if isinstance(existing, dict) and existing.get("redacted_for_forgetting"):
                continue
            replacement = json.dumps(
                {
                    "redacted_for_forgetting": True,
                    "original_detail_hash": stable_hash(detail),
                },
                ensure_ascii=False,
                sort_keys=True,
            )
            self.conn.execute(
                "UPDATE audit SET detail = ? WHERE audit_id = ?",
                (replacement, row["audit_id"]),
            )
            redacted += 1

        if not redacted:
            return 0
        previous = ""
        for row in self.conn.execute(
            "SELECT * FROM audit ORDER BY audit_id ASC"
        ).fetchall():
            event_hash = stable_hash(
                "|".join(
                    [
                        previous,
                        row["event_type"],
                        row["memory_id"] or "",
                        row["detail"],
                        row["created_at"],
                    ]
                )
            )
            self.conn.execute(
                "UPDATE audit SET prev_hash = ?, event_hash = ? WHERE audit_id = ?",
                (previous, event_hash, row["audit_id"]),
            )
            previous = event_hash
        return redacted

    def list_memories(self, include_deleted: bool = False, tier: Optional[str] = None, limit: Optional[int] = None, governance_state: Optional[str] = "active") -> List[Dict[str, Any]]:
        sql = "SELECT * FROM memory"
        clauses = []
        params = []
        if not include_deleted:
            clauses.append("deleted = 0")
        if tier:
            clauses.append("tier = ?")
            params.append(tier)
        if governance_state:
            clauses.append("governance_state = ?")
            params.append(governance_state)
        if clauses:
            sql += " WHERE " + " AND ".join(clauses)
        sql += " ORDER BY updated_at DESC"
        if limit is not None:
            sql += " LIMIT ?"
            params.append(max(0, int(limit)))
        return [dict(row) for row in self.conn.execute(sql, params)]

    def list_recent_memories(
        self,
        limit: int,
        exclude_ids: Optional[List[str]] = None,
        session_id: str = "",
        project_root: Optional[str] = None,
    ) -> List[Dict[str, Any]]:
        effective_limit = max(0, int(limit))
        if effective_limit == 0:
            return []
        excluded = list(dict.fromkeys(str(memory_id) for memory_id in (exclude_ids or []) if memory_id))
        sql = """SELECT * FROM memory WHERE deleted = 0 AND governance_state = 'active'
                 AND (retention_session_id = '' OR retention_session_id = ?)
                 AND (? IS NULL OR project_root = '' OR project_root = ?)"""
        params: List[Any] = [session_id, project_root, project_root]
        if excluded:
            placeholders = ",".join("?" for _ in excluded)
            sql += " AND memory_id NOT IN (%s)" % placeholders
            params.extend(excluded)
        sql += " ORDER BY updated_at DESC LIMIT ?"
        params.append(effective_limit)
        return [dict(row) for row in self.conn.execute(sql, params)]

    def list_session_memories(self, session_id: str) -> List[Dict[str, Any]]:
        if not session_id:
            return []
        return [dict(row) for row in self.conn.execute(
            """SELECT * FROM memory WHERE retention_session_id = ?
               AND tier IN ('working', 'session') AND deleted = 0
               ORDER BY updated_at DESC""",
            (session_id,),
        )]

    def list_stale_memory_sessions(self, cutoff: str, limit: int = 100) -> List[Dict[str, Any]]:
        return [dict(row) for row in self.conn.execute(
            """SELECT retention_session_id AS session_id, COUNT(*) AS memory_count,
                      MAX(updated_at) AS last_write_at
               FROM memory
               WHERE retention_session_id != '' AND tier IN ('working', 'session')
                 AND deleted = 0
               GROUP BY retention_session_id
               HAVING MAX(updated_at) < ?
               ORDER BY last_write_at ASC LIMIT ?""",
            (cutoff, max(1, min(int(limit), 500))),
        )]

    def record_verified_tool_outcomes(
        self, scope: str, project_root: str, session_id: str,
        outcomes: List[tuple[str, str, bool]],
    ) -> List[Dict[str, Any]]:
        keys = set()
        with self.conn:
            for domain, tool, success in outcomes:
                keys.add((domain, tool))
                self.conn.execute(
                    """INSERT INTO verified_tool_outcome
                       (scope, project_root, session_id, domain, tool,
                        success_seen, failure_seen, updated_at)
                       VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                       ON CONFLICT(scope, project_root, session_id, domain, tool)
                       DO UPDATE SET success_seen = MAX(success_seen, excluded.success_seen),
                                     failure_seen = MAX(failure_seen, excluded.failure_seen),
                                     updated_at = excluded.updated_at""",
                    (scope, project_root, session_id, domain, tool,
                     int(success), int(not success), utc_now()),
                )
        results = []
        for domain, tool in sorted(keys):
            row = self.conn.execute(
                """SELECT SUM(success_seen) AS successful_sessions,
                          SUM(failure_seen) AS failed_sessions
                   FROM verified_tool_outcome
                   WHERE scope = ? AND project_root = ? AND domain = ? AND tool = ?""",
                (scope, project_root, domain, tool),
            ).fetchone()
            results.append({"domain": domain, "tool": tool,
                            "successful_sessions": int(row["successful_sessions"] or 0),
                            "failed_sessions": int(row["failed_sessions"] or 0)})
        return results

    def claim_verified_tool_experience(
        self, scope: str, project_root: str, domain: str, tool: str,
    ) -> str:
        key = (scope, project_root, domain, tool)
        claim = new_id("claim")
        now = utc_now()
        stale_before = (datetime.fromisoformat(now) - timedelta(minutes=30)).isoformat()
        try:
            self.conn.execute("BEGIN IMMEDIATE")
            row = self.conn.execute(
                """SELECT memory_id, created_at FROM verified_tool_experience
                   WHERE scope = ? AND project_root = ? AND domain = ? AND tool = ?""",
                key,
            ).fetchone()
            if row is None:
                self.conn.execute(
                    """INSERT INTO verified_tool_experience
                       (scope, project_root, domain, tool, memory_id, created_at)
                       VALUES (?, ?, ?, ?, ?, ?)""",
                    (*key, claim, now),
                )
            elif str(row["memory_id"]).startswith("claim_") and row["created_at"] < stale_before:
                self.conn.execute(
                    """UPDATE verified_tool_experience SET memory_id = ?, created_at = ?
                       WHERE scope = ? AND project_root = ? AND domain = ? AND tool = ?""",
                    (claim, now, *key),
                )
            else:
                claim = ""
            self.conn.commit()
            return claim
        except Exception:
            self.conn.rollback()
            raise

    def finish_verified_tool_experience_claim(
        self, scope: str, project_root: str, domain: str, tool: str,
        claim: str, memory_id: str,
    ) -> bool:
        with self.conn:
            return self.conn.execute(
                """UPDATE verified_tool_experience SET memory_id = ?
                   WHERE scope = ? AND project_root = ? AND domain = ? AND tool = ?
                     AND memory_id = ?""",
                (memory_id, scope, project_root, domain, tool, claim),
            ).rowcount == 1

    def release_verified_tool_experience_claim(
        self, scope: str, project_root: str, domain: str, tool: str, claim: str,
    ) -> None:
        with self.conn:
            self.conn.execute(
                """DELETE FROM verified_tool_experience
                   WHERE scope = ? AND project_root = ? AND domain = ? AND tool = ?
                     AND memory_id = ?""",
                (scope, project_root, domain, tool, claim),
            )

    def get_memory(self, memory_id: str) -> Optional[Dict[str, Any]]:
        row = self.conn.execute("SELECT * FROM memory WHERE memory_id = ?", (memory_id,)).fetchone()
        return dict(row) if row else None

    @staticmethod
    def _decode_fact_conflict(row: Any) -> Dict[str, Any]:
        item = dict(row)
        for column, key, empty in (
            ("candidate_memory_ids_json", "candidate_memory_ids", []),
            ("candidate_version_ids_json", "candidate_version_ids", {}),
            ("source_link_json", "source_link", {}),
        ):
            try:
                decoded = json.loads(item.pop(column, "") or json.dumps(empty))
            except (TypeError, ValueError):
                decoded = empty
            item[key] = decoded if isinstance(decoded, type(empty)) else empty
        return item

    def _validate_fact_candidate_snapshot_no_commit(
        self, *, scope: str, tier: str, project_root: str,
        subject: str, predicate: str, candidates: List[str],
        versions: Optional[Dict[str, str]],
    ) -> Dict[str, str]:
        live = self.find_active_fact_candidates(
            scope, subject, predicate, tier=tier, project_root=project_root,
        )
        live_versions = {str(row["memory_id"]): str(row["version_id"]) for row in live}
        if (
            not candidates
            or set(candidates) != set(live_versions)
            or any(row.get("valid_to") or row.get("superseded_by") for row in live)
            or (versions is not None and versions != live_versions)
        ):
            raise FactConflictChanged("candidate_version_changed")
        return live_versions

    def _validate_fact_conflict_write_no_commit(
        self, conflict_id: str, previous: MemoryRecord, record: MemoryRecord,
    ) -> str:
        row = self.conn.execute(
            "SELECT * FROM fact_conflict WHERE conflict_id = ?", (str(conflict_id),),
        ).fetchone()
        if row is None:
            raise FactConflictChanged("conflict_not_found")
        if row["status"] != "pending":
            raise FactConflictChanged("conflict_already_closed")
        case = self._decode_fact_conflict(row)
        target_id = previous.memory_id
        if (
            target_id not in case["candidate_memory_ids"]
            or case["candidate_version_ids"].get(target_id) != previous.version.version_id
            or record.memory_id != target_id
            or record.content != str(case["incoming_content"] or "")
            or not record.content
            or record.scope != previous.scope
            or record.tier != previous.tier
            or record.retention_session_id != previous.retention_session_id
            or record.project_root != previous.project_root
        ):
            raise FactConflictChanged("candidate_version_changed")
        self._validate_fact_candidate_snapshot_no_commit(
            scope=case["scope"], tier=case["tier"], project_root=case["project_root"],
            subject=case["subject"], predicate=case["predicate"],
            candidates=case["candidate_memory_ids"], versions=case["candidate_version_ids"],
        )
        incoming = parse_fact_slot(record.content) if record.type == "knowledge" else None
        if (
            incoming is None
            or incoming.subject != case["subject"]
            or incoming.predicate != case["predicate"]
            or incoming.value != case["incoming_value"]
            or fact_slot_scope(previous.scope, previous.retention_session_id) != case["scope"]
            or previous.tier != case["tier"]
            or previous.project_root != case["project_root"]
        ):
            raise FactConflictChanged("candidate_version_changed")
        try:
            source_link_json = self._staged_source_link_json(case["source_link"])
        except (TypeError, ValueError, AttributeError) as exc:
            raise FactConflictChanged("candidate_version_changed") from exc
        if source_link_json != str(row["source_link_json"] or ""):
            raise FactConflictChanged("candidate_version_changed")
        return source_link_json

    def create_fact_conflict(
        self,
        *,
        scope: str,
        tier: str,
        project_root: str = "",
        subject: str,
        predicate: str,
        incoming_value: str,
        incoming_content: str,
        source_kind: str,
        source_uri: str,
        candidate_memory_ids: List[str],
        candidate_version_ids: Optional[Dict[str, str]] = None,
        source_link: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        """Persist a bounded fact-review case without storing hidden payloads.

        The incoming value is the structured fact slot value, not an arbitrary
        user message.  This makes the review queue useful to operators while
        keeping the source event and full text behind the existing governed
        evidence path.
        """

        candidates = list(dict.fromkeys(str(item) for item in candidate_memory_ids if str(item)))
        if not candidates:
            raise ValueError("fact conflict requires at least one candidate")
        candidate_versions = None if candidate_version_ids is None else {
            str(memory_id): str(version_id or "")
            for memory_id, version_id in candidate_version_ids.items()
            if str(memory_id) in candidates
        }
        now = utc_now()
        # Serialize the read-then-insert pair.  A review request is idempotent
        # even when two Agent events report the same fact concurrently.
        self.conn.execute("BEGIN IMMEDIATE")
        try:
            candidate_versions = self._validate_fact_candidate_snapshot_no_commit(
                scope=scope, tier=tier, project_root=project_root,
                subject=subject, predicate=predicate, candidates=candidates,
                versions=candidate_versions,
            )
            try:
                source_link_json = self._staged_source_link_json(source_link)
            except (TypeError, ValueError, AttributeError) as exc:
                raise FactConflictChanged("candidate_version_changed") from exc
            existing_rows = self.conn.execute(
                """SELECT * FROM fact_conflict
                   WHERE scope = ? AND tier = ? AND project_root = ?
                     AND subject = ? AND predicate = ?
                     AND incoming_value = ? AND status = 'pending'
                   ORDER BY created_at DESC""",
                (scope, tier, project_root, subject, predicate, incoming_value),
            ).fetchall()
            existing = None
            for row in existing_rows:
                item = self._decode_fact_conflict(row)
                if (
                    set(item["candidate_memory_ids"]) == set(candidates)
                    and item["candidate_version_ids"] == candidate_versions
                    and str(row["source_link_json"] or "") == source_link_json
                ):
                    existing = item
                else:
                    self.conn.execute(
                        """UPDATE fact_conflict
                           SET status = 'dismissed', resolution = 'candidate_snapshot_changed',
                               resolved_at = ?, resolved_memory_id = '',
                               incoming_value = '', incoming_content = '', subject = '',
                               project_root = '', source_kind = 'forgotten', source_uri = '',
                               source_link_json = ''
                           WHERE conflict_id = ?""",
                        (now, row["conflict_id"]),
                    )
            if existing is not None:
                self.conn.commit()
                return existing
            conflict_id = new_id("conflict")
            self.conn.execute(
                """INSERT INTO fact_conflict(
                    conflict_id, scope, tier, project_root, subject, predicate,
                    incoming_value, incoming_content, source_kind, source_uri,
                    candidate_memory_ids_json, candidate_version_ids_json,
                    source_link_json,
                    status, resolution, created_at, resolved_at, resolved_memory_id
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'pending', '', ?, '', '')""",
                (
                    conflict_id, scope, tier, project_root, subject, predicate,
                    incoming_value, incoming_content, source_kind, source_uri,
                    json.dumps(candidates, ensure_ascii=False),
                    json.dumps(candidate_versions, ensure_ascii=False, sort_keys=True),
                    source_link_json,
                    now,
                ),
            )
            self.conn.commit()
            return {
                "conflict_id": conflict_id,
                "scope": scope,
                "tier": tier,
                "project_root": project_root,
                "subject": subject,
                "predicate": predicate,
                "incoming_value": incoming_value,
                "incoming_content": incoming_content,
                "source_kind": source_kind,
                "source_uri": source_uri,
                "candidate_memory_ids": candidates,
                "candidate_version_ids": candidate_versions,
                "source_link": json.loads(source_link_json) if source_link_json else {},
                "status": "pending",
                "resolution": "",
                "created_at": now,
                "resolved_at": "",
                "resolved_memory_id": "",
            }
        except Exception:
            self.conn.rollback()
            raise

    def list_fact_conflicts(
        self, *, status: Optional[str] = "pending", limit: int = 100,
    ) -> List[Dict[str, Any]]:
        clauses = []
        params: List[Any] = []
        if status:
            if status not in {"pending", "resolved", "dismissed"}:
                raise ValueError("invalid fact conflict status")
            clauses.append("status = ?")
            params.append(status)
        sql = "SELECT * FROM fact_conflict"
        if clauses:
            sql += " WHERE " + " AND ".join(clauses)
        sql += " ORDER BY created_at DESC, conflict_id DESC LIMIT ?"
        params.append(max(1, min(int(limit), 1000)))
        return [self._decode_fact_conflict(row) for row in self.conn.execute(sql, params)]

    def resolve_fact_conflicts(
        self,
        *,
        subject: str,
        predicate: str,
        scope: str,
        resolution: str,
        resolved_memory_id: str = "",
        tier: str = "",
        project_root: str = "",
        incoming_value: str = "",
    ) -> int:
        clauses = ["scope = ?", "subject = ?", "predicate = ?", "project_root = ?", "status = 'pending'"]
        params: List[Any] = [scope, subject, predicate, project_root]
        if tier:
            clauses.append("tier = ?")
            params.append(tier)
        if resolved_memory_id:
            clauses.append(
                "EXISTS (SELECT 1 FROM json_each(fact_conflict.candidate_memory_ids_json) WHERE value = ?)"
            )
            params.append(resolved_memory_id)
        if incoming_value:
            clauses.append("incoming_value = ?")
            params.append(incoming_value)
        params = [resolution, utc_now(), resolved_memory_id, *params]
        with self.conn:
            cursor = self.conn.execute(
                """UPDATE fact_conflict
                   SET status = 'resolved', resolution = ?, resolved_at = ?,
                       resolved_memory_id = ?
                   WHERE %s""" % " AND ".join(clauses),
                params,
            )
        return int(cursor.rowcount)

    def get_fact_conflict(self, conflict_id: str) -> Optional[Dict[str, Any]]:
        row = self.conn.execute(
            "SELECT * FROM fact_conflict WHERE conflict_id = ?", (str(conflict_id),)
        ).fetchone()
        if row is None:
            return None
        return self._decode_fact_conflict(row)

    def resolve_fact_conflict(
        self,
        conflict_id: str,
        *,
        resolution: str,
        resolved_memory_id: str = "",
        expected_memory_id: str = "",
        expected_version_id: str = "",
    ) -> Dict[str, Any]:
        """Resolve one review case with optional candidate/version CAS checks."""

        allowed = {"accept_incoming", "keep_existing", "dismissed", "redacted_for_forgetting"}
        if resolution not in allowed:
            raise ValueError("invalid fact conflict resolution")
        self.conn.execute("BEGIN IMMEDIATE")
        try:
            row = self.conn.execute(
                "SELECT * FROM fact_conflict WHERE conflict_id = ?", (str(conflict_id),)
            ).fetchone()
            if row is None:
                self.conn.rollback()
                return {"resolved": False, "reason": "conflict_not_found", "conflict_id": conflict_id}
            if str(row["status"]) != "pending":
                self.conn.commit()
                return {"resolved": False, "reason": "conflict_already_closed", "conflict_id": conflict_id, "status": row["status"]}
            candidates = json.loads(row["candidate_memory_ids_json"] or "[]")
            versions = json.loads(row["candidate_version_ids_json"] or "{}")
            if expected_memory_id and expected_memory_id not in candidates:
                self.conn.rollback()
                return {"resolved": False, "reason": "candidate_changed", "conflict_id": conflict_id}
            if expected_version_id and expected_memory_id and str(versions.get(expected_memory_id) or "") != expected_version_id:
                self.conn.rollback()
                return {"resolved": False, "reason": "candidate_version_changed", "conflict_id": conflict_id}
            if expected_version_id and expected_memory_id:
                current = self.conn.execute(
                    "SELECT version_id, deleted FROM memory WHERE memory_id = ?",
                    (expected_memory_id,),
                ).fetchone()
                if current is None or current["deleted"] or str(current["version_id"]) != expected_version_id:
                    self.conn.rollback()
                    return {"resolved": False, "reason": "candidate_version_changed", "conflict_id": conflict_id}
            status = "dismissed" if resolution in {"dismissed", "redacted_for_forgetting"} else "resolved"
            self.conn.execute(
                """UPDATE fact_conflict
                   SET status = ?, resolution = ?, resolved_at = ?, resolved_memory_id = ?
                   WHERE conflict_id = ? AND status = 'pending'""",
                (status, resolution, utc_now(), resolved_memory_id, str(conflict_id)),
            )
            self.conn.commit()
            return {
                "resolved": True,
                "conflict_id": conflict_id,
                "status": status,
                "resolution": resolution,
                "resolved_memory_id": resolved_memory_id,
            }
        except Exception:
            self.conn.rollback()
            raise

    def redact_fact_conflicts(self, memory_ids: List[str]) -> int:
        with self.conn:
            return self._redact_fact_conflicts_no_commit(memory_ids)

    def _redact_fact_conflicts_no_commit(self, memory_ids: List[str]) -> int:
        targets = {str(item) for item in memory_ids if str(item)}
        if not targets:
            return 0
        changed = 0
        for row in self.conn.execute("SELECT * FROM fact_conflict").fetchall():
            case = self._decode_fact_conflict(row)
            candidates = case["candidate_memory_ids"]
            if (
                not targets.intersection(str(item) for item in candidates)
                and str(row["resolved_memory_id"] or "") not in targets
            ):
                continue
            surviving = [str(item) for item in candidates if str(item) not in targets]
            self._redact_fact_conflict_no_commit(case, surviving)
            changed += 1
        return changed

    def _redact_fact_conflict_no_commit(
        self, case: Dict[str, Any], surviving: List[str],
    ) -> None:
        surviving_versions = {
            key: value for key, value in case["candidate_version_ids"].items() if key in surviving
        }
        self.conn.execute(
            """UPDATE fact_conflict
               SET incoming_value = '', incoming_content = '', subject = '', project_root = '',
                   source_kind = 'forgotten', source_uri = '', source_link_json = '',
                   candidate_memory_ids_json = ?, candidate_version_ids_json = ?,
                   status = 'dismissed', resolution = 'redacted_for_forgetting',
                   resolved_at = ?, resolved_memory_id = ''
               WHERE conflict_id = ?""",
            (json.dumps(surviving, ensure_ascii=False),
             json.dumps(surviving_versions, ensure_ascii=False, sort_keys=True),
             utc_now(), case["conflict_id"]),
        )

    def _enqueue_memory_embedding_no_commit(
        self,
        memory_id: str,
        content_hash: str,
        backend: str,
        model_revision: str,
        *,
        now: Optional[str] = None,
    ) -> bool:
        timestamp = now or utc_now()
        cursor = self.conn.execute(
            """
            INSERT INTO memory_embedding (
                memory_id, content_hash, backend, model_revision, dimension,
                vector_blob, status, failure_reason, created_at, updated_at,
                attempt_count, next_attempt_at, job_token
            )
            SELECT ?, ?, ?, ?, 0, NULL, 'pending', '', ?, ?, 0, '', ?
            WHERE EXISTS (
                SELECT 1
                FROM memory
                WHERE memory_id = ?
                  AND deleted = 0
                  AND governance_state = 'active'
                  AND trust_level IN ('trusted', 'verified', 'reviewed')
                  AND memguard_content_hash(summary, content) = ?
            )
            ON CONFLICT(memory_id, backend, model_revision, content_hash) DO NOTHING
            """,
            (
                memory_id,
                content_hash,
                backend,
                model_revision,
                timestamp,
                timestamp,
                new_id("embjob"),
                memory_id,
                content_hash,
            ),
        )
        return cursor.rowcount == 1

    def enqueue_memory_embedding(
        self,
        memory_id: str,
        content_hash: str,
        backend: str,
        model_revision: str,
    ) -> bool:
        with self.conn:
            return self._enqueue_memory_embedding_no_commit(
                memory_id, content_hash, backend, model_revision
            )

    def list_pending_embeddings(
        self,
        backend: str,
        model_revision: str,
        limit: int,
    ) -> List[Dict[str, Any]]:
        effective_limit = int(limit)
        if effective_limit <= 0:
            return []
        if effective_limit == 1:
            rows = self.conn.execute(
                """
                SELECT embedding.memory_id, embedding.content_hash,
                       embedding.backend, embedding.model_revision,
                       embedding.status, embedding.failure_reason,
                       embedding.created_at, embedding.updated_at,
                       embedding.attempt_count, embedding.next_attempt_at,
                       embedding.job_token,
                       memory.summary, memory.content
                FROM memory_embedding AS embedding
                JOIN memory ON memory.memory_id = embedding.memory_id
                WHERE embedding.backend = ? AND embedding.model_revision = ?
                  AND embedding.status IN ('pending', 'failed')
                  AND (
                      embedding.status = 'pending'
                      OR embedding.next_attempt_at = ''
                      OR embedding.next_attempt_at <= ?
                  )
                  AND memory.deleted = 0
                  AND memory.governance_state = 'active'
                  AND memory.trust_level IN ('trusted', 'verified', 'reviewed')
                  AND embedding.content_hash =
                      memguard_content_hash(memory.summary, memory.content)
                ORDER BY embedding.updated_at, embedding.memory_id, embedding.content_hash
                LIMIT 1
                """,
                (backend, model_revision, utc_now()),
            ).fetchall()
            return [dict(row) for row in rows]

        def select_rows(status: str, row_limit: int, offset: int = 0) -> List[Dict[str, Any]]:
            if status not in {"pending", "failed"}:
                raise ValueError("unsupported embedding status")
            now = utc_now()
            rows = self.conn.execute(
                """
                SELECT embedding.memory_id, embedding.content_hash,
                       embedding.backend, embedding.model_revision,
                       embedding.status, embedding.failure_reason,
                       embedding.created_at, embedding.updated_at,
                       embedding.attempt_count, embedding.next_attempt_at,
                       embedding.job_token,
                       memory.summary, memory.content
                FROM memory_embedding AS embedding
                JOIN memory ON memory.memory_id = embedding.memory_id
                WHERE embedding.backend = ? AND embedding.model_revision = ?
                  AND embedding.status = ?
                  AND (? = 'pending' OR embedding.next_attempt_at = '' OR embedding.next_attempt_at <= ?)
                  AND memory.deleted = 0
                  AND memory.governance_state = 'active'
                  AND memory.trust_level IN ('trusted', 'verified', 'reviewed')
                  AND embedding.content_hash =
                      memguard_content_hash(memory.summary, memory.content)
                ORDER BY embedding.updated_at, embedding.memory_id, embedding.content_hash
                LIMIT ? OFFSET ?
                """,
                (backend, model_revision, status, status, now, row_limit, offset),
            ).fetchall()
            return [dict(row) for row in rows]

        pending_limit = effective_limit - 1
        pending = select_rows("pending", pending_limit)
        failed = select_rows("failed", 1)
        selected = pending + failed
        remaining = effective_limit - len(selected)
        if remaining > 0:
            if failed:
                selected.extend(select_rows("failed", remaining, offset=1))
            else:
                selected.extend(select_rows("pending", remaining, offset=len(pending)))
        return selected

    def mark_embedding_failed(
        self,
        memory_id: str,
        content_hash: str,
        backend: str,
        model_revision: str,
        reason: str,
        *,
        expected_attempt_count: int,
        expected_job_token: str,
    ) -> bool:
        with self.conn:
            previous_attempts = int(expected_attempt_count)
            attempt_count = previous_attempts + 1
            delay_seconds = min(300, 2 ** min(previous_attempts, 9))
            now = utc_now()
            parsed_now = datetime.fromisoformat(now.replace("Z", "+00:00"))
            next_attempt_at = (parsed_now + timedelta(seconds=delay_seconds)).isoformat()
            cursor = self.conn.execute(
                """
                UPDATE memory_embedding
                SET status = 'failed', vector_blob = NULL, dimension = 0,
                    failure_reason = ?, attempt_count = ?, next_attempt_at = ?,
                    updated_at = ?
                WHERE memory_id = ? AND content_hash = ?
                  AND backend = ? AND model_revision = ?
                  AND status IN ('pending', 'failed')
                  AND attempt_count = ?
                  AND job_token = ?
                  AND EXISTS (
                      SELECT 1
                      FROM memory
                      WHERE memory.memory_id = memory_embedding.memory_id
                        AND memory.deleted = 0
                        AND memory.governance_state = 'active'
                        AND memory.trust_level IN ('trusted', 'verified', 'reviewed')
                        AND memguard_content_hash(memory.summary, memory.content) =
                            memory_embedding.content_hash
                  )
                """,
                (
                    str(reason)[:500],
                    attempt_count,
                    next_attempt_at,
                    now,
                    memory_id,
                    content_hash,
                    backend,
                    model_revision,
                    previous_attempts,
                    expected_job_token,
                ),
            )
            return cursor.rowcount == 1

    def complete_memory_embedding(
        self,
        memory_id: str,
        content_hash: str,
        backend: str,
        model_revision: str,
        vector: List[float],
        *,
        expected_attempt_count: int,
        expected_job_token: str,
    ) -> bool:
        vector_blob = encode_vector(vector)
        with self.conn:
            cursor = self.conn.execute(
                """
                UPDATE memory_embedding
                SET dimension = ?, vector_blob = ?, status = 'ready',
                    failure_reason = '', next_attempt_at = '', updated_at = ?
                WHERE memory_id = ? AND content_hash = ?
                  AND backend = ? AND model_revision = ?
                  AND status IN ('pending', 'failed')
                  AND attempt_count = ?
                  AND job_token = ?
                  AND EXISTS (
                      SELECT 1
                      FROM memory
                      WHERE memory.memory_id = memory_embedding.memory_id
                        AND memory.deleted = 0
                        AND memory.governance_state = 'active'
                        AND memory.trust_level IN ('trusted', 'verified', 'reviewed')
                        AND memguard_content_hash(memory.summary, memory.content) =
                            memory_embedding.content_hash
                  )
                """,
                (
                    len(vector),
                    sqlite3.Binary(vector_blob),
                    utc_now(),
                    memory_id,
                    content_hash,
                    backend,
                    model_revision,
                    int(expected_attempt_count),
                    expected_job_token,
                ),
            )
            return cursor.rowcount == 1

    def upsert_memory_embedding(
        self,
        memory_id: str,
        content_hash: str,
        backend: str,
        model_revision: str,
        vector: List[float],
    ) -> None:
        # Explicit import/test API. Workers must use complete_memory_embedding so stale
        # results cannot recreate deleted or superseded jobs.
        vector_blob = encode_vector(vector)
        now = utc_now()
        self.conn.execute(
            """
            INSERT INTO memory_embedding (
                memory_id, content_hash, backend, model_revision, dimension,
                vector_blob, status, failure_reason, created_at, updated_at,
                job_token
            ) VALUES (?, ?, ?, ?, ?, ?, 'ready', '', ?, ?, ?)
            ON CONFLICT(memory_id, backend, model_revision, content_hash) DO UPDATE SET
                dimension=excluded.dimension,
                vector_blob=excluded.vector_blob,
                status='ready',
                failure_reason='',
                updated_at=excluded.updated_at,
                job_token=excluded.job_token
            """,
            (
                memory_id,
                content_hash,
                backend,
                model_revision,
                len(vector),
                sqlite3.Binary(vector_blob),
                now,
                now,
                new_id("embjob"),
            ),
        )
        self.conn.commit()

    def _select_embedding_scan_window(
        self,
        last_memory_id: str,
        scan_limit: int,
    ) -> List[Dict[str, Any]]:
        rows = self.conn.execute(
            """
            SELECT memory_id, summary, content
            FROM memory
            WHERE deleted = 0
              AND governance_state = 'active'
              AND trust_level IN ('trusted', 'verified', 'reviewed')
              AND memory_id > ?
            ORDER BY memory_id
            LIMIT ?
            """,
            (last_memory_id, scan_limit),
        ).fetchall()
        return [dict(row) for row in rows]

    def enqueue_missing_embedding_jobs(
        self,
        backend: str,
        model_revision: str,
        scan_limit: int,
    ) -> Dict[str, Any]:
        effective_limit = int(scan_limit)
        if effective_limit <= 0:
            cursor_row = self.conn.execute(
                """
                SELECT last_memory_id
                FROM embedding_scan_cursor
                WHERE backend = ? AND model_revision = ?
                """,
                (backend, model_revision),
            ).fetchone()
            last_memory_id = str(cursor_row["last_memory_id"]) if cursor_row else ""
            return {
                "scanned": 0,
                "enqueued": 0,
                "next_cursor": last_memory_id,
                "wrapped": False,
            }

        with self.conn:
            self.conn.execute("BEGIN IMMEDIATE")
            cursor_row = self.conn.execute(
                """
                SELECT last_memory_id
                FROM embedding_scan_cursor
                WHERE backend = ? AND model_revision = ?
                """,
                (backend, model_revision),
            ).fetchone()
            last_memory_id = str(cursor_row["last_memory_id"]) if cursor_row else ""
            rows = self._select_embedding_scan_window(last_memory_id, effective_limit)
            enqueued = 0
            for row in rows:
                content_hash = vector_content_hash(row["summary"], row["content"])
                if self._enqueue_memory_embedding_no_commit(
                    row["memory_id"],
                    content_hash,
                    backend,
                    model_revision,
                ):
                    enqueued += 1

            wrapped = len(rows) < effective_limit
            next_cursor = "" if wrapped else str(rows[-1]["memory_id"])
            self.conn.execute(
                """
                INSERT INTO embedding_scan_cursor (
                    backend, model_revision, last_memory_id, updated_at
                ) VALUES (?, ?, ?, ?)
                ON CONFLICT(backend, model_revision) DO UPDATE SET
                    last_memory_id=excluded.last_memory_id,
                    updated_at=excluded.updated_at
                """,
                (backend, model_revision, next_cursor, utc_now()),
            )
        return {
            "scanned": len(rows),
            "enqueued": enqueued,
            "next_cursor": next_cursor,
            "wrapped": wrapped,
        }

    def list_memories_missing_embeddings(
        self,
        backend: str,
        model_revision: str,
        limit: int,
    ) -> List[Dict[str, Any]]:
        effective_limit = int(limit)
        if effective_limit <= 0:
            return []
        rows = self._select_embedding_scan_window("", effective_limit)
        missing = []
        for row in rows:
            content_hash = vector_content_hash(row["summary"], row["content"])
            exists = self.conn.execute(
                """
                SELECT 1
                FROM memory_embedding
                WHERE memory_id = ? AND content_hash = ?
                  AND backend = ? AND model_revision = ?
                LIMIT 1
                """,
                (row["memory_id"], content_hash, backend, model_revision),
            ).fetchone()
            if exists is None:
                row["content_hash"] = content_hash
                missing.append(row)
        return missing

    def get_memory_embedding(
        self,
        memory_id: str,
        content_hash: str,
        backend: str,
        model_revision: str,
    ) -> Optional[PersistedVector]:
        row = self.conn.execute(
            """
            SELECT embedding.memory_id, embedding.content_hash, embedding.backend,
                   embedding.model_revision, embedding.dimension,
                   embedding.vector_blob, embedding.status, embedding.last_used_at
            FROM memory_embedding AS embedding
            JOIN memory ON memory.memory_id = embedding.memory_id
            WHERE embedding.memory_id = ? AND embedding.content_hash = ?
              AND embedding.backend = ? AND embedding.model_revision = ?
              AND embedding.status = 'ready'
              AND memory.deleted = 0
              AND memory.governance_state = 'active'
              AND memory.trust_level IN ('trusted', 'verified', 'reviewed')
              AND embedding.content_hash =
                  memguard_content_hash(memory.summary, memory.content)
            """,
            (memory_id, content_hash, backend, model_revision),
        ).fetchone()
        if row is None or row["vector_blob"] is None:
            return None

        persisted = PersistedVector(
            memory_id=row["memory_id"],
            content_hash=row["content_hash"],
            backend=row["backend"],
            model_revision=row["model_revision"],
            dimension=row["dimension"],
            vector=decode_vector(row["vector_blob"], row["dimension"]),
            status=row["status"],
        )
        now = utc_now()
        should_touch = row["last_used_at"] is None
        if not should_touch:
            try:
                elapsed = datetime.fromisoformat(now) - datetime.fromisoformat(
                    row["last_used_at"]
                )
                should_touch = elapsed.total_seconds() >= 60
            except (TypeError, ValueError):
                should_touch = True
        if should_touch:
            self.conn.execute(
                """
                UPDATE memory_embedding SET last_used_at = ?
                WHERE memory_id = ? AND content_hash = ?
                  AND backend = ? AND model_revision = ?
                  AND status = 'ready' AND vector_blob IS NOT NULL
                """,
                (now, memory_id, content_hash, backend, model_revision),
            )
            self.conn.commit()
        return persisted

    def get_memory_embeddings(
        self,
        memory_hashes: Dict[str, str],
        backend: str,
        model_revision: str,
    ) -> Dict[str, List[float]]:
        """Load current, governed vectors for a candidate set in one query."""
        if not memory_hashes:
            return {}
        memory_ids = list(memory_hashes)
        rows = self.conn.execute(
            """
            WITH requested(memory_id) AS (
                SELECT CAST(value AS TEXT) FROM json_each(?)
            )
            SELECT embedding.memory_id, embedding.content_hash,
                   embedding.dimension, embedding.vector_blob
            FROM memory_embedding AS embedding
            JOIN requested ON requested.memory_id = embedding.memory_id
            JOIN memory ON memory.memory_id = embedding.memory_id
            WHERE embedding.backend = ? AND embedding.model_revision = ?
              AND embedding.status = 'ready' AND embedding.vector_blob IS NOT NULL
              AND memory.deleted = 0
              AND memory.governance_state = 'active'
              AND memory.trust_level IN ('trusted', 'verified', 'reviewed')
              AND embedding.content_hash =
                  memguard_content_hash(memory.summary, memory.content)
            """,
            (json.dumps(memory_ids), backend, model_revision),
        ).fetchall()
        vectors: Dict[str, List[float]] = {}
        for row in rows:
            memory_id = str(row["memory_id"])
            if memory_hashes.get(memory_id) != row["content_hash"]:
                continue
            try:
                vectors[memory_id] = decode_vector(
                    row["vector_blob"], int(row["dimension"])
                )
            except (TypeError, ValueError):
                # A damaged cache row must not break the retrieval read path.
                continue
        return vectors

    def list_dense_candidate_vectors(
        self,
        backend: str,
        model_revision: str,
        namespace: str,
        max_vectors: int,
        session_id: str = "",
        project_root: Optional[str] = None,
    ) -> tuple[List[tuple[str, List[float]]], bool]:
        """Return a complete, governed vector snapshot or decline an oversized scan."""
        if max_vectors <= 0:
            return [], False
        scopes = [namespace, "user"] if namespace == "default" else [namespace]
        rows = self.conn.execute(
            """
            SELECT embedding.memory_id, embedding.dimension, embedding.vector_blob
            FROM memory_embedding AS embedding
            JOIN memory ON memory.memory_id = embedding.memory_id
            WHERE embedding.backend = ? AND embedding.model_revision = ?
              AND embedding.status = 'ready' AND embedding.vector_blob IS NOT NULL
              AND memory.deleted = 0 AND memory.governance_state = 'active'
              AND memory.trust_level IN ('trusted', 'verified', 'reviewed')
              AND memory.scope IN (SELECT value FROM json_each(?))
              AND (memory.retention_session_id = '' OR memory.retention_session_id = ?)
              AND (? IS NULL OR memory.project_root = '' OR memory.project_root = ?)
              AND embedding.content_hash =
                  memguard_content_hash(memory.summary, memory.content)
            ORDER BY embedding.memory_id
            LIMIT ?
            """,
            (backend, model_revision, json.dumps(scopes), session_id,
             project_root, project_root, max_vectors + 1),
        ).fetchall()
        if len(rows) > max_vectors:
            return [], False
        vectors = []
        for row in rows:
            try:
                vector = decode_vector(row["vector_blob"], int(row["dimension"]))
            except (TypeError, ValueError):
                continue
            vectors.append((str(row["memory_id"]), vector))
        return vectors, True

    def embedding_revision(self, backend: str, model_revision: str) -> str:
        row = self.conn.execute(
            """
            SELECT COUNT(*) AS total, COALESCE(MAX(updated_at), '') AS latest
            FROM memory_embedding
            WHERE backend = ? AND model_revision = ? AND status = 'ready'
            """,
            (backend, model_revision),
        ).fetchone()
        return "%s:%s" % (row["total"], row["latest"])

    def delete_memory_embeddings(self, memory_id: str) -> int:
        cursor = self.conn.execute(
            "DELETE FROM memory_embedding WHERE memory_id = ?", (memory_id,)
        )
        self.conn.commit()
        return cursor.rowcount

    def embedding_cache_status(self, backend: str, model_revision: str) -> Dict[str, Any]:
        now = utc_now()
        row = self.conn.execute(
            """
            SELECT
                SUM(CASE WHEN embedding.status = 'pending' THEN 1 ELSE 0 END) AS pending,
                SUM(CASE WHEN embedding.status = 'ready' THEN 1 ELSE 0 END) AS ready,
                SUM(CASE WHEN embedding.status = 'failed' THEN 1 ELSE 0 END) AS failed,
                SUM(CASE WHEN embedding.status = 'pending' THEN 1 ELSE 0 END) +
                SUM(CASE WHEN embedding.status = 'failed' AND
                    (embedding.next_attempt_at = '' OR embedding.next_attempt_at <= ?)
                    THEN 1 ELSE 0 END) AS due,
                SUM(CASE WHEN embedding.status = 'failed' AND
                    embedding.next_attempt_at > ? THEN 1 ELSE 0 END) AS retry_deferred,
                MIN(CASE WHEN embedding.status = 'failed' AND
                    embedding.next_attempt_at > ? THEN embedding.next_attempt_at END) AS next_retry_at
            FROM memory_embedding AS embedding
            JOIN memory ON memory.memory_id = embedding.memory_id
            WHERE embedding.backend = ? AND embedding.model_revision = ?
              AND memory.deleted = 0
              AND memory.governance_state = 'active'
              AND memory.trust_level IN ('trusted', 'verified', 'reviewed')
              AND embedding.content_hash =
                  memguard_content_hash(memory.summary, memory.content)
            """,
            (now, now, now, backend, model_revision),
        ).fetchone()
        return {
            "pending": int(row["pending"] or 0),
            "ready": int(row["ready"] or 0),
            "failed": int(row["failed"] or 0),
            "due": int(row["due"] or 0),
            "retry_deferred": int(row["retry_deferred"] or 0),
            "next_retry_at": row["next_retry_at"],
        }

    def find_active_memory_by_content(
        self, content: str, scope: str, tier: Optional[str] = None,
        retention_session_id: str = "", project_root: str = "",
    ) -> Optional[Dict[str, Any]]:
        tier_filter = "AND tier = ? AND retention_session_id = ?" if tier else ""
        params = (scope, content, project_root, tier, retention_session_id) if tier else (scope, content, project_root)
        row = self.conn.execute(
            """
            SELECT * FROM memory
            WHERE deleted = 0 AND governance_state = 'active'
              AND scope = ? AND content = ? AND project_root = ?
            """ + tier_filter + """
            ORDER BY updated_at DESC
            LIMIT 1
            """,
            params,
        ).fetchone()
        return dict(row) if row else None

    def find_active_fact_candidates(
        self, scope: str, subject: str, predicate: str, tier: Optional[str] = None,
        project_root: str = "",
    ) -> List[Dict[str, Any]]:
        tier_filter = "AND m.tier = ?" if tier else ""
        # Reading may reuse global facts; updating a fact slot stays within
        # its exact project so a local correction cannot rewrite global data.
        project_filter = "AND COALESCE(m.project_root, '') = ?"
        params = (
            (scope, subject, predicate, tier, project_root)
            if tier
            else (scope, subject, predicate, project_root)
        )
        return [dict(row) for row in self.conn.execute(
            """SELECT m.* FROM fact_slot f
               JOIN memory m ON m.memory_id = f.memory_id
               WHERE f.scope = ? AND f.subject = ? AND f.predicate = ?
                 AND m.deleted = 0 AND m.governance_state = 'active'
            """ + tier_filter + " " + project_filter + " ORDER BY m.updated_at DESC, m.memory_id LIMIT 2",
            params,
        )]

    def record_memory_hits(self, memory_ids: List[str]) -> int:
        """Count retrieval hits so tier eviction can prefer cold memories.

        Deliberately does not touch updated_at: a read must not look like an
        edit, otherwise retrieval would keep resetting the freshness signal.
        """

        unique = [str(item).strip() for item in memory_ids if str(item).strip()]
        if not unique:
            return 0
        now = utc_now()
        self.conn.executemany(
            "UPDATE memory SET hit_count = hit_count + 1, last_hit_at = ? WHERE memory_id = ?",
            [(now, memory_id) for memory_id in unique],
        )
        self.conn.commit()
        return len(unique)

    def set_governance_state(
        self,
        memory_id: str,
        state: str,
        trust_level: str,
        trust_score: float,
        reason: str,
    ) -> None:
        self.conn.execute(
            "UPDATE memory SET governance_state = ?, trust_level = ?, trust_score = ?, "
            "governance_reason = ?, updated_at = ? WHERE memory_id = ?",
            (state, trust_level, float(trust_score), reason, utc_now(), memory_id),
        )
        self.conn.commit()

    def insert_agent_event(self, event: Any) -> Dict[str, Any]:
        cursor = self.conn.execute(
            """
            INSERT OR IGNORE INTO agent_events (
                event_id, provenance_hash, schema_version, agent_name, agent_version,
                session_id, turn_id, sequence, event_type, role, content, tool_name,
                tool_args_json, tool_result, exit_code, state_before, state_after,
                user_feedback, project_root, task_type, timestamp, source_uri, created_at
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                event.event_id,
                event.provenance_hash,
                event.schema_version,
                event.agent_name,
                event.agent_version,
                event.session_id,
                event.turn_id,
                event.sequence,
                event.event_type,
                event.role,
                event.content,
                event.tool_name,
                json.dumps(event.tool_args, ensure_ascii=False, sort_keys=True),
                event.tool_result,
                event.exit_code,
                event.state_before,
                event.state_after,
                event.user_feedback,
                event.project_root,
                event.task_type,
                event.timestamp,
                event.source_uri,
                utc_now(),
            ),
        )
        self.conn.commit()
        stored = cursor.rowcount == 1
        row = self.conn.execute(
            "SELECT event_id FROM agent_events WHERE provenance_hash = ?",
            (event.provenance_hash,),
        ).fetchone()
        return {
            "stored": stored,
            "duplicate": not stored,
            "event_id": row["event_id"] if row else event.event_id,
            "provenance_hash": event.provenance_hash,
        }

    def list_agent_events(
        self,
        session_id: Optional[str] = None,
        event_type: Optional[str] = None,
        limit: int = 100,
    ) -> List[Dict[str, Any]]:
        clauses = []
        params: List[Any] = []
        if session_id:
            clauses.append("session_id = ?")
            params.append(session_id)
        if event_type:
            clauses.append("event_type = ?")
            params.append(event_type)
        sql = "SELECT * FROM agent_events"
        if clauses:
            sql += " WHERE " + " AND ".join(clauses)
        sql += " ORDER BY agent_name, session_id, sequence, event_id LIMIT ?"
        params.append(max(0, int(limit)))
        rows = self.conn.execute(sql, params).fetchall()
        result = []
        for row in rows:
            item = dict(row)
            item["tool_args"] = json.loads(item.pop("tool_args_json"))
            item["forgotten"] = bool(item.get("forgotten"))
            result.append(item)
        return result

    def agent_forget_impact(self, filters: Dict[str, str]) -> Dict[str, Any]:
        event_clauses = []
        event_params: List[Any] = []
        for key in ("agent_name", "session_id", "turn_id"):
            value = str(filters.get(key) or "").strip()
            if value:
                event_clauses.append("e.%s = ?" % key)
                event_params.append(value)
        start_time = str(filters.get("start_time") or "").strip()
        end_time = str(filters.get("end_time") or "").strip()
        if start_time:
            event_clauses.append("e.timestamp >= ?")
            event_params.append(start_time)
        if end_time:
            event_clauses.append("e.timestamp <= ?")
            event_params.append(end_time)
        topic = str(filters.get("preference_topic") or "").strip()

        if topic:
            sql = (
                "SELECT DISTINCT e.event_id FROM preference_observations p "
                "JOIN agent_events e ON e.event_id = p.event_id "
                "WHERE p.topic = ?"
            )
            params: List[Any] = [topic]
            if event_clauses:
                sql += " AND " + " AND ".join(event_clauses)
                params.extend(event_params)
        else:
            sql = "SELECT DISTINCT e.event_id FROM agent_events e"
            params = list(event_params)
            if event_clauses:
                sql += " WHERE " + " AND ".join(event_clauses)
        sql += " ORDER BY e.event_id"
        event_ids = [str(row["event_id"]) for row in self.conn.execute(sql, params)]
        if not event_ids:
            return {
                "event_ids": [],
                "memory_ids": [],
                "observation_count": 0,
                "context_usage_count": 0,
            }

        placeholders = ",".join("?" for _ in event_ids)
        memory_params: List[Any] = list(event_ids)
        if topic:
            # Only comma-separated SQLite parameter markers are interpolated; values remain bound parameters.
            memory_query = (
                """
                SELECT DISTINCT promoted_memory_id AS memory_id
                FROM preference_observations
                WHERE event_id IN (%s) AND topic = ? AND promoted_memory_id IS NOT NULL
                ORDER BY promoted_memory_id
                """
                % placeholders  # nosec B608
            )
            memory_rows = self.conn.execute(
                memory_query,
                memory_params + [topic],
            ).fetchall()
            observation_query = (
                "SELECT COUNT(*) AS count FROM preference_observations "
                "WHERE event_id IN (%s) AND topic = ?" % placeholders  # nosec B608
            )
            observation_count = int(
                self.conn.execute(
                    observation_query,
                    memory_params + [topic],
                ).fetchone()["count"]
            )
        else:
            memory_query = (
                "SELECT DISTINCT memory_id FROM memory_event_links "
                "WHERE event_id IN (%s) ORDER BY memory_id" % placeholders  # nosec B608
            )
            memory_rows = self.conn.execute(
                memory_query,
                memory_params,
            ).fetchall()
            observation_query = (
                "SELECT COUNT(*) AS count FROM preference_observations "
                "WHERE event_id IN (%s)" % placeholders  # nosec B608
            )
            observation_count = int(
                self.conn.execute(
                    observation_query,
                    memory_params,
                ).fetchone()["count"]
            )
        memory_ids = [str(row["memory_id"]) for row in memory_rows if row["memory_id"]]
        context_usage_count = 0
        if memory_ids:
            for row in self.conn.execute(
                "SELECT detail FROM audit WHERE event_type = 'agent_context_build'"
            ):
                try:
                    used = json.loads(row["detail"]).get("memory_ids", [])
                except (json.JSONDecodeError, TypeError, AttributeError):
                    used = []
                if any(memory_id in used for memory_id in memory_ids):
                    context_usage_count += 1
        return {
            "event_ids": event_ids,
            "memory_ids": memory_ids,
            "observation_count": observation_count,
            "context_usage_count": context_usage_count,
        }

    def forget_agent_event_payloads(self, event_ids: List[str]) -> int:
        with self.conn:
            return self._forget_agent_event_payloads_no_commit(event_ids)

    def _forget_agent_event_payloads_no_commit(self, event_ids: List[str]) -> int:
        if not event_ids:
            return 0
        targets = {str(event_id) for event_id in event_ids}
        redacted_conflict_ids = []
        for row in self.conn.execute(
            "SELECT * FROM fact_conflict WHERE source_link_json <> ''"
        ).fetchall():
            case = self._decode_fact_conflict(row)
            if str(case["source_link"].get("event_id") or "") in targets:
                self._redact_fact_conflict_no_commit(case, case["candidate_memory_ids"])
                redacted_conflict_ids.append(str(case["conflict_id"]))
        if redacted_conflict_ids:
            self._redact_audit_payloads_no_commit([], redacted_conflict_ids)
        placeholders = ",".join("?" for _ in event_ids)
        # Only SQLite parameter markers are interpolated; event IDs remain bound parameters.
        update_query = (
            """
            UPDATE agent_events
            SET content = '', tool_args_json = '{}', tool_result = '',
                state_before = '', state_after = '', user_feedback = '',
                forgotten = 1, forgotten_at = ?
            WHERE event_id IN (%s)
            """
            % placeholders  # nosec B608
        )
        cursor = self.conn.execute(
            update_query,
            [utc_now()] + list(event_ids),
        )
        return int(cursor.rowcount)

    def delete_preference_observations(
        self,
        event_ids: List[str],
        preference_topic: str = "",
    ) -> int:
        if not event_ids:
            return 0
        placeholders = ",".join("?" for _ in event_ids)
        # Only SQLite parameter markers are interpolated; event IDs remain bound parameters.
        sql = (
            "DELETE FROM preference_observations WHERE event_id IN (%s)"
            % placeholders  # nosec B608
        )
        params: List[Any] = list(event_ids)
        if preference_topic:
            sql += " AND topic = ?"
            params.append(preference_topic)
        cursor = self.conn.execute(sql, params)
        self.conn.commit()
        return int(cursor.rowcount)

    def delete_preference_observations_for_memories(
        self,
        memory_ids: List[str],
    ) -> int:
        clean_ids = [str(memory_id).strip() for memory_id in memory_ids if str(memory_id).strip()]
        if not clean_ids:
            return 0
        placeholders = ",".join("?" for _ in clean_ids)
        cursor = self.conn.execute(
            "DELETE FROM preference_observations WHERE promoted_memory_id IN (%s)"
            % placeholders,  # nosec B608 - only parameter placeholders are interpolated
            clean_ids,
        )
        self.conn.commit()
        return int(cursor.rowcount)

    def link_memory_event(
        self, memory_id: str, event_id: str, relation: str,
        source: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        memory = self.conn.execute(
            "SELECT memory_id FROM memory WHERE memory_id = ?",
            (memory_id,),
        ).fetchone()
        if memory is None:
            return {"linked": False, "reason": "memory_not_found", "memory_id": memory_id, "event_id": event_id}
        with self.conn:
            created = self._insert_memory_event_link_no_commit(
                memory_id, event_id, relation, source,
            )
        if created is None:
            return {"linked": False, "reason": "event_not_found", "memory_id": memory_id, "event_id": event_id}
        return {
            "linked": True,
            "created": created,
            "memory_id": memory_id,
            "event_id": event_id,
            "relation": relation,
        }

    def _insert_memory_event_link_no_commit(
        self, memory_id: str, event_id: str, relation: str,
        source: Optional[Dict[str, Any]] = None,
    ) -> Optional[bool]:
        event = self.conn.execute(
            """SELECT event_id, content, user_feedback, forgotten
               FROM agent_events WHERE event_id = ?""",
            (event_id,),
        ).fetchone()
        if event is None or event["forgotten"]:
            return None
        source_fields = self._validated_source_fields(source, event)
        cursor = self.conn.execute(
            """
            INSERT OR IGNORE INTO memory_event_links(
                memory_id, event_id, relation, source_field,
                source_start, source_end, source_excerpt, created_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (memory_id, event_id, relation, *source_fields, utc_now()),
        )
        return cursor.rowcount == 1

    @staticmethod
    def _validated_source_fields(
        source: Optional[Dict[str, Any]], event: Any,
    ) -> tuple[str, int, int, str]:
        if not source:
            return ("", -1, -1, "")
        field = str(source.get("source_field") or "")
        if field not in {"content", "user_feedback"}:
            raise ValueError("unsupported source field")
        start = int(source.get("source_start", -1))
        end = int(source.get("source_end", -1))
        excerpt = str(source.get("source_excerpt") or "")
        value = event[field] if isinstance(event, sqlite3.Row) else getattr(event, field)
        if not (0 <= start < end <= len(value)) or value[start:end] != excerpt or len(excerpt) > 500:
            raise ValueError("source excerpt does not match stored event")
        return (field, start, end, excerpt)

    def memory_event_evidence(self, memory_id: str) -> List[Dict[str, Any]]:
        rows = self.conn.execute(
            """
            SELECT e.*, e.rowid AS ingest_order,
                   l.relation, l.created_at AS linked_at,
                   l.source_field, l.source_start, l.source_end, l.source_excerpt
            FROM memory_event_links l
            JOIN agent_events e ON e.event_id = l.event_id
            WHERE l.memory_id = ?
            ORDER BY l.created_at ASC, e.event_id ASC
            """,
            (memory_id,),
        ).fetchall()
        result = []
        for row in rows:
            item = dict(row)
            item["tool_args"] = json.loads(item.pop("tool_args_json"))
            result.append(item)
        return result

    def redact_events_for_memories(self, memory_ids: List[str]) -> int:
        """Remove source payloads that could retain a forgotten atom's text.

        An event can support several atoms. The event payload is cleared as a
        whole; independent per-memory excerpts are governed by the erasure
        transaction, not by this low-level payload helper.
        """

        clean_ids = list(dict.fromkeys(str(item).strip() for item in memory_ids if str(item).strip()))
        if not clean_ids:
            return 0
        placeholders = ",".join("?" for _ in clean_ids)
        rows = self.conn.execute(
            "SELECT DISTINCT event_id FROM memory_event_links "
            "WHERE memory_id IN (%s)" % placeholders,  # nosec B608
            clean_ids,
        ).fetchall()
        return self.forget_agent_event_payloads([str(row["event_id"]) for row in rows])

    def add_preference_observation(
        self, observation: Any, event: Any,
        source: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        source_fields = self._validated_source_fields(source, event)
        cursor = self.conn.execute(
            """
            INSERT OR IGNORE INTO preference_observations (
                event_id, agent_name, session_id, topic, value, polarity,
                preference_scope, explicit, confidence, project_root, task_type,
                scope_agent_name, scope_session_id, source_field,
                source_start, source_end, source_excerpt, created_at
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                event.event_id,
                event.agent_name,
                event.session_id,
                observation.topic,
                observation.value,
                observation.polarity,
                observation.scope,
                1 if observation.explicit else 0,
                observation.confidence,
                event.project_root if observation.scope == "project" else "",
                event.task_type if observation.scope == "task_type" else "",
                event.agent_name if observation.scope == "agent" else "",
                event.session_id if observation.scope == "session" else "",
                *source_fields,
                utc_now(),
            ),
        )
        self.conn.commit()
        return {
            "created": cursor.rowcount == 1,
            "event_id": event.event_id,
            "topic": observation.topic,
            "value": observation.value,
        }

    def list_preference_aggregates(self) -> List[Dict[str, Any]]:
        rows = self.conn.execute(
            """
            SELECT
                topic,
                value,
                preference_scope,
                project_root,
                task_type,
                scope_agent_name,
                scope_session_id,
                COUNT(*) AS evidence_count,
                COUNT(DISTINCT session_id) AS distinct_sessions,
                SUM(CASE WHEN polarity = 'positive' THEN 1 ELSE 0 END) AS positive_count,
                MAX(explicit) AS explicit_confirmation,
                MAX(promoted_memory_id) AS promoted_memory_id,
                MIN(created_at) AS first_seen_at,
                MAX(created_at) AS last_seen_at
            FROM preference_observations AS po
            WHERE po.promoted_memory_id IS NULL
               OR EXISTS (
                    SELECT 1 FROM memory AS m
                    WHERE m.memory_id = po.promoted_memory_id
                      AND m.deleted = 0
                      AND m.governance_state = 'active'
               )
            GROUP BY topic, value, preference_scope, project_root, task_type, scope_agent_name, scope_session_id
            ORDER BY topic, value, preference_scope, project_root, task_type, scope_agent_name, scope_session_id
            """
        ).fetchall()
        result = []
        for row in rows:
            item = dict(row)
            contradiction = self.conn.execute(
                """
                SELECT COUNT(*) AS count
                FROM preference_observations
                WHERE topic = ? AND value != ?
                  AND preference_scope = ?
                  AND project_root = ?
                  AND task_type = ?
                  AND scope_agent_name = ?
                  AND scope_session_id = ?
                  AND (
                        promoted_memory_id IS NULL
                        OR EXISTS (
                            SELECT 1 FROM memory AS m
                            WHERE m.memory_id = promoted_memory_id
                              AND m.deleted = 0
                              AND m.governance_state = 'active'
                        )
                  )
                """,
                (
                    item["topic"],
                    item["value"],
                    item["preference_scope"],
                    item["project_root"],
                    item["task_type"],
                    item["scope_agent_name"],
                    item["scope_session_id"],
                ),
            ).fetchone()
            item["contradiction_count"] = int(contradiction["count"] if contradiction else 0)
            evidence_count = max(1, int(item["evidence_count"]))
            item["positive_ratio"] = float(item.pop("positive_count") or 0) / evidence_count
            item["explicit_confirmation"] = bool(item["explicit_confirmation"])
            result.append(item)
        return result

    def preference_observation_event_ids(self, aggregate: Dict[str, Any]) -> List[str]:
        rows = self.conn.execute(
            """
            SELECT event_id
            FROM preference_observations
            WHERE topic = ? AND value = ? AND preference_scope = ?
              AND project_root = ? AND task_type = ?
              AND scope_agent_name = ? AND scope_session_id = ?
            ORDER BY created_at ASC, event_id ASC
            """,
            (
                aggregate["topic"],
                aggregate["value"],
                aggregate["preference_scope"],
                aggregate["project_root"],
                aggregate["task_type"],
                aggregate["scope_agent_name"],
                aggregate["scope_session_id"],
            ),
        ).fetchall()
        return [str(row["event_id"]) for row in rows]

    def mark_preference_promoted(self, aggregate: Dict[str, Any], memory_id: str) -> None:
        self.conn.execute(
            """
            UPDATE preference_observations
            SET promoted_memory_id = ?
            WHERE topic = ? AND value = ? AND preference_scope = ?
              AND project_root = ? AND task_type = ?
              AND scope_agent_name = ? AND scope_session_id = ?
            """,
            (
                memory_id,
                aggregate["topic"],
                aggregate["value"],
                aggregate["preference_scope"],
                aggregate["project_root"],
                aggregate["task_type"],
                aggregate["scope_agent_name"],
                aggregate["scope_session_id"],
            ),
        )
        self.conn.commit()

    def search_text(
        self, query: str, limit: int = 10, session_id: str = "",
        project_root: Optional[str] = None,
    ) -> List[Dict[str, Any]]:
        query = query.strip()
        if not query:
            return []
        if self._fts_enabled:
            fts_query = self._build_fts_query(query)
            if fts_query:
                try:
                    rows = self.conn.execute(
                        """
                        SELECT m.*, bm25(memory_fts) AS rank
                        FROM memory_fts
                        JOIN memory m ON m.memory_id = memory_fts.memory_id
                        WHERE memory_fts MATCH ? AND m.deleted = 0 AND m.governance_state = 'active'
                          AND (m.retention_session_id = '' OR m.retention_session_id = ?)
                          AND (? IS NULL OR m.project_root = '' OR m.project_root = ?)
                        ORDER BY rank
                        LIMIT ?
                        """,
                        (fts_query, session_id, project_root, project_root, limit),
                    ).fetchall()
                    if rows:
                        return [dict(row) for row in rows]
                except sqlite3.OperationalError:
                    pass
        pattern = "%" + query + "%"
        rows = self.conn.execute(
            """
            SELECT *, 0.0 AS rank FROM memory
            WHERE deleted = 0 AND governance_state = 'active'
              AND (retention_session_id = '' OR retention_session_id = ?)
              AND (? IS NULL OR project_root = '' OR project_root = ?)
              AND (content LIKE ? OR summary LIKE ? OR type LIKE ?)
            LIMIT ?
            """,
            (session_id, project_root, project_root, pattern, pattern, pattern, limit),
        ).fetchall()
        return [dict(row) for row in rows]

    def memory_revision(self) -> str:
        row = self.conn.execute(
            """
            SELECT
                COUNT(*) AS total_count,
                SUM(CASE WHEN deleted = 0 AND governance_state = 'active' THEN 1 ELSE 0 END) AS live_count,
                COALESCE(MAX(updated_at), '') AS max_updated_at
            FROM memory
            """
        ).fetchone()
        if not row:
            return "0:0:"
        return "%s:%s:%s" % (row["total_count"] or 0, row["live_count"] or 0, row["max_updated_at"] or "")

    def graph_search(
        self, entity_query: str, limit: int = 10, session_id: str = "",
        project_root: Optional[str] = None,
    ) -> List[Dict[str, Any]]:
        pattern = "%" + entity_query + "%"
        exact = entity_query.lower()
        rows = self.conn.execute(
            """
            SELECT DISTINCT m.*, me.entity AS matched_entity,
                CASE WHEN lower(me.entity) = ? THEN 8 ELSE 0 END
                + CASE WHEN lower(m.summary) LIKE ? THEN 4 ELSE 0 END
                + CASE WHEN lower(m.content) LIKE ? THEN 3 ELSE 0 END
                + CASE WHEN m.subtype IN ('fact', 'workflow', 'procedure') THEN 1 ELSE 0 END AS graph_rank
            FROM memory_entity me
            JOIN memory m ON m.memory_id = me.memory_id
            WHERE m.deleted = 0 AND m.governance_state = 'active' AND me.entity LIKE ?
              AND (m.retention_session_id = '' OR m.retention_session_id = ?)
              AND (? IS NULL OR m.project_root = '' OR m.project_root = ?)
            ORDER BY graph_rank DESC, m.updated_at ASC
            LIMIT ?
            """,
            (exact, pattern.lower(), pattern.lower(), pattern, session_id,
             project_root, project_root, limit),
        ).fetchall()
        return [dict(row) for row in rows]

    def related_memories(
        self, memory_id: str, limit: int = 10, session_id: str = "",
        project_root: Optional[str] = None,
    ) -> List[Dict[str, Any]]:
        rows = self.conn.execute(
            """
            SELECT m.*, MIN(me.entity) AS via_entity
            FROM memory_entity seed
            JOIN memory origin ON origin.memory_id = seed.memory_id
            JOIN memory_entity me ON me.entity = seed.entity AND me.memory_id != seed.memory_id
            JOIN memory m ON m.memory_id = me.memory_id
            WHERE seed.memory_id = ? AND m.deleted = 0 AND m.governance_state = 'active'
              AND (m.retention_session_id = '' OR m.retention_session_id = ?)
              AND (? IS NULL OR m.project_root = '' OR m.project_root = ?)
              AND origin.deleted = 0 AND origin.governance_state = 'active'
              AND (origin.retention_session_id = '' OR origin.retention_session_id = ?)
              AND (? IS NULL OR origin.project_root = '' OR origin.project_root = ?)
            GROUP BY m.memory_id
            ORDER BY m.updated_at DESC, m.memory_id
            LIMIT ?
            """,
            (memory_id, session_id, project_root, project_root,
             session_id, project_root, project_root, limit),
        ).fetchall()
        return [dict(row) for row in rows]

    def memory_graph(self, memory_id: str) -> Dict[str, Any]:
        entities = [
            row["entity"]
            for row in self.conn.execute(
                "SELECT entity FROM memory_entity WHERE memory_id = ? ORDER BY entity",
                (memory_id,),
            ).fetchall()
        ]
        relations = [
            dict(row)
            for row in self.conn.execute(
                "SELECT source, relation, target FROM relation WHERE memory_id = ? ORDER BY relation_id",
                (memory_id,),
            ).fetchall()
        ]
        return {"memory_id": memory_id, "entities": entities, "relations": relations}

    def add_audit(self, event_type: str, memory_id: Optional[str], detail: Dict[str, Any]) -> Dict[str, Any]:
        try:
            self.conn.execute("BEGIN IMMEDIATE")
            result = self._add_audit_no_commit(event_type, memory_id, detail)
            self.conn.commit()
            return result
        except Exception:
            self.conn.rollback()
            raise

    def _add_audit_no_commit(
        self, event_type: str, memory_id: Optional[str], detail: Dict[str, Any],
    ) -> Dict[str, Any]:
        detail_json = json.dumps(detail, ensure_ascii=False, sort_keys=True)
        created_at = utc_now()
        prev_hash = self._last_audit_hash()
        event_hash = stable_hash("|".join([prev_hash, event_type, memory_id or "", detail_json, created_at]))
        cursor = self.conn.execute(
            """
            INSERT INTO audit(event_type, memory_id, detail, created_at, prev_hash, event_hash)
            VALUES (?, ?, ?, ?, ?, ?)
            """,
            (event_type, memory_id, detail_json, created_at, prev_hash, event_hash),
        )
        return {
            "audit_id": int(cursor.lastrowid),
            "prev_hash": prev_hash,
            "event_hash": event_hash,
            "created_at": created_at,
        }

    def list_audit(self, limit: int = 50, event_type: Optional[str] = None) -> List[Dict[str, Any]]:
        if event_type:
            rows = self.conn.execute(
                "SELECT * FROM audit WHERE event_type = ? ORDER BY audit_id DESC LIMIT ?",
                (event_type, limit),
            ).fetchall()
        else:
            rows = self.conn.execute(
                "SELECT * FROM audit ORDER BY audit_id DESC LIMIT ?",
                (limit,),
            ).fetchall()
        return [dict(row) for row in rows]

    def list_memory_audit(self, memory_id: str, limit: int = 200) -> List[Dict[str, Any]]:
        """List the complete bounded audit history for one memory id.

        The generic recent-audit view is intentionally global and can miss an
        older version operation when the namespace is busy.  Lifecycle views
        need a memory-scoped query so their evidence counts remain truthful.
        """

        rows = self.conn.execute(
            """SELECT * FROM audit
               WHERE memory_id = ?
               ORDER BY audit_id ASC
               LIMIT ?""",
            (str(memory_id), max(1, min(int(limit), 2000))),
        ).fetchall()
        return [dict(row) for row in rows]

    def list_memory_context_uses(self, memory_id: str, limit: int = 200) -> List[Dict[str, Any]]:
        """Find bounded Agent-context audit entries that name one memory.

        Context audits store selected ids as structured JSON.  The id is
        generated by the service and is therefore safe to use as an exact
        token probe; this scoped query avoids scanning an arbitrary global
        audit window for every lifecycle detail request.
        """

        rows = self.conn.execute(
            """SELECT * FROM audit
               WHERE event_type = 'agent_context_build'
                 AND detail LIKE ?
               ORDER BY audit_id DESC
               LIMIT ?""",
            ("%%%s%%" % str(memory_id), max(1, min(int(limit), 1000))),
        ).fetchall()
        return [dict(row) for row in rows]

    def verify_audit_chain(self) -> Dict[str, Any]:
        rows = self.conn.execute("SELECT * FROM audit ORDER BY audit_id ASC").fetchall()
        previous = ""
        checked = 0
        for row in rows:
            detail = row["detail"]
            expected = stable_hash("|".join([previous, row["event_type"], row["memory_id"] or "", detail, row["created_at"]]))
            if row["prev_hash"] != previous or row["event_hash"] != expected:
                return {
                    "ok": False,
                    "checked": checked,
                    "failed_audit_id": row["audit_id"],
                    "expected_hash": expected,
                    "actual_hash": row["event_hash"],
                }
            previous = row["event_hash"]
            checked += 1
        return {"ok": True, "checked": checked, "head_hash": previous}

    @staticmethod
    def _build_fts_query(query: str) -> str:
        cleaned = []
        for char in query:
            if char.isalnum() or "\u4e00" <= char <= "\u9fff":
                cleaned.append(char)
            else:
                cleaned.append(" ")
        tokens = [token for token in "".join(cleaned).split() if token]
        return " OR ".join(tokens[:8])

    def _last_audit_hash(self) -> str:
        row = self.conn.execute("SELECT event_hash FROM audit ORDER BY audit_id DESC LIMIT 1").fetchone()
        if not row:
            return ""
        return row["event_hash"] or ""

    def _create_memory_embedding_table(
        self, table: str, *, if_not_exists: bool = False
    ) -> None:
        if table not in {"memory_embedding", "memory_embedding_rebuild"}:
            raise ValueError("unapproved embedding table: %s" % table)
        clause = "IF NOT EXISTS " if if_not_exists else ""
        self.conn.execute(
            """
            CREATE TABLE %s%s (
                memory_id TEXT NOT NULL,
                content_hash TEXT NOT NULL,
                backend TEXT NOT NULL,
                model_revision TEXT NOT NULL,
                dimension INTEGER NOT NULL,
                vector_blob BLOB,
                status TEXT NOT NULL CHECK(status IN ('pending', 'ready', 'failed')),
                failure_reason TEXT NOT NULL DEFAULT '',
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL,
                last_used_at TEXT,
                attempt_count INTEGER NOT NULL DEFAULT 0,
                next_attempt_at TEXT NOT NULL DEFAULT '',
                job_token TEXT NOT NULL,
                CHECK(
                    status != 'ready'
                    OR (
                        vector_blob IS NOT NULL
                        AND dimension > 0
                        AND length(vector_blob) = dimension * 4
                    )
                ),
                PRIMARY KEY(memory_id, backend, model_revision, content_hash)
            )
            """
            % (clause, table)
        )

    @staticmethod
    def _memory_embedding_needs_rebuild(
        table_info: Dict[str, sqlite3.Row], table_sql: str
    ) -> bool:
        required_columns = {
            "memory_id",
            "content_hash",
            "backend",
            "model_revision",
            "dimension",
            "vector_blob",
            "status",
            "failure_reason",
            "created_at",
            "updated_at",
            "last_used_at",
            "attempt_count",
            "next_attempt_at",
            "job_token",
        }
        if not required_columns.issubset(table_info):
            return True
        required_not_null = required_columns - {"vector_blob", "last_used_at"}
        if any(not table_info[name]["notnull"] for name in required_not_null):
            return True
        if table_info["vector_blob"]["notnull"]:
            return True
        empty_defaults = ("failure_reason", "next_attempt_at")
        if any(table_info[name]["dflt_value"] != "''" for name in empty_defaults):
            return True
        if str(table_info["attempt_count"]["dflt_value"]).strip("() ") != "0":
            return True
        if table_info["job_token"]["dflt_value"] is not None:
            return True
        primary_key = sorted(
            (
                (row["pk"], name)
                for name, row in table_info.items()
                if row["pk"]
            )
        )
        if [name for _, name in primary_key] != [
            "memory_id",
            "backend",
            "model_revision",
            "content_hash",
        ]:
            return True
        normalized_sql = "".join((table_sql or "").lower().split())
        required_constraints = (
            "check(statusin('pending','ready','failed'))",
            "check(status!='ready'or(vector_blobisnotnullanddimension>0"
            "andlength(vector_blob)=dimension*4))",
        )
        return any(fragment not in normalized_sql for fragment in required_constraints)

    def _rebuild_memory_embedding(self, old_columns: set) -> None:
        source_cursor = self.conn.execute("SELECT * FROM memory_embedding")
        self.conn.execute("DROP TABLE IF EXISTS memory_embedding_rebuild")
        self._create_memory_embedding_table("memory_embedding_rebuild")
        used_tokens = set()
        while rows := source_cursor.fetchmany(256):
            for row in rows:
                status = row["status"] if "status" in old_columns else ""
                if status not in {"pending", "ready", "failed"}:
                    continue
                dimension = row["dimension"] if "dimension" in old_columns else 0
                vector_blob = (
                    row["vector_blob"] if "vector_blob" in old_columns else None
                )
                if status == "ready":
                    try:
                        dimension = int(dimension)
                        valid_ready = (
                            vector_blob is not None
                            and dimension > 0
                            and len(vector_blob) == dimension * 4
                        )
                    except (TypeError, ValueError):
                        valid_ready = False
                    if not valid_ready:
                        continue
                else:
                    dimension = 0
                    vector_blob = None

                token = row["job_token"] if "job_token" in old_columns else ""
                token = str(token or "")
                while not token or token in used_tokens:
                    token = new_id("embjob")
                used_tokens.add(token)
                failure_reason = (
                    row["failure_reason"] if "failure_reason" in old_columns else ""
                )
                attempt_count = (
                    row["attempt_count"] if "attempt_count" in old_columns else 0
                )
                next_attempt_at = (
                    row["next_attempt_at"] if "next_attempt_at" in old_columns else ""
                )
                self.conn.execute(
                    """
                    INSERT INTO memory_embedding_rebuild (
                        memory_id, content_hash, backend, model_revision, dimension,
                        vector_blob, status, failure_reason, created_at, updated_at,
                        last_used_at, attempt_count, next_attempt_at, job_token
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        row["memory_id"],
                        row["content_hash"],
                        row["backend"],
                        row["model_revision"],
                        dimension,
                        vector_blob,
                        status,
                        failure_reason or "",
                        row["created_at"],
                        row["updated_at"],
                        row["last_used_at"] if "last_used_at" in old_columns else None,
                        attempt_count if attempt_count is not None else 0,
                        next_attempt_at or "",
                        token,
                    ),
                )
        source_cursor.close()
        self.conn.execute("DROP TABLE memory_embedding")
        self.conn.execute(
            "ALTER TABLE memory_embedding_rebuild RENAME TO memory_embedding"
        )

    def _init_memory_embedding_schema(self) -> None:
        try:
            self.conn.execute("BEGIN IMMEDIATE")
            self._create_memory_embedding_table(
                "memory_embedding", if_not_exists=True
            )
            table_info = {
                row["name"]: row
                for row in self.conn.execute(
                    "PRAGMA table_info(memory_embedding)"
                ).fetchall()
            }
            table_row = self.conn.execute(
                """
                SELECT sql
                FROM sqlite_master
                WHERE type = 'table' AND name = 'memory_embedding'
                """
            ).fetchone()
            table_sql = table_row["sql"] if table_row else ""
            if self._memory_embedding_needs_rebuild(table_info, table_sql):
                self._rebuild_memory_embedding(set(table_info))

            self.conn.execute(
                """
                DELETE FROM memory_embedding
                WHERE NOT EXISTS (
                    SELECT 1
                    FROM memory
                    WHERE memory.memory_id = memory_embedding.memory_id
                      AND memory.deleted = 0
                      AND memory.governance_state = 'active'
                      AND memory.trust_level IN ('trusted', 'verified', 'reviewed')
                      AND memguard_content_hash(memory.summary, memory.content) =
                          memory_embedding.content_hash
                )
                """
            )
            legacy_jobs = self.conn.execute(
                """
                SELECT memory_id, backend, model_revision, content_hash
                FROM memory_embedding
                WHERE job_token = ''
                """
            ).fetchall()
            for row in legacy_jobs:
                self.conn.execute(
                    """
                    UPDATE memory_embedding
                    SET job_token = ?
                    WHERE memory_id = ? AND backend = ? AND model_revision = ?
                      AND content_hash = ? AND job_token = ''
                    """,
                    (
                        new_id("embjob"),
                        row["memory_id"],
                        row["backend"],
                        row["model_revision"],
                        row["content_hash"],
                    ),
                )
            self.conn.commit()
        except Exception:
            self.conn.rollback()
            raise

    def _ensure_column(self, table: str, column: str, declaration: str) -> None:
        migration = (table, column, declaration)
        if migration not in _ALLOWED_COLUMN_MIGRATIONS:
            raise ValueError("unapproved schema migration: %s.%s" % (table, column))
        # The complete table/column/declaration tuple is matched against a fixed schema allowlist.
        rows = self.conn.execute("PRAGMA table_info(%s)" % table).fetchall()
        names = {row["name"] for row in rows}
        if column in names:
            return
        try:
            self.conn.execute("BEGIN IMMEDIATE")
            rows = self.conn.execute("PRAGMA table_info(%s)" % table).fetchall()
            names = {row["name"] for row in rows}
            if column not in names:
                self.conn.execute("ALTER TABLE %s ADD COLUMN %s %s" % migration)
            self.conn.commit()
        except sqlite3.OperationalError as exc:
            self.conn.rollback()
            if "duplicate column name" not in str(exc).lower():
                raise
            rows = self.conn.execute("PRAGMA table_info(%s)" % table).fetchall()
            if column not in {row["name"] for row in rows}:
                raise
        except Exception:
            self.conn.rollback()
            raise

    def _upsert_graph(self, record: MemoryRecord) -> None:
        self.conn.execute("DELETE FROM memory_entity WHERE memory_id = ?", (record.memory_id,))
        self.conn.execute("DELETE FROM relation WHERE memory_id = ?", (record.memory_id,))
        if record.deleted:
            return
        for entity in record.entities:
            self.conn.execute(
                "INSERT OR REPLACE INTO entity(entity, updated_at) VALUES (?, ?)",
                (entity, utc_now()),
            )
            self.conn.execute(
                "INSERT OR IGNORE INTO memory_entity(memory_id, entity) VALUES (?, ?)",
                (record.memory_id, entity),
            )
        for relation in record.relations:
            self.conn.execute(
                "INSERT INTO relation(memory_id, source, relation, target) VALUES (?, ?, ?, ?)",
                (
                    record.memory_id,
                    str(relation.get("source", "")),
                    str(relation.get("relation", "related_to")),
                    str(relation.get("target", "")),
                ),
            )
