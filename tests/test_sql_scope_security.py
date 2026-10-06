import pytest

from memguard.extraction.fact_slot import parse_fact_slot
from memguard.forgetting.plan import ErasurePlan
from memguard.integrations.events import AgentInteractionEvent
from memguard.models import MemorySource, make_memory
from memguard.store.sqlite_meta import SQLiteMetaStore, fact_slot_scope


PAYLOADS = ("' OR 1=1 --", "'); DROP TABLE memory; --")
FACT_CONTENT = "Atlas\u53d1\u5e03\u65e5\u662fMonday"


@pytest.fixture
def meta(tmp_path):
    store = SQLiteMetaStore(tmp_path / "meta.sqlite3")
    try:
        yield store
    finally:
        store.close()


def _record(meta, content="stored knowledge", **fields):
    fields.setdefault("scope", "scope")
    record = make_memory(content, "knowledge", MemorySource(kind="manual"), **fields)
    meta.upsert_memory(record)
    return record


def _link_event(meta, record, **fields):
    fields.setdefault("agent_name", "trusted-agent")
    fields.setdefault("session_id", "trusted-session")
    event = AgentInteractionEvent(
        agent_version="1", turn_id="turn", sequence=1,
        event_type="user_message", role="user", content=record.content,
        **fields,
    )
    result = meta.insert_agent_event(event)
    assert meta.link_memory_event(record.memory_id, result["event_id"], "source_clause")["linked"]
    return result["event_id"]


def _conflict(meta, record, incoming_value="Tuesday"):
    fact = parse_fact_slot(record.content)
    assert fact is not None
    return meta.create_fact_conflict(
        scope=fact_slot_scope(record.scope, record.retention_session_id),
        tier=record.tier, project_root=record.project_root,
        subject=fact.subject, predicate=fact.predicate,
        incoming_value=incoming_value,
        incoming_content="Atlas\u53d1\u5e03\u65e5\u662f" + incoming_value,
        source_kind="manual", source_uri="",
        candidate_memory_ids=[record.memory_id],
    )


@pytest.mark.parametrize("payload", PAYLOADS)
@pytest.mark.parametrize(
    "level,field,record_field",
    [
        ("agent", "agent_name", "agent_name"),
        ("session", "session_id", "preference_session_id"),
        ("session", "session_id", "retention_session_id"),
        ("project", "project_root", "project_root"),
    ],
)
def test_forget_scope_values_are_literal(meta, payload, level, field, record_field):
    control = _record(
        meta, agent_name="trusted-agent", preference_session_id="trusted-session",
        retention_session_id="trusted-session", project_root="/safe",
    )
    _link_event(meta, control, project_root="/safe")
    before = meta.get_memory(control.memory_id)
    plan = ErasurePlan.from_intent({"scope": {"level": level, field: payload}})
    assert meta.select_forget_candidates(plan) == []

    direct = _record(meta, "direct scoped knowledge", **{record_field: payload})
    linked = _record(meta, "linked scoped knowledge")
    _link_event(meta, linked, **{field: payload})
    expected = {direct.memory_id}
    if level != "project":
        expected.add(linked.memory_id)
    assert {row["memory_id"] for row in meta.select_forget_candidates(plan)} == expected
    assert meta.get_memory(control.memory_id) == before


def test_forget_agent_and_session_must_match_the_same_live_event(meta):
    agent = "agent' OR 1=1 --"
    session = "session' OR 1=1 --"
    split = _record(meta, "split event knowledge")
    _link_event(meta, split, agent_name=agent, session_id="other-session")
    _link_event(meta, split, agent_name="other-agent", session_id=session)
    linked = _record(meta, "matching event knowledge")
    _link_event(meta, linked, agent_name=agent, session_id=session)
    forgotten = _record(meta, "forgotten event knowledge")
    event_id = _link_event(meta, forgotten, agent_name=agent, session_id=session)
    assert meta.forget_agent_event_payloads([event_id]) == 1
    direct = _record(meta, "matching record knowledge", agent_name=agent, retention_session_id=session)
    _record(meta, "wrong record session", agent_name=agent, retention_session_id="other-session")
    plan = ErasurePlan.from_intent({
        "scope": {"level": "session", "agent_name": agent, "session_id": session},
    })
    assert {row["memory_id"] for row in meta.select_forget_candidates(plan)} == {
        linked.memory_id, direct.memory_id,
    }


