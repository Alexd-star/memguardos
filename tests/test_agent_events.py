import json

import pytest

from memguard.integrations.events import AgentInteractionEvent
from memguard.integrations.normalizer import normalize_event, normalize_events, normalize_tool_args
from memguard.api.server import handle_request
from memguard.service import MemGuardService


def make_event(**overrides):
    payload = {
        "agent_name": "openhands",
        "agent_version": "1.28.1",
        "session_id": "session-a",
        "turn_id": "turn-1",
        "sequence": 1,
        "event_type": "user_message",
        "role": "user",
        "content": "以后搜索代码优先使用 rg。",
        "source_uri": "openhands://session-a/event-1",
    }
    payload.update(overrides)
    return AgentInteractionEvent(**payload)


def test_event_round_trip_preserves_fields_and_hash():
    event = make_event(tool_args={"path": "memguard", "query": "build_context"})

    restored = AgentInteractionEvent.from_json(event.to_json())

    assert restored == event
    assert restored.provenance_hash == event.provenance_hash
    assert json.loads(event.to_json())["schema_version"] == "1.0"


@pytest.mark.parametrize(
    ("field", "value", "message"),
    [
        ("session_id", "", "session_id is required"),
        ("turn_id", "", "turn_id is required"),
        ("role", "developer", "unsupported role"),
        ("event_type", "memory_guess", "unsupported event_type"),
    ],
)
def test_event_rejects_invalid_required_fields(field, value, message):
    with pytest.raises(ValueError, match=message):
        make_event(**{field: value})


def test_event_hash_is_stable_for_equivalent_normalized_payloads():
    first = make_event(
        content=" 以后搜索代码优先使用   rg。\r\n",
        tool_args={"query": " build_context ", "path": "memguard"},
    )
    second = make_event(
        content="以后搜索代码优先使用 rg。",
        tool_args={"path": "memguard", "query": "build_context"},
    )

    assert first.provenance_hash == second.provenance_hash


def test_event_hash_changes_when_turn_or_tool_result_changes():
    base = make_event(event_type="tool_result", role="tool", tool_name="shell", tool_result="ok")
    later_turn = make_event(
        event_type="tool_result",
        role="tool",
        tool_name="shell",
        tool_result="ok",
        turn_id="turn-2",
    )
    changed_result = make_event(
        event_type="tool_result",
        role="tool",
        tool_name="shell",
        tool_result="failed",
    )

    assert base.provenance_hash != later_turn.provenance_hash
    assert base.provenance_hash != changed_result.provenance_hash


def test_event_from_dict_rejects_supplied_hash_that_does_not_match_payload():
    payload = make_event().to_dict()
    payload["provenance_hash"] = "0" * 64

    with pytest.raises(ValueError, match="provenance_hash does not match"):
        AgentInteractionEvent.from_dict(payload)


def test_tool_arguments_are_recursively_redacted_before_persistence():
    normalized = normalize_tool_args(
        {
            "path": "/tmp/demo",
            "password": "plain-password",
            "env": {
                "API_TOKEN": "token-value",
                "MEMGUARD_MODE": "demo",
            },
            "headers": {
                "Authorization": "Bearer secret-token",
                "Accept": "application/json",
            },
        }
    )

    assert normalized["path"] == "/tmp/demo"
    assert normalized["password"] == "[REDACTED]"
    assert normalized["env"]["API_TOKEN"] == "[REDACTED]"
    assert normalized["env"]["MEMGUARD_MODE"] == "demo"
    assert normalized["headers"]["Authorization"] == "[REDACTED]"
    assert normalized["headers"]["Accept"] == "application/json"


def test_normalize_event_recomputes_hash_after_redaction():
    event = make_event(
        event_type="tool_call",
        role="assistant",
        tool_name="shell",
        tool_args={"command": "curl", "api_key": "secret-value"},
    )

    normalized = normalize_event(event)

    assert normalized.tool_args["api_key"] == "[REDACTED]"
    assert normalized.provenance_hash != event.provenance_hash


def test_normalize_events_orders_one_session_by_sequence():
    events = [
        make_event(sequence=3, event_type="assistant_message", role="assistant"),
        make_event(sequence=1),
        make_event(sequence=2, event_type="tool_call", role="assistant"),
    ]

    normalized = normalize_events(events)

    assert [event.sequence for event in normalized] == [1, 2, 3]


