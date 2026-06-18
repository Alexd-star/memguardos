import json
from pathlib import Path
import subprocess
import sys
from types import SimpleNamespace

from memguard.integrations.openhands_runner import (
    OpenHandsMemoryRunner,
    build_openhands_llm_kwargs,
)
from memguard.api.server import handle_request
from memguard.cli.memctl import main as memctl_main
from memguard.service import MemGuardService


def text(value):
    return SimpleNamespace(text=value)


def fake_event(name, **fields):
    event_type = type(name, (), {})
    event = event_type()
    for key, value in fields.items():
        setattr(event, key, value)
    return event


class FakeConversation:
    def __init__(self, callback):
        self.callback = callback
        self.messages = []
        self.closed = False

    def send_message(self, message):
        self.messages.append(message)
        self.callback(
            fake_event(
                "MessageEvent",
                id="fake-user-1",
                source="user",
                llm_message=SimpleNamespace(role="user", content=[text(message)]),
            )
        )

    def run(self):
        self.callback(
            fake_event(
                "ActionEvent",
                id="fake-action-1",
                source="agent",
                tool_name="terminal",
                tool_call_id="call-1",
                tool_call=SimpleNamespace(arguments='{"command":"rg build_agent_context"}'),
                thought=[text("查找实现")],
            )
        )
        self.callback(
            fake_event(
                "ObservationEvent",
                id="fake-observation-1",
                source="environment",
                tool_name="terminal",
                tool_call_id="call-1",
                observation=SimpleNamespace(
                    content=[text("memguard/service.py:1297")],
                    is_error=False,
                    exit_code=0,
                ),
            )
        )
        self.callback(
            fake_event(
                "MessageEvent",
                id="fake-assistant-1",
                source="agent",
                llm_message=SimpleNamespace(role="assistant", content=[text("已找到实现。")]),
            )
        )

    def close(self):
        self.closed = True


class FakeErrorConversation(FakeConversation):
    def run(self):
        self.callback(
            fake_event(
                "ConversationErrorEvent",
                id="fake-conversation-error-1",
                source="environment",
                error="Agent reached maximum iterations limit.",
            )
        )


def make_service(tmp_path):
    return MemGuardService(
        store_path=tmp_path / "memory.segment",
        db_path=tmp_path / "memory.sqlite3",
    )


def test_runner_builds_traceable_three_stage_context_without_changing_user_prompt(tmp_path):
    service = make_service(tmp_path)
    try:
        runner = OpenHandsMemoryRunner(
            service=service,
            session_id="session-runner-a",
            workspace=tmp_path / "workspace",
            state_dir=tmp_path / "state",
        )

        bundle = runner.prepare_context("请查找 build_agent_context 的实现。")

        assert bundle["trace_id"].startswith("ctx_")
        assert set(bundle["stages"]) == {
            "planning",
            "tool_selection",
            "response_generation",
        }
        context_file = Path(bundle["context_file"])
        assert context_file.is_file()
        stored = json.loads(context_file.read_text(encoding="utf-8"))
        assert stored["user_task"] == "请查找 build_agent_context 的实现。"
        assert stored["trace_id"] == bundle["trace_id"]
        assert stored["allowed_tools"] == []
        assert "请查找 build_agent_context 的实现。" not in stored["additional_context"]
    finally:
        service.close()


def test_runner_callback_automatically_persists_real_agent_events(tmp_path):
    service = make_service(tmp_path)
    created = {}

    def conversation_factory(**kwargs):
        created.update(kwargs)
        return FakeConversation(kwargs["callback"])

    try:
        runner = OpenHandsMemoryRunner(
            service=service,
            session_id="session-runner-b",
            workspace=tmp_path / "workspace",
            state_dir=tmp_path / "state",
            conversation_factory=conversation_factory,
        )

        result = runner.run("请查找 build_agent_context 的实现。")

        assert result["ok"] is True
        assert result["event_summary"]["stored"] == 4
        assert result["event_summary"]["tool_calls"] == 1
        assert result["event_summary"]["tool_results"] == 1
        assert created["workspace"] == (tmp_path / "workspace").resolve()
        assert created["context_file"].is_file()
        assert created["callback"].__self__ is runner
        assert created["max_iterations"] == 8
        assert created["tool_profile"] == "default"
        stored_context = json.loads(created["context_file"].read_text(encoding="utf-8"))
        assert all(
            stage["budget"]["token_budget"] == 256
            for stage in stored_context["stages"].values()
        )
        events = service.meta.list_agent_events(session_id="session-runner-b")
        assert [item["event_type"] for item in events] == [
            "user_message",
            "tool_call",
            "tool_result",
            "assistant_message",
        ]
    finally:
        service.close()


