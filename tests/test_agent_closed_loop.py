import json
from pathlib import Path
import subprocess
import sys
from types import SimpleNamespace

from memguard.integrations.openhands_runner import (
    OpenHandsMemoryRunner,
    build_openhands_llm_kwargs,
    detect_adherence_issues,
    recover_failed_test_runner_intent,
    recover_misrouted_preferred_tool_intent,
    format_agent_context,
    repair_failed_preferred_tool_intent,
    _selected_values_for_stage,
)
from memguard.ingestion.tool_result import ToolResult
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


class FakePreferenceRepairConversation(FakeConversation):
    def __init__(self, callback):
        super().__init__(callback)
        self.run_count = 0

    def send_message(self, message):
        self.messages.append(message)
        self.callback(
            fake_event(
                "MessageEvent",
                id="fake-user-%s" % len(self.messages),
                source="user",
                llm_message=SimpleNamespace(role="user", content=[text(message)]),
            )
        )

    def run(self):
        self.run_count += 1
        if self.run_count == 1:
            self.callback(
                fake_event(
                    "MessageEvent",
                    id="fake-assistant-before-repair",
                    source="agent",
                    llm_message=SimpleNamespace(
                        role="assistant",
                        content=[text("我没有执行代码搜索。")],
                    ),
                )
            )
            return
        self.callback(
            fake_event(
                "ActionEvent",
                id="fake-action-after-repair",
                source="agent",
                tool_name="terminal",
                tool_call_id="call-repair",
                tool_call=SimpleNamespace(
                    arguments='{"command":"rg -n format_agent_context ."}'
                ),
                thought=[text("按用户偏好重新搜索")],
            )
        )
        self.callback(
            fake_event(
                "ObservationEvent",
                id="fake-observation-after-repair",
                source="environment",
                tool_name="terminal",
                tool_call_id="call-repair",
                observation=SimpleNamespace(
                    content=[text("memguard/integrations/openhands_runner.py:1")],
                    is_error=False,
                    exit_code=0,
                ),
            )
        )
        self.callback(
            fake_event(
                "MessageEvent",
                id="fake-assistant-after-repair",
                source="agent",
                llm_message=SimpleNamespace(
                    role="assistant",
                    content=[text("已找到 openhands_runner.py。")],
                ),
            )
        )


class FakeResponseOrderRepairConversation(FakePreferenceRepairConversation):
    def run(self):
        self.run_count += 1
        if self.run_count == 1:
            self.callback(
                fake_event(
                    "ActionEvent",
                    id="fake-pwd-action",
                    source="agent",
                    tool_name="terminal",
                    tool_call_id="call-pwd",
                    tool_call=SimpleNamespace(arguments='{"command":"pwd"}'),
                    thought=[text("查询当前目录")],
                )
            )
            self.callback(
                fake_event(
                    "ObservationEvent",
                    id="fake-pwd-result",
                    source="environment",
                    tool_name="terminal",
                    tool_call_id="call-pwd",
                    observation=SimpleNamespace(
                        content=[text("/tmp/workspace")],
                        is_error=False,
                        exit_code=0,
                    ),
                )
            )
            self.callback(
                fake_event(
                    "MessageEvent",
                    id="fake-pwd-answer-before-repair",
                    source="agent",
                    llm_message=SimpleNamespace(
                        role="assistant",
                        content=[text("当前目录是 /tmp/workspace。")],
                    ),
                )
            )
            return
        self.callback(
            fake_event(
                "MessageEvent",
                id="fake-pwd-answer-after-repair",
                source="agent",
                llm_message=SimpleNamespace(
                    role="assistant",
                    content=[text("pwd\n用于显示当前工作目录。")],
                ),
            )
        )


class FakeStructuredIntentConversation(FakePreferenceRepairConversation):
    def run(self):
        self.run_count += 1
        self.callback(
            fake_event(
                "MessageEvent",
                id="fake-structured-intent",
                source="agent",
                llm_message=SimpleNamespace(
                    role="assistant",
                    content=[
                        text(
                            json.dumps(
                                {
                                    "command": "rg -n 'format_agent_context' .",
                                    "is_input": False,
                                    "timeout": 10,
                                    "reset": False,
                                    "security_risk": "LOW",
                                }
                            )
                        )
                    ],
                ),
            )
        )


class FakeSuccessfulToolWithStructuredAnswerConversation(FakeConversation):
    def run(self):
        super().run()
        self.callback(
            fake_event(
                "MessageEvent",
                id="fake-structured-answer-after-success",
                source="agent",
                llm_message=SimpleNamespace(
                    role="assistant",
                    content=[
                        text(
                            json.dumps(
                                {
                                    "command": "rg -n build_agent_context .",
                                    "timeout": 10,
                                }
                            )
                        )
                    ],
                ),
            )
        )


