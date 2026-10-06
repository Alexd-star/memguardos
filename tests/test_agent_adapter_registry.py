from memguard.api.server import handle_request
from memguard.integrations.adapter_registry import (
    AgentAdapterRegistry,
    AdapterCapabilities,
    GenericAgentEventAdapter,
    builtin_agent_adapter_registry,
)
from memguard.service import MemGuardService


def test_generic_adapter_maps_common_provider_neutral_event_aliases():
    adapter = GenericAgentEventAdapter(
        agent_name="demo-agent",
        agent_version="0.4",
        session_id="session-generic",
        project_root="/tmp/project",
        task_type="code_search",
    )

    events = adapter.convert_many(
        [
            {"type": "message", "role": "user", "text": "以后搜索代码优先使用 rg"},
            {
                "type": "action",
                "id": "call-1",
                "tool": "terminal",
                "arguments": {"command": "rg build_context", "api_key": "secret"},
            },
            {
                "type": "observation",
                "id": "result-1",
                "tool": "terminal",
                "output": "memguard/service.py:4201",
                "exit_code": 0,
            },
        ]
    )

    assert [event.event_type for event in events] == [
        "user_message",
        "tool_call",
        "tool_result",
    ]
    assert events[0].role == "user"
    assert events[1].tool_name == "terminal"
    assert events[1].tool_args["api_key"] == "secret"
    assert events[2].tool_result == "memguard/service.py:4201"
    assert [event.sequence for event in events] == [1, 2, 3]
    assert all(event.session_id == "session-generic" for event in events)


def test_generic_adapter_generates_stable_non_colliding_ids_when_provider_omits_ids():
    first_adapter = GenericAgentEventAdapter(session_id="session-a")
    second_adapter = GenericAgentEventAdapter(session_id="session-b")
    payload = {"type": "message", "role": "user", "content": "查看服务状态"}

    first = first_adapter.convert(payload)
    repeated = GenericAgentEventAdapter(session_id="session-a").convert(payload)
    other_session = second_adapter.convert(payload)

    assert first.event_id == repeated.event_id
    assert first.event_id != other_session.event_id
    assert first.event_id.startswith("evt_")


def test_generic_adapter_fails_closed_for_unknown_event_and_missing_session():
    adapter = GenericAgentEventAdapter()

    try:
        adapter.convert({"type": "unknown_provider_event"})
    except ValueError as exc:
        assert "unsupported event_type" in str(exc)
    else:
        raise AssertionError("unknown event types must not become memory events")

    try:
        adapter.convert({"type": "message", "content": "hello"})
    except ValueError as exc:
        assert "session_id is required" in str(exc)
    else:
        raise AssertionError("generic events without a session must be rejected")


def test_registry_lists_generic_and_openhands_capabilities():
    registry = builtin_agent_adapter_registry()

    assert registry.names() == ("generic-json", "openhands")
    descriptions = {item["name"]: item for item in registry.describe()}
    assert descriptions["generic-json"]["supports_event_ingest"] is True
    assert descriptions["openhands"]["supports_checkpoints"] is True
    assert set(descriptions["generic-json"]["context_stages"]) == {
        "planning", "tool_selection", "response_generation",
    }


def test_registry_rejects_mismatched_capability_names():
    registry = AgentAdapterRegistry()
    capabilities = AdapterCapabilities(
        name="other",
        version="1",
        description="test",
        event_types=(),
    )
    try:
        registry.register("generic-json", GenericAgentEventAdapter, capabilities)
    except ValueError as exc:
        assert "capability name" in str(exc)
    else:
        raise AssertionError("registry must keep the advertised name consistent")


def test_service_ingests_generic_events_only_after_whole_batch_converts(tmp_path):
    service = MemGuardService(tmp_path / "memory.segment", tmp_path / "meta.sqlite3")
    try:
        result = service.ingest_agent_events(
            [
                {"type": "message", "role": "user", "content": "以后回答先给结论。"},
                {"type": "complete", "content": "任务完成"},
            ],
            agent_name="demo-agent",
            agent_version="0.4",
            session_id="session-generic",
        )
        assert result["adapter"] == "generic-json"
        assert result["event_count"] == 2
        assert result["stored_count"] == 2
        assert [item["event_type"] for item in service.meta.list_agent_events()] == [
            "user_message", "task_complete",
        ]

        try:
            service.ingest_agent_events(
                [
                    {"type": "message", "role": "user", "content": "这条不能部分写入"},
                    {"type": "unsupported-provider-event", "content": "invalid"},
                ],
                session_id="session-invalid",
            )
        except ValueError as exc:
            assert "unsupported event_type" in str(exc)
        else:
            raise AssertionError("invalid batches must be rejected before persistence")
        assert not service.meta.list_agent_events(session_id="session-invalid")
    finally:
        service.close()


def test_local_api_exposes_adapter_catalog_and_generic_ingest(tmp_path):
    service = MemGuardService(tmp_path / "memory.segment", tmp_path / "meta.sqlite3")
    try:
        catalog = handle_request(service, {"command": "agent_adapters"})
        assert catalog["ok"] is True
        assert {item["name"] for item in catalog["result"]["adapters"]} == {
            "generic-json", "openhands",
        }

        response = handle_request(
            service,
            {
                "command": "ingest_agent_events",
                "agent_name": "demo-agent",
                "agent_version": "0.4",
                "session_id": "session-api",
                "events": [
                    {"type": "message", "role": "user", "content": "记住这个项目使用 systemd。"},
                ],
            },
        )
        assert response["ok"] is True
        assert response["result"]["adapter"] == "generic-json"
        assert response["result"]["stored_count"] == 1
    finally:
        service.close()