def test_runner_rejects_workspace_that_is_not_a_dedicated_directory(tmp_path):
    service = make_service(tmp_path)
    try:
        runner = OpenHandsMemoryRunner(
            service=service,
            session_id="session-runner-c",
            workspace=tmp_path,
            state_dir=tmp_path / "state",
        )

        try:
            runner.run("测试")
        except ValueError as exc:
            assert "dedicated workspace" in str(exc)
        else:
            raise AssertionError("runner accepted a non-dedicated workspace")
    finally:
        service.close()


def test_runner_reports_conversation_error_as_failed_run(tmp_path):
    service = make_service(tmp_path)

    def conversation_factory(**kwargs):
        return FakeErrorConversation(kwargs["callback"])

    try:
        runner = OpenHandsMemoryRunner(
            service=service,
            session_id="session-runner-error",
            workspace=tmp_path / "workspace",
            state_dir=tmp_path / "state",
            conversation_factory=conversation_factory,
        )
        result = runner.run("测试错误状态")

        assert result["ok"] is False
        assert result["event_summary"]["task_errors"] == 1
    finally:
        service.close()


def test_openhands_ollama_model_uses_native_ollama_base_url():
    kwargs = build_openhands_llm_kwargs(
        model="ollama/qwen2.5-coder:1.5b",
        api_key="",
        base_url="http://127.0.0.1:11434",
    )

    assert kwargs["model"] == "ollama/qwen2.5-coder:1.5b"
    assert kwargs["ollama_base_url"] == "http://127.0.0.1:11434"
    assert "base_url" not in kwargs
    assert kwargs["reasoning_effort"] == "none"
    assert kwargs["enable_encrypted_reasoning"] is False


