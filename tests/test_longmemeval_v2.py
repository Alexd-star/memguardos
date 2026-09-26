import json

from memguard.integrations.longmemeval_v2 import (
    LONGMEMEVAL_V2_CODE_REVISION,
    LongMemEvalV2Backend,
    evaluate_questions,
    flatten_trajectory,
    validate_context_items,
)


def trajectory_fixture():
    return {
        "id": "trajectory-001",
        "domain": "enterprise",
        "environment": "workarena",
        "goal": "Assign an incident and close the task.",
        "outcome": "success",
        "states": [
            {
                "state_index": 0,
                "url": "https://example.invalid/incidents",
                "thought": "Open the Incidents module.",
                "action": "click('Incidents')",
                "accessibility_tree": "button Incidents; option My Open Incidents; option Incident Portal",
            },
            {
                "state_index": 1,
                "thought": "Select Closed Complete.",
                "action": "select('State', 'Closed Complete')",
                "accessibility_tree": "State: Closed Complete; button Update",
            },
        ],
    }


def test_flatten_trajectory_preserves_provenance_and_operational_evidence():
    chunks = flatten_trajectory(trajectory_fixture(), max_chars=1200)

    assert len(chunks) == 3
    assert chunks[0]["source_uri"] == "longmemeval-v2://trajectory-001/summary"
    assert "Assign an incident" in chunks[0]["text"]
    assert "My Open Incidents" in chunks[1]["text"]
    assert chunks[2]["state_index"] == 1


def test_backend_matches_official_insert_query_context_contract(tmp_path):
    backend = LongMemEvalV2Backend(
        {
            "workspace_dir": str(tmp_path),
            "namespace": "lme-test",
            "context_limit": 4,
        }
    )
    try:
        inserted = backend.insert(trajectory_fixture())
        backend.set_query_context(
            {"question_id": "question-1", "question_type": "dynamic-environment"}
        )
        contexts = backend.query("Which Incident filter options are available?")
    finally:
        backend.close()

    assert inserted["stored_count"] == 3
    assert validate_context_items(contexts) == []
    assert contexts
    assert all(item["type"] == "text" and item["value"].strip() for item in contexts)
    assert any("Incident" in item["value"] for item in contexts)


def test_external_evaluation_separates_retrieval_proxy_from_answer_accuracy(tmp_path, monkeypatch):
    trajectories = tmp_path / "trajectories.jsonl"
    questions = tmp_path / "questions.jsonl"
    trajectories.write_text(json.dumps(trajectory_fixture()) + "\n", encoding="utf-8")
    questions.write_text(
        json.dumps(
            {
                "id": "question-1",
                "domain": "enterprise",
                "question_type": "dynamic-environment",
                "question": "Which Incident filter option is present?",
                "answer": "My Open Incidents",
                "eval_function": "norm_phrase_set_match",
            }
        )
        + "\n",
        encoding="utf-8",
    )

    original_query = LongMemEvalV2Backend.query

    def query_without_labels(self, query, query_image=None):
        assert self.get_query_context() == {
            "question_id": "question-1",
            "question_type": "dynamic-environment",
        }
        return original_query(self, query, query_image=query_image)

    monkeypatch.setattr(LongMemEvalV2Backend, "query", query_without_labels)
    result = evaluate_questions(
        trajectories_path=trajectories,
        questions_path=questions,
        work_dir=tmp_path / "work",
        limit=1,
    )

    assert result["provenance"]["code_revision"] == LONGMEMEVAL_V2_CODE_REVISION
    assert result["retrieval_layer"]["question_count"] == 1
    assert result["retrieval_layer"]["context_schema_valid_rate"] == 1.0
    assert result["retrieval_layer"]["answer_lexical_coverage_rate"] == 1.0
    assert result["answer_layer"] == {
        "status": "not_run",
        "accuracy": None,
        "reason": "reader and official answer evaluator were not configured",
    }
    assert result["retrieval_layer"]["p95_latency_ms"] >= 0


def test_backend_discards_reference_labels_from_external_query_context(tmp_path):
    backend = LongMemEvalV2Backend({"workspace_dir": str(tmp_path), "namespace": "label-isolation"})
    try:
        backend.set_query_context({
            "question_id": "q-1",
            "raw_question_item": {"answer": "secret answer"},
            "answer": "secret answer",
            "eval_function": "exact_match",
        })
        assert backend.get_query_context() == {"question_id": "q-1"}
    finally:
        backend.close()