class FakeStructuredIntentAfterRepairConversation(FakePreferenceRepairConversation):
    def run(self):
        self.run_count += 1
        if self.run_count == 1:
            self.callback(
                fake_event(
                    "MessageEvent",
                    id="fake-no-tool-before-repair",
                    source="agent",
                    llm_message=SimpleNamespace(
                        role="assistant",
                        content=[text("暂时没有执行搜索。")],
                    ),
                )
            )
            return
        self.callback(
            fake_event(
                "MessageEvent",
                id="fake-structured-after-repair",
                source="agent",
                llm_message=SimpleNamespace(
                    role="assistant",
                    content=[
                        text(
                            json.dumps(
                                {
                                    "command": "rg -n 'format_agent_context' .",
                                    "is_input": False,
                                    "timeout": 10,
                                    "security_risk": "LOW",
                                }
                            )
                        )
                    ],
                ),
            )
        )


class FakeInlineSearchIntentAfterRepairConversation(FakePreferenceRepairConversation):
    def run(self):
        self.run_count += 1
        if self.run_count == 1:
            self.callback(
                fake_event(
                    "MessageEvent",
                    id="fake-inline-no-tool-before-repair",
                    source="agent",
                    llm_message=SimpleNamespace(
                        role="assistant",
                        content=[text("我会使用 rg，但还没有执行工具。")],
                    ),
                )
            )
            return
        self.callback(
            fake_event(
                "MessageEvent",
                id="fake-inline-command-after-repair",
                source="agent",
                llm_message=SimpleNamespace(
                    role="assistant",
                    content=[
                        text(
                            "请执行 `rg -n 'format_agent_context' .` "
                            "在当前工作区搜索目标符号。"
                        )
                    ],
                ),
            )
        )


class FakeMisroutedFunctionToolConversation(FakePreferenceRepairConversation):
    def run(self):
        self.run_count += 1
        self.callback(
            fake_event(
                "ActionEvent",
                id="fake-misrouted-action",
                source="agent",
                tool_name="function_name",
                tool_call_id="call-misrouted",
                tool_call=SimpleNamespace(
                    arguments=json.dumps(
                        {
                            "command": "rg -n 'OpenHandsMemoryRunner' .",
                            "security_risk": "LOW",
                        }
                    )
                ),
                thought=[text("错误地把 terminal 参数发给 function_name")],
            )
        )
        self.callback(
            fake_event(
                "ConversationErrorEvent",
                id="fake-misrouted-error",
                source="environment",
                error="Tool 'function_name' not found. Available: ['terminal', 'finish', 'think']",
            )
        )


class FakeFailedBarePytestConversation(FakePreferenceRepairConversation):
    def run(self):
        self.run_count += 1
        self.callback(
            fake_event(
                "ActionEvent",
                id="fake-bare-pytest-action",
                source="agent",
                tool_name="terminal",
                tool_call_id="call-pytest",
                tool_call=SimpleNamespace(
                    arguments=json.dumps(
                        {
                            "command": "pytest tests/test_core.py",
                            "security_risk": "LOW",
                        }
                    )
                ),
                thought=[text("错误地直接调用 pytest")],
            )
        )
        self.callback(
            fake_event(
                "ObservationEvent",
                id="fake-bare-pytest-result",
                source="environment",
                tool_name="terminal",
                tool_call_id="call-pytest",
                observation=SimpleNamespace(
                    content=[text("bash: pytest: command not found")],
                    is_error=True,
                    exit_code=127,
                ),
            )
        )


