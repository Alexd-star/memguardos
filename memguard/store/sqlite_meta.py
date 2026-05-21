from pathlib import Path
from typing import Any, Dict, List, Optional
import json
import sqlite3

from memguard.models import MemoryRecord, stable_hash, utc_now


class SQLiteMetaStore:
    def __init__(self, path: Path):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.conn = sqlite3.connect(str(self.path))
        self.conn.row_factory = sqlite3.Row
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
                deleted INTEGER NOT NULL DEFAULT 0,
                updated_at TEXT NOT NULL
            )
            """
        )
        self._ensure_column("memory", "subtype", "TEXT NOT NULL DEFAULT 'general'")
        self._ensure_column("memory", "tier", "TEXT NOT NULL DEFAULT 'long_term'")
        self._ensure_column("memory", "sensitivity_redacted", "INTEGER NOT NULL DEFAULT 0")
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
        self.conn.commit()

    def close(self) -> None:
        self.conn.close()

    def upsert_memory(self, record: MemoryRecord) -> None:
        self.conn.execute(
            """
            INSERT INTO memory (
                memory_id, type, scope, tier, summary, content, source_kind, source_uri,
                sensitivity_level, sensitivity_labels, sensitivity_redacted, version_id, deleted, updated_at, subtype
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
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
                1 if record.deleted else 0,
                utc_now(),
                record.subtype,
            ),
        )
        if self._fts_enabled:
            self.conn.execute("DELETE FROM memory_fts WHERE memory_id = ?", (record.memory_id,))
            if not record.deleted:
                self.conn.execute(
                    "INSERT INTO memory_fts(memory_id, content, summary, type) VALUES (?, ?, ?, ?)",
                    (record.memory_id, record.content, record.summary + " " + record.subtype, record.type),
                )
        self._upsert_graph(record)
        self.conn.commit()

    def update_tier(self, memory_id: str, tier: str) -> None:
        self.conn.execute(
            "UPDATE memory SET tier = ?, updated_at = ? WHERE memory_id = ? AND deleted = 0",
            (tier, utc_now(), memory_id),
        )
        self.conn.commit()

    def mark_deleted(self, memory_id: str) -> None:
        self.conn.execute(
            "UPDATE memory SET deleted = 1, content = '', summary = 'FORGOTTEN', updated_at = ? WHERE memory_id = ?",
            (utc_now(), memory_id),
        )
        if self._fts_enabled:
            self.conn.execute("DELETE FROM memory_fts WHERE memory_id = ?", (memory_id,))
        self.conn.execute("DELETE FROM memory_entity WHERE memory_id = ?", (memory_id,))
        self.conn.execute("DELETE FROM relation WHERE memory_id = ?", (memory_id,))
        self.conn.commit()

    def list_memories(self, include_deleted: bool = False, tier: Optional[str] = None) -> List[Dict[str, Any]]:
        sql = "SELECT * FROM memory"
        clauses = []
        params = []
        if not include_deleted:
            clauses.append("deleted = 0")
        if tier:
            clauses.append("tier = ?")
            params.append(tier)
        if clauses:
            sql += " WHERE " + " AND ".join(clauses)
        sql += " ORDER BY updated_at DESC"
        return [dict(row) for row in self.conn.execute(sql, params)]

    def get_memory(self, memory_id: str) -> Optional[Dict[str, Any]]:
        row = self.conn.execute("SELECT * FROM memory WHERE memory_id = ?", (memory_id,)).fetchone()
        return dict(row) if row else None

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
                        WHERE memory_fts MATCH ? AND m.deleted = 0
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
            WHERE deleted = 0 AND (content LIKE ? OR summary LIKE ? OR type LIKE ?)
            LIMIT ?
            """,
            (pattern, pattern, pattern, limit),
        ).fetchall()
        return [dict(row) for row in rows]

    def graph_search(self, entity_query: str, limit: int = 10) -> List[Dict[str, Any]]:
        pattern = "%" + entity_query + "%"
        rows = self.conn.execute(
            """
            SELECT DISTINCT m.*, me.entity AS matched_entity
            FROM memory_entity me
            JOIN memory m ON m.memory_id = me.memory_id
            WHERE m.deleted = 0 AND me.entity LIKE ?
            ORDER BY m.updated_at DESC
            LIMIT ?
            """,
            (pattern, limit),
        ).fetchall()
        return [dict(row) for row in rows]

    def related_memories(self, memory_id: str, limit: int = 10) -> List[Dict[str, Any]]:
        rows = self.conn.execute(
            """
            SELECT DISTINCT m.*, me.entity AS via_entity
            FROM memory_entity seed
            JOIN memory_entity me ON me.entity = seed.entity AND me.memory_id != seed.memory_id
            JOIN memory m ON m.memory_id = me.memory_id
            WHERE seed.memory_id = ? AND m.deleted = 0
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
        prev_hash = self._last_audit_hash()
        event_hash = stable_hash("|".join([prev_hash, event_type, memory_id or "", detail_json, created_at]))
        self.conn.execute(
            """
            INSERT INTO audit(event_type, memory_id, detail, created_at, prev_hash, event_hash)
            VALUES (?, ?, ?, ?, ?, ?)
            """,
            (event_type, memory_id, detail_json, created_at, prev_hash, event_hash),
        )
        self.conn.commit()
        return {"prev_hash": prev_hash, "event_hash": event_hash, "created_at": created_at}

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
        rows = self.conn.execute("PRAGMA table_info(%s)" % table).fetchall()
        names = {row["name"] for row in rows}
        if column not in names:
            self.conn.execute("ALTER TABLE %s ADD COLUMN %s %s" % (table, column, declaration))
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
