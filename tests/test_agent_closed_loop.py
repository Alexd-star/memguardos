import json
from pathlib import Path
import subprocess
import sys
from types import SimpleNamespace

from memguard.integrations.openhands_runner import OpenHandsMemoryRunner
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


def test_openhands_hook_injects_context_and_blocks_high_risk_commands(tmp_path):
    context_file = tmp_path / "context.json"
    context_file.write_text(
        json.dumps({"additional_context": "只使用已筛选记忆。"}, ensure_ascii=False),
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
