from types import SimpleNamespace

import pytest

from memguard.integrations.openhands_runner import OpenHandsMemoryRunner
from memguard.integrations.adherence import (
    final_visible_output,
    response_starts_with_command_or_conclusion,
)
from memguard.service import MemGuardService
from scripts import run_openhands_behavior_suite as suite


def sdk_event(name, **fields):
    return type(name, (), fields)()


def message(content):
    return sdk_event(
        "MessageEvent",
        id="message",
        source="user",
        llm_message=SimpleNamespace(
            role="user", content=[SimpleNamespace(text=content)]
        ),
    )


@pytest.fixture
def runtime(tmp_path):
    service = MemGuardService(tmp_path / "memory.segment", tmp_path / "memory.sqlite3")
    runner = OpenHandsMemoryRunner(
        service, "capture-live", tmp_path / "project", tmp_path / "run"
    )
    try:
        yield service, runner
    finally:
        service.close()


def test_capture_preserves_visible_newlines_and_detaches_return_value(runtime):
    _, runner = runtime
    runner.on_event(message("请复核发布说明。\n第二行是补充条件。"))
    events = runner.recorded_events()
    assert events[0]["display_content"] == "请复核发布说明。\n第二行是补充条件。"
    assert events[0]["capture_origin"] == "sdk_callback"
    events[0]["display_content"] = "changed"
    assert "changed" not in runner.recorded_events()[0]["display_content"]


def test_capture_keeps_multiline_tool_commands_and_outputs(runtime):
    _, runner = runtime
    runner.on_event(
        sdk_event(
            "ActionEvent",
            id="action",
            tool_name="terminal",
            tool_call_id="call",
            tool_call=SimpleNamespace(arguments={"command": "printf 'a\\nb'\npwd"}),
        )
    )
    runner.on_event(
        sdk_event(
            "ObservationEvent",
            id="observation",
            tool_name="terminal",
            tool_call_id="call",
            observation=SimpleNamespace(
                content=[SimpleNamespace(text="a\nb\n/project")],
                exit_code=0,
                is_error=False,
            ),
        )
    )
    events = runner.recorded_events()
    assert events[0]["display_tool_args"]["command"].endswith("\npwd")
    assert events[1]["display_tool_result"] == "a\nb\n/project"


@pytest.mark.parametrize("fence", ["```bash", "```", "~~~shell"])
def test_command_first_accepts_leading_code_block_without_accepting_late_command(fence):
    command = "rg -n load_settings settings.py"
    assert response_starts_with_command_or_conclusion(
        f"{fence}\n{command}\n```\n解释", [command]
    )
    assert not response_starts_with_command_or_conclusion(
        f"这里先解释。\n{fence}\n{command}\n```", [command]
    )


def test_adherence_uses_actual_sdk_display_text(runtime):
    _, runner = runtime
    answer = "```bash\nrg -n load_settings settings.py\n```\n\n读取 JSON 设置。"
    runner.on_event(
        sdk_event(
            "MessageEvent",
            id="answer",
            source="agent",
            llm_message=SimpleNamespace(
                role="assistant", content=[SimpleNamespace(text=answer)]
            ),
        )
    )
    assert final_visible_output(runner._live_events) == answer
    assert final_visible_output(runner.recorded_events()) == answer


def test_capture_omits_secrets_even_when_memory_is_disabled(runtime):
    _, runner = runtime
    runner.memory_enabled = False
    runner.on_event(message("记住我的密码 11515"))
    captured = runner.recorded_events()[0]
    assert captured["capture_redacted"]
    assert "11515" not in str(captured)


def test_disabled_run_captures_without_learning_or_reading_memory(runtime, monkeypatch):
    service, runner = runtime
    runner.memory_enabled = False

    def forbidden(*args, **kwargs):
        raise AssertionError("memory access in disabled arm")

    for name in (
        "build_agent_context",
        "ingest_interaction",
        "agent_dashboard_snapshot",
        "consolidate_verified_tool_outcomes",
    ):
        monkeypatch.setattr(service, name, forbidden)

    class Conversation:
        def send_message(self, task):
            runner.on_event(message(task))

        def run(self):
            runner.on_event(
                sdk_event(
                    "MessageEvent",
                    id="answer",
                    source="agent",
                    llm_message=SimpleNamespace(
                        role="assistant",
                        content=[SimpleNamespace(text="当前目录命令是 pwd。")],
                    ),
                )
            )

        def close(self):
            pass

    runner.conversation_factory = lambda **kwargs: Conversation()
    runner.verify_hook_contract = False
    result = runner.run("如何显示当前工作目录？")
    assert result["memory_enabled"] is False
    assert len(result["events"]) == 2
    assert result["event_summary"]["stored"] == 0
    assert all(not ids for ids in result["context"]["selected_memory_ids"].values())
    assert service.meta.list_agent_events(limit=10) == []


@pytest.mark.parametrize("mode", ["on", "off"])
@pytest.mark.parametrize("fails", [False, True])
def test_suite_uses_actual_switch_and_retains_partial_events(
    tmp_path, monkeypatch, mode, fails
):
    observed = {}

    class Runner:
        def __init__(self, **kwargs):
            observed.update(kwargs)

        def recorded_events(self):
            return [
                {
                    "event_type": "user_message",
                    "role": "user",
                    "session_id": "r01-live",
                    "content": "实际已开始",
                }
            ]

        def run(self, task):
            if fails:
                raise RuntimeError("recorded model failure")
            return {
                "ok": True,
                "memory_enabled": observed["memory_enabled"],
                "context": {},
                "adherence": {"passed": True},
            }

    monkeypatch.setattr(suite, "OpenHandsMemoryRunner", Runner)
    args = suite.build_parser().parse_args(["--output-dir", str(tmp_path)])
    case = {
        "case_id": "R01",
        "title": "protocol",
        "category": "capture",
        "task": "普通任务",
        "workspace_fixture": {"files": {}},
    }
    result = suite.run_case(case, tmp_path, args, memory_mode=mode)
    assert observed["memory_enabled"] is (mode == "on")
    assert result["task"] == "普通任务"
    assert result["events"][0]["content"] == "实际已开始"
    if fails:
        assert result["evaluation"]["status"] == "fail"
        assert "recorded model failure" in result["run_exception"]["message"]
