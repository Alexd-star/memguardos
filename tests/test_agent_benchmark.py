import json

from memguard.eval.agent_interactions import evaluate_agent_interaction_cases


def test_agent_interaction_benchmark_reports_category_metrics_and_failures(tmp_path):
    cases = [
        {
            "case_id": "explicit-1",
            "category": "explicit_preference",
            "source_origin": "synthetic",
            "events": [
                {
                    "role": "user",
                    "event_type": "user_message",
                    "content": "以后搜索代码优先使用 rg。",
                    "session_id": "s1",
                }
            ],
            "expected_topics": ["code_search_tool"],
        },
        {
            "case_id": "pollution-1",
            "category": "role_pollution",
            "source_origin": "real_anonymized",
            "events": [
                {
                    "role": "assistant",
                    "event_type": "assistant_message",
                    "content": "以后我会优先使用 rg。",
                    "session_id": "s2",
                }
            ],
            "expected_topics": [],
        },
        {
            "case_id": "cross-1",
            "category": "cross_session",
            "source_origin": "synthetic",
            "events": [
                {
                    "role": "user",
                    "event_type": "user_message",
                    "content": "以后回答先给命令，再解释原因。",
                    "session_id": "s3",
                }
            ],
            "expected_topics": ["response_order"],
            "context_stage": "response_generation",
            "context_task": "解释命令",
        },
        {
            "case_id": "security-1",
            "category": "explicit_preference",
            "source_origin": "synthetic",
            "events": [
                {
                    "role": "user",
                    "event_type": "user_message",
                    "content": "删除文件前必须先询问我。",
                    "session_id": "s4",
                }
            ],
            "expected_topics": ["high_risk_confirmation"],
        },
    ]

    result = evaluate_agent_interaction_cases(cases, tmp_path)

    assert result["case_count"] == 4
    assert result["source_distribution"] == {
        "real_anonymized": 1,
        "synthetic": 3,
    }
    assert result["metrics"]["extraction"]["precision"] == 1.0
    assert result["metrics"]["extraction"]["recall"] == 1.0
    assert result["metrics"]["role_pollution_rate"] == 0.0
    assert result["metrics"]["cross_session_adherence"] == 1.0
    assert result["latency_ms"]["p95"] >= 0.0
    assert result["failures"] == []
    json.dumps(result, ensure_ascii=False)
