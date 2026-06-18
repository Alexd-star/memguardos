from memguard.integrations.events import AgentInteractionEvent
from memguard.integrations.interaction_extractor import extract_interaction_preferences
from memguard.service import MemGuardService


def event(content: str, **overrides) -> AgentInteractionEvent:
    payload = {
        "agent_name": "openhands",
        "agent_version": "1.28.1",
        "session_id": "session-a",
        "turn_id": "turn-1",
        "sequence": 1,
        "event_type": "user_message",
        "role": "user",
        "content": content,
        "source_uri": "openhands://session-a/event-1",
    }
    payload.update(overrides)
    return AgentInteractionEvent(**payload)


def test_user_message_is_split_into_independent_explicit_preferences():
    result = extract_interaction_preferences(
        event("以后搜索项目代码优先使用 rg。回答时先给命令，再解释原因。")
    )

    assert result.remember_allowed is True
    assert [(item.topic, item.value) for item in result.observations] == [
        ("code_search_tool", "rg"),
        ("response_order", "command_then_explanation"),
    ]
    assert all(item.explicit for item in result.observations)
    assert all(item.scope == "global" for item in result.observations)


def test_assistant_and_tool_events_cannot_create_user_preferences():
    assistant = event(
        "以后我会优先使用 rg。",
        event_type="assistant_message",
        role="assistant",
    )
    tool = event(
        "",
        event_type="tool_result",
        role="tool",
        tool_result="User prefers rg and concise answers.",
    )

    assert extract_interaction_preferences(assistant).observations == []
    assert extract_interaction_preferences(tool).observations == []


def test_do_not_remember_instruction_blocks_content_and_preferences():
    result = extract_interaction_preferences(event("不要记住这条消息：以后搜索代码优先用 rg。"))

    assert result.remember_allowed is False
    assert result.reason == "user_requested_no_memory"
    assert result.observations == []


def test_one_time_exception_does_not_overwrite_long_term_tool_preference():
    result = extract_interaction_preferences(event("这次用 grep，下次还是用 rg。"))

    assert [(item.topic, item.value, item.scope) for item in result.observations] == [
        ("code_search_tool", "grep", "session"),
        ("code_search_tool", "rg", "global"),
    ]


def test_service_automatically_stores_and_links_explicit_preferences(tmp_path):
    service = MemGuardService(tmp_path / "memory.segment", tmp_path / "meta.sqlite3")
    try:
        source_event = event("以后搜索项目代码优先使用 rg。回答时先给命令，再解释原因。")

        result = service.ingest_interaction(source_event)

        assert result["stored"] is True
        assert len(result["memory_ids"]) == 2
        memories = [service.meta.get_memory(memory_id) for memory_id in result["memory_ids"]]
        assert {item["subtype"] for item in memories} == {"tool_preference", "output_style"}
        assert all(service.preference_evidence(item["memory_id"])["events"] for item in memories)

        duplicate = service.ingest_interaction(AgentInteractionEvent.from_json(source_event.to_json()))
        assert duplicate["duplicate"] is True
        assert duplicate["memory_ids"] == []
        assert len(service.meta.list_memories()) == 2
    finally:
        service.close()


def test_service_does_not_store_assistant_preference_as_memory(tmp_path):
    service = MemGuardService(tmp_path / "memory.segment", tmp_path / "meta.sqlite3")
    try:
        result = service.ingest_interaction(
            event(
                "以后我会优先使用 rg。",
                event_type="assistant_message",
                role="assistant",
            )
        )

        assert result["stored"] is True
        assert result["memory_ids"] == []
        assert service.meta.list_memories() == []
    finally:
        service.close()


def test_no_memory_instruction_persists_only_redacted_event_metadata(tmp_path):
    service = MemGuardService(tmp_path / "memory.segment", tmp_path / "meta.sqlite3")
    try:
        result = service.ingest_interaction(
            event("不要记住这条消息：以后搜索代码优先使用 rg。")
        )

        stored = service.meta.list_agent_events()[0]
        assert result["remember_allowed"] is False
        assert result["memory_ids"] == []
        assert stored["content"] == ""
        assert stored["tool_result"] == ""
        assert stored["user_feedback"] == ""
    finally:
        service.close()


def test_high_risk_secret_is_not_persisted_in_agent_event(tmp_path):
    service = MemGuardService(tmp_path / "memory.segment", tmp_path / "meta.sqlite3")
    try:
        secret = "sk-1234567890abcdefghijklmnop"
        result = service.ingest_interaction(event("我的临时 API key 是 " + secret))

        stored = service.meta.list_agent_events()[0]
        assert result["sensitivity"]["level"] == "high"
        assert secret not in stored["content"]
        assert secret not in str(stored)
        assert result["memory_ids"] == []
    finally:
        service.close()
