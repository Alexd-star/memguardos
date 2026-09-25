import json

from memguard.integrations.events import AgentInteractionEvent
from memguard.service import MemGuardService


def _service(tmp_path):
    return MemGuardService(tmp_path / "memory.segment", tmp_path / "meta.sqlite3")


def _mixed_event():
    return AgentInteractionEvent(
        agent_name="openhands", agent_version="test", session_id="two-preferences",
        turn_id="turn-1", sequence=1, event_type="user_message", role="user",
        content="以后搜索代码统一使用 grep，并且回答时先给命令再解释。",
    )


def test_each_observation_is_promoted_to_its_matching_memory(tmp_path):
    service = _service(tmp_path)
    try:
        result = service.ingest_interaction(_mixed_event())
        assert len(result["memory_ids"]) == 2
        rows = service.meta.conn.execute(
            """SELECT p.topic, p.promoted_memory_id, m.preference_topic
               FROM preference_observations p
               JOIN memory m ON m.memory_id = p.promoted_memory_id"""
        ).fetchall()
        assert {row["topic"] for row in rows} == {"code_search_tool", "response_order"}
        assert all(row["topic"] == row["preference_topic"] for row in rows)
    finally:
        service.close()


def test_existing_wrong_owner_is_repaired_only_with_unique_matching_evidence(tmp_path):
    service = _service(tmp_path)
    service.ingest_interaction(_mixed_event())
    preference_ids = {
        row["preference_topic"]: row["memory_id"]
        for row in service.list_memories()
    }
    service.meta.conn.execute(
        """UPDATE preference_observations SET promoted_memory_id = ?
           WHERE topic = 'code_search_tool'""",
        (preference_ids["response_order"],),
    )
    service.meta.conn.execute(
        "DELETE FROM schema_migrations WHERE name = 'preference_observation_owner_v1'"
    )
    service.meta.conn.commit()
    service.close()

    recovered = _service(tmp_path)
    try:
        owner = recovered.meta.conn.execute(
            "SELECT promoted_memory_id FROM preference_observations WHERE topic = 'code_search_tool'"
        ).fetchone()["promoted_memory_id"]
        assert owner == preference_ids["code_search_tool"]
        repair = recovered.audit(event_type="preference_observation_owner_repair")[0]
        assert json.loads(repair["detail"])["repaired"] == 1
        assert recovered.meta.verify_audit_chain()["ok"] is True
        forgotten = recovered.forget("忘掉 grep 的习惯", confirm=True, compact=True)
        assert forgotten["deleted_preference_observation_count"] == 1
        assert recovered.meta.conn.execute(
            "SELECT COUNT(*) FROM preference_observations WHERE topic = 'response_order'"
        ).fetchone()[0] == 1
    finally:
        recovered.close()


def test_unmatched_old_observation_is_left_for_manual_review(tmp_path):
    service = _service(tmp_path)
    service.ingest_interaction(_mixed_event())
    service.meta.conn.execute(
        """UPDATE preference_observations SET topic = 'unknown_legacy_topic'
           WHERE topic = 'code_search_tool'"""
    )
    service.meta.conn.execute(
        "DELETE FROM schema_migrations WHERE name = 'preference_observation_owner_v1'"
    )
    service.meta.conn.commit()
    service.close()

    recovered = _service(tmp_path)
    try:
        row = recovered.meta.conn.execute(
            """SELECT p.promoted_memory_id, m.preference_topic
               FROM preference_observations p
               JOIN memory m ON m.memory_id = p.promoted_memory_id
               WHERE p.topic = 'unknown_legacy_topic'"""
        ).fetchone()
        assert row["promoted_memory_id"] is not None
        assert row["preference_topic"] == "code_search_tool"
        repair = recovered.audit(event_type="preference_observation_owner_repair")[0]
        assert json.loads(repair["detail"]) == {"repaired": 0, "unresolved": 1}
        assert recovered.meta.verify_audit_chain()["ok"] is True
    finally:
        recovered.close()


def test_contradiction_link_is_not_used_as_repair_evidence(tmp_path):
    service = _service(tmp_path)
    service.ingest_interaction(_mixed_event())
    preference_ids = {
        row["preference_topic"]: row["memory_id"]
        for row in service.list_memories()
    }
    observation = service.meta.conn.execute(
        "SELECT event_id FROM preference_observations WHERE topic = 'code_search_tool'"
    ).fetchone()
    service.meta.conn.execute(
        """UPDATE preference_observations SET promoted_memory_id = ?
           WHERE topic = 'code_search_tool'""",
        (preference_ids["response_order"],),
    )
    service.meta.conn.execute(
        "DELETE FROM memory_event_links WHERE event_id = ? AND memory_id = ?",
        (observation["event_id"], preference_ids["code_search_tool"]),
    )
    service.meta.conn.execute(
        """INSERT INTO memory_event_links(memory_id, event_id, relation, created_at)
           VALUES (?, ?, 'contradicted_by', datetime('now'))""",
        (preference_ids["code_search_tool"], observation["event_id"]),
    )
    service.meta.conn.execute(
        "DELETE FROM schema_migrations WHERE name = 'preference_observation_owner_v1'"
    )
    service.meta.conn.commit()
    service.close()

    recovered = _service(tmp_path)
    try:
        owner = recovered.meta.conn.execute(
            "SELECT promoted_memory_id FROM preference_observations WHERE topic = 'code_search_tool'"
        ).fetchone()["promoted_memory_id"]
        assert owner == preference_ids["response_order"]
        repair = recovered.audit(event_type="preference_observation_owner_repair")[0]
        assert json.loads(repair["detail"]) == {"repaired": 0, "unresolved": 1}
    finally:
        recovered.close()
