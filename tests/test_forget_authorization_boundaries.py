from memguard.service import MemGuardService


def test_all_topic_candidates_are_not_limited_to_top_twenty(tmp_path):
    service = MemGuardService(tmp_path / "memory.segment", tmp_path / "meta.sqlite3")
    try:
        ids = {
            service.ingest_text(
                "星桥项目巡检记录：设备编号N%03d的维护日期是本周五。" % index
            )["record"].memory_id
            for index in range(25)
        }
        preview = service.forget("忘记星桥项目的所有记忆")
        assert {item["memory_id"] for item in preview["candidates"]} == ids
    finally:
        service.close()


def test_session_and_topic_do_not_expand_to_every_agent_event(tmp_path):
    service = MemGuardService(tmp_path / "memory.segment", tmp_path / "meta.sqlite3")
    try:
        service.ingest_natural_memory(
            "以后搜索项目代码优先使用 rg。", session_id="sess-42"
        )
        preview = service.forget_by_intent(
            "删除会话 sess-42 中关于 API Key 的所有记忆"
        )
        assert preview["route"] == "managed_memory"
        assert preview["result"]["candidates"] == []
        assert service.meta.list_agent_events(session_id="sess-42")
    finally:
        service.close()


def test_missing_plaintext_probe_cannot_prove_irreversible_erasure(tmp_path):
    service = MemGuardService(tmp_path / "memory.segment", tmp_path / "meta.sqlite3")
    try:
        text = "苍松实验的备用会议室是三楼东侧的第二间。"
        memory_id = service.ingest_natural_memory(text)["record"].memory_id
        result = service.forget_memory_ids([memory_id], reason="user forget", compact=False)

        assert service.store.raw_contains(text)
        assert result["ok"] is False
        assert result["verification"][0]["segment_plaintext_leak"] is True
        assert service.verify_forget(memory_id)["verification_status"] == "unknown"
        assert service.verify_forget(memory_id)["irreversible_ok"] is False
    finally:
        service.close()


def test_forgetting_one_atom_redacts_shared_event_but_keeps_other_atom(tmp_path):
    service = MemGuardService(tmp_path / "memory.segment", tmp_path / "meta.sqlite3")
    try:
        service.ingest_natural_memory(
            "以后搜索项目代码优先使用 grep。回答时先给命令，再解释原因。",
            session_id="retention-check",
        )
        memories = service.list_memories()
        target = next(
            item["memory_id"] for item in memories
            if item.get("preference_topic") == "code_search_tool"
        )
        result = service.forget_memory_ids([target], reason="user forget", compact=True)

        assert result["ok"] is True
        assert result["scrubbed_event_count"] >= 1
        assert all("grep" not in str(item) for item in service.meta.list_agent_events())
        assert any(
            item.get("preference_topic") == "response_order"
            for item in service.list_memories()
        )
    finally:
        service.close()


def test_changed_version_cannot_be_deleted_using_an_old_preview(tmp_path):
    service = MemGuardService(tmp_path / "memory.segment", tmp_path / "meta.sqlite3")
    try:
        memory_id = service.ingest_text("星桥项目发布时间是周二。")["record"].memory_id
        preview = service.forget("忘掉星桥项目发布时间")
        old_version = preview["candidates"][0]["version_id"]
        service.update_memory(memory_id, "星桥项目发布时间是周四。")

        result = service.forget_memory_ids(
            [memory_id], reason="old preview", expected_versions={memory_id: old_version}
        )
        assert result["reason"] == "stale_preview"
        assert result["forgotten_memory_ids"] == []
        assert service.meta.get_memory(memory_id)["deleted"] == 0
    finally:
        service.close()


def test_unresolved_forget_intent_does_not_delete_anything(tmp_path):
    service = MemGuardService(tmp_path / "memory.segment", tmp_path / "meta.sqlite3")
    try:
        record = service.ingest_text("星桥项目发布时间是周四。")["record"]
        result = service.forget("星桥项目发布时间", confirm=True, compact=True)
        assert result["forgotten"] == []
        assert result["time_window"]["scope_blocked"] is True
        assert service.meta.get_memory(record.memory_id)["deleted"] == 0
    finally:
        service.close()
