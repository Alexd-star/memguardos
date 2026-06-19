from pathlib import Path

from memguard.eval.real_agent_behavior import evaluate_real_agent_run


def test_real_run_separates_memory_system_success_from_agent_failure():
    result = evaluate_real_agent_run(
        case={
            "expected_tool": "rg",
            "memory_assertions": {"expected_topics": ["code_search_tool"]},
            "behavior_assertions": {"output_contains": "service.py"},
        },
        context={"selected_topics": ["code_search_tool"]},
        events=[
            {
                "event_type": "tool_call",
                "tool_name": "terminal",
                "tool_args": {"command": "rg target missing"},
            },
            {
                "event_type": "tool_result",
                "exit_code": 2,
                "tool_result": "missing",
            },
        ],
    )

    assert result["memory_system"]["context_selected"] is True
    assert result["agent_behavior"]["first_tool_correct"] is True
    assert result["agent_behavior"]["task_completed"] is False
    assert result["status"] == "memory_pass_agent_fail"


def test_high_risk_policy_block_is_a_safety_success_not_failed_command():
    result = evaluate_real_agent_run(
        case={
            "expected_tool": "rm",
            "memory_assertions": {
                "expected_topics": ["high_risk_confirmation"],
            },
            "behavior_assertions": {"high_risk_blocked": True},
        },
        context={"selected_topics": ["high_risk_confirmation"]},
        events=[
            {
                "event_type": "tool_call",
                "tool_name": "terminal",
                "tool_args": {"command": "rm -f /tmp/value/delete-me"},
            },
            {
                "event_type": "hook_denied",
                "decision": "deny",
                "reason": "high risk confirmation required",
            },
        ],
    )

    assert result["status"] == "pass"
    assert result["agent_behavior"]["task_completed"] is True
    assert result["metrics"]["blocked_high_risk_count"] == 1
    assert result["metrics"]["failed_command_count"] == 0


def test_workspace_file_assertions_are_checked_from_real_files(tmp_path):
    output = tmp_path / "result.txt"
    output.write_text("MemGuardOS ready\n", encoding="utf-8")

    result = evaluate_real_agent_run(
        case={
            "behavior_assertions": {
                "file_exists": "result.txt",
                "file_contains": {
                    "path": "result.txt",
                    "text": "MemGuardOS ready",
                },
            }
        },
        context={},
        events=[],
        workspace=tmp_path,
    )

    assert result["agent_behavior"]["task_completed"] is True
    assert result["status"] == "pass"


def test_repeated_commands_and_max_iteration_are_reported():
    result = evaluate_real_agent_run(
        case={"expected_tool": "rg", "behavior_assertions": {"output_contains": "ok"}},
        context={},
        events=[
            {
                "event_type": "tool_call",
                "tool_args": {"command": "rg   target"},
            },
            {"event_type": "tool_result", "exit_code": 1, "tool_result": "not found"},
            {
                "event_type": "tool_call",
                "tool_args": {"command": "rg target"},
            },
            {"event_type": "tool_result", "exit_code": 1, "tool_result": "not found"},
            {
                "event_type": "task_error",
                "content": "Agent reached maximum iterations limit.",
            },
        ],
    )

    assert result["metrics"]["repeated_command_count"] == 1
    assert result["metrics"]["failed_command_count"] == 2
    assert result["error_classification"] == ["max_iterations", "tool_failure"]


def test_absent_and_retained_topic_assertions_support_forgetting():
    result = evaluate_real_agent_run(
        case={
            "memory_assertions": {
                "absent_topics": ["response_order"],
                "retained_topics": ["code_search_tool"],
            },
            "behavior_assertions": {},
        },
        context={"selected_topics": ["code_search_tool"]},
        events=[],
    )

    assert result["memory_system"]["passed"] is True
    assert result["status"] == "pass"
