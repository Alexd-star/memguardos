from memguard.integrations.context_compaction import choose_representation, render_representation
from memguard.service import MemGuardService


def memory(**overrides):
    value = {
        "memory_id": "mem-context-1",
        "type": "knowledge",
        "subtype": "fact",
        "tier": "long_term",
        "summary": "项目构建需要先完成依赖检查和测试。",
        "content": "项目构建需要先完成依赖检查和测试，然后再生成发布包。",
        "source_kind": "manual",
        "version_id": "ver-context-1",
        "confidence": 0.92,
    }
    value.update(overrides)
    return value


def test_adaptive_policy_changes_representation_by_stage():
    procedure = memory(subtype="procedural_trace", source_kind="manual")
    preference = memory(
        memory_id="mem-context-preference",
        type="preference",
        subtype="tool_preference",
        preference_topic="code_search_tool",
        preference_value="rg",
    )

    assert choose_representation(procedure, stage="tool_selection")[0] == "full"
    assert choose_representation(procedure, stage="response_generation")[0] == "evidence"
    assert choose_representation(preference, stage="tool_selection")[0] == "evidence"
    assert choose_representation(memory(), stage="planning")[0] == "summary"


def test_evidence_representation_keeps_directive_and_provenance():
    line = render_representation(
        memory(
            type="preference",
            subtype="tool_preference",
            preference_topic="code_search_tool",
            preference_value="rg",
        ),
        representation="evidence",
        selection_reason="applicable scoped preference",
    )

    assert "directive: code_search_tool=rg" in line
    assert "source=manual" in line
    assert "version=ver-context-1" in line
    assert "项目构建需要先完成依赖检查和测试" in line


def test_agent_context_receipt_records_adaptive_compaction(tmp_path):
    service = MemGuardService(tmp_path / "memory.segment", tmp_path / "meta.sqlite3")
    try:
        service.ingest_text(
            "项目 Alpha 构建需要先完成依赖检查和测试，然后再生成发布包。",
            source_kind="manual",
        )
        result = service.build_agent_context(
            "查找项目 Alpha 构建流程",
            stage="planning",
            agent_name="test-agent",
            session_id="session-context",
            token_budget=500,
        )

        assert result["compaction"] == "adaptive"
        assert result["context_receipt"]["compaction"] == "adaptive"
        assert result["context_receipt"]["representations"]
        assert result["budget"]["representation_counts"]
        assert result["explainability"]["estimated_saved_tokens"] >= 0
        assert any(item.get("representation") == "summary" for item in result["memories"])
    finally:
        service.close()
