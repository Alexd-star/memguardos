from pathlib import Path

from memguard.cli.memguard import _friendly_agent_error, _memory_mode, _user_setting
from memguard.integrations.events import AgentInteractionEvent
from memguard.integrations.openhands_runner import (
    OpenHandsMemoryRunner,
    _execution_trace,
    _runtime_preference_effect_proofs,
)


class _NoMemoryService:
    namespace = "demo-test"

    def build_agent_context(self, **kwargs):
        raise AssertionError("memory-off run must not build memory context")

    def ingest_interaction(self, event):
        raise AssertionError("memory-off run must not persist interaction events")


def _event(event_type, *, sequence=1, tool_args=None, exit_code=None):
    return AgentInteractionEvent(
        agent_name="openhands",
        agent_version="test",
        session_id="session-demo",
        turn_id="turn-1",
        sequence=sequence,
        event_type=event_type,
        role="assistant" if event_type == "tool_call" else "tool",
        tool_name="terminal",
        tool_args=tool_args or {},
        exit_code=exit_code,
    )


def test_memory_mode_is_explicit_and_reversible(capsys):
    state = {"memory_enabled": True}

    assert _memory_mode("/memory off", state) == 0
    assert state["memory_enabled"] is False
    assert "不读取或写入长期记忆" in capsys.readouterr().out

    assert _memory_mode("/memory on", state) == 0
    assert state["memory_enabled"] is True
    assert "已开启" in capsys.readouterr().out


def test_memory_off_runner_builds_empty_context_and_does_not_persist(tmp_path):
    workspace = tmp_path / "workspace"
    state_dir = tmp_path / "state"
    workspace.mkdir()
    runner = OpenHandsMemoryRunner(
        service=_NoMemoryService(),
        session_id="session-demo",
        workspace=workspace,
        state_dir=state_dir,
        memory_enabled=False,
    )

    bundle = runner.prepare_context("查找函数定义")
    assert all(not stage["memories"] for stage in bundle["stages"].values())
    runner._ingest_event(
        _event("tool_call", tool_args={"command": "rg -n build_agent_context memguard"})
    )
    assert runner._event_types["tool_call"] == 1
    assert runner._event_results == []


def test_execution_trace_hides_credentials_and_keeps_command_evidence():
    trace = _execution_trace(
        [
            {
                "event_type": "tool_call",
                "tool_name": "terminal",
                "tool_args": {"command": "curl -H 'Authorization: Bearer sk-example-secret-123456' /health"},
            },
            {"event_type": "tool_result", "tool_name": "terminal", "exit_code": 0},
            {
                "event_type": "tool_call",
                "tool_name": "finish",
                "tool_args": {"message": "memguard/service.py:2727"},
            },
        ]
    )

    assert trace[0]["command"].startswith("curl")
    assert "sk-example" not in trace[0]["command"]
    assert "[已隐藏凭据]" in trace[0]["command"]
    assert trace[1]["outcome"] == "success"
    assert trace[2]["message"] == "memguard/service.py:2727"


def test_cli_can_explicitly_allow_current_project_workspace(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    runner = OpenHandsMemoryRunner(
        service=_NoMemoryService(),
        session_id="session-demo",
        workspace=Path.cwd(),
        state_dir=tmp_path.parent / "agent-state",
        memory_enabled=False,
        allow_in_place_workspace=True,
    )

    runner._validate_workspace()
    assert "响应超时" in _friendly_agent_error(TimeoutError("request timed out"))


def test_user_setting_prefers_environment_and_reads_local_config(tmp_path, monkeypatch):
    config = tmp_path / ".config" / "memguardos"
    config.mkdir(parents=True)
    (config / "openhands_model").write_text("openai/example-model\n", encoding="utf-8")

    monkeypatch.delenv("MEMGUARD_OPENHANDS_MODEL", raising=False)
    assert _user_setting(
        "MEMGUARD_OPENHANDS_MODEL", "openhands_model", home=tmp_path
    ) == "openai/example-model"

    monkeypatch.setenv("MEMGUARD_OPENHANDS_MODEL", "openai/env-model")
    assert _user_setting(
        "MEMGUARD_OPENHANDS_MODEL", "openhands_model", home=tmp_path
    ) == "openai/env-model"


def test_runtime_proofs_use_only_preselected_memories_and_real_finish_message():
    stages = {
        "planning": {"memories": []},
        "tool_selection": {
            "memories": [
                {
                    "memory_id": "mem-tool",
                    "preference_topic": "code_search_tool",
                    "preference_value": "grep",
                }
            ]
        },
        "response_generation": {
            "memories": [
                {
                    "memory_id": "mem-order",
                    "preference_topic": "response_order",
                    "preference_value": "command_then_explanation",
                }
            ]
        },
    }
    events = [
        {
            "event_type": "tool_call",
            "tool_name": "terminal",
            "tool_args": {"command": "grep -RIn target memguard"},
        },
        {
            "event_type": "tool_call",
            "tool_name": "finish",
            "tool_args": {"message": "命令：grep -RIn target memguard\n说明：已找到。"},
        },
    ]

    proofs = _runtime_preference_effect_proofs(
        stages,
        events,
        task="查找 target",
        agent_name="openhands",
        session_id="session-demo",
    )

    assert [(item["preference"]["topic"], item["compliance"]) for item in proofs] == [
        ("code_search_tool", "passed"),
        ("response_order", "passed"),
    ]
