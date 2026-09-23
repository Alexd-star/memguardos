import sqlite3
from dataclasses import replace

import pytest

from memguard.integrations.evidence_spans import preference_source_span
from memguard.integrations.events import AgentInteractionEvent
from memguard.integrations.interaction_extractor import extract_interaction_preferences
from memguard.service import MemGuardService


def _service(tmp_path):
    return MemGuardService(tmp_path / "memory.segment", tmp_path / "meta.sqlite3")


def _event(text):
    return AgentInteractionEvent(
        agent_name="test-agent", agent_version="1", session_id="source-spans",
        turn_id="turn-1", sequence=1, event_type="user_message", role="user",
        content=text,
    )


def test_preference_spans_cover_only_their_own_clauses():
    event = _event(
        "以后搜索项目代码优先使用 grep。回答时先给命令，再解释原因。"
    )
    observations = extract_interaction_preferences(event).observations
    spans = {
        item.topic: preference_source_span(event, item)
        for item in observations
    }
    assert spans["code_search_tool"].excerpt == "以后搜索项目代码优先使用 grep"
    assert spans["response_order"].excerpt == "回答时先给命令，再解释原因"
    for span in spans.values():
        assert event.content[span.start:span.end] == span.excerpt


def test_ambiguous_source_is_not_saved_as_independent_excerpt():
    event = _event("请先用 grep 再用 rg 搜索项目代码。")
    observations = extract_interaction_preferences(event).observations
    assert all(preference_source_span(event, item) is None for item in observations)


def test_polarity_mismatch_and_oversized_event_have_no_excerpt():
    event = _event("以后搜索项目代码优先使用 rg。")
    observation = extract_interaction_preferences(event).observations[0]
    assert preference_source_span(event, replace(observation, polarity="negative")) is None
    oversized = _event("无关背景。" * 1000 + event.content)
    assert preference_source_span(oversized, observation) is None


def test_forgetting_one_preference_preserves_other_independent_source(tmp_path):
    service = _service(tmp_path)
    try:
        service.ingest_natural_memory(
            "以后搜索项目代码优先使用 grep。回答时先给命令，再解释原因。",
            session_id="source-spans",
        )
        memories = service.list_memories()
        target = next(
            row["memory_id"] for row in memories
            if row["preference_topic"] == "code_search_tool"
        )
        retained = next(
            row["memory_id"] for row in memories
            if row["preference_topic"] == "response_order"
        )
        before = service.meta.memory_event_evidence(retained)[0]
        assert before["source_excerpt"] == "回答时先给命令，再解释原因"

        result = service.forget_memory_ids([target], reason="user forget", compact=True)
        assert result["ok"] is True
        assert service.meta.memory_event_evidence(target)[0]["source_excerpt"] == ""
        after = service.meta.memory_event_evidence(retained)[0]
        assert after["content"] == ""
        assert after["forgotten"] == 1
        assert after["source_excerpt"] == before["source_excerpt"]
        assert "grep" not in str(service.meta.list_agent_events())
        assert service.meta.verify_audit_chain()["ok"] is True
    finally:
        service.close()


def test_forgetting_preference_keeps_independent_fact_source(tmp_path):
    service = _service(tmp_path)
    try:
        result = service.ingest_natural_memory(
            "以后搜索项目代码优先使用 rg，白鹭项目发布日是周四。",
            session_id="mixed-source",
        )
        rows = [service.meta.get_memory(memory_id) for memory_id in result["memory_ids"]]
        preference = next(row for row in rows if row["preference_topic"] == "code_search_tool")
        fact = next(row for row in rows if row["type"] == "knowledge")
        source = service.meta.memory_event_evidence(fact["memory_id"])[0]
        assert source["source_excerpt"] == "白鹭项目发布日是周四"

        forgotten = service.forget_memory_ids(
            [preference["memory_id"]], reason="user forget", compact=True,
        )
        assert forgotten["ok"] is True
        remaining = service.meta.memory_event_evidence(fact["memory_id"])[0]
        assert remaining["source_excerpt"] == "白鹭项目发布日是周四"
        assert remaining["content"] == ""
        assert service.meta.get_memory(fact["memory_id"])["deleted"] == 0
    finally:
        service.close()


