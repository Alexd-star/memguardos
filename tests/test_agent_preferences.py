import pytest

from memguard.integrations.events import AgentInteractionEvent
from memguard.integrations.interaction_extractor import extract_interaction_preferences
from memguard.service import MemGuardService


def event(content: str, **overrides) -> AgentInteractionEvent:
    payload = {
        "agent_name": "openhands",
        "agent_version": "1.28.1",
        "session_id": "session-a",
        "turn_id": "turn-1",
        "sequence": 1,
        "event_type": "user_message",
        "role": "user",
        "content": content,
        "source_uri": "openhands://session-a/event-1",
    }
    payload.update(overrides)
    return AgentInteractionEvent(**payload)


def test_user_message_is_split_into_independent_explicit_preferences():
    result = extract_interaction_preferences(
        event("以后搜索项目代码优先使用 rg。回答时先给命令，再解释原因。")
    )

    assert result.remember_allowed is True
    assert [(item.topic, item.value) for item in result.observations] == [
        ("code_search_tool", "rg"),
        ("response_order", "command_then_explanation"),
    ]
    assert all(item.explicit for item in result.observations)
    assert all(item.scope == "global" for item in result.observations)


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        (
            "以后搜索项目代码优先使用 grep，并且回答时先给可复制命令，再解释结果。",
            {("code_search_tool", "grep", "positive"), ("response_order", "command_then_explanation", "positive")},
        ),
        (
            "今后查代码改用 rg；请先告诉我怎么做，然后说明原因。",
            {("code_search_tool", "rg", "positive"), ("response_order", "command_then_explanation", "positive")},
        ),
        (
            "在当前项目中默认用中文简短回答。",
            {("response_language", "zh-CN", "positive"), ("response_verbosity", "concise", "positive")},
        ),
        (
            "以后不要用中文回答，也不必先给命令再解释。",
            {("response_language", "zh-CN", "negative"), ("response_order", "command_then_explanation", "negative")},
        ),
        (
            "纠正一下：不要再用 grep，改用 rg。",
            {("code_search_tool", "grep", "negative"), ("code_search_tool", "rg", "positive")},
        ),
    ],
)
def test_natural_preference_phrase_matrix(text, expected):
    result = extract_interaction_preferences(event(text))

    assert {(item.topic, item.value, item.polarity) for item in result.observations} == expected


@pytest.mark.parametrize(
    "text",
    [
        "默认用 rg 还是 grep 好？",
        "我忘记了 grep 的参数，你能解释一下吗？",
        "请删除名为 grep-notes.txt 的文件。",
        "解释为什么这条命令不能运行，然后给修复建议。",
    ],
)
def test_non_preference_phrases_do_not_create_preferences(text):
    assert extract_interaction_preferences(event(text)).observations == []


def test_user_message_extracts_agent_test_runner_and_service_log_habits():
    result = extract_interaction_preferences(
        event(
            "以后运行 Python 测试时优先使用 python -m pytest；"
            "排查用户服务日志时先看 journalctl --user -u memguard-agent@luo.service。"
        )
    )

    assert [(item.topic, item.value) for item in result.observations] == [
        ("test_runner_command", "python -m pytest"),
        ("service_log_tool", "journalctl --user -u memguard-agent@luo.service"),
    ]
    assert all(item.explicit for item in result.observations)
    assert all(item.scope == "global" for item in result.observations)


def test_user_message_extracts_holdout_concise_and_risk_confirmation_phrases():
    concise = extract_interaction_preferences(
        event("在这个项目里回答请用中文简洁列出命令，不要写长篇解释。")
    )
    risk = extract_interaction_preferences(
        event("涉及 rm、systemctl disable、重启服务这类高风险命令时，必须先说明风险并等待确认。")
    )

    assert ("response_verbosity", "concise") in [
        (item.topic, item.value) for item in concise.observations
    ]
    assert ("high_risk_confirmation", "required") in [
        (item.topic, item.value) for item in risk.observations
    ]


