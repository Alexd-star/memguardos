from memguard.api.server import handle_request
from memguard.integrations.action_plan import build_action_plan
from memguard.memory_admission import assess_memory_admission
from memguard.service import MemGuardService


def make_service(tmp_path):
    return MemGuardService(
        store_path=tmp_path / "memories.mmap",
        db_path=tmp_path / "meta.sqlite3",
        namespace="admission-test",
    )


def test_admission_policy_separates_explicit_automatic_and_sensitive_sources():
    explicit = assess_memory_admission(
        source_kind="manual",
        inferred_tier="long_term",
        memory_type="preference",
        quality_score=0.9,
        extraction_confidence=0.9,
    )
    automatic = assess_memory_admission(
        source_kind="tool_result",
        inferred_tier="long_term",
        memory_type="knowledge",
        quality_score=0.9,
        extraction_confidence=0.9,
    )
    sensitive = assess_memory_admission(
        source_kind="manual",
        inferred_tier="long_term",
        memory_type="knowledge",
        sensitivity_level="high",
    )
    assert explicit["decision"] == "admit_long_term"
    assert automatic["decision"] == "retain_session"
    assert automatic["evidence_requirements"]
    assert sensitive["decision"] == "deny"


def test_service_admission_preview_does_not_persist(tmp_path):
    service = make_service(tmp_path)
    try:
        before = service.meta.list_memories(include_deleted=True, governance_state=None)
        result = service.preview_memory_admission(
            "工具结果：本次搜索使用 rg 成功，后续可能复用。",
            source_kind="tool_result",
            session_id="demo-session",
            project_root=str(tmp_path),
        )
        after = service.meta.list_memories(include_deleted=True, governance_state=None)
        assert result["persisted"] is False
        assert result["admission"]["decision"] == "retain_session"
        assert len(after) == len(before)
    finally:
        service.close()


def test_enforced_admission_demotes_automatic_observation_to_session(tmp_path):
    service = make_service(tmp_path)
    try:
        result = service.ingest_text(
            "工具结果：本次搜索使用 rg 成功，后续可能复用。",
            source_kind="tool_result",
            retention_session_id="enforced-session",
            enforce_admission=True,
        )
        assert result["stored"] is True
        assert result["record"].tier == "session"
        assert result["admission"]["enforcement"] == "applied"
        explicit_tier = service.ingest_text(
            "工具结果：另一次代码搜索成功完成，可供以后参考。",
            source_kind="tool_result",
            tier="long_term",
            retention_session_id="enforced-session",
            enforce_admission=True,
        )
        assert explicit_tier["stored"] is True
        assert explicit_tier["record"].tier == "session"
    finally:
        service.close()


def test_preview_denies_no_store_request_and_rejected_quality(tmp_path):
    service = make_service(tmp_path)
    try:
        no_store = service.preview_memory_admission("这段信息不要记住。", source_kind="manual")
        assert no_store["admission"]["decision"] == "deny"
        assert no_store["persisted"] is False
        rejected = service.preview_memory_admission("!!!", source_kind="manual")
        assert rejected["admission"]["decision"] == "deny"
    finally:
        service.close()


def test_action_plan_digest_is_stable_for_nested_input_order():
    preflight = {"decision": "allow", "evidence": []}
    first = build_action_plan(
        task="检查项目", tool_name="terminal",
        tool_input={"command": "pwd", "options": {"cwd": "/tmp", "env": {"A": 1, "B": 2}}},
        preflight=preflight,
    )
    reordered = build_action_plan(
        task="检查项目", tool_name="terminal",
        tool_input={"options": {"env": {"B": 2, "A": 1}, "cwd": "/tmp"}, "command": "pwd"},
        preflight=preflight,
    )
    assert first["plan_id"] == reordered["plan_id"]
    assert first["tool"]["input_digest"] == reordered["tool"]["input_digest"]


def test_explicit_admission_and_action_plan_are_explainable(tmp_path):
    service = make_service(tmp_path)
    try:
        preview = service.preview_memory_admission(
            "以后搜索项目代码优先使用 rg。",
            source_kind="manual",
        )
        assert preview["admission"]["decision"] == "admit_long_term"
        stored = service.ingest_natural_memory("以后搜索项目代码优先使用 rg。", source_kind="user_cli")
        assert stored["stored"] is True
        plan = service.build_agent_action_plan(
            "查找 build_agent_context 的定义",
            tool_name="terminal",
            tool_input={"command": "grep -n build_agent_context ."},
            agent_name="generic-agent",
            session_id="plan-session",
        )
        assert plan["schema"] == "memguardos-agent-action-plan-v1"
        assert plan["plan_id"].startswith("plan_")
        assert plan["execution_gate"] == "replace_then_execute"
        assert plan["evidence"]
        api = handle_request(service, {
            "command": "admission_preview",
            "text": "记住回答先给命令，再说明风险。",
            "source_kind": "manual",
        })
        assert api["ok"] is True
        assert api["result"]["persisted"] is False
        plan_api = handle_request(service, {
            "command": "agent_action_plan",
            "task": "查找 build_agent_context 的定义",
            "tool_name": "terminal",
            "tool_input": {"command": "grep -n build_agent_context ."},
            "session_id": "plan-api",
        })
        assert plan_api["ok"] is True
        assert plan_api["result"]["schema"] == "memguardos-agent-action-plan-v1"
    finally:
        service.close()
