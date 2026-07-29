import pytest

from memguard.integrations.events import AgentInteractionEvent
from memguard.service import MemGuardService


def _event(content: str) -> AgentInteractionEvent:
    return AgentInteractionEvent(
        agent_name="openhands",
        agent_version="test",
        session_id="forget-natural-tool",
        turn_id="turn-1",
        sequence=1,
        event_type="user_message",
        role="user",
        content=content,
    )


@pytest.mark.parametrize(
    "forget_text",
    [
        "忘记搜索代码使用grep。",
        "忘记使用grep",
        "清除用 grep 查找代码的习惯",
        "删除采用 grep 搜索代码的设置",
    ],
)
def test_natural_tool_forgetting_targets_active_preference_not_tool_trace(tmp_path, forget_text):
    service = MemGuardService(tmp_path / "memory.segment", tmp_path / "memory.sqlite3")
    try:
        interaction = service.ingest_interaction(
            _event("以后搜索项目代码时优先使用 grep。")
        )
        preference_ids = set(interaction["memory_ids"])
        trace = service.ingest_text(
            'Tool result: command=grep -RIn "build_agent_context" /home/luo/memguardos '
            "status=success exit_code=0",
            source_kind="tool_result",
        )["record"]

        preview = service.forget(forget_text, confirm=False)
        candidate_ids = {item["memory_id"] for item in preview["candidates"]}

        assert preview["erasure_plan"]["memory_types"] == ["preference"]
        assert preview["erasure_plan"]["normalized_preference_topic"] == "code_search_tool"
        assert candidate_ids
        assert candidate_ids <= preference_ids
        assert trace.memory_id not in candidate_ids
        assert all(item["preference_topic_match"] for item in preview["candidates"])

        result = service.forget(forget_text, confirm=True, compact=True)
        assert set(result["forgotten"]) == candidate_ids
        assert service.meta.get_memory(trace.memory_id)["deleted"] == 0
        stable_topics = {
            item["topic"]
            for item in service.interaction_status()["preference_aggregates"]
            if item["stable"]
        }
        assert "code_search_tool" not in stable_topics
    finally:
        service.close()


def test_explicit_all_memories_is_not_silently_narrowed_to_tool_preference(tmp_path):
    service = MemGuardService(tmp_path / "memory.segment", tmp_path / "memory.sqlite3")
    try:
        service.ingest_interaction(_event("以后搜索项目代码时优先使用 grep。"))
        trace = service.ingest_text(
            "知识记录：所有记忆中提到使用 grep 的历史工具结果。",
            source_kind="manual",
        )["record"]

        preview = service.forget("忘记所有使用 grep 的记忆", confirm=False)

        assert preview["erasure_plan"]["memory_types"] == ["all"]
        assert preview["erasure_plan"]["normalized_preference_topic"] == ""
        assert trace.memory_id in {item["memory_id"] for item in preview["candidates"]}
    finally:
        service.close()
