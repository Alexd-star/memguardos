"""Version-bound derivation edges, separate from the similarity graph."""

import json

from memguard.models import utc_now


class DependencyChanged(ValueError):
    """A derived write no longer has its validated source snapshot."""


class DependencyStoreMixin:
    def _init_dependency_schema(self):
        self.conn.execute("""CREATE TABLE IF NOT EXISTS memory_dependency (
            child_memory_id TEXT NOT NULL, child_version_id TEXT NOT NULL,
            parent_memory_id TEXT NOT NULL, parent_version_id TEXT NOT NULL,
            relation TEXT NOT NULL CHECK(relation = 'derived_from'),
            created_at TEXT NOT NULL,
            PRIMARY KEY(child_memory_id, parent_memory_id)
        )""")
        self.conn.execute("""CREATE INDEX IF NOT EXISTS idx_dependency_parent
            ON memory_dependency(parent_memory_id, child_memory_id)""")
        self.conn.execute("""CREATE TABLE IF NOT EXISTS procedure_feedback (
            memory_id TEXT NOT NULL, version_id TEXT NOT NULL,
            session_id TEXT NOT NULL, use_id TEXT NOT NULL,
            status TEXT NOT NULL CHECK(status IN ('pass', 'fail', 'unknown')),
            created_at TEXT NOT NULL,
            PRIMARY KEY(memory_id, version_id, session_id, use_id)
        )""")
        # Eligibility always joins the committed memory row. Invalidation is
        # atomic with the source change, even if vector/cache cleanup lags.
        trigger = self.conn.execute("SELECT sql FROM sqlite_master WHERE type='trigger' AND name='invalidate_derived_memory'").fetchone()
        if trigger and "NEW.superseded_by IS NOT OLD.superseded_by" not in trigger["sql"]:
            if not self.conn.in_transaction:
                self.conn.execute("BEGIN IMMEDIATE")
            self.conn.execute("DROP TRIGGER invalidate_derived_memory")
        self.conn.execute("""CREATE TRIGGER IF NOT EXISTS invalidate_derived_memory
            AFTER UPDATE ON memory
            WHEN NEW.version_id != OLD.version_id OR NEW.deleted != OLD.deleted
                OR NEW.governance_state != OLD.governance_state
                OR NEW.trust_level != OLD.trust_level
                OR NEW.valid_to IS NOT OLD.valid_to
                OR NEW.superseded_by IS NOT OLD.superseded_by
                OR NEW.scope != OLD.scope OR NEW.project_root != OLD.project_root
                OR NEW.preference_scope != OLD.preference_scope
                OR NEW.retention_session_id != OLD.retention_session_id
                OR NEW.preference_session_id != OLD.preference_session_id
                OR NEW.agent_name != OLD.agent_name OR NEW.task_type != OLD.task_type
            BEGIN
                UPDATE memory SET governance_state = 'source_invalidated',
                    governance_reason = 'source_changed'
                WHERE deleted = 0 AND governance_state = 'active'
                    AND memory_id IN (
                        WITH RECURSIVE descendants(id) AS (
                            SELECT child_memory_id FROM memory_dependency
                            WHERE parent_memory_id = NEW.memory_id
                            UNION
                            SELECT d.child_memory_id FROM memory_dependency d
                                JOIN descendants p ON d.parent_memory_id = p.id
                        ) SELECT id FROM descendants
                    );
            END""")

    def dependency_descendants(self, memory_ids):
        ids = list(dict.fromkeys(memory_ids))
        if not ids:
            return []
        return [dict(row) for row in self.conn.execute(
            """WITH RECURSIVE descendants(id) AS (
                SELECT child_memory_id FROM memory_dependency
                WHERE parent_memory_id IN (SELECT value FROM json_each(?))
                UNION
                SELECT d.child_memory_id FROM memory_dependency d
                    JOIN descendants p ON d.parent_memory_id = p.id
            ) SELECT m.* FROM descendants d JOIN memory m ON m.memory_id = d.id
                WHERE m.deleted = 0 ORDER BY m.memory_id""", (json.dumps(ids),),
        )]

    def memory_dependencies(self, memory_id):
        return [dict(row) for row in self.conn.execute(
            """SELECT d.*, m.version_id AS current_parent_version,
                m.deleted AS parent_deleted, m.governance_state AS parent_state
                FROM memory_dependency d LEFT JOIN memory m
                    ON m.memory_id = d.parent_memory_id
                WHERE child_memory_id = ? ORDER BY parent_memory_id""", (memory_id,),
        )]

    def _validate_dependencies_no_commit(self, record):
        deps = record.dependencies
        if not isinstance(deps, list) or len(deps) > 32:
            raise DependencyChanged("invalid dependency list")
        existing = self.memory_dependencies(record.memory_id)
        if existing and not deps and not record.deleted:
            raise DependencyChanged("derived memory requires revalidation")
        seen = set()
        descendants = {row["memory_id"] for row in self.dependency_descendants([record.memory_id])}
        for dep in deps:
            if not isinstance(dep, dict) or set(dep) != {"memory_id", "version_id", "relation"}:
                raise DependencyChanged("invalid dependency fields")
            parent_id = dep["memory_id"]
            if (
                not all(isinstance(value, str) and value for value in dep.values())
                or dep["relation"] != "derived_from" or parent_id == record.memory_id
                or parent_id in seen or parent_id in descendants
            ):
                raise DependencyChanged("invalid or cyclic derivation")
            seen.add(parent_id)
            parent = self.get_memory(parent_id)
            if (
                parent is None or parent["deleted"] or parent["valid_to"] is not None
                or parent["superseded_by"] is not None
                or parent["version_id"] != dep["version_id"]
                or parent["governance_state"] != "active"
                or parent["trust_level"] not in {"trusted", "verified", "reviewed"}
                or parent["scope"] != record.scope
                or parent["preference_scope"] not in {"global", "project"}
                or parent["preference_session_id"]
                or (parent["project_root"] and parent["project_root"] != record.project_root)
                or (parent["retention_session_id"] and (
                    parent["retention_session_id"] != record.retention_session_id
                    or record.tier == "long_term"
                ))
            ):
                raise DependencyChanged("source snapshot changed or scope is incompatible")

    def _register_dependencies_no_commit(self, record):
        self.conn.execute("DELETE FROM memory_dependency WHERE child_memory_id = ?", (record.memory_id,))
        self.conn.executemany(
            "INSERT INTO memory_dependency VALUES (?, ?, ?, ?, ?, ?)",
            [(record.memory_id, record.version.version_id, dep["memory_id"],
              dep["version_id"], dep["relation"], utc_now()) for dep in record.dependencies],
        )

    def _validate_staged_dependencies_no_commit(self, record):
        try:
            self._validate_dependencies_no_commit(record)
        except DependencyChanged:
            self.conn.execute("DELETE FROM pending_memory_write WHERE version_id = ?", (record.version.version_id,))
            self._add_audit_no_commit("derived_write_cancelled", record.memory_id,
                                      {"version_id": record.version.version_id})
            self.conn.commit()
            raise

    def _check_erasure_dependency_closure_no_commit(self, items):
        ids = {item["memory_id"] for item in items}
        descendants = {row["memory_id"] for row in self.dependency_descendants(list(ids))}
        if descendants - ids:
            raise DependencyChanged("erasure impact changed; preview derived memories again")

    def _cancel_revoked_derived_writes_no_commit(self, items):
        targets = {item["memory_id"] for item in items}
        for pending in self.conn.execute("SELECT version_id, record_json FROM pending_memory_write").fetchall():
            payload = json.loads(pending["record_json"])
            deps = payload.get("dependencies", [])
            if any(dep["memory_id"] in targets for dep in deps):
                self.conn.execute("DELETE FROM pending_memory_write WHERE version_id = ?", (pending["version_id"],))
                self._add_audit_no_commit("derived_write_revoked", None,
                                          {"version_id": pending["version_id"]})

    def _erase_dependency_metadata_no_commit(self, memory_ids):
        for memory_id in memory_ids:
            self.conn.execute("DELETE FROM procedure_feedback WHERE memory_id = ?", (memory_id,))
            self.conn.execute("DELETE FROM memory_dependency WHERE child_memory_id = ? OR parent_memory_id = ?",
                              (memory_id, memory_id))

    def record_procedure_feedback(self, memory_id, version_id, session_id, use_id, status):
        if status not in {"pass", "fail", "unknown"} or not use_id or not session_id:
            raise ValueError("feedback needs a session, use identifier and measured status")
        if any(not isinstance(value, str) or len(value) > 256 for value in (session_id, use_id)):
            raise ValueError("feedback identity is invalid")
        with self.conn:
            self.conn.execute("BEGIN IMMEDIATE")
            row = self.get_memory(memory_id)
            if (not row or row["deleted"] or row["version_id"] != version_id
                    or row["governance_state"] != "active"
                    or row["trust_level"] not in {"trusted", "verified", "reviewed"}
                    or row["valid_to"] is not None or row["superseded_by"] is not None
                    or row["preference_scope"] not in {"global", "project"}
                    or row["preference_session_id"]
                    or (row["retention_session_id"] and row["retention_session_id"] != session_id)):
                raise DependencyChanged("procedure changed during verification")
            if row["type"] != "procedure":
                raise ValueError("memory is not a procedure")
            for dep in self.memory_dependencies(memory_id):
                parent = self.get_memory(dep["parent_memory_id"])
                if (not parent or parent["deleted"] or parent["governance_state"] != "active"
                        or parent["version_id"] != dep["parent_version_id"]
                        or parent["trust_level"] not in {"trusted", "verified", "reviewed"}
                        or parent["valid_to"] is not None or parent["superseded_by"] is not None
                        or parent["scope"] != row["scope"]
                        or parent["preference_scope"] not in {"global", "project"}
                        or parent["preference_session_id"]
                        or (parent["project_root"] and parent["project_root"] != row["project_root"])
                        or (parent["retention_session_id"] and (
                            parent["retention_session_id"] != row["retention_session_id"]
                            or row["tier"] == "long_term"
                        ))):
                    raise DependencyChanged("source changed during procedure verification")
            previous = self.conn.execute("SELECT status FROM procedure_feedback WHERE memory_id=? AND version_id=? AND session_id=? AND use_id=?",
                                         (memory_id, version_id, session_id, use_id)).fetchone()
            if previous and previous["status"] != status:
                raise ValueError("use identifier already has another verification result")
            self.conn.execute("INSERT OR IGNORE INTO procedure_feedback VALUES (?, ?, ?, ?, ?, ?)",
                              (memory_id, version_id, session_id, use_id, status, utc_now()))
            return {"recorded": previous is None, "duplicate": previous is not None, "status": status}

    def procedure_stats(self, memory_id):
        counts = {"pass": 0, "fail": 0, "unknown": 0}
        for row in self.conn.execute("""SELECT f.status, COUNT(*) AS count
            FROM procedure_feedback f JOIN memory m ON m.memory_id = f.memory_id
                AND m.version_id = f.version_id
            WHERE f.memory_id = ? AND m.deleted = 0 GROUP BY f.status""", (memory_id,)):
            counts[row["status"]] = row["count"]
        measured = counts["pass"] + counts["fail"]
        return {**counts, "measured": measured,
                "success_rate": counts["pass"] / measured if measured else None}