def test_assistant_and_tool_events_cannot_create_user_preferences():
    assistant = event(
        "以后我会优先使用 rg。",
        event_type="assistant_message",
        role="assistant",
    )
    tool = event(
        "",
        event_type="tool_result",
        role="tool",
        tool_result="User prefers rg and concise answers.",
    )

    assert extract_interaction_preferences(assistant).observations == []
    assert extract_interaction_preferences(tool).observations == []


def test_do_not_remember_instruction_blocks_content_and_preferences():
    result = extract_interaction_preferences(event("不要记住这条消息：以后搜索代码优先用 rg。"))

    assert result.remember_allowed is False
    assert result.reason == "user_requested_no_memory"
    assert result.observations == []


def test_one_time_exception_does_not_overwrite_long_term_tool_preference():
    result = extract_interaction_preferences(event("这次用 grep，下次还是用 rg。"))

    assert [(item.topic, item.value, item.scope) for item in result.observations] == [
        ("code_search_tool", "grep", "session"),
        ("code_search_tool", "rg", "global"),
    ]


def test_service_automatically_stores_and_links_explicit_preferences(tmp_path):
    service = MemGuardService(tmp_path / "memory.segment", tmp_path / "meta.sqlite3")
    try:
        source_event = event("以后搜索项目代码优先使用 rg。回答时先给命令，再解释原因。")

        result = service.ingest_interaction(source_event)

        assert result["stored"] is True
        assert len(result["memory_ids"]) == 2
        memories = [service.meta.get_memory(memory_id) for memory_id in result["memory_ids"]]
        assert {item["subtype"] for item in memories} == {"tool_preference", "output_style"}
        assert all(service.preference_evidence(item["memory_id"])["events"] for item in memories)

        duplicate = service.ingest_interaction(AgentInteractionEvent.from_json(source_event.to_json()))
        assert duplicate["duplicate"] is True
        assert duplicate["memory_ids"] == []
        assert len(service.meta.list_memories()) == 2
    finally:
        service.close()


def test_service_stores_agent_command_habits_as_tool_preferences(tmp_path):
    service = MemGuardService(tmp_path / "memory.segment", tmp_path / "meta.sqlite3")
    try:
        result = service.ingest_interaction(
            event("以后运行 Python 测试时优先使用 python -m pytest，不要直接用 pytest。")
        )

        assert result["stored"] is True
        assert len(result["memory_ids"]) == 1
        memory = service.meta.get_memory(result["memory_ids"][0])
        assert memory["subtype"] == "tool_preference"
        assert memory["preference_topic"] == "test_runner_command"
        assert memory["preference_value"] == "python -m pytest"
    finally:
        service.close()


def test_service_does_not_store_assistant_preference_as_memory(tmp_path):
    service = MemGuardService(tmp_path / "memory.segment", tmp_path / "meta.sqlite3")
    try:
        result = service.ingest_interaction(
            event(
                "以后我会优先使用 rg。",
                event_type="assistant_message",
                role="assistant",
            )
        )

        assert result["stored"] is True
        assert result["memory_ids"] == []
        assert service.meta.list_memories() == []
    finally:
        service.close()


def test_no_memory_instruction_persists_only_redacted_event_metadata(tmp_path):
    service = MemGuardService(tmp_path / "memory.segment", tmp_path / "meta.sqlite3")
    try:
        result = service.ingest_interaction(
            event("不要记住这条消息：以后搜索代码优先使用 rg。")
        )

        stored = service.meta.list_agent_events()[0]
        assert result["remember_allowed"] is False
        assert result["memory_ids"] == []
        assert stored["content"] == ""
        assert stored["tool_result"] == ""
        assert stored["user_feedback"] == ""
    finally:
        service.close()


def test_high_risk_secret_is_not_persisted_in_agent_event(tmp_path):
    service = MemGuardService(tmp_path / "memory.segment", tmp_path / "meta.sqlite3")
    try:
        secret = "sk-1234567890abcdefghijklmnop"
        result = service.ingest_interaction(event("我的临时 API key 是 " + secret))

        stored = service.meta.list_agent_events()[0]
        assert result["sensitivity"]["level"] == "high"
        assert secret not in stored["content"]
        assert secret not in str(stored)
        assert result["memory_ids"] == []
    finally:
        service.close()