class FakeIncompleteProceduralAnswerConversation(FakeConversation):
    def run(self):
        self.callback(
            fake_event(
                "MessageEvent",
                id="fake-incomplete-procedural-answer",
                source="agent",
                llm_message=SimpleNamespace(
                    role="assistant",
                    content=[text("已经定位到 service.py 文件。")],
                ),
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
        assert result["event_summary"]["procedural_memories"] == 1
        assert result["duration_ms"] >= 0.0
        assert result["context"]["trace_id"] == result["trace_id"]
        assert set(result["context"]["selected_memory_ids"]) == {
            "planning",
            "tool_selection",
            "response_generation",
        }
        assert set(result["context"]["selected_subtypes"]) == {
            "planning",
            "tool_selection",
            "response_generation",
        }
        assert set(result["context"]["selected_values"]) == {
            "planning",
            "tool_selection",
            "response_generation",
        }
        assert created["workspace"] == (tmp_path / "workspace").resolve()
        assert created["context_file"].is_file()
        assert created["callback"].__self__ is runner
        assert created["max_iterations"] == 8
        assert created["tool_profile"] == "default"
        stored_context = json.loads(created["context_file"].read_text(encoding="utf-8"))
        assert all(
            stage["budget"]["token_budget"] == 512
            for stage in stored_context["stages"].values()
        )
        events = service.meta.list_agent_events(session_id="session-runner-b")
        assert [item["event_type"] for item in events] == [
            "user_message",
            "tool_call",
            "tool_result",
            "assistant_message",
        ]
        memories = service.list_memories(include_deleted=False)
        assert any(item["subtype"] == "procedural_trace" for item in memories)
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


def test_runner_performs_one_bounded_preference_repair_without_user_memory_pollution(
    tmp_path,
):
    service = make_service(tmp_path)
    created = {}
    service.ingest_interaction(
        interaction_event(
            "以后搜索项目代码优先使用 rg。",
            "evt-preference-repair-history",
            1,
            session_id="session-preference-history",
        )
    )

    def conversation_factory(**kwargs):
        conversation = FakePreferenceRepairConversation(kwargs["callback"])
        created["conversation"] = conversation
        return conversation

    try:
        runner = OpenHandsMemoryRunner(
            service=service,
            session_id="session-preference-repair",
            workspace=tmp_path / "workspace",
            state_dir=tmp_path / "state",
            conversation_factory=conversation_factory,
        )

        result = runner.run("请查找 format_agent_context 的定义位置。")

        assert result["adherence"]["repair_attempted"] is True
        assert result["adherence"]["repair_attempt_count"] == 1
        assert result["adherence"]["initial_issues"][0]["topic"] == "code_search_tool"
        assert result["adherence"]["remaining_issues"] == []
        assert created["conversation"].run_count == 2
        events = service.meta.list_agent_events(
            session_id="session-preference-repair"
        )
        repair_events = [
            event
            for event in events
            if "MemGuardOS 合规修复" in str(event.get("content") or "")
        ]
        assert len(repair_events) == 1
        assert repair_events[0]["role"] == "system"
        assert repair_events[0]["event_type"] == "system_message"
    finally:
        service.close()


def test_runner_repairs_response_order_by_reusing_successful_command(tmp_path):
    service = make_service(tmp_path)
    service.ingest_interaction(
        interaction_event(
            "以后回答先给命令，再解释原因。",
            "evt-response-order-history",
            1,
            session_id="session-response-order-history",
        )
    )

    def conversation_factory(**kwargs):
        return FakeResponseOrderRepairConversation(kwargs["callback"])

    try:
        runner = OpenHandsMemoryRunner(
            service=service,
            session_id="session-response-order-repair",
            workspace=tmp_path / "workspace",
            state_dir=tmp_path / "state",
            conversation_factory=conversation_factory,
        )

        result = runner.run("如何在 Linux 中显示当前工作目录？")

        assert result["adherence"]["repair_attempted"] is True
        assert result["adherence"]["initial_issues"] == [
            {
                "topic": "response_order",
                "expected_value": "command_then_explanation",
                "reason": "最终回答未先呈现已执行或建议的命令",
            }
        ]
        assert result["adherence"]["remaining_issues"] == []
        events = service.meta.list_agent_events(
            session_id="session-response-order-repair"
        )
        repair = next(
            event
            for event in events
            if "MemGuardOS 合规修复" in str(event.get("content") or "")
        )
        assert "`pwd`" in repair["content"]
        assert repair["role"] == "system"
    finally:
        service.close()


def test_runner_recovers_strict_read_only_structured_tool_intent(tmp_path):
    service = make_service(tmp_path)
    service.ingest_interaction(
        interaction_event(
            "以后搜索项目代码优先使用 rg。",
            "evt-structured-recovery-history",
            1,
            session_id="session-structured-recovery-history",
        )
    )
    executions = []

    def recovery_executor(argv, **kwargs):
        executions.append((argv, kwargs))
        return SimpleNamespace(
            returncode=0,
            stdout="memguard/integrations/openhands_runner.py:1",
            stderr="",
        )

    def conversation_factory(**kwargs):
        return FakeStructuredIntentConversation(kwargs["callback"])

    try:
        runner = OpenHandsMemoryRunner(
            service=service,
            session_id="session-structured-recovery",
            workspace=tmp_path / "workspace",
            state_dir=tmp_path / "state",
            conversation_factory=conversation_factory,
            structured_intent_executor=recovery_executor,
        )

        result = runner.run("请查找 format_agent_context 的定义位置。")

        assert result["structured_intent_recovery"]["attempted"] is True
        assert result["structured_intent_recovery"]["executed"] is True
        assert result["structured_intent_recovery"]["command"] == (
            "rg -n 'format_agent_context' ."
        )
        assert result["adherence"]["repair_attempted"] is False
        assert result["adherence"]["remaining_issues"] == []
        assert executions[0][0] == ["rg", "-n", "format_agent_context", "."]
        assert executions[0][1]["cwd"] == (tmp_path / "workspace").resolve()
        events = service.meta.list_agent_events(
            session_id="session-structured-recovery"
        )
        recovered_call = next(
            event
            for event in events
            if event["event_type"] == "tool_call"
            and event["tool_args"].get("execution_origin")
            == "structured_intent_recovery"
        )
        assert recovered_call["tool_args"]["command"] == (
            "rg -n 'format_agent_context' ."
        )
    finally:
        service.close()


def test_runner_does_not_reexecute_structured_intent_after_successful_preferred_tool(
    tmp_path,
):
    service = make_service(tmp_path)
    service.ingest_interaction(
        interaction_event(
            "以后搜索项目代码优先使用 rg。",
            "evt-no-duplicate-recovery-history",
            1,
            session_id="session-no-duplicate-recovery-history",
        )
    )
    executions = []

    def recovery_executor(argv, **kwargs):
        executions.append((argv, kwargs))
        raise AssertionError("successful preferred tool must not be re-executed")

    def conversation_factory(**kwargs):
        return FakeSuccessfulToolWithStructuredAnswerConversation(kwargs["callback"])

    try:
        runner = OpenHandsMemoryRunner(
            service=service,
            session_id="session-no-duplicate-recovery",
            workspace=tmp_path / "workspace",
            state_dir=tmp_path / "state",
            conversation_factory=conversation_factory,
            structured_intent_executor=recovery_executor,
        )

        result = runner.run("请查找 build_agent_context 的定义位置。")

        assert result["ok"] is True
        assert result["structured_intent_recovery"]["attempted"] is False
        assert (
            result["structured_intent_recovery"]["reason"]
            == "successful preferred tool already available"
        )
        assert executions == []
    finally:
        service.close()


def test_runner_recovers_structured_intent_emitted_after_bounded_repair(tmp_path):
    service = make_service(tmp_path)
    service.ingest_interaction(
        interaction_event(
            "以后搜索项目代码优先使用 rg。",
            "evt-after-repair-history",
            1,
            session_id="session-after-repair-history",
        )
    )

    def recovery_executor(argv, **kwargs):
        return SimpleNamespace(
            returncode=0,
            stdout="memguard/integrations/openhands_runner.py:1",
            stderr="",
        )

    def conversation_factory(**kwargs):
        return FakeStructuredIntentAfterRepairConversation(kwargs["callback"])

    try:
        runner = OpenHandsMemoryRunner(
            service=service,
            session_id="session-after-repair",
            workspace=tmp_path / "workspace",
            state_dir=tmp_path / "state",
            conversation_factory=conversation_factory,
            structured_intent_executor=recovery_executor,
        )

        result = runner.run("请查找 format_agent_context 的定义位置。")

        assert result["adherence"]["repair_attempted"] is True
        assert result["structured_intent_recovery"]["attempted"] is True
        assert result["structured_intent_recovery"]["executed"] is True
        assert result["adherence"]["remaining_issues"] == []
    finally:
        service.close()


def test_runner_recovers_inline_search_command_emitted_after_repair(tmp_path):
    service = make_service(tmp_path)
    service.ingest_interaction(
        interaction_event(
            "以后搜索项目代码优先使用 rg。",
            "evt-inline-recovery-history",
            1,
            session_id="session-inline-recovery-history",
        )
    )
    executions = []

    def recovery_executor(argv, **kwargs):
        executions.append((argv, kwargs))
        return SimpleNamespace(
            returncode=0,
            stdout="memguard/integrations/openhands_runner.py:1",
            stderr="",
        )

    def conversation_factory(**kwargs):
        return FakeInlineSearchIntentAfterRepairConversation(kwargs["callback"])

    try:
        runner = OpenHandsMemoryRunner(
            service=service,
            session_id="session-inline-recovery",
            workspace=tmp_path / "workspace",
            state_dir=tmp_path / "state",
            conversation_factory=conversation_factory,
            structured_intent_executor=recovery_executor,
        )

        result = runner.run("请查找 format_agent_context 的定义位置。")

        assert result["structured_intent_recovery"]["attempted"] is True
        assert result["structured_intent_recovery"]["executed"] is True
        assert executions[0][0] == ["rg", "-n", "format_agent_context", "."]
        assert result["adherence"]["remaining_issues"] == []
    finally:
        service.close()


def test_runner_recovers_safe_search_command_misrouted_to_unknown_tool(tmp_path):
    service = make_service(tmp_path)
    service.ingest_interaction(
        interaction_event(
            "以后搜索项目代码优先使用 rg。",
            "evt-misrouted-tool-history",
            1,
            session_id="session-misrouted-tool-history",
        )
    )
    executions = []

    def recovery_executor(argv, **kwargs):
        executions.append((argv, kwargs))
        return SimpleNamespace(
            returncode=0,
            stdout="memguard/integrations/openhands_runner.py:33",
            stderr="",
        )

    def conversation_factory(**kwargs):
        return FakeMisroutedFunctionToolConversation(kwargs["callback"])

    try:
        runner = OpenHandsMemoryRunner(
            service=service,
            session_id="session-misrouted-tool",
            workspace=tmp_path / "workspace",
            state_dir=tmp_path / "state",
            conversation_factory=conversation_factory,
            structured_intent_executor=recovery_executor,
        )

        result = runner.run("请查找 OpenHandsMemoryRunner 的定义位置。")

        assert result["ok"] is True
        assert result["structured_intent_recovery"]["attempted"] is True
        assert result["structured_intent_recovery"]["executed"] is True
        assert executions[0][0] == ["rg", "-n", "OpenHandsMemoryRunner", "."]
        assert result["event_summary"]["task_errors"] == 1
        assert result["event_summary"]["recovered_task_errors"] == 1
        assert result["adherence"]["remaining_issues"] == []
    finally:
        service.close()


def test_runner_recovers_bare_pytest_when_test_runner_preference_is_selected(tmp_path):
    service = make_service(tmp_path)
    service.ingest_interaction(
        interaction_event(
            "以后运行 Python 测试时优先使用 python -m pytest，不要直接用 pytest。",
            "evt-test-runner-history",
            1,
            session_id="session-test-runner-history",
        )
    )
    workspace = tmp_path / "workspace"
    (workspace / "tests").mkdir(parents=True)
    (workspace / "tests" / "test_core.py").write_text(
        "def test_placeholder():\n    assert True\n",
        encoding="utf-8",
    )
    executions = []

    def recovery_executor(argv, **kwargs):
        executions.append((argv, kwargs))
        return SimpleNamespace(returncode=0, stdout="1 passed", stderr="")

    def conversation_factory(**kwargs):
        return FakeFailedBarePytestConversation(kwargs["callback"])

    try:
        runner = OpenHandsMemoryRunner(
            service=service,
            session_id="session-test-runner",
            workspace=workspace,
            state_dir=tmp_path / "state",
            conversation_factory=conversation_factory,
            structured_intent_executor=recovery_executor,
        )

        result = runner.run("请复验核心模块测试。")

        assert result["structured_intent_recovery"]["attempted"] is True
        assert result["structured_intent_recovery"]["executed"] is True
        assert result["structured_intent_recovery"]["command"] == (
            "python -m pytest tests/test_core.py"
        )
        project_python = Path.cwd() / ".venv" / "bin" / "python"
        expected_python = (
            str(project_python.absolute()) if project_python.exists() else sys.executable
        )
        assert executions[0][0] == [
            expected_python,
            "-m",
            "pytest",
            "tests/test_core.py",
        ]
        events = service.meta.list_agent_events(session_id="session-test-runner")
        assert any(
            event["event_type"] == "assistant_message"
            and "python -m pytest tests/test_core.py" in event["content"]
            for event in events
        )
    finally:
        service.close()


def test_test_runner_recovery_prefers_project_test_virtualenv(tmp_path, monkeypatch):
    project_python = tmp_path / ".venv" / "bin" / "python"
    project_python.parent.mkdir(parents=True)
    project_python.write_text("# fake python\n", encoding="utf-8")
    workspace = tmp_path / "workspace"
    (workspace / "tests").mkdir(parents=True)
    (workspace / "tests" / "test_core.py").write_text(
        "def test_placeholder():\n    assert True\n",
        encoding="utf-8",
    )
    monkeypatch.chdir(tmp_path)
    events = [
        {
            "event_type": "tool_call",
            "tool_name": "terminal",
            "tool_args": {"command": "pytest tests/test_core.py"},
        },
        {
            "event_type": "tool_result",
            "exit_code": 127,
            "tool_result": "pytest: command not found",
        },
    ]

    intent = recover_failed_test_runner_intent(
        events,
        preferred_command="python -m pytest",
        workspace=workspace,
    )

    assert intent["command"] == "python -m pytest tests/test_core.py"
    assert intent["argv"][0] == str(project_python.absolute())
    assert intent["argv"][1:] == ["-m", "pytest", "tests/test_core.py"]


def test_test_runner_recovery_finds_virtualenv_in_workspace_ancestors(
    tmp_path,
    monkeypatch,
):
    project = tmp_path / "project"
    project_python = project / ".venv" / "bin" / "python"
    project_python.parent.mkdir(parents=True)
    project_python.write_text("# fake python\n", encoding="utf-8")
    workspace = project / "outputs" / "suite" / "cases" / "R01" / "workspace"
    (workspace / "tests").mkdir(parents=True)
    (workspace / "tests" / "test_core.py").write_text(
        "def test_placeholder():\n    assert True\n",
        encoding="utf-8",
    )
    monkeypatch.chdir(tmp_path)
    events = [
        {
            "event_type": "tool_call",
            "tool_name": "terminal",
            "tool_args": {"command": "pytest tests/test_core.py"},
        },
        {"event_type": "tool_result", "exit_code": 127},
    ]

    intent = recover_failed_test_runner_intent(
        events,
        preferred_command="python -m pytest",
        workspace=workspace,
    )

    assert intent["argv"][0] == str(project_python.absolute())


def test_runner_resets_per_run_metrics_when_instance_is_reused(tmp_path):
    service = make_service(tmp_path)

    def conversation_factory(**kwargs):
        return FakeConversation(kwargs["callback"])

    try:
        runner = OpenHandsMemoryRunner(
            service=service,
            session_id="session-reused-runner",
            workspace=tmp_path / "workspace",
            state_dir=tmp_path / "state",
            conversation_factory=conversation_factory,
        )

        first = runner.run("第一次查找 build_agent_context。")
        second = runner.run("第二次查找 build_agent_context。")

        assert first["event_summary"]["tool_calls"] == 1
        assert second["event_summary"]["tool_calls"] == 1
        assert second["event_summary"]["tool_results"] == 1
        assert second["event_summary"]["task_errors"] == 0
    finally:
        service.close()


def test_runner_adds_verified_procedural_result_when_model_answer_is_incomplete(
    tmp_path,
):
    service = make_service(tmp_path)
    service.ingest_tool_result(
        ToolResult(
            command="find memguard -name service.py",
            cwd=".",
            exit_code=0,
            stdout="memguard/service.py",
            tool_name="terminal",
            state_before="查找服务文件",
            state_after="定位成功",
            lesson="后续同类任务复用已验证路径",
        ),
        source_uri="openhands://procedural-history/tool",
    )

    def conversation_factory(**kwargs):
        return FakeIncompleteProceduralAnswerConversation(kwargs["callback"])

    try:
        runner = OpenHandsMemoryRunner(
            service=service,
            session_id="session-procedural-fallback",
            workspace=tmp_path / "workspace",
            state_dir=tmp_path / "state",
            conversation_factory=conversation_factory,
        )

        result = runner.run("请定位名为 service.py 的文件。")

        assert result["procedural_memory_fallback"]["applied"] is True
        assert result["procedural_memory_fallback"]["evidence"] == (
            "memguard/service.py"
        )
        events = service.meta.list_agent_events(
            session_id="session-procedural-fallback"
        )
        fallback = next(
            event
            for event in events
            if event["tool_args"].get("execution_origin")
            == "procedural_memory_fallback"
        )
        assert "memguard/service.py" in fallback["content"]
        assert fallback["role"] == "assistant"
    finally:
        service.close()


def test_adherence_requires_successful_preferred_tool_after_policy_denial():
    stages = {
        "planning": {"memories": []},
        "tool_selection": {
            "memories": [
                {
                    "preference_topic": "code_search_tool",
                    "preference_value": "rg",
                }
            ]
        },
        "response_generation": {"memories": []},
    }
    events = [
        {
            "event_type": "policy_decision",
            "exit_code": 2,
            "tool_args": {
                "decision": "deny",
                "policy_category": "preference_enforcement",
            },
        },
        {
            "event_type": "tool_call",
            "tool_name": "terminal",
            "tool_args": {"command": "ls | grep service.py"},
        },
        {
            "event_type": "policy_decision",
            "exit_code": 0,
            "tool_args": {"decision": "allow"},
        },
        {
            "event_type": "tool_call",
            "tool_name": "terminal",
            "tool_args": {"command": "rg -n service.py .py"},
        },
        {
            "event_type": "tool_result",
            "tool_name": "terminal",
            "exit_code": 2,
            "tool_result": "No such file or directory",
        },
    ]

    assert detect_adherence_issues(stages, events) == [
        {
            "topic": "code_search_tool",
            "expected_value": "rg",
            "reason": "未观察到成功执行的当前偏好工具命令",
        }
    ]
    repaired = repair_failed_preferred_tool_intent(
        events,
        preferred_tool="rg",
    )
    assert repaired == {
        "command": "rg -n service.py .",
        "argv": ["rg", "-n", "service.py", "."],
        "timeout": 10,
    }


def test_failed_rg_repair_drops_files_mode_and_preserves_only_query():
    repaired = repair_failed_preferred_tool_intent(
        [
            {
                "event_type": "tool_call",
                "tool_name": "terminal",
                "tool_args": {
                    "command": (
                        "rg -n 'OpenHandsMemoryRunner' . "
                        "--ignore-case --files"
                    )
                },
            },
            {
                "event_type": "tool_result",
                "tool_name": "terminal",
                "exit_code": 2,
                "tool_result": "invalid argument order",
            },
        ],
        preferred_tool="rg",
    )

    assert repaired == {
        "command": "rg -n -i OpenHandsMemoryRunner .",
        "argv": ["rg", "-n", "-i", "OpenHandsMemoryRunner", "."],
        "timeout": 10,
    }


def test_failed_rg_repair_recovers_missing_binary_attempt():
    repaired = repair_failed_preferred_tool_intent(
        [
            {
                "event_type": "tool_call",
                "tool_name": "terminal",
                "tool_args": {
                    "command": "rg -n 'detect_openhands_runtime' .",
                },
            },
            {
                "event_type": "tool_result",
                "tool_name": "terminal",
                "exit_code": 127,
                "tool_result": "bash: rg: 未找到命令",
            },
        ],
        preferred_tool="rg",
    )

    assert repaired == {
        "command": "rg -n detect_openhands_runtime .",
        "argv": ["rg", "-n", "detect_openhands_runtime", "."],
        "timeout": 10,
    }


def test_structured_intent_recovery_rejects_shell_and_workspace_escape():
    from memguard.integrations.openhands_runner import parse_structured_tool_intent

    assert (
        parse_structured_tool_intent(
            '{"command":"rg -n token .; cat /etc/shadow"}',
            preferred_tool="rg",
        )
        is None
    )
    assert (
        parse_structured_tool_intent(
            '{"command":"rg -n token ../"}',
            preferred_tool="rg",
        )
        is None
    )
    assert (
        parse_structured_tool_intent(
            '{"command":"grep -R token ."}',
            preferred_tool="rg",
        )
        is None
    )
    assert (
        parse_structured_tool_intent(
            '{"command":"rg --pre /bin/cat token ."}',
            preferred_tool="rg",
        )
        is None
    )
    assert (
        parse_structured_tool_intent(
            '{"command":"/tmp/rg -n token ."}',
            preferred_tool="rg",
        )
        is None
    )


def test_misrouted_tool_recovery_rejects_unsafe_or_wrong_preference():
    events = [
        {
            "event_type": "tool_call",
            "tool_name": "function_name",
            "tool_args": {"command": "rg -n token .; cat /etc/shadow"},
        },
        {
            "event_type": "tool_call",
            "tool_name": "function_name",
            "tool_args": {"command": "grep -R token ."},
        },
        {
            "event_type": "tool_call",
            "tool_name": "function_name",
            "tool_args": {"command": "rg -n OpenHandsMemoryRunner ."},
        },
    ]

    assert recover_misrouted_preferred_tool_intent(
        events,
        preferred_tool="rg",
    ) == {
        "command": "rg -n OpenHandsMemoryRunner .",
        "argv": ["rg", "-n", "OpenHandsMemoryRunner", "."],
        "timeout": 10,
    }
    assert (
        recover_misrouted_preferred_tool_intent(
            events[:2],
            preferred_tool="rg",
        )
        is None
    )


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


def test_formatted_agent_context_turns_structured_preferences_into_clear_directives():
    stages = {
        "planning": {"context": "", "memories": []},
        "tool_selection": {
            "context": "evidence",
            "memories": [
                {
                    "preference_topic": "code_search_tool",
                    "preference_value": "rg",
                },
                {
                    "preference_topic": "high_risk_confirmation",
                    "preference_value": "required",
                },
                {
                    "preference_topic": "test_runner_command",
                    "preference_value": "python -m pytest",
                },
                {
                    "preference_topic": "service_log_tool",
                    "preference_value": "journalctl --user -u memguard-agent@luo.service",
                },
            ],
        },
        "response_generation": {
            "context": "evidence",
            "memories": [
                {
                    "preference_topic": "response_order",
                    "preference_value": "command_then_explanation",
                }
            ],
        },
    }

    formatted = format_agent_context("ctx-test", stages)

    assert "代码搜索必须优先使用 `rg`" in formatted
    assert "`rg -n '关键词' .`" in formatted
    assert "高风险操作必须先获得用户确认" in formatted
    assert "Python 测试必须优先使用 `python -m pytest`" in formatted
    assert "用户服务日志优先使用 `journalctl --user -u memguard-agent@luo.service`" in formatted
    assert "最终回答必须先给命令或结论，再解释原因" in formatted


def test_formatted_agent_context_turns_tool_history_into_reuse_and_avoid_guidance():
    stages = {
        "planning": {
            "context": (
                "Tool result: command=find memguard -name service.py "
                "status=success exit_code=0\n"
                "Tool result: command=fd service.py status=failure exit_code=127"
            ),
            "memories": [
                {"subtype": "procedural_trace"},
                {"subtype": "tool_failure_case"},
            ],
        },
        "tool_selection": {"context": "", "memories": []},
        "response_generation": {"context": "", "memories": []},
    }

    formatted = format_agent_context("ctx-procedure", stages)

    assert "优先直接复用 `find memguard -name service.py`" in formatted
    assert "不要重复执行 `fd service.py`" in formatted


def test_selected_values_include_procedural_command_executables():
    result = {
        "memories": [{"subtype": "procedural_trace"}],
        "context": (
            "Tool result: command=rg -n OpenHandsMemoryRunner memguard "
            "status=success exit_code=0 stdout_summary=memguard/integrations/openhands_runner.py"
        ),
    }

    assert "rg" in _selected_values_for_stage(result)


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

    service_restart = subprocess.run(
        [
            sys.executable,
            str(script),
            "--context-file",
            str(context_file),
            "--guard-tool",
        ],
        input=json.dumps(
            {
                "tool_name": "terminal",
                "tool_input": {"command": "sudo systemctl restart memguardd"},
            }
        ),
        text=True,
        capture_output=True,
        check=False,
    )
    assert service_restart.returncode == 2
    service_restart_payload = json.loads(service_restart.stdout)
    assert service_restart_payload["policy_category"] == "high_risk"
    assert service_restart_payload["blocked_marker"] == "systemctl restart"

    relative_rm = subprocess.run(
        [
            sys.executable,
            str(script),
            "--context-file",
            str(context_file),
            "--guard-tool",
        ],
        input=json.dumps(
            {
                "tool_name": "terminal",
                "tool_input": {"command": "rm -rf ./important-data"},
            }
        ),
        text=True,
        capture_output=True,
        check=False,
    )
    assert relative_rm.returncode == 2
    relative_rm_payload = json.loads(relative_rm.stdout)
    assert relative_rm_payload["policy_category"] == "high_risk"
    assert relative_rm_payload["blocked_marker"] == "rm -rf"

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


def test_openhands_hook_enforces_selected_code_search_preference(tmp_path):
    context_file = tmp_path / "context.json"
    context_file.write_text(
        json.dumps(
            {
                "additional_context": "使用已筛选记忆。",
                "allowed_tools": ["terminal", "finish"],
                "stages": {
                    "planning": {"memories": []},
                    "tool_selection": {
                        "memories": [
                            {
                                "preference_topic": "code_search_tool",
                                "preference_value": "rg",
                            }
                        ]
                    },
                    "response_generation": {"memories": []},
                },
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    script = Path(__file__).parents[1] / "scripts" / "openhands_memory_hook.py"

    denied = subprocess.run(
        [
            sys.executable,
            str(script),
            "--context-file",
            str(context_file),
            "--guard-tool",
        ],
        input=json.dumps(
            {
                "tool_name": "terminal",
                "tool_input": {"command": "ls -R | grep OpenHandsMemoryRunner"},
            }
        ),
        text=True,
        capture_output=True,
        check=False,
    )
    allowed = subprocess.run(
        [
            sys.executable,
            str(script),
            "--context-file",
            str(context_file),
            "--guard-tool",
        ],
        input=json.dumps(
            {
                "tool_name": "terminal",
                "tool_input": {
                    "command": "rg -n 'OpenHandsMemoryRunner' ."
                },
            }
        ),
        text=True,
        capture_output=True,
        check=False,
    )

    denied_payload = json.loads(denied.stdout)
    assert denied.returncode == 2
    assert denied_payload["decision"] == "deny"
    assert denied_payload["policy_category"] == "preference_enforcement"
    assert denied_payload["preference_topic"] == "code_search_tool"
    assert denied_payload["expected_value"] == "rg"
    assert allowed.returncode == 0
    assert json.loads(allowed.stdout)["decision"] == "allow"


def test_openhands_hook_blocks_bare_pytest_when_test_runner_preference_is_active(
    tmp_path,
):
    context_file = tmp_path / "context.json"
    context_file.write_text(
        json.dumps(
            {
                "additional_context": "使用已筛选记忆。",
                "allowed_tools": ["terminal", "finish"],
                "stages": {
                    "planning": {"memories": []},
                    "tool_selection": {
                        "memories": [
                            {
                                "preference_topic": "test_runner_command",
                                "preference_value": "python -m pytest",
                            }
                        ]
                    },
                    "response_generation": {"memories": []},
                },
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    script = Path(__file__).parents[1] / "scripts" / "openhands_memory_hook.py"

    denied = subprocess.run(
        [
            sys.executable,
            str(script),
            "--context-file",
            str(context_file),
            "--guard-tool",
        ],
        input=json.dumps(
            {
                "tool_name": "terminal",
                "tool_input": {"command": "pytest tests/test_core.py"},
            }
        ),
        text=True,
        capture_output=True,
        check=False,
    )
    allowed = subprocess.run(
        [
            sys.executable,
            str(script),
            "--context-file",
            str(context_file),
            "--guard-tool",
        ],
        input=json.dumps(
            {
                "tool_name": "terminal",
                "tool_input": {
                    "command": "python -m pytest tests/test_core.py"
                },
            }
        ),
        text=True,
        capture_output=True,
        check=False,
    )

    denied_payload = json.loads(denied.stdout)
    assert denied.returncode == 2
    assert denied_payload["decision"] == "deny"
    assert denied_payload["policy_category"] == "preference_enforcement"
    assert denied_payload["preference_topic"] == "test_runner_command"
    assert denied_payload["expected_value"] == "python -m pytest"
    assert allowed.returncode == 0
    assert json.loads(allowed.stdout)["decision"] == "allow"


def test_openhands_hook_blocks_false_high_risk_finish_completion(tmp_path):
    context_file = tmp_path / "context.json"
    context_file.write_text(
        json.dumps(
            {
                "additional_context": "使用已筛选记忆。",
                "allowed_tools": ["terminal", "finish"],
                "stages": {
                    "planning": {
                        "memories": [
                            {
                                "preference_topic": "high_risk_confirmation",
                                "preference_value": "required",
                            }
                        ]
                    },
                    "tool_selection": {"memories": []},
                    "response_generation": {"memories": []},
                },
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    script = Path(__file__).parents[1] / "scripts" / "openhands_memory_hook.py"

    denied = subprocess.run(
        [
            sys.executable,
            str(script),
            "--context-file",
            str(context_file),
            "--guard-tool",
        ],
        input=json.dumps(
            {
                "tool_name": "finish",
                "tool_input": {
                    "message": "高风险删除操作执行完成。"
                },
            },
            ensure_ascii=False,
        ),
        text=True,
        capture_output=True,
        check=False,
    )
    denied_false_confirmation = subprocess.run(
        [
            sys.executable,
            str(script),
            "--context-file",
            str(context_file),
            "--guard-tool",
        ],
        input=json.dumps(
            {
                "tool_name": "finish",
                "tool_input": {
                    "message": (
                        "您已确认执行删除操作。"
                        "请确认是否要删除当前目录下的所有文件和子目录。"
                    )
                },
            },
            ensure_ascii=False,
        ),
        text=True,
        capture_output=True,
        check=False,
    )
    allowed_refusal = subprocess.run(
        [
            sys.executable,
            str(script),
            "--context-file",
            str(context_file),
            "--guard-tool",
        ],
        input=json.dumps(
            {
                "tool_name": "finish",
                "tool_input": {
                    "message": "该操作属于高风险删除操作，未确认前不能直接执行。"
                },
            },
            ensure_ascii=False,
        ),
        text=True,
        capture_output=True,
        check=False,
    )

    denied_payload = json.loads(denied.stdout)
    assert denied.returncode == 2
    assert denied_payload["policy_category"] == "high_risk"
    assert denied_payload["blocked_marker"] == "finish_false_completion"
    false_confirmation_payload = json.loads(denied_false_confirmation.stdout)
    assert denied_false_confirmation.returncode == 2
    assert false_confirmation_payload["policy_category"] == "high_risk"
    assert false_confirmation_payload["blocked_marker"] == "finish_false_confirmation"
    assert allowed_refusal.returncode == 0
    assert json.loads(allowed_refusal.stdout)["decision"] == "allow"


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
