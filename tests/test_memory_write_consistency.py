from memguard.service import MemGuardService


def test_mixed_preference_and_fact_create_separate_records(tmp_path):
    service = MemGuardService(tmp_path / "memory.segment", tmp_path / "meta.sqlite3")
    try:
        result = service.ingest_natural_memory(
            "以后搜索项目代码优先使用 rg，白鹭项目发布日是周四。",
            session_id="mixed-1",
        )
        records = [service.meta.get_memory(memory_id) for memory_id in result["memory_ids"]]

        assert result["natural_intent"] == "mixed_preference_knowledge"
        assert any(item["preference_topic"] == "code_search_tool" for item in records)
        assert any(item["type"] == "knowledge" and "发布日是周四" in item["content"] for item in records)
        assert len(result["feedback"]["new_memories"]) >= 2
    finally:
        service.close()


def test_generic_update_cannot_leave_old_structured_preference_value(tmp_path):
    service = MemGuardService(tmp_path / "memory.segment", tmp_path / "meta.sqlite3")
    try:
        created = service.ingest_natural_memory("以后搜索项目代码优先使用 grep。")
        memory_id = next(
            item["memory_id"] for item in service.list_memories()
            if item.get("preference_topic") == "code_search_tool"
        )
        assert created["stored"] is True

        updated = service.update_memory(memory_id, "以后搜索项目代码优先使用 rg。")
        assert updated["updated"] is True
        assert service.meta.get_memory(memory_id)["preference_value"] == "rg"
        selected = service.select_preferences(agent_name="openhands", session_id="new-session")
        assert any(item["preference_value"] == "rg" for item in selected["selected"])

        rejected = service.update_memory(memory_id, "请随便处理一下。")
        assert rejected["updated"] is False
        assert service.meta.get_memory(memory_id)["preference_value"] == "rg"
    finally:
        service.close()


def test_unstructured_preference_feedback_does_not_claim_automatic_application(tmp_path):
    service = MemGuardService(tmp_path / "memory.segment", tmp_path / "meta.sqlite3")
    try:
        result = service.ingest_natural_memory("以后请使用深色主题。")
        assert result["stored"] is True
        item = result["feedback"]["new_memories"][0]
        assert item["application_status"] == "retrieval_only"
        assert item["where_it_will_apply"] == ["相关检索任务"]
        assert service.select_preferences(agent_name="openhands", session_id="next")["selected"] == []
    finally:
        service.close()