def test_one_tool_request_remains_an_implicit_candidate(tmp_path):
    service = MemGuardService(tmp_path / "memory.segment", tmp_path / "meta.sqlite3")
    try:
        result = service.ingest_interaction(event("请用 rg 搜索 build_context。"))

        assert result["memory_ids"] == []
        status = service.interaction_status()
        assert status["preference_aggregates"][0]["topic"] == "code_search_tool"
        assert status["preference_aggregates"][0]["value"] == "rg"
        assert status["preference_aggregates"][0]["evidence_count"] == 1
        assert status["preference_aggregates"][0]["stable"] is False
    finally:
        service.close()


def test_repeated_tool_requests_across_sessions_promote_one_stable_habit(tmp_path):
    service = MemGuardService(tmp_path / "memory.segment", tmp_path / "meta.sqlite3")
    try:
        events = [
            event("请用 rg 搜索 build_context。", event_id="evt-habit-1"),
            event(
                "用 rg 查找精准遗忘实现。",
                event_id="evt-habit-2",
                turn_id="turn-2",
                sequence=2,
            ),
            event(
                "请使用 rg 搜索 systemd 配置。",
                event_id="evt-habit-3",
                session_id="session-b",
                turn_id="turn-1",
                sequence=1,
            ),
        ]

        results = [service.ingest_interaction(item) for item in events]

        assert results[0]["memory_ids"] == []
        assert results[1]["memory_ids"] == []
        assert len(results[2]["memory_ids"]) == 1
        memories = service.meta.list_memories()
        assert len(memories) == 1
        assert memories[0]["subtype"] == "tool_preference"
        evidence = service.preference_evidence(memories[0]["memory_id"])
        assert {item["event_id"] for item in evidence["events"]} == {
            "evt-habit-1",
            "evt-habit-2",
            "evt-habit-3",
        }
        aggregate = service.interaction_status()["preference_aggregates"][0]
        assert aggregate["stable"] is True
        assert aggregate["distinct_sessions"] == 2
        assert aggregate["promoted_memory_id"] == memories[0]["memory_id"]
    finally:
        service.close()


def test_conflicting_tool_observation_prevents_implicit_promotion(tmp_path):
    service = MemGuardService(tmp_path / "memory.segment", tmp_path / "meta.sqlite3")
    try:
        observations = [
            event("请用 rg 搜索第一处定义。", event_id="evt-conflict-1"),
            event(
                "用 rg 查找第二处定义。",
                event_id="evt-conflict-2",
                turn_id="turn-2",
                sequence=2,
            ),
            event(
                "请用 grep 搜索第三处定义。",
                event_id="evt-conflict-3",
                session_id="session-b",
            ),
            event(
                "使用 rg 查找第四处定义。",
                event_id="evt-conflict-4",
                session_id="session-b",
                turn_id="turn-2",
                sequence=2,
            ),
        ]

        for item in observations:
            service.ingest_interaction(item)

        assert service.meta.list_memories() == []
        rg = next(
            item
            for item in service.interaction_status()["preference_aggregates"]
            if item["value"] == "rg"
        )
        assert rg["evidence_count"] == 3
        assert rg["contradiction_count"] == 1
        assert rg["stable"] is False
    finally:
        service.close()


def test_explicit_confirmation_promotes_existing_implicit_evidence(tmp_path):
    service = MemGuardService(tmp_path / "memory.segment", tmp_path / "meta.sqlite3")
    try:
        service.ingest_interaction(event("请用 rg 搜索第一处定义。", event_id="evt-confirm-1"))
        service.ingest_interaction(
            event(
                "用 rg 查找第二处定义。",
                event_id="evt-confirm-2",
                turn_id="turn-2",
                sequence=2,
            )
        )

        confirmed = service.ingest_interaction(
            event(
                "对，以后默认使用 rg。",
                event_id="evt-confirm-3",
                session_id="session-b",
            )
        )

        assert len(confirmed["memory_ids"]) == 1
        evidence = service.preference_evidence(confirmed["memory_ids"][0])
        assert {item["event_id"] for item in evidence["events"]} == {
            "evt-confirm-1",
            "evt-confirm-2",
            "evt-confirm-3",
        }
    finally:
        service.close()