@pytest.mark.parametrize("payload", PAYLOADS)
@pytest.mark.parametrize("field", ["content", "scope", "tier", "retention_session_id", "project_root"])
def test_content_lookup_values_cannot_widen_scope(meta, payload, field):
    query = {
        "content": "stored knowledge", "scope": "scope", "tier": "session",
        "retention_session_id": "trusted-session", "project_root": "/safe",
    }
    control = _record(meta, **query)
    before = meta.get_memory(control.memory_id)
    query[field] = payload
    assert meta.find_active_memory_by_content(**query) is None

    literal = _record(meta, **query)
    found = meta.find_active_memory_by_content(**query)
    assert found["memory_id"] == literal.memory_id
    assert found[field] == payload
    assert meta.get_memory(control.memory_id) == before


@pytest.mark.parametrize("tier", [None, ""])
def test_content_lookup_without_tier_keeps_project_but_ignores_session(meta, tier):
    target = _record(meta, tier="session", retention_session_id="trusted-session", project_root="/safe")
    _record(meta, tier="session", project_root="/other")
    _record(meta, tier="session", project_root="/safe", scope="other-scope")
    _record(meta, tier="session", project_root="/safe", governance_state="quarantined")
    found = meta.find_active_memory_by_content(
        target.content, target.scope, tier=tier,
        retention_session_id="unmatched-session", project_root="/safe",
    )
    assert found["memory_id"] == target.memory_id


@pytest.mark.parametrize("payload", PAYLOADS)
@pytest.mark.parametrize("field", ["scope", "subject", "predicate", "tier", "project_root"])
def test_fact_slot_lookup_values_cannot_widen_scope(meta, payload, field):
    control = _record(meta, FACT_CONTENT, project_root="/safe")
    before = meta.get_memory(control.memory_id)
    query = {
        "scope": "scope", "subject": "atlas", "predicate": "release_date",
        "tier": "long_term", "project_root": "/safe",
    }
    assert [row["memory_id"] for row in meta.find_active_fact_candidates(**query)] == [control.memory_id]
    query[field] = payload
    assert meta.find_active_fact_candidates(**query) == []
    if field in {"scope", "tier", "project_root"}:
        literal = _record(
            meta, FACT_CONTENT, scope=query["scope"], tier=query["tier"], project_root=query["project_root"],
        )
        assert [row["memory_id"] for row in meta.find_active_fact_candidates(**query)] == [literal.memory_id]
    assert meta.get_memory(control.memory_id) == before


@pytest.mark.parametrize("tier", [None, ""])
def test_fact_slot_lookup_without_tier_stays_in_exact_project(meta, tier):
    target = _record(meta, FACT_CONTENT, project_root="/safe")
    _record(meta, FACT_CONTENT)
    _record(meta, FACT_CONTENT, project_root="/other")
    _record(meta, FACT_CONTENT, project_root="/safe", governance_state="quarantined")
    found = meta.find_active_fact_candidates("scope", "atlas", "release_date", tier=tier, project_root="/safe")
    assert [row["memory_id"] for row in found] == [target.memory_id]


@pytest.mark.parametrize("payload", PAYLOADS)
@pytest.mark.parametrize(
    "field", ["scope", "subject", "predicate", "project_root", "tier", "resolved_memory_id", "incoming_value"],
)
def test_fact_conflict_resolution_values_cannot_widen_update(meta, payload, field):
    target = _record(meta, FACT_CONTENT, project_root="/safe")
    conflict = _conflict(meta, target)
    other = _record(meta, FACT_CONTENT, project_root="/other")
    other_conflict = _conflict(meta, other)
    before = meta.get_fact_conflict(conflict["conflict_id"])
    other_before = meta.get_fact_conflict(other_conflict["conflict_id"])
    query = {
        "scope": "scope", "subject": "atlas", "predicate": "release_date", "project_root": "/safe",
        "tier": "long_term", "resolved_memory_id": target.memory_id, "incoming_value": "Tuesday",
        "resolution": "explicit_update",
    }
    attack = {**query, field: payload}
    assert meta.resolve_fact_conflicts(**attack) == 0
    assert meta.get_fact_conflict(conflict["conflict_id"]) == before
    assert meta.get_fact_conflict(other_conflict["conflict_id"]) == other_before

    assert meta.resolve_fact_conflicts(**{**query, "resolution": payload}) == 1
    resolved = meta.get_fact_conflict(conflict["conflict_id"])
    assert resolved["status"] == "resolved" and resolved["resolution"] == payload
    assert meta.resolve_fact_conflicts(**query) == 0
    assert meta.get_fact_conflict(other_conflict["conflict_id"]) == other_before


