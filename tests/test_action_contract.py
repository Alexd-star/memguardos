from memguard.integrations.events import AgentInteractionEvent
from memguard.api.server import handle_request
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
            content="以后搜索代码优先使用 rg；删除或安装系统软件前必须先让我确认。",
            project_root="/work/demo",
            task_type="code_search",
            source_uri="test://session-a/1",
            event_id="event-action-1",
        )
    )
    service.ingest_interaction(
        AgentInteractionEvent(
            agent_name="generic-agent",
            agent_version="test",
            session_id="session-a",
            turn_id="turn-2",
            sequence=2,
            event_type="user_message",
            role="user",
            content="涉及 rm、systemctl disable、重启服务这类高风险命令时，必须先说明风险并等待确认。",
            project_root="/work/demo",
            task_type="maintenance",
            source_uri="test://session-a/2",
            event_id="event-action-2",
        )
    )
    return service


def test_action_preflight_redirects_conflicting_search_tool(tmp_path):
    result = _service(tmp_path).preflight_agent_action(
        "搜索项目代码中的 build_agent_context",
        tool_name="terminal",
        tool_input={"command": "grep -n build_agent_context ."},
        agent_name="generic-agent",
        session_id="session-a",
        project_root="/work/demo",
        task_type="code_search",
    )
    assert result["decision"] == "redirect"
    assert result["expected_value"] == "rg"
    assert result["evidence"]
    assert result["context_receipt"]["schema"] == "memguardos-context-receipt-v1"


def test_action_preflight_requires_confirmation_for_scoped_system_change(tmp_path):
    result = _service(tmp_path).preflight_agent_action(
        "安装依赖",
        tool_name="terminal",
        tool_input={"command": "sudo apt install ripgrep"},
        agent_name="generic-agent",
        session_id="session-a",
        project_root="/work/demo",
        task_type="maintenance",
    )
    assert result["decision"] == "confirm"
    assert result["confirmation_required"] is True


def test_action_preflight_denies_known_destructive_signature(tmp_path):
    result = _service(tmp_path).preflight_agent_action(
        "清理临时文件",
        tool_name="terminal",
        tool_input={"command": "rm -rf /"},
        agent_name="generic-agent",
        session_id="session-a",
        project_root="/work/demo",
    )
    assert result["decision"] == "deny"
    assert result["policy_category"] == "high_risk"


def test_action_preflight_allows_unrelated_tool_and_scopes_evidence(tmp_path):
    result = _service(tmp_path).preflight_agent_action(
        "读取当前目录",
        tool_name="terminal",
        tool_input={"command": "pwd"},
        agent_name="generic-agent",
        session_id="session-other",
        project_root="/work/other",
    )
    assert result["decision"] == "allow"
    assert result["decision"] == "allow"
    assert result["scope"]["project_root"] == "/work/other"


def test_action_preflight_api_returns_same_contract(tmp_path):
    service = _service(tmp_path)
    response = handle_request(
        service,
        {
            "command": "preflight_agent_action",
            "task": "搜索代码",
            "tool_name": "terminal",
            "tool_input": {"command": "grep -n foo ."},
            "agent_name": "generic-agent",
            "session_id": "session-a",
            "project_root": "/work/demo",
            "task_type": "code_search",
        },
    )
    assert response["ok"] is True
    assert response["result"]["decision"] == "redirect"
    assert response["result"]["context_receipt"]["fingerprint"]
