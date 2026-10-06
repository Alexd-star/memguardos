from memguard.eval.action_contract import evaluate_cases


def test_action_contract_benchmark_covers_decision_surface():
    rows = [
        {"id": "a", "tool_name": "terminal", "tool_input": {"command": "pwd"}, "expected_decision": "allow"},
        {
            "id": "r",
            "tool_name": "terminal",
            "tool_input": {"command": "grep -n foo ."},
            "memories": [{"memory_id": "m", "version_id": "v", "preference_topic": "code_search_tool", "preference_value": "rg"}],
            "expected_decision": "redirect",
        },
        {
            "id": "c",
            "tool_name": "terminal",
            "tool_input": {"command": "sudo apt install ripgrep"},
            "memories": [{"memory_id": "m2", "version_id": "v", "preference_topic": "high_risk_confirmation", "preference_value": "required"}],
            "expected_decision": "confirm",
        },
        {"id": "d", "tool_name": "terminal", "tool_input": {"command": "rm -rf /"}, "expected_decision": "deny"},
    ]
    report = evaluate_cases(rows)
    assert report["accuracy"] == 1.0
    assert set(report["expected_counts"]) == {"allow", "redirect", "confirm", "deny"}