def test_openhands_hook_injects_context_and_blocks_high_risk_commands(tmp_path):
    context_file = tmp_path / "context.json"
    context_file.write_text(
        json.dumps(
            {
                "additional_context": "只使用已筛选记忆。",
                "allowed_tools": ["terminal", "finish"],
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    script = Path(__file__).parents[1] / "scripts" / "openhands_memory_hook.py"

    inject = subprocess.run(
        [sys.executable, str(script), "--context-file", str(context_file)],
        input=json.dumps({"message": "测试"}),
        text=True,
        capture_output=True,
        check=False,
    )
    guard = subprocess.run(
        [
            sys.executable,
            str(script),
            "--context-file",
            str(context_file),
            "--guard-tool",
        ],
        input=json.dumps({"tool_input": {"command": "rm -rf /"}}),
        text=True,
        capture_output=True,
        check=False,
    )

    assert inject.returncode == 0
    assert json.loads(inject.stdout)["additionalContext"] == "只使用已筛选记忆。"
    assert guard.returncode == 2
    assert json.loads(guard.stdout)["decision"] == "deny"

    disallowed = subprocess.run(
        [
            sys.executable,
            str(script),
            "--context-file",
            str(context_file),
            "--guard-tool",
        ],
        input=json.dumps({"tool_name": "think", "tool_input": {"summary": "x"}}),
        text=True,
        capture_output=True,
        check=False,
    )
    assert disallowed.returncode == 2
    assert "not allowed" in json.loads(disallowed.stdout)["reason"]


def interaction_event(content, event_id, sequence, session_id="session-forget-a"):
    from memguard.integrations.events import AgentInteractionEvent

    return AgentInteractionEvent(
        agent_name="openhands",
        agent_version="1.28.1",
        session_id=session_id,
        turn_id="turn-%s" % sequence,
        sequence=sequence,
        event_type="user_message",
        role="user",
        content=content,
        source_uri="openhands://%s/%s" % (session_id, event_id),
        event_id=event_id,
    )


def test_agent_forget_dry_run_reports_precise_impact_and_external_residue(tmp_path):
    service = make_service(tmp_path)
    try:
        response = service.ingest_interaction(
            interaction_event("以后回答先给命令，再解释原因。", "evt-forget-response", 1)
        )
        service.ingest_interaction(
            interaction_event("以后搜索代码优先使用 rg。", "evt-forget-tool", 2)
        )

        plan = service.forget_agent_interactions(
            {"agent_name": "openhands", "preference_topic": "response_order"},
            confirm=False,
            external_persistence=True,
        )

        assert plan["confirmed"] is False
        assert plan["affected"]["event_ids"] == ["evt-forget-response"]
        assert plan["affected"]["memory_ids"] == response["memory_ids"]
        assert plan["external_residue"]["risk"] == "external_agent_persistence"
        assert plan["external_residue"]["requires_separate_cleanup"] is True
    finally:
        service.close()


def test_agent_forget_removes_only_target_preference_from_all_context_stages(tmp_path):
    service = make_service(tmp_path)
    try:
        response = service.ingest_interaction(
            interaction_event("以后回答先给命令，再解释原因。", "evt-forget-response-2", 1)
        )
        tool = service.ingest_interaction(
            interaction_event("以后搜索代码优先使用 rg。", "evt-forget-tool-2", 2)
        )
        before = service.build_agent_context(
            "查找代码并回答",
            stage="response_generation",
            agent_name="openhands",
            session_id="session-new-before",
        )
        assert response["memory_ids"][0] in [item["memory_id"] for item in before["memories"]]

        result = service.forget_agent_interactions(
            {"agent_name": "openhands", "preference_topic": "response_order"},
            confirm=True,
            compact=True,
            external_persistence=False,
        )

        assert result["forgotten_memory_ids"] == response["memory_ids"]
        assert result["verification"]["all_stages_absent"] is True
        assert result["verification"]["event_payload_scrubbed"] is True
        assert result["verification"]["segment_plaintext_absent"] is True
        stored_event = service.meta.list_agent_events(session_id="session-forget-a")[0]
        assert stored_event["forgotten"] is True
        assert stored_event["content"] == ""
        tool_context = service.build_agent_context(
            "查找代码",
            stage="tool_selection",
            agent_name="openhands",
            session_id="session-new-after",
        )
        assert tool["memory_ids"][0] in [item["memory_id"] for item in tool_context["memories"]]
    finally:
        service.close()


def test_agent_forget_requires_at_least_one_scope_filter(tmp_path):
    service = make_service(tmp_path)
    try:
        try:
            service.forget_agent_interactions({}, confirm=False)
        except ValueError as exc:
            assert "at least one" in str(exc)
        else:
            raise AssertionError("unscoped Agent forgetting was accepted")
    finally:
        service.close()


def test_agent_forget_is_exposed_by_api_and_cli(tmp_path, monkeypatch, capsys):
    service = make_service(tmp_path)
    try:
        service.ingest_interaction(
            interaction_event("以后回答先给命令，再解释原因。", "evt-forget-api", 1)
        )
        response = handle_request(
            service,
            {
                "command": "forget_agent_interactions",
                "filters": {
                    "agent_name": "openhands",
                    "preference_topic": "response_order",
                },
                "confirm": False,
            },
        )
        assert response["ok"] is True
        assert response["result"]["affected"]["event_ids"] == ["evt-forget-api"]
    finally:
        service.close()

    monkeypatch.setenv("MEMGUARD_HOME", str(tmp_path / "cli-home"))
    cli_service = MemGuardService()
    try:
        cli_service.ingest_interaction(
            interaction_event("以后搜索代码优先使用 rg。", "evt-forget-cli", 1)
        )
    finally:
        cli_service.close()
    assert (
        memctl_main(
            [
                "agent",
                "forget",
                "--agent-name",
                "openhands",
                "--preference-topic",
                "code_search_tool",
            ]
        )
        == 0
    )
    output = json.loads(capsys.readouterr().out)
    assert output["confirmed"] is False
    assert output["affected"]["event_ids"] == ["evt-forget-cli"]


def test_agent_dashboard_snapshot_exposes_timeline_preferences_and_stage_contexts(tmp_path):
    service = make_service(tmp_path)
    try:
        service.ingest_interaction(
            interaction_event("以后搜索代码优先使用 rg。", "evt-dashboard-agent", 1)
        )
        snapshot = service.agent_dashboard_snapshot(
            session_id="session-forget-a",
            task="查找精准遗忘实现",
        )

        assert snapshot["summary"]["event_count"] == 1
        assert snapshot["timeline"][0]["event_id"] == "evt-dashboard-agent"
        assert snapshot["timeline"][0]["content"] == "以后搜索代码优先使用 rg。"
        assert snapshot["preference_aggregates"][0]["topic"] == "code_search_tool"
        assert set(snapshot["contexts"]) == {
            "planning",
            "tool_selection",
            "response_generation",
        }
        assert snapshot["contexts"]["tool_selection"]["memory_count"] == 1
    finally:
        service.close()
