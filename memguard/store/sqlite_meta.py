from pathlib import Path
from typing import Any, Dict, List, Optional
import json
import sqlite3

from memguard.models import MemoryRecord, stable_hash, utc_now
from memguard.retrieval.vector_cache import PersistedVector, decode_vector, encode_vector


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
    ("memory", "project_root", "TEXT NOT NULL DEFAULT ''"),
    ("memory", "task_type", "TEXT NOT NULL DEFAULT ''"),
    ("memory", "trust_level", "TEXT NOT NULL DEFAULT 'trusted'"),
    ("memory", "trust_score", "REAL NOT NULL DEFAULT 1.0"),
    ("memory", "governance_state", "TEXT NOT NULL DEFAULT 'active'"),
    ("memory", "governance_reason", "TEXT NOT NULL DEFAULT ''"),
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
}


class SQLiteMetaStore:
    def __init__(self, path: Path):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.conn = sqlite3.connect(str(self.path), timeout=30.0)
        self.conn.row_factory = sqlite3.Row
        self.conn.execute("PRAGMA busy_timeout = 30000")
        self.conn.execute("PRAGMA temp_store = MEMORY")
        self.conn.execute("PRAGMA cache_size = -32768")
        self.conn.execute("PRAGMA mmap_size = 268435456")
        try:
            self.conn.execute("PRAGMA journal_mode = WAL")
            self.conn.execute("PRAGMA synchronous = NORMAL")
        except sqlite3.OperationalError:
            pass
        self._fts_enabled = False
        self.init_schema()

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
        self._ensure_column("memory", "project_root", "TEXT NOT NULL DEFAULT ''")
        self._ensure_column("memory", "task_type", "TEXT NOT NULL DEFAULT ''")
        self._ensure_column("memory", "trust_level", "TEXT NOT NULL DEFAULT 'trusted'")
        self._ensure_column("memory", "trust_score", "REAL NOT NULL DEFAULT 1.0")
        self._ensure_column("memory", "governance_state", "TEXT NOT NULL DEFAULT 'active'")
        self._ensure_column("memory", "governance_reason", "TEXT NOT NULL DEFAULT ''")
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
                created_at TEXT NOT NULL,
                PRIMARY KEY (memory_id, event_id, relation)
            )
            """
        )
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
        self.conn.execute(
            """
            CREATE TABLE IF NOT EXISTS memory_embedding (
                memory_id TEXT NOT NULL,
                content_hash TEXT NOT NULL,
                backend TEXT NOT NULL,
                model_revision TEXT NOT NULL,
                dimension INTEGER NOT NULL,
                vector_blob BLOB NOT NULL,
                status TEXT NOT NULL CHECK(status IN ('pending', 'ready', 'failed')),
                failure_reason TEXT,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL,
                last_used_at TEXT,
                PRIMARY KEY(memory_id, backend, model_revision, content_hash)
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
        self.conn.execute("CREATE INDEX IF NOT EXISTS idx_memory_live_updated ON memory(deleted, updated_at DESC)")
        self.conn.execute("CREATE INDEX IF NOT EXISTS idx_memory_tier_live ON memory(tier, deleted, updated_at DESC)")
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
        self.conn.commit()

    def close(self) -> None:
        self.conn.close()

    def upsert_memory(self, record: MemoryRecord) -> None:
        self.conn.execute(
            """
            INSERT INTO memory (
                memory_id, type, scope, tier, summary, content, source_kind, source_uri,
                sensitivity_level, sensitivity_labels, sensitivity_redacted, version_id,
                valid_from, valid_to, superseded_by, preference_topic, preference_value,
                preference_scope, preference_explicit, agent_name, preference_session_id,
                project_root, task_type, trust_level, trust_score, governance_state,
                governance_reason, deleted, updated_at, subtype
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
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
                project_root=excluded.project_root,
                task_type=excluded.task_type,
                trust_level=excluded.trust_level,
                trust_score=excluded.trust_score,
                governance_state=excluded.governance_state,
                governance_reason=excluded.governance_reason,
                deleted=excluded.deleted,
                updated_at=excluded.updated_at
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
        self.conn.commit()

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
        now = utc_now()
        self.conn.execute(
            """
            UPDATE memory
            SET deleted = 1, content = '', summary = 'FORGOTTEN',
                valid_to = ?, superseded_by = ?, updated_at = ?
            WHERE memory_id = ?
            """,
            (now, superseded_by, now, memory_id),
        )
        if self._fts_enabled:
            self.conn.execute("DELETE FROM memory_fts WHERE memory_id = ?", (memory_id,))
        self.conn.execute("DELETE FROM memory_entity WHERE memory_id = ?", (memory_id,))
        self.conn.execute("DELETE FROM relation WHERE memory_id = ?", (memory_id,))
        self.conn.commit()

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
    ) -> List[Dict[str, Any]]:
        effective_limit = max(0, int(limit))
        if effective_limit == 0:
            return []
        excluded = list(dict.fromkeys(str(memory_id) for memory_id in (exclude_ids or []) if memory_id))
        sql = "SELECT * FROM memory WHERE deleted = 0 AND governance_state = 'active'"
        params: List[Any] = []
        if excluded:
            placeholders = ",".join("?" for _ in excluded)
            sql += " AND memory_id NOT IN (%s)" % placeholders
            params.extend(excluded)
        sql += " ORDER BY updated_at DESC LIMIT ?"
        params.append(effective_limit)
        return [dict(row) for row in self.conn.execute(sql, params)]

    def get_memory(self, memory_id: str) -> Optional[Dict[str, Any]]:
        row = self.conn.execute("SELECT * FROM memory WHERE memory_id = ?", (memory_id,)).fetchone()
        return dict(row) if row else None

    def upsert_memory_embedding(
        self,
        memory_id: str,
        content_hash: str,
        backend: str,
        model_revision: str,
        vector: List[float],
        status: str = "ready",
        failure_reason: Optional[str] = None,
    ) -> None:
        vector_blob = encode_vector(vector)
        now = utc_now()
        self.conn.execute(
            """
            INSERT INTO memory_embedding (
                memory_id, content_hash, backend, model_revision, dimension,
                vector_blob, status, failure_reason, created_at, updated_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(memory_id, backend, model_revision, content_hash) DO UPDATE SET
                dimension=excluded.dimension,
                vector_blob=excluded.vector_blob,
                status=excluded.status,
                failure_reason=excluded.failure_reason,
                updated_at=excluded.updated_at
            """,
            (
                memory_id,
                content_hash,
                backend,
                model_revision,
                len(vector),
                sqlite3.Binary(vector_blob),
                status,
                failure_reason,
                now,
                now,
            ),
        )
        self.conn.commit()

    def get_memory_embedding(
        self,
        memory_id: str,
        content_hash: str,
        backend: str,
        model_revision: str,
    ) -> Optional[PersistedVector]:
        row = self.conn.execute(
            """
            SELECT memory_id, content_hash, backend, model_revision,
                   dimension, vector_blob, status
            FROM memory_embedding
            WHERE memory_id = ? AND content_hash = ?
              AND backend = ? AND model_revision = ?
            """,
            (memory_id, content_hash, backend, model_revision),
        ).fetchone()
        if row is None:
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
        self.conn.execute(
            """
            UPDATE memory_embedding SET last_used_at = ?
            WHERE memory_id = ? AND content_hash = ?
              AND backend = ? AND model_revision = ?
            """,
            (utc_now(), memory_id, content_hash, backend, model_revision),
        )
        self.conn.commit()
        return persisted

    def delete_memory_embeddings(self, memory_id: str) -> int:
        cursor = self.conn.execute(
            "DELETE FROM memory_embedding WHERE memory_id = ?", (memory_id,)
        )
        self.conn.commit()
        return cursor.rowcount

    def embedding_cache_status(self, backend: str, model_revision: str) -> Dict[str, Any]:
        counts = {"pending": 0, "ready": 0, "failed": 0}
        rows = self.conn.execute(
            """
            SELECT status, COUNT(*) AS count
            FROM memory_embedding
            WHERE backend = ? AND model_revision = ?
            GROUP BY status
            """,
            (backend, model_revision),
        ).fetchall()
        for row in rows:
            counts[row["status"]] = row["count"]
        return counts

    def find_active_memory_by_content(self, content: str, scope: str) -> Optional[Dict[str, Any]]:
        row = self.conn.execute(
            """
            SELECT * FROM memory
            WHERE deleted = 0 AND governance_state = 'active'
              AND scope = ? AND content = ?
            ORDER BY updated_at DESC
            LIMIT 1
            """,
            (scope, content),
        ).fetchone()
        return dict(row) if row else None

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
        if not event_ids:
            return 0
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
        self.conn.commit()
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

    def link_memory_event(self, memory_id: str, event_id: str, relation: str) -> Dict[str, Any]:
        memory = self.conn.execute(
            "SELECT memory_id FROM memory WHERE memory_id = ?",
            (memory_id,),
        ).fetchone()
        if memory is None:
            return {"linked": False, "reason": "memory_not_found", "memory_id": memory_id, "event_id": event_id}
        event = self.conn.execute(
            "SELECT event_id FROM agent_events WHERE event_id = ?",
            (event_id,),
        ).fetchone()
        if event is None:
            return {"linked": False, "reason": "event_not_found", "memory_id": memory_id, "event_id": event_id}
        cursor = self.conn.execute(
            """
            INSERT OR IGNORE INTO memory_event_links(memory_id, event_id, relation, created_at)
            VALUES (?, ?, ?, ?)
            """,
            (memory_id, event_id, relation, utc_now()),
        )
        self.conn.commit()
        return {
            "linked": True,
            "created": cursor.rowcount == 1,
            "memory_id": memory_id,
            "event_id": event_id,
            "relation": relation,
        }

    def memory_event_evidence(self, memory_id: str) -> List[Dict[str, Any]]:
        rows = self.conn.execute(
            """
            SELECT e.*, e.rowid AS ingest_order,
                   l.relation, l.created_at AS linked_at
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

    def add_preference_observation(self, observation: Any, event: Any) -> Dict[str, Any]:
        cursor = self.conn.execute(
            """
            INSERT OR IGNORE INTO preference_observations (
                event_id, agent_name, session_id, topic, value, polarity,
                preference_scope, explicit, confidence, project_root, task_type,
                scope_agent_name, scope_session_id, created_at
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
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
            FROM preference_observations
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

    def search_text(self, query: str, limit: int = 10) -> List[Dict[str, Any]]:
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
                        ORDER BY rank
                        LIMIT ?
                        """,
                        (fts_query, limit),
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
              AND (content LIKE ? OR summary LIKE ? OR type LIKE ?)
            LIMIT ?
            """,
            (pattern, pattern, pattern, limit),
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

    def graph_search(self, entity_query: str, limit: int = 10) -> List[Dict[str, Any]]:
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
            ORDER BY graph_rank DESC, m.updated_at ASC
            LIMIT ?
            """,
            (exact, pattern.lower(), pattern.lower(), pattern, limit),
        ).fetchall()
        return [dict(row) for row in rows]

    def related_memories(self, memory_id: str, limit: int = 10) -> List[Dict[str, Any]]:
        rows = self.conn.execute(
            """
            SELECT DISTINCT m.*, me.entity AS via_entity
            FROM memory_entity seed
            JOIN memory_entity me ON me.entity = seed.entity AND me.memory_id != seed.memory_id
            JOIN memory m ON m.memory_id = me.memory_id
            WHERE seed.memory_id = ? AND m.deleted = 0 AND m.governance_state = 'active'
            ORDER BY m.updated_at DESC
            LIMIT ?
            """,
            (memory_id, limit),
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
        detail_json = json.dumps(detail, ensure_ascii=False, sort_keys=True)
        created_at = utc_now()
        try:
            self.conn.execute("BEGIN IMMEDIATE")
            prev_hash = self._last_audit_hash()
            event_hash = stable_hash("|".join([prev_hash, event_type, memory_id or "", detail_json, created_at]))
            cursor = self.conn.execute(
                """
                INSERT INTO audit(event_type, memory_id, detail, created_at, prev_hash, event_hash)
                VALUES (?, ?, ?, ?, ?, ?)
                """,
                (event_type, memory_id, detail_json, created_at, prev_hash, event_hash),
            )
            self.conn.commit()
        except sqlite3.Error:
            self.conn.rollback()
            raise
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

    def _ensure_column(self, table: str, column: str, declaration: str) -> None:
        migration = (table, column, declaration)
        if migration not in _ALLOWED_COLUMN_MIGRATIONS:
            raise ValueError("unapproved schema migration: %s.%s" % (table, column))
        # The complete table/column/declaration tuple is matched against a fixed schema allowlist.
        rows = self.conn.execute("PRAGMA table_info(%s)" % table).fetchall()
        names = {row["name"] for row in rows}
        if column not in names:
            self.conn.execute("ALTER TABLE %s ADD COLUMN %s %s" % migration)
            self.conn.commit()

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