def test_project_preference_overrides_global_only_in_matching_project(tmp_path):
    service = MemGuardService(tmp_path / "memory.segment", tmp_path / "meta.sqlite3")
    try:
        service.ingest_interaction(
            event("以后默认使用 rg 搜索代码。", event_id="evt-global")
        )
        service.ingest_interaction(
            event(
                "在当前项目中默认使用 grep 搜索代码。",
                event_id="evt-project",
                session_id="session-project",
                project_root="/work/legacy",
            )
        )

        legacy = service.select_preferences(
            agent_name="openhands",
            session_id="new-session",
            project_root="/work/legacy",
            task_type="code_search",
        )
        modern = service.select_preferences(
            agent_name="openhands",
            session_id="new-session",
            project_root="/work/modern",
            task_type="code_search",
        )

        assert legacy["selected"][0]["value"] == "grep"
        assert legacy["selected"][0]["preference_scope"] == "project"
        assert any(item["reason"] == "lower_scope_priority" for item in legacy["excluded"])
        assert modern["selected"][0]["value"] == "rg"
        assert modern["selected"][0]["preference_scope"] == "global"
        assert any(item["reason"] == "scope_not_applicable" for item in modern["excluded"])
    finally:
        service.close()


def test_project_preference_update_does_not_supersede_other_project(tmp_path):
    service = MemGuardService(tmp_path / "memory.segment", tmp_path / "meta.sqlite3")
    try:
        first = service.ingest_interaction(event(
            "在当前项目中默认使用 grep 搜索代码。",
            event_id="evt-project-a-old", project_root="/work/a",
        ))
        second = service.ingest_interaction(event(
            "在当前项目中默认使用 rg 搜索代码。",
            event_id="evt-project-b", project_root="/work/b",
        ))
        first_id = first["memory_ids"][0]
        second_id = second["memory_ids"][0]
        assert service.meta.get_memory(first_id)["governance_state"] == "active"
        assert service.meta.get_memory(second_id)["governance_state"] == "active"

        updated = service.ingest_interaction(event(
            "在当前项目中以后改用 rg 搜索代码。",
            event_id="evt-project-a-new", project_root="/work/a", session_id="next-a",
        ))
        assert first_id in updated["preference_updates"][0]["superseded_memory_ids"]
        assert service.meta.get_memory(first_id)["governance_state"] == "superseded"
        assert service.meta.get_memory(second_id)["governance_state"] == "active"
        assert service.select_preferences(
            agent_name="openhands", session_id="next", project_root="/work/b",
        )["selected"][0]["value"] == "rg"
    finally:
        service.close()


def test_session_preference_overrides_project_and_global_preferences(tmp_path):
    service = MemGuardService(tmp_path / "memory.segment", tmp_path / "meta.sqlite3")
    try:
        service.ingest_interaction(event("以后默认使用 rg 搜索代码。", event_id="evt-scope-global"))
        service.ingest_interaction(
            event(
                "在当前项目中默认使用 grep 搜索代码。",
                event_id="evt-scope-project",
                project_root="/work/legacy",
            )
        )
        session_result = service.ingest_interaction(
            event(
                "本轮使用 rg 搜索代码。",
                event_id="evt-scope-session",
                session_id="session-special",
                project_root="/work/legacy",
            )
        )

        selected = service.select_preferences(
            agent_name="openhands",
            session_id="session-special",
            project_root="/work/legacy",
            task_type="code_search",
        )

        assert len(session_result["memory_ids"]) == 1
        assert selected["selected"][0]["value"] == "rg"
        assert selected["selected"][0]["preference_scope"] == "session"
    finally:
        service.close()


