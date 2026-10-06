from memguard.api.server import handle_request
from memguard.integrations.action_feedback import classify_action_outcome
from memguard.integrations.events import AgentInteractionEvent
from memguard.service import MemGuardService


def _service(tmp_path):
    service = MemGuardService(tmp_path / "memory.segment", tmp_path / "meta.sqlite3")
    service.ingest_interaction(
        AgentInteractionEvent(
            agent_name="generic-agent",
            agent_version="test",
            session_id="session-a",
            turn_id="turn-1",
            sequence=1,
            event_type="user_message",
            role="user",
            content="以后搜索代码优先使用 rg。",
            project_root=str(tmp_path),
            task_type="code_search",
            source_uri="test://feedback/1",
            event_id="event-feedback-1",
        )
    )
    return service


def test_classifier_distinguishes_success_deviation_and_blocked():
    base = {
        "decision": "redirect",
        "tool_name": "terminal",
        "expected_value": "rg",
        "suggested_command": "rg -n target .",
    }
    followed = classify_action_outcome(
        base,
        exit_code=0,
        actual_tool_input={"command": "rg -n target ."},
    )
    assert followed["outcome"] == "success"
    assert followed["reinforce"] is True
    deviated = classify_action_outcome(
        base,
        exit_code=0,
        actual_tool_input={"command": "grep -n target ."},
    )
    assert deviated["outcome"] == "deviation"
    assert deviated["reinforce"] is False
    blocked = classify_action_outcome({"decision": "deny"})
    assert blocked["outcome"] == "blocked"


def test_record_action_outcome_is_idempotent_and_updates_decay_evidence(tmp_path):
    service = _service(tmp_path)
    preflight = service.preflight_agent_action(
        "搜索项目代码",
        tool_name="terminal",
        tool_input={"command": "grep -n target ."},
        agent_name="generic-agent",
        session_id="session-a",
        project_root=str(tmp_path),
        task_type="code_search",
    )
    assert preflight["decision"] == "redirect"
    first = service.record_agent_action_outcome(
        preflight,
        tool_input={"command": "rg -n target ."},
        exit_code=0,
        idempotency_key="feedback-1",
    )
    assert first["ok"] is True
    assert first["outcome"] == "success"
    assert first["stored"] == len(preflight["evidence"])
    second = service.record_agent_action_outcome(
        preflight,
        tool_input={"command": "rg -n target ."},
        exit_code=0,
        idempotency_key="feedback-1",
    )
    assert second["stored"] == 0
    assert second["duplicates"] == first["stored"]
    stats = service.meta.action_outcome_stats(preflight["evidence"][0]["memory_id"])
    assert stats["success_count"] == 1
    assert stats["total_count"] == 1

    memory_id = preflight["evidence"][0]["memory_id"]
    service.meta.conn.execute(
        "UPDATE memory SET updated_at = ?, last_hit_at = '', hit_count = 0 WHERE memory_id = ?",
        ("2025-01-01T00:00:00+00:00", memory_id),
    )
    service.meta.conn.commit()
    plan = service.build_decay_plan(as_of="2026-01-31T00:00:00+00:00")
    candidate = next(item for item in plan["candidates"] if item["memory_id"] == memory_id)
    assert candidate["action_success_count"] == 1
    assert candidate["recommended_action"] == "retain_explicit_preference"


def test_stale_context_and_forget_remove_action_feedback(tmp_path):
    service = _service(tmp_path)
    preflight = service.preflight_agent_action(
        "搜索项目代码",
        tool_name="terminal",
        tool_input={"command": "grep -n target ."},
        agent_name="generic-agent",
        session_id="session-a",
        project_root=str(tmp_path),
        task_type="code_search",
    )
    memory_id = preflight["evidence"][0]["memory_id"]
    service.record_agent_action_outcome(preflight, tool_input={"command": "rg -n target ."}, exit_code=0)
    service.update_memory(memory_id, "以后搜索代码优先使用 rg，并先检查工作区。")
    stale = service.record_agent_action_outcome(preflight, tool_input={"command": "rg -n target ."}, exit_code=0)
    assert stale["ok"] is False
    assert stale["reason"] == "stale_memory_context"

    result = service.forget_memory_ids([memory_id], reason="test action outcome erasure")
    assert memory_id in result["forgotten_memory_ids"]
    assert service.meta.action_outcome_stats(memory_id)["total_count"] == 0


def test_allow_with_unrelated_context_does_not_reinforce_every_memory(tmp_path):
    service = _service(tmp_path)
    preflight = service.preflight_agent_action(
        "读取当前目录", tool_name="terminal", tool_input={"command": "pwd"},
        agent_name="generic-agent", session_id="session-a", project_root=str(tmp_path),
    )
    assert preflight["decision"] == "allow"
    result = service.record_agent_action_outcome(preflight, tool_input={"command": "pwd"}, exit_code=0)
    assert result["ok"] is True
    assert result["stored"] == 0
    assert result["evidence_count"] == 0


def test_action_feedback_api_contract(tmp_path):
    service = _service(tmp_path)
    preflight = service.preflight_agent_action(
        "搜索项目代码",
        tool_name="terminal",
        tool_input={"command": "rg -n target ."},
        agent_name="generic-agent",
        session_id="session-a",
        project_root=str(tmp_path),
        task_type="code_search",
    )
    response = handle_request(
        service,
        {
            "command": "record_agent_action_outcome",
            "preflight": preflight,
            "tool_input": {"command": "rg -n target ."},
            "exit_code": 0,
            "idempotency_key": "api-feedback-1",
        },
    )
    assert response["ok"] is True
    assert response["result"]["schema"] == "memguardos-agent-action-feedback-v1"
