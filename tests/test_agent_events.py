import json

import pytest

from memguard.integrations.events import AgentInteractionEvent


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