@pytest.mark.parametrize("filter_tier", [False, True])
@pytest.mark.parametrize("filter_candidate", [False, True])
@pytest.mark.parametrize("filter_value", [False, True])
def test_fact_conflict_optional_filters_preserve_exact_project(meta, filter_tier, filter_candidate, filter_value):
    target = _record(meta, FACT_CONTENT, project_root="/safe")
    first = _conflict(meta, target)
    second = _conflict(meta, _record(meta, FACT_CONTENT, project_root="/safe", tier="working"), "Wednesday")
    global_case = _conflict(meta, _record(meta, FACT_CONTENT))
    other_case = _conflict(meta, _record(meta, FACT_CONTENT, project_root="/other"))
    narrowed = filter_tier or filter_candidate or filter_value
    count = meta.resolve_fact_conflicts(
        scope="scope", subject="atlas", predicate="release_date", project_root="/safe",
        resolution="explicit_update", tier="long_term" if filter_tier else "",
        resolved_memory_id=target.memory_id if filter_candidate else "",
        incoming_value="Tuesday" if filter_value else "",
    )
    assert count == (1 if narrowed else 2)
    assert meta.get_fact_conflict(first["conflict_id"])["status"] == "resolved"
    assert meta.get_fact_conflict(second["conflict_id"])["status"] == ("pending" if narrowed else "resolved")
    assert meta.get_fact_conflict(global_case["conflict_id"])["status"] == "pending"
    assert meta.get_fact_conflict(other_case["conflict_id"])["status"] == "pending"


@pytest.mark.parametrize("payload", PAYLOADS)
@pytest.mark.parametrize("field", ["memory_id", "expected_version_id"])
def test_guarded_erasure_rejects_injection_and_rolls_back(meta, payload, field):
    target = _record(meta, FACT_CONTENT, project_root="/safe")
    other = _record(meta, "unrelated knowledge")
    before = meta.get_memory(target.memory_id)
    other_before = meta.get_memory(other.memory_id)
    arguments = {
        "memory_id": target.memory_id, "tombstone_version_id": "tombstone",
        "expected_version_id": target.version.version_id, "compact": False, "plaintext": target.content,
    }
    with pytest.raises(ValueError, match="stale memory version"):
        meta.begin_memory_erasure(**{**arguments, field: payload})
    assert meta.get_memory(target.memory_id) == before
    assert meta.get_memory(other.memory_id) == other_before
    assert meta.pending_erasures() == []
    assert [row["memory_id"] for row in meta.find_active_fact_candidates(
        "scope", "atlas", "release_date", project_root="/safe",
    )] == [target.memory_id]

    meta.begin_memory_erasure(**arguments)
    assert meta.get_memory(target.memory_id)["deleted"] == 1
    assert meta.get_memory(target.memory_id)["content"] == ""
    assert meta.get_memory(other.memory_id) == other_before
    assert meta.pending_erasures()[0]["memory_id"] == target.memory_id


@pytest.mark.parametrize("payload", PAYLOADS)
def test_unguarded_deletion_binds_ids_and_superseding_text(meta, payload):
    target = _record(meta)
    other = _record(meta, "unrelated knowledge")
    before = meta.get_memory(target.memory_id)
    other_before = meta.get_memory(other.memory_id)
    meta.mark_deleted(payload)
    assert meta.get_memory(target.memory_id) == before
    assert meta.get_memory(other.memory_id) == other_before
    meta.mark_deleted(target.memory_id, superseded_by=payload)
    assert meta.get_memory(target.memory_id)["deleted"] == 1
    assert meta.get_memory(target.memory_id)["superseded_by"] == payload
    meta.mark_deleted(target.memory_id, superseded_by="replacement")
    assert meta.get_memory(target.memory_id)["superseded_by"] == "replacement"
    assert meta.get_memory(other.memory_id) == other_before