def test_service_ingest_interaction_is_idempotent_and_audited(tmp_path):
    service = MemGuardService(tmp_path / "memory.segment", tmp_path / "meta.sqlite3")
    try:
        event = make_event()

        first = service.ingest_interaction(event)
        second = service.ingest_interaction(AgentInteractionEvent.from_json(event.to_json()))

        assert first["stored"] is True
        assert first["duplicate"] is False
        assert second["stored"] is False
        assert second["duplicate"] is True
        assert len(service.meta.list_agent_events()) == 1
        assert len(service.audit(event_type="interaction_ingest")) == 1
        assert len(service.audit(event_type="interaction_duplicate")) == 1
    finally:
        service.close()


def test_existing_database_is_migrated_without_losing_memories(tmp_path):
    db_path = tmp_path / "meta.sqlite3"
    service = MemGuardService(tmp_path / "memory.segment", db_path)
    try:
        memory = service.ingest_text("openKylin 使用 systemd 管理 MemGuardOS 服务。")["record"]
    finally:
        service.close()

    reopened = MemGuardService(tmp_path / "memory.segment", db_path)
    try:
        table = reopened.meta.conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name='agent_events'"
        ).fetchone()
        assert table is not None
        assert reopened.meta.get_memory(memory.memory_id)["deleted"] == 0
    finally:
        reopened.close()


def test_memory_can_trace_back_to_multiple_agent_events(tmp_path):
    service = MemGuardService(tmp_path / "memory.segment", tmp_path / "meta.sqlite3")
    try:
        first_event = make_event(event_id="evt-first", sequence=1)
        second_event = make_event(
            event_id="evt-second",
            session_id="session-b",
            turn_id="turn-2",
            sequence=1,
            event_type="user_feedback",
            user_feedback="确认以后都优先使用 rg。",
        )
        service.ingest_interaction(first_event)
        service.ingest_interaction(second_event)
        memory = service.ingest_text("以后搜索代码优先使用 rg。")["record"]

        assert service.link_memory_event(memory.memory_id, first_event.event_id, "extracted_from")["linked"] is True
        assert service.link_memory_event(memory.memory_id, second_event.event_id, "confirmed_by")["linked"] is True

        evidence = service.preference_evidence(memory.memory_id)

        assert evidence["memory_id"] == memory.memory_id
        assert evidence["deleted"] is False
        assert [item["event_id"] for item in evidence["events"]] == ["evt-first", "evt-second"]
        assert evidence["events"][0]["content"] == "以后搜索代码优先使用 rg。"
        assert evidence["events"][1]["relation"] == "confirmed_by"
        assert evidence["events"][1]["session_id"] == "session-b"
    finally:
        service.close()


def test_deleted_memory_evidence_does_not_return_original_event_text(tmp_path):
    service = MemGuardService(tmp_path / "memory.segment", tmp_path / "meta.sqlite3")
    try:
        event = make_event(event_id="evt-forgotten")
        service.ingest_interaction(event)
        memory = service.ingest_text("以后搜索代码优先使用 rg。")["record"]
        service.link_memory_event(memory.memory_id, event.event_id, "extracted_from")
        service.meta.mark_deleted(memory.memory_id)

        evidence = service.preference_evidence(memory.memory_id)

        assert evidence["deleted"] is True
        assert evidence["events"][0]["content"] == ""
        assert evidence["events"][0]["tool_result"] == ""
        assert evidence["events"][0]["user_feedback"] == ""
        assert evidence["events"][0]["provenance_hash"] == event.provenance_hash
    finally:
        service.close()


def test_preference_evidence_is_available_through_local_api(tmp_path):
    service = MemGuardService(tmp_path / "memory.segment", tmp_path / "meta.sqlite3")
    try:
        event = make_event(event_id="evt-api")
        service.ingest_interaction(event)
        memory = service.ingest_text("以后搜索代码优先使用 rg。")["record"]
        service.link_memory_event(memory.memory_id, event.event_id, "extracted_from")

        response = handle_request(
            service,
            {"command": "preference_evidence", "memory_id": memory.memory_id},
        )

        assert response["ok"] is True
        assert response["result"]["events"][0]["event_id"] == "evt-api"
    finally:
        service.close()