def test_link_rejects_excerpt_that_does_not_match_event(tmp_path):
    service = _service(tmp_path)
    try:
        memory = service.ingest_text("The Lark project meets on Thursday.")["record"]
        event = _event("The Lark project meets on Thursday.")
        service.meta.insert_agent_event(event)
        with pytest.raises(ValueError, match="does not match"):
            service.meta.link_memory_event(
                memory.memory_id, event.event_id, "extracted_from",
                source={
                    "source_field": "content", "source_start": 0,
                    "source_end": 5, "source_excerpt": "other",
                },
            )
        assert service.meta.memory_event_evidence(memory.memory_id) == []
    finally:
        service.close()


@pytest.mark.parametrize("target_has_span", [True, False])
def test_overlapping_or_unknown_source_is_not_retained(tmp_path, target_has_span):
    service = _service(tmp_path)
    try:
        target = service.ingest_text("The Lark project meets on Thursday.")["record"]
        retained = service.ingest_text("The Lark project uses a shared calendar.")["record"]
        event = _event("The Lark project meets on Thursday and uses a shared calendar.")
        service.meta.insert_agent_event(event)
        text = event.content
        target_span = {
            "source_field": "content", "source_start": 0,
            "source_end": 36, "source_excerpt": text[:36],
        } if target_has_span else None
        retained_span = {
            "source_field": "content", "source_start": 28,
            "source_end": len(text), "source_excerpt": text[28:],
        }
        service.meta.link_memory_event(
            target.memory_id, event.event_id, "extracted_from", source=target_span,
        )
        service.meta.link_memory_event(
            retained.memory_id, event.event_id, "extracted_from", source=retained_span,
        )
        service.forget_memory_ids([target.memory_id], reason="user forget", compact=True)
        assert service.meta.memory_event_evidence(target.memory_id)[0]["source_excerpt"] == ""
        assert service.meta.memory_event_evidence(retained.memory_id)[0]["source_excerpt"] == ""
        assert service.meta.get_memory(retained.memory_id)["deleted"] == 0
    finally:
        service.close()


def test_existing_evidence_tables_migrate_without_dropping_rows(tmp_path):
    db_path = tmp_path / "meta.sqlite3"
    with sqlite3.connect(db_path) as conn:
        conn.execute(
            """CREATE TABLE memory_event_links (
                memory_id TEXT NOT NULL, event_id TEXT NOT NULL,
                relation TEXT NOT NULL, created_at TEXT NOT NULL,
                PRIMARY KEY (memory_id, event_id, relation)
            )"""
        )
        conn.execute(
            "INSERT INTO memory_event_links VALUES ('mem-old', 'evt-old', 'extracted_from', 'now')"
        )
        conn.execute(
            """CREATE TABLE preference_observations (
                observation_id INTEGER PRIMARY KEY AUTOINCREMENT,
                event_id TEXT NOT NULL, agent_name TEXT NOT NULL,
                session_id TEXT NOT NULL, topic TEXT NOT NULL,
                value TEXT NOT NULL, polarity TEXT NOT NULL,
                preference_scope TEXT NOT NULL, explicit INTEGER NOT NULL,
                confidence REAL NOT NULL, project_root TEXT NOT NULL DEFAULT '',
                task_type TEXT NOT NULL DEFAULT '',
                scope_agent_name TEXT NOT NULL DEFAULT '',
                scope_session_id TEXT NOT NULL DEFAULT '',
                promoted_memory_id TEXT, created_at TEXT NOT NULL,
                UNIQUE(event_id, topic, value, polarity, preference_scope)
            )"""
        )

    service = _service(tmp_path)
    try:
        for table in ("memory_event_links", "preference_observations"):
            columns = {
                row["name"] for row in service.meta.conn.execute(
                    "PRAGMA table_info(%s)" % table
                )
            }
            assert {"source_field", "source_start", "source_end", "source_excerpt"} <= columns
        old = service.meta.conn.execute(
            "SELECT * FROM memory_event_links WHERE memory_id = 'mem-old'"
        ).fetchone()
        assert old["event_id"] == "evt-old"
        assert old["source_excerpt"] == ""
    finally:
        service.close()


