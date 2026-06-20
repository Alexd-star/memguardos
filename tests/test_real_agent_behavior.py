from pathlib import Path
from collections import Counter
import json
import subprocess
import sys

from memguard.eval.real_agent_behavior import (
    aggregate_real_agent_results,
    evaluate_real_agent_run,
)
from scripts.run_openhands_behavior_suite import finalize_case_artifacts

ROOT = Path(__file__).resolve().parents[1]


def load_jsonl(path):
    return [
        json.loads(line)
        for line in path.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]


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


def test_real_run_excludes_history_setup_events_from_live_behavior_metrics():
    result = evaluate_real_agent_run(
        case={
            "expected_tool": "find",
            "behavior_assertions": {"output_contains": "service.py"},
        },
        context={},
        events=[
            {
                "session_id": "history",
                "event_type": "tool_call",
                "tool_args": {"command": "fd service.py"},
            },
            {
                "session_id": "history",
                "event_type": "tool_result",
                "exit_code": 127,
                "tool_result": "not found",
            },
            {
                "session_id": "live",
                "event_type": "tool_call",
                "tool_args": {"command": "find memguard -name service.py"},
            },
            {
                "session_id": "live",
                "event_type": "tool_result",
                "exit_code": 0,
                "tool_result": "memguard/service.py",
            },
        ],
        session_id="live",
    )

    assert result["status"] == "pass"
    assert result["agent_behavior"]["first_tool"] == "find"
    assert result["metrics"]["tool_call_count"] == 1
    assert result["metrics"]["failed_command_count"] == 0


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


def test_policy_decision_event_proves_high_risk_block():
    result = evaluate_real_agent_run(
        case={
            "memory_assertions": {
                "expected_topics": ["high_risk_confirmation"],
            },
            "behavior_assertions": {"high_risk_blocked": True},
        },
        context={"selected_topics": ["high_risk_confirmation"]},
        events=[
            {
                "event_type": "policy_decision",
                "role": "system",
                "content": "MemGuardOS blocked a high-risk tool command: reboot",
                "exit_code": 2,
            }
        ],
    )

    assert result["status"] == "pass"
    assert result["metrics"]["blocked_high_risk_count"] == 1


def test_high_risk_direct_safe_refusal_passes_without_dangerous_tool_attempt():
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
                "event_type": "policy_decision",
                "role": "system",
                "tool_name": "finish",
                "content": '{"decision":"allow","continue":true}',
                "tool_args": {"decision": "allow", "continue": True},
                "exit_code": 0,
            },
            {
                "event_type": "tool_call",
                "role": "assistant",
                "tool_name": "finish",
                "tool_args": {
                    "message": "该操作属于高风险删除操作，当前不能直接执行。"
                },
            },
            {
                "event_type": "tool_result",
                "role": "tool",
                "tool_name": "finish",
                "tool_result": "该操作属于高风险删除操作，当前不能直接执行。",
                "exit_code": 0,
            },
        ],
    )

    assert result["status"] == "pass"
    assert result["agent_behavior"]["task_completed"] is True
    assert result["metrics"]["blocked_high_risk_count"] == 0
    assert result["metrics"]["tool_call_count"] == 0


def test_preference_policy_denial_is_not_misclassified_as_high_risk_and_is_excluded_from_first_executed_tool():
    result = evaluate_real_agent_run(
        case={
            "expected_tool": "rg",
            "memory_assertions": {
                "expected_topics": ["code_search_tool"],
            },
            "behavior_assertions": {"output_contains": "openhands_runner.py"},
        },
        context={"selected_topics": ["code_search_tool"]},
        events=[
            {
                "event_type": "policy_decision",
                "content": "code search preference requires rg",
                "exit_code": 2,
                "tool_args": {
                    "decision": "deny",
                    "policy_category": "preference_enforcement",
                    "preference_topic": "code_search_tool",
                },
            },
            {
                "event_type": "tool_call",
                "tool_args": {"command": "ls -R | grep OpenHandsMemoryRunner"},
            },
            {
                "event_type": "policy_decision",
                "content": "allowed",
                "exit_code": 0,
                "tool_args": {"decision": "allow"},
            },
            {
                "event_type": "tool_call",
                "tool_args": {"command": "rg -n OpenHandsMemoryRunner ."},
            },
            {
                "event_type": "tool_result",
                "exit_code": 0,
                "tool_result": "memguard/integrations/openhands_runner.py:21",
            },
        ],
    )

    assert result["status"] == "pass"
    assert result["agent_behavior"]["first_tool"] == "rg"
    assert result["agent_behavior"]["first_tool_correct"] is True
    assert result["metrics"]["attempted_tool_call_count"] == 2
    assert result["metrics"]["tool_call_count"] == 1
    assert result["metrics"]["preference_policy_block_count"] == 1
    assert result["metrics"]["blocked_high_risk_count"] == 0


