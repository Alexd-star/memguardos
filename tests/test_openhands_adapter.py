from types import SimpleNamespace

from memguard.integrations.openhands_adapter import OpenHandsEventAdapter, detect_openhands_runtime


def test_openhands_runtime_reports_incompatible_python_before_importing_packages():
    status = detect_openhands_runtime(
        python_version=(3, 9, 13),
        version_getter=lambda _name: "1.28.1",
        module_loader=lambda _name: SimpleNamespace(),
    )

    assert status["available"] is False
    assert status["python_compatible"] is False
    assert "Python 3.12" in status["install_hint"]


def test_openhands_runtime_requires_matching_sdk_and_tools_versions():
    versions = {"openhands-sdk": "1.28.1", "openhands-tools": "1.27.0"}

    status = detect_openhands_runtime(
        python_version=(3, 12, 2),
        version_getter=lambda name: versions[name],
        module_loader=lambda name: (
            SimpleNamespace(LLM=object, Agent=object, Conversation=object)
            if name == "openhands.sdk"
            else SimpleNamespace(TerminalTool=object)
        ),
    )

    assert status["available"] is False
    assert status["versions_match"] is False
    assert status["sdk_version"] == "1.28.1"
    assert status["tools_version"] == "1.27.0"


def test_openhands_runtime_reports_required_core_symbols():
    modules = {
        "openhands.sdk": SimpleNamespace(LLM=object, Agent=object, Conversation=object),
        "openhands.tools": SimpleNamespace(TerminalTool=object),
    }

    status = detect_openhands_runtime(
        python_version=(3, 12, 2),
        version_getter=lambda _name: "1.28.1",
        module_loader=lambda name: modules[name],
    )

    assert status["available"] is True
    assert status["versions_match"] is True
    assert status["symbols"] == {
        "LLM": True,
        "Agent": True,
        "Conversation": True,
        "TerminalTool": True,
    }


def test_openhands_runtime_reports_missing_optional_dependency_cleanly():
    def missing(_name):
        raise ModuleNotFoundError("not installed")

    status = detect_openhands_runtime(
        python_version=(3, 12, 2),
        version_getter=missing,
        module_loader=missing,
    )

    assert status["available"] is False
    assert status["installed"] is False
    assert "memguardos[agent-openhands]" in status["install_hint"]


def text(value):
    return SimpleNamespace(text=value)


def fake_event(name, **fields):
    event_type = type(name, (), {})
    event = event_type()
    for key, value in fields.items():
        setattr(event, key, value)
    return event


def test_openhands_message_event_maps_user_role_and_content():
    adapter = OpenHandsEventAdapter("session-a", agent_version="1.28.1", project_root="/work/demo")
    raw = fake_event(
        "MessageEvent",
        id="oh-user-1",
        timestamp="2026-06-19T00:00:00Z",
        source="user",
        llm_message=SimpleNamespace(role="user", content=[text("以后使用 rg。")]),
    )

    event = adapter.convert(raw)

    assert event.event_type == "user_message"
    assert event.role == "user"
    assert event.content == "以后使用 rg。"
    assert event.source_uri == "openhands://session-a/oh-user-1"


def test_openhands_action_and_observation_map_tool_trace():
    adapter = OpenHandsEventAdapter("session-a", agent_version="1.28.1")
    action = fake_event(
        "ActionEvent",
        id="oh-action-1",
        timestamp="2026-06-19T00:00:01Z",
        source="agent",
        tool_name="terminal",
        tool_call_id="call-1",
        tool_call=SimpleNamespace(arguments='{"command":"rg build_context"}'),
        thought=[text("查找实现")],
    )
    observation = fake_event(
        "ObservationEvent",
        id="oh-observation-1",
        timestamp="2026-06-19T00:00:02Z",
        source="user",
        tool_name="terminal",
        tool_call_id="call-1",
        observation=SimpleNamespace(content=[text("memguard/service.py:1310")], is_error=False),
    )

    tool_call = adapter.convert(action)
    tool_result = adapter.convert(observation)

    assert tool_call.event_type == "tool_call"
    assert tool_call.tool_args == {"command": "rg build_context"}
    assert tool_result.event_type == "tool_result"
    assert tool_result.tool_result == "memguard/service.py:1310"
    assert tool_result.exit_code == 0
    assert tool_result.turn_id == tool_call.turn_id


def test_openhands_adapter_ignores_unknown_events_without_raising():
    unknown = fake_event("ConversationStateUpdateEvent", id="unknown-1", source="environment")
    seen = []
    adapter = OpenHandsEventAdapter("session-a", on_unknown=lambda item: seen.append(item))

    assert adapter.convert(unknown) is None
    assert seen == [unknown]


def test_openhands_environment_feedback_is_not_mapped_as_user_message():
    adapter = OpenHandsEventAdapter("session-a")
    raw = fake_event(
        "MessageEvent",
        id="oh-environment-feedback-1",
        source="user",
        llm_message=SimpleNamespace(
            role="user",
            content=[
                text(
                    "Your last response did not include a function call or a message. "
                    "Please use a tool to proceed with the task."
                )
            ],
        ),
    )

    event = adapter.convert(raw)

    assert event.event_type == "system_message"
    assert event.role == "system"


def test_openhands_conversation_error_maps_to_task_error():
    adapter = OpenHandsEventAdapter("session-a")
    raw = fake_event(
        "ConversationErrorEvent",
        id="oh-conversation-error-1",
        source="environment",
        error="Agent reached maximum iterations limit.",
    )

    event = adapter.convert(raw)

    assert event.event_type == "task_error"
    assert event.role == "assistant"
    assert event.exit_code == 1
    assert "maximum iterations" in event.content


def test_openhands_blocked_hook_maps_to_policy_decision_event():
    adapter = OpenHandsEventAdapter("session-a")
    raw = fake_event(
        "HookExecutionEvent",
        id="oh-hook-1",
        source="hook",
        hook_event_type="PreToolUse",
        hook_command="python openhands_memory_hook.py --guard-tool",
        tool_name="terminal",
        success=True,
        blocked=True,
        exit_code=2,
        stdout='{"decision":"deny"}',
        stderr="",
        reason="MemGuardOS blocked a high-risk tool command: reboot",
        additional_context=None,
        error=None,
    )

    event = adapter.convert(raw)

    assert event.event_type == "policy_decision"
    assert event.role == "system"
    assert event.tool_name == "terminal"
    assert event.exit_code == 2
    assert "blocked a high-risk tool command" in event.content