def test_mixed_fact_and_source_link_replay_together(tmp_path, monkeypatch):
    service = _service(tmp_path)
    original = service.meta._insert_memory_event_link_no_commit

    def fail_source_link(memory_id, event_id, relation, source=None):
        if relation == "source_clause":
            raise OSError("simulated source-link failure")
        return original(memory_id, event_id, relation, source)

    monkeypatch.setattr(service.meta, "_insert_memory_event_link_no_commit", fail_source_link)
    with pytest.raises(OSError, match="source-link failure"):
        service.ingest_natural_memory(
            "以后搜索项目代码优先使用 rg，白鹭项目发布日是周四。",
            session_id="source-replay",
        )
    pending = service.meta.pending_memory_writes()
    assert len(pending) == 1
    fact_id = pending[0]["memory_id"]
    assert service.meta.get_memory(fact_id) is None
    assert service.store.get_latest(fact_id) is not None
    service.close()

    recovered = _service(tmp_path)
    try:
        assert recovered.meta.pending_memory_writes() == []
        assert recovered.meta.get_memory(fact_id)["deleted"] == 0
        links = recovered.meta.memory_event_evidence(fact_id)
        assert len(links) == 1
        assert links[0]["relation"] == "source_clause"
        assert links[0]["source_excerpt"] == "白鹭项目发布日是周四"
        assert len(recovered.store.get_versions(fact_id)) == 1
        assert recovered.meta.verify_audit_chain()["ok"] is True
    finally:
        recovered.close()


def test_mixed_fact_span_uses_redacted_event_offsets(tmp_path):
    service = _service(tmp_path)
    try:
        result = service.ingest_natural_memory(
            "以后搜索项目代码优先使用 rg，联系邮箱是 hello@example.com，白鹭项目发布日是周四。",
            session_id="redacted-source",
        )
        fact = next(
            service.meta.get_memory(memory_id)
            for memory_id in result["memory_ids"]
            if service.meta.get_memory(memory_id)["type"] == "knowledge"
            and "白鹭项目" in service.meta.get_memory(memory_id)["content"]
        )
        source = service.meta.memory_event_evidence(fact["memory_id"])[0]
        assert "hello@example.com" not in source["content"]
        assert source["source_excerpt"] == "白鹭项目发布日是周四"
        assert source["content"][source["source_start"]:source["source_end"]] == source["source_excerpt"]
    finally:
        service.close()


def test_mixed_duplicate_fact_adds_new_source_evidence(tmp_path):
    service = _service(tmp_path)
    try:
        existing = service.ingest_text("白鹭项目发布日是周四")["record"]
        result = service.ingest_natural_memory(
            "以后搜索项目代码优先使用 rg，白鹭项目发布日是周四。",
            session_id="duplicate-source",
        )
        assert result["knowledge_results"][0]["duplicate"] is True
        source = service.meta.memory_event_evidence(existing.memory_id)
        assert len(source) == 1
        assert source[0]["source_excerpt"] == "白鹭项目发布日是周四"
    finally:
        service.close()


def test_erasure_waits_for_pending_fact_link_to_shared_event(tmp_path, monkeypatch):
    service = _service(tmp_path)
    original = service.meta._insert_memory_event_link_no_commit

    def fail_fact_link(memory_id, event_id, relation, source=None):
        if relation == "source_clause":
            raise OSError("simulated interrupted fact link")
        return original(memory_id, event_id, relation, source)

    monkeypatch.setattr(service.meta, "_insert_memory_event_link_no_commit", fail_fact_link)
    try:
        with pytest.raises(OSError, match="interrupted fact link"):
            service.ingest_natural_memory(
                "以后搜索项目代码优先使用 rg，白鹭项目发布日是周四。",
                session_id="pending-shared-source",
            )
        preference = next(
            row for row in service.list_memories()
            if row["preference_topic"] == "code_search_tool"
        )
        with pytest.raises(ValueError, match="pending source write"):
            service.meta.begin_memory_erasure(
                preference["memory_id"], "ver-pending-shared-source",
                compact=True, plaintext=preference["content"],
                expected_version_id=preference["version_id"],
            )
        assert service.meta.get_memory(preference["memory_id"])["deleted"] == 0
        assert service.meta.memory_event_evidence(preference["memory_id"])[0]["forgotten"] == 0

        monkeypatch.setattr(service.meta, "_insert_memory_event_link_no_commit", original)
        assert service._recover_pending_memory_writes() == 1
        fact_id = next(
            row["memory_id"] for row in service.list_memories()
            if row["type"] == "knowledge"
        )
        assert service.forget_memory_ids(
            [preference["memory_id"]], reason="user forget", compact=True,
        )["ok"] is True
        assert service.meta.memory_event_evidence(fact_id)[0]["source_excerpt"] == "白鹭项目发布日是周四"
    finally:
        service.close()