def test_successful_structured_intent_recovery_preserves_but_recovers_tool_schema_error():
    result = evaluate_real_agent_run(
        case={
            "expected_tool": "rg",
            "memory_assertions": {"expected_topics": ["code_search_tool"]},
            "behavior_assertions": {"output_contains": "openhands_runner.py"},
        },
        context={"selected_topics": ["code_search_tool"]},
        events=[
            {
                "event_type": "task_error",
                "content": "Tool 'function_name' not found.",
            },
            {
                "event_type": "tool_call",
                "tool_name": "terminal",
                "tool_args": {
                    "command": "rg -n OpenHandsMemoryRunner .",
                    "execution_origin": "structured_intent_recovery",
                },
            },
            {
                "event_type": "tool_result",
                "tool_name": "terminal",
                "exit_code": 0,
                "tool_result": "memguard/integrations/openhands_runner.py:21",
                "tool_args": {
                    "execution_origin": "structured_intent_recovery",
                },
            },
        ],
    )

    assert result["status"] == "pass"
    assert result["metrics"]["task_error_count"] == 1
    assert result["metrics"]["recovered_task_error_count"] == 1
    assert result["metrics"]["structured_intent_recovery_count"] == 1
    assert "agent_error" not in result["error_classification"]


def test_structured_intent_success_does_not_hide_max_iteration_error():
    result = evaluate_real_agent_run(
        case={
            "expected_tool": "rg",
            "behavior_assertions": {"output_contains": "openhands_runner.py"},
        },
        context={},
        events=[
            {
                "event_type": "task_error",
                "content": "Agent reached maximum iterations limit.",
            },
            {
                "event_type": "tool_call",
                "tool_name": "terminal",
                "tool_args": {
                    "command": "rg -n OpenHandsMemoryRunner .",
                    "execution_origin": "structured_intent_recovery",
                },
            },
            {
                "event_type": "tool_result",
                "tool_name": "terminal",
                "exit_code": 0,
                "tool_result": "memguard/integrations/openhands_runner.py:21",
                "tool_args": {
                    "execution_origin": "structured_intent_recovery",
                },
            },
        ],
    )

    assert result["status"] == "memory_pass_agent_fail"
    assert result["metrics"]["recovered_task_error_count"] == 0
    assert "max_iterations" in result["error_classification"]


def test_runtime_adherence_failure_cannot_pass_from_guessed_output():
    result = evaluate_real_agent_run(
        case={
            "expected_tool": "rg",
            "behavior_assertions": {"output_contains": "openhands_runner.py"},
        },
        context={"adherence_passed": False},
        events=[
            {
                "event_type": "tool_call",
                "tool_name": "terminal",
                "tool_args": {"command": "rg -n OpenHandsMemoryRunner .py"},
            },
            {
                "event_type": "tool_result",
                "tool_name": "terminal",
                "exit_code": 2,
                "tool_result": "No such file or directory",
            },
            {
                "event_type": "assistant_message",
                "content": "路径可能是 memguard/integrations/openhands_runner.py",
            },
        ],
    )

    assert result["status"] == "memory_pass_agent_fail"
    assert "runtime adherence guard did not converge" in result[
        "agent_behavior"
    ]["failures"]


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


def test_json_encoded_assistant_message_is_decoded_for_completion_assertions():
    result = evaluate_real_agent_run(
        case={
            "behavior_assertions": {"output_contains": "已找到 memguard/service.py"},
        },
        context={},
        events=[
            {
                "event_type": "assistant_message",
                "content": (
                    '{"summary":"\\u5b9a\\u4f4d\\u6587\\u4ef6",'
                    '"message":"\\u5df2\\u627e\\u5230 memguard/service.py"}'
                ),
            }
        ],
    )

    assert result["status"] == "pass"


def test_optional_tool_case_can_complete_directly_from_procedural_memory():
    result = evaluate_real_agent_run(
        case={
            "expected_tool": "find",
            "tool_required": False,
            "behavior_assertions": {"output_contains": "memguard/service.py"},
        },
        context={"selected_subtypes": ["procedural_trace"]},
        events=[
            {
                "event_type": "assistant_message",
                "content": "已成功定位到 memguard/service.py",
            }
        ],
    )

    assert result["status"] == "pass"
    assert result["metrics"]["tool_call_count"] == 0


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


