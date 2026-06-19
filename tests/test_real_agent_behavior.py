from pathlib import Path
from collections import Counter
import json
import subprocess
import sys

from memguard.eval.real_agent_behavior import (
    aggregate_real_agent_results,
    evaluate_real_agent_run,
)

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