def test_explicit_preference_wins_over_promoted_implicit_preference_at_same_scope(tmp_path):
    service = MemGuardService(tmp_path / "memory.segment", tmp_path / "meta.sqlite3")
    try:
        service.ingest_interaction(event("请用 rg 搜索第一处。", event_id="evt-explicit-1"))
        service.ingest_interaction(
            event("用 rg 查找第二处。", event_id="evt-explicit-2", turn_id="turn-2", sequence=2)
        )
        service.ingest_interaction(
            event("请使用 rg 搜索第三处。", event_id="evt-explicit-3", session_id="session-b")
        )
        service.ingest_interaction(
            event("以后默认使用 grep 搜索代码。", event_id="evt-explicit-4", session_id="session-c")
        )

        selected = service.select_preferences(
            agent_name="openhands",
            session_id="session-new",
            project_root="/work/demo",
            task_type="code_search",
        )

        assert selected["selected"][0]["value"] == "grep"
        assert selected["selected"][0]["preference_explicit"] is True
        # 同一 topic+scope 下的旧取值必须被裁决退役，而不是继续作为可召回候选并存。
        active_values = {
            str(row.get("preference_value") or "")
            for row in service.list_memories(governance_state="active")
            if str(row.get("preference_topic") or "") == "code_search_tool"
        }
        assert active_values == {"grep"}
        superseded = [
            row
            for row in service.list_memories(governance_state="superseded")
            if str(row.get("preference_value") or "") == "rg"
        ]
        assert superseded, "旧偏好应标记为 superseded 以保留审计痕迹"
        assert any(event["event_type"] == "preference_update" for event in service.audit(limit=50))
    finally:
        service.close()


def test_latest_explicit_preference_uses_event_ingest_order_when_timestamps_tie(
    tmp_path,
    monkeypatch,
):
    fixed_time = "2026-06-20T00:00:00+00:00"
    monkeypatch.setattr("memguard.store.sqlite_meta.utc_now", lambda: fixed_time)
    service = MemGuardService(tmp_path / "memory.segment", tmp_path / "meta.sqlite3")
    try:
        service.ingest_interaction(
            event(
                "以前搜索代码优先使用 grep。",
                event_id="evt-tied-old",
                timestamp=fixed_time,
            )
        )
        service.ingest_interaction(
            event(
                "更新偏好：以后不要用 grep，改为优先使用 rg。",
                event_id="evt-tied-new",
                session_id="session-b",
                timestamp=fixed_time,
            )
        )

        selected = service.select_preferences(
            agent_name="openhands",
            session_id="session-new",
            project_root="",
            task_type="code_search",
        )

        assert selected["selected"][0]["value"] == "rg"
        # 时间戳相同时按事件写入顺序裁决：后写入的 rg 生效，grep 退役并留痕。
        active_values = {
            str(row.get("preference_value") or "")
            for row in service.list_memories(governance_state="active")
            if str(row.get("preference_topic") or "") == "code_search_tool"
        }
        assert active_values == {"rg"}
        assert any(
            str(row.get("preference_value") or "") == "grep"
            for row in service.list_memories(governance_state="superseded")
        )
    finally:
        service.close()


def test_explicit_withdrawal_stops_active_preference_without_erasing_history(tmp_path):
    service = MemGuardService(tmp_path / "memory.segment", tmp_path / "meta.sqlite3")
    try:
        original = service.ingest_interaction(
            event("以后搜索项目代码优先使用 grep。", event_id="evt-withdraw-old")
        )
        withdrawn = service.ingest_interaction(
            event(
                "以后不要再用 grep 搜索项目代码。",
                event_id="evt-withdraw-new",
                session_id="session-b",
            )
        )

        assert original["memory_ids"]
        assert withdrawn["preference_withdrawals"][0]["retired_memory_ids"] == original["memory_ids"]
        assert not [
            item
            for item in service.interaction_status()["preference_aggregates"]
            if item["topic"] == "code_search_tool" and item["value"] == "grep" and item["stable"]
        ]
        assert service.meta.get_memory(original["memory_ids"][0])["deleted"] == 0
        assert service.meta.get_memory(original["memory_ids"][0])["governance_state"] == "superseded"
        feedback = withdrawn["feedback"]["updated_memories"][0]
        assert feedback["category_label"] == "工具偏好"
        assert feedback["status_label"] == "已停止使用"
    finally:
        service.close()
