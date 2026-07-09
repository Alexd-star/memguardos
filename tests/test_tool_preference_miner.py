from memguard.ingestion.tool_preference import ToolPreferenceMiner
from memguard.ingestion.tool_result import ToolResult
from memguard.service import MemGuardService


def test_tool_preference_miner_turns_success_failure_stats_into_preference():
    miner = ToolPreferenceMiner()
    candidates = miner.mine(
        [
            ToolResult(command="grep -R build_context memguard", exit_code=1, stderr="slow or noisy", duration_ms=2400),
            ToolResult(command="rg -n build_context memguard", exit_code=0, stdout="memguard/service.py:1", duration_ms=180),
            ToolResult(command="rg -n memory_id memguard", exit_code=0, stdout="memguard/models.py:1", duration_ms=160),
        ]
    )

    assert len(candidates) == 1
    candidate = candidates[0]
    assert candidate.domain == "code_search"
    assert candidate.preferred_tool == "rg"
    assert candidate.success_count == 2
    assert candidate.avoid_tools == ["grep"]
    assert "以后进行代码搜索时优先使用 rg" in candidate.to_memory_text()


def test_service_promotes_tool_result_batch_preference_memory(tmp_path):
    service = MemGuardService(store_path=tmp_path / "memory.segment", db_path=tmp_path / "memguard.sqlite3")
    try:
        result = service.ingest_tool_results(
            [
                ToolResult(command="grep -R audit memguard", exit_code=1, stderr="binary file noise", duration_ms=1800),
                ToolResult(command="rg -n audit memguard", exit_code=0, stdout="memguard/service.py:88", duration_ms=190),
                ToolResult(command="rg -n service memguard", exit_code=0, stdout="memguard/service.py:1", duration_ms=170),
            ],
            source_uri="batch_tool_stats.json",
        )
        assert result["stored_count"] == 3
        assert result["preference_count"] == 1

        hits = service.search("代码搜索 优先使用 rg", limit=5)
        assert any(hit["type"] == "preference" and hit["subtype"] == "tool_preference" for hit in hits)
    finally:
        service.close()