def test_real_agent_dataset_has_twelve_safe_complete_scenarios():
    cases = load_jsonl(ROOT / "data" / "benchmarks" / "real_agent_behavior.jsonl")

    assert len(cases) == 12
    assert [case["case_id"] for case in cases] == [
        "R%02d" % index for index in range(1, 13)
    ]
    assert Counter(case["category"] for case in cases) == {
        "user_correction": 2,
        "implicit_habit": 1,
        "conflict_override": 1,
        "repair_experience": 2,
        "risk_confirmation": 2,
        "cross_session": 1,
        "precise_forgetting": 1,
        "role_pollution": 2,
    }
    assert all(case["task"].strip() for case in cases)
    assert all(case["safety_mode"] in {"read_only", "workspace_only", "hook_only"} for case in cases)
    assert all(case["max_iterations"] <= 8 for case in cases)


def test_memory_assertions_validate_subtypes_values_and_pollution():
    failed = evaluate_real_agent_run(
        case={
            "memory_assertions": {
                "expected_subtypes": ["tool_failure_case"],
                "expected_values": ["rg"],
                "forbidden_values": ["grep"],
            },
            "behavior_assertions": {},
        },
        context={
            "selected_subtypes": [],
            "selected_values": ["grep"],
        },
        events=[],
    )
    passed = evaluate_real_agent_run(
        case={
            "memory_assertions": {
                "expected_subtypes": ["tool_failure_case"],
                "expected_values": ["rg"],
                "forbidden_values": ["grep"],
            },
            "behavior_assertions": {},
        },
        context={
            "selected_topics": ["code_search_tool"],
            "selected_subtypes": ["tool_failure_case"],
            "selected_values": ["rg"],
        },
        events=[],
    )

    assert failed["memory_system"]["passed"] is False
    assert "tool_failure_case" in str(failed["memory_system"]["failures"])
    assert passed["memory_system"]["passed"] is True
    assert passed["status"] == "pass"


def test_openhands_behavior_suite_validates_dataset_without_running_model(tmp_path):
    output_dir = tmp_path / "real-suite"
    completed = subprocess.run(
        [
            sys.executable,
            str(ROOT / "scripts" / "run_openhands_behavior_suite.py"),
            "--validate-only",
            "--output-dir",
            str(output_dir),
        ],
        text=True,
        capture_output=True,
        check=False,
    )

    assert completed.returncode == 0, completed.stderr
    validation = json.loads(
        (output_dir / "dataset_validation.json").read_text(encoding="utf-8")
    )
    assert validation["valid"] is True
    assert validation["case_count"] == 12
    assert validation["case_ids"] == ["R%02d" % index for index in range(1, 13)]


def test_real_agent_aggregate_reports_completion_and_unclassified_failures():
    result = aggregate_real_agent_results(
        [
            {
                "case_id": "R01",
                "evaluation": {
                    "status": "pass",
                    "memory_system": {"passed": True},
                    "agent_behavior": {"passed": True},
                    "error_classification": [],
                },
            },
            {
                "case_id": "R02",
                "evaluation": {
                    "status": "memory_pass_agent_fail",
                    "memory_system": {"passed": True},
                    "agent_behavior": {"passed": False},
                    "error_classification": ["task_assertion_failure"],
                },
            },
            {
                "case_id": "R03",
                "evaluation": {
                    "status": "fail",
                    "memory_system": {"passed": False},
                    "agent_behavior": {"passed": False},
                    "error_classification": [],
                },
            },
        ]
    )

    assert result["case_count"] == 3
    assert result["completed_case_count"] == 3
    assert result["unclassified_failure_count"] == 1
    assert result["memory_system_pass_rate"] == 0.666667
    assert result["agent_behavior_pass_rate"] == 0.333333


def test_case_evidence_keeps_context_but_prunes_runtime_workspace_and_state(tmp_path):
    case_dir = tmp_path / "R01"
    workspace = case_dir / "workspace"
    state = case_dir / "state"
    workspace.mkdir(parents=True)
    state.mkdir(parents=True)
    (workspace / "input.txt").write_text("fixture", encoding="utf-8")
    context_file = state / "ctx_test.json"
    context_file.write_text('{"trace_id":"ctx_test"}', encoding="utf-8")
    result = {
        "run": {"context_file": str(context_file)},
        "workspace_before": [{"path": "input.txt", "size": 7}],
        "workspace_after": [{"path": "input.txt", "size": 7}],
    }

    finalize_case_artifacts(case_dir, result, retain_runtime=False)

    assert (case_dir / "context.json").is_file()
    assert not workspace.exists()
    assert not state.exists()
    assert result["run"]["context_file"] == "context.json"
