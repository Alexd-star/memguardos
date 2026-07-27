from collections import Counter
from pathlib import Path
import json
import subprocess
import sys

from memguard.eval.memory_value import (
    aggregate_paired_results,
    evaluate_memory_value_cases,
    evaluate_paired_case,
    normalize_command,
    summarize_group,
)

ROOT = Path(__file__).resolve().parents[1]


def load_jsonl(path):
    return [
        json.loads(line)
        for line in path.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]


def test_paired_case_reports_memory_value_without_using_no_memory_as_token_baseline():
    result = evaluate_paired_case(
        {
            "case_id": "value-1",
            "groups": {
                "no_memory": {
                    "task_completed": False,
                    "tool_calls": [{"command": "grep target src", "exit_code": 1}],
                    "expected_tool": "rg",
                    "interaction_turns": 3,
                    "prompt_tokens": 40,
                },
                "full_history": {
                    "task_completed": True,
                    "tool_calls": [{"command": "rg target src", "exit_code": 0}],
                    "expected_tool": "rg",
                    "interaction_turns": 2,
                    "prompt_tokens": 500,
                },
                "memguard": {
                    "task_completed": True,
                    "tool_calls": [{"command": "rg target src", "exit_code": 0}],
                    "expected_tool": "rg",
                    "interaction_turns": 1,
                    "prompt_tokens": 120,
                },
            },
        }
    )

    assert result["deltas"]["completion_vs_no_memory"] == 1
    assert result["deltas"]["tool_accuracy_vs_no_memory"] == 1
    assert result["deltas"]["interaction_turns_vs_no_memory"] == -2
    assert result["deltas"]["prompt_tokens_vs_full_history"] == -380
    assert result["deltas"]["prompt_token_reduction_ratio"] == 0.76


def test_group_summary_counts_failures_repeats_and_security_blocks_separately():
    result = summarize_group(
        {
            "task_completed": False,
            "expected_tool": "rg",
            "interaction_turns": 4,
            "prompt_tokens": 90,
            "tool_calls": [
                {"command": " rg   target src ", "exit_code": 2},
                {"command": "rg target src", "exit_code": 2},
                {
                    "command": "rm -rf /tmp/value-test",
                    "exit_code": 126,
                    "blocked_by_policy": True,
                },
            ],
        }
    )

    assert result["first_tool_correct"] is True
    assert result["failed_command_count"] == 2
    assert result["repeated_command_count"] == 1
    assert result["blocked_high_risk_count"] == 1
    assert result["tool_call_count"] == 3


def test_prompt_reduction_ratio_is_none_without_full_history_tokens():
    result = evaluate_paired_case(
        {
            "case_id": "value-zero-history",
            "groups": {
                "no_memory": {"prompt_tokens": 0},
                "full_history": {"prompt_tokens": 0},
                "memguard": {"prompt_tokens": 10},
            },
        }
    )

    assert result["deltas"]["prompt_token_reduction_ratio"] is None


def test_incomplete_memory_run_is_not_reported_as_turn_efficiency_improvement():
    result = evaluate_paired_case(
        {
            "case_id": "value-incomplete",
            "groups": {
                "no_memory": {
                    "task_completed": True,
                    "interaction_turns": 3,
                },
                "full_history": {},
                "memguard": {
                    "task_completed": False,
                    "interaction_turns": 1,
                },
            },
        }
    )

    assert result["deltas"]["interaction_turns_vs_no_memory"] == -2
    assert result["deltas"]["interaction_efficiency_improved"] is False


def test_normalize_command_collapses_whitespace_without_changing_tokens():
    assert normalize_command("  rg   -n  target\tmemguard  ") == "rg -n target memguard"


def test_aggregate_results_recomputes_rates_and_means_from_cases():
    cases = [
        evaluate_paired_case(
            {
                "case_id": "a",
                "groups": {
                    "no_memory": {
                        "task_completed": False,
                        "expected_tool": "rg",
                        "tool_calls": [{"command": "grep x", "exit_code": 1}],
                    },
                    "full_history": {"prompt_tokens": 100},
                    "memguard": {
                        "task_completed": True,
                        "expected_tool": "rg",
                        "tool_calls": [{"command": "rg x", "exit_code": 0}],
                        "prompt_tokens": 40,
                    },
                },
            }
        ),
        evaluate_paired_case(
            {
                "case_id": "b",
                "groups": {
                    "no_memory": {"task_completed": True},
                    "full_history": {"prompt_tokens": 200},
                    "memguard": {"task_completed": True, "prompt_tokens": 100},
                },
            }
        ),
    ]

    result = aggregate_paired_results(cases)

    assert result["case_count"] == 2
    assert result["groups"]["no_memory"]["task_completion_rate"] == 0.5
    assert result["groups"]["memguard"]["task_completion_rate"] == 1.0
    assert result["mean_deltas"]["completion_vs_no_memory"] == 0.5
    assert result["mean_deltas"]["prompt_token_reduction_ratio"] == 0.55


def test_memory_value_dataset_has_sixteen_balanced_cases():
    cases = load_jsonl(ROOT / "data" / "benchmarks" / "agent_memory_value.jsonl")

    assert len(cases) == 16
    assert Counter(case["category"] for case in cases) == {
        "user_correction": 2,
        "implicit_habit": 2,
        "conflict_override": 2,
        "repair_experience": 2,
        "risk_confirmation": 2,
        "cross_session": 2,
        "precise_forgetting": 2,
        "role_pollution": 2,
    }
    assert [case["case_id"] for case in cases] == [
        "D%02d" % index for index in range(1, 17)
    ]
    assert all(
        set(case["groups"]) == {"no_memory", "full_history", "memguard"}
        for case in cases
    )
    assert all(case["history_events"] for case in cases)


def test_all_deterministic_cases_validate_memguard_system_behavior(tmp_path):
    cases = load_jsonl(ROOT / "data" / "benchmarks" / "agent_memory_value.jsonl")

    result = evaluate_memory_value_cases(cases, tmp_path)

    assert result["aggregate"]["case_count"] == 16
    assert result["evidence_class"] == "deterministic_authored_fixture"
    assert result["aggregate"]["arm_source"] == "authored_fixture"
    assert result["aggregate"]["inference"]["task_completion"]["paired_test"]["method"] == "mcnemar_exact_two_sided"
    assert result["system_validation"]["passed"] == 16
    assert result["system_validation"]["failed"] == 0
    assert result["failures"] == []


def test_memory_value_cli_writes_traceable_json_and_chinese_report(tmp_path):
    output_dir = tmp_path / "evaluation"

    completed = subprocess.run(
        [
            sys.executable,
            str(ROOT / "scripts" / "evaluate_memory_value.py"),
            "--output-dir",
            str(output_dir),
        ],
        text=True,
        capture_output=True,
        check=False,
    )

    assert completed.returncode == 0, completed.stderr
    metrics = json.loads((output_dir / "metrics.json").read_text(encoding="utf-8"))
    cases = json.loads((output_dir / "cases.json").read_text(encoding="utf-8"))
    report = (output_dir / "report.md").read_text(encoding="utf-8")
    assert metrics["case_count"] == 16
    assert metrics["system_validation"]["failed"] == 0
    assert len(cases) == 16
    assert "任务完成率" in report
    assert "Prompt token" in report
    assert "失败与边界" in report
    assert "authored_fixture" in report
    assert "统计推断" in report
    assert "McNemar" in report
