import json
import socket
import threading
import time

import pytest

from memguard.api.server import handle_request, serve_unix_socket
from memguard.cli.memctl import main as memctl_main
from memguard.ingestion.tool_result import ToolResult
from memguard.integrations.events import AgentInteractionEvent
from memguard.service import MemGuardService


def user_event(content: str, event_id: str, sequence: int = 1) -> AgentInteractionEvent:
    return AgentInteractionEvent(
        agent_name="openhands",
        agent_version="1.28.1",
        session_id="session-a",
        turn_id="turn-%s" % sequence,
        sequence=sequence,
        event_type="user_message",
        role="user",
        content=content,
        project_root="/work/demo",
        task_type="code_search",
        source_uri="openhands://session-a/%s" % event_id,
        event_id=event_id,
    )


def seeded_service(tmp_path) -> MemGuardService:
    service = MemGuardService(tmp_path / "memory.segment", tmp_path / "meta.sqlite3")
    service.ingest_interaction(
        user_event(
            "以后搜索代码优先使用 rg。回答时先给命令，再解释原因。删除文件前必须询问我。",
            "evt-context-preferences",
        )
    )
    service.ingest_text(
        "工作流程：排查 systemd 服务失败时先运行 systemctl status，再查看 journalctl。",
        source_kind="manual",
    )
    service.ingest_tool_result(
        ToolResult(
            command="systemctl restart memguardd",
            exit_code=0,
            state_before="memguardd inactive",
            state_after="memguardd active",
            lesson="先检查配置再重启服务",
        ),
        source_uri="agent://tool-result-1",
    )
    return service


def test_agent_context_filters_memories_by_stage(tmp_path):
    service = seeded_service(tmp_path)
    try:
        planning = service.build_agent_context(
            "修复 MemGuardOS 服务并查找实现",
            stage="planning",
            agent_name="openhands",
            session_id="session-a",
            project_root="/work/demo",
            task_type="code_search",
        )
        tool = service.build_agent_context(
            "修复 MemGuardOS 服务并查找实现",
            stage="tool_selection",
            agent_name="openhands",
            session_id="session-a",
            project_root="/work/demo",
            task_type="code_search",
        )
        response = service.build_agent_context(
            "修复 MemGuardOS 服务并查找实现",
            stage="response_generation",
            agent_name="openhands",
            session_id="session-a",
            project_root="/work/demo",
            task_type="code_search",
        )

        assert all(item["subtype"] not in {"tool_preference", "output_style"} for item in planning["memories"])
        assert any(item.get("preference_value") == "rg" for item in tool["memories"])
        assert any(item["subtype"] == "procedural_trace" for item in tool["memories"])
        assert any(item.get("preference_value") == "command_then_explanation" for item in response["memories"])
        assert all(item["subtype"] != "tool_trace" for item in response["memories"])
    finally:
        service.close()


def test_agent_context_explains_sources_and_filter_reasons(tmp_path):
    service = seeded_service(tmp_path)
    try:
        result = service.build_agent_context(
            "搜索代码",
            stage="tool_selection",
            agent_name="openhands",
            session_id="session-a",
            project_root="/work/demo",
            task_type="code_search",
            token_budget=500,
        )

        assert result["retrieval_passes"] == 1
        assert result["stage"] == "tool_selection"
        assert result["memories"]
        assert all(item["selection_reason"] for item in result["memories"])
        preference = next(item for item in result["memories"] if item.get("preference_value") == "rg")
        assert preference["source_event_ids"] == ["evt-context-preferences"]
        assert result["explainability"]["filtered"]
        assert all(item["reason"] for item in result["explainability"]["filtered"])
        assert result["budget"]["estimated_context_tokens"] <= 500
    finally:
        service.close()


def test_agent_context_uses_one_candidate_search(tmp_path, monkeypatch):
    service = seeded_service(tmp_path)
    try:
        calls = {"count": 0}
        original = service.search

        def counted_search(query, limit=5, audit=True):
            calls["count"] += 1
            return original(query, limit=limit, audit=audit)

        monkeypatch.setattr(service, "search", counted_search)

        service.build_agent_context(
            "搜索代码",
            stage="tool_selection",
            agent_name="openhands",
            session_id="session-a",
            project_root="/work/demo",
            task_type="code_search",
        )

        assert calls["count"] == 1
    finally:
        service.close()


def test_agent_context_rejects_unknown_stage(tmp_path):
    service = seeded_service(tmp_path)
    try:
        with pytest.raises(ValueError, match="unsupported agent context stage"):
            service.build_agent_context(
                "搜索代码",
                stage="unknown",
                agent_name="openhands",
                session_id="session-a",
            )
    finally:
        service.close()


def test_agent_api_ingests_events_and_builds_context(tmp_path):
    service = MemGuardService(tmp_path / "memory.segment", tmp_path / "meta.sqlite3")
    try:
        source = user_event("以后搜索代码优先使用 rg。", "evt-api-agent")

        ingested = handle_request(
            service,
            {"command": "ingest_interaction", "event": source.to_dict()},
        )
        status = handle_request(service, {"command": "interaction_status"})
        context = handle_request(
            service,
            {
                "command": "build_agent_context",
                "task": "搜索 build_context",
                "stage": "tool_selection",
                "agent_name": "openhands",
                "session_id": "session-a",
                "project_root": "/work/demo",
                "task_type": "code_search",
                "token_budget": 500,
            },
        )

        assert ingested["ok"] is True
        assert ingested["result"]["memory_ids"]
        assert status["ok"] is True
        assert status["result"]["event_count"] == 1
        assert context["ok"] is True
        assert context["result"]["stage"] == "tool_selection"
        assert context["result"]["memories"][0]["preference_value"] == "rg"
    finally:
        service.close()


def test_agent_api_rejects_oversized_event_batch(tmp_path):
    service = MemGuardService(tmp_path / "memory.segment", tmp_path / "meta.sqlite3")
    try:
        response = handle_request(
            service,
            {
                "command": "ingest_interactions",
                "events": [user_event("请用 rg 搜索。", "evt-%s" % index).to_dict() for index in range(501)],
            },
        )

        assert response["ok"] is False
        assert response["error"] == "invalid_request"
        assert "at most 500" in response["detail"]
    finally:
        service.close()


def test_agent_cli_supports_ingest_context_status_evidence_and_replay(tmp_path, monkeypatch, capsys):
    monkeypatch.setenv("MEMGUARD_HOME", str(tmp_path / "home"))
    event_file = tmp_path / "event.json"
    event_file.write_text(
        user_event("以后搜索代码优先使用 rg。", "evt-cli-agent").to_json(),
        encoding="utf-8",
    )
    replay_file = tmp_path / "session.jsonl"
    replay_file.write_text(
        user_event(
            "回答时先给命令，再解释原因。",
            "evt-cli-replay",
            sequence=2,
        ).to_json()
        + "\n",
        encoding="utf-8",
    )

    assert memctl_main(["agent", "ingest-event", "--file", str(event_file)]) == 0
    ingest_output = capsys.readouterr().out
    memory_id = json.loads(ingest_output)["memory_ids"][0]

    assert memctl_main(["agent", "replay", "--file", str(replay_file)]) == 0
    assert json.loads(capsys.readouterr().out)["stored_count"] == 1

    assert memctl_main(["agent", "status"]) == 0
    assert json.loads(capsys.readouterr().out)["event_count"] == 2

    assert memctl_main(
        [
            "agent",
            "context",
            "--stage",
            "tool_selection",
            "--task",
            "搜索代码",
            "--agent-name",
            "openhands",
            "--session-id",
            "session-a",
            "--project-root",
            "/work/demo",
            "--task-type",
            "code_search",
        ]
    ) == 0
    assert json.loads(capsys.readouterr().out)["stage"] == "tool_selection"

    assert memctl_main(["agent", "evidence", memory_id]) == 0
    assert json.loads(capsys.readouterr().out)["events"][0]["event_id"] == "evt-cli-agent"


def test_unix_socket_can_build_agent_context_from_service_created_in_parent_thread(tmp_path):
    if not hasattr(socket, "AF_UNIX"):
        pytest.skip("Unix domain sockets are unavailable")
    service = MemGuardService(tmp_path / "memory.segment", tmp_path / "meta.sqlite3")
    stop = {"value": False}
    socket_path = tmp_path / "agent.sock"
    try:
        service.ingest_interaction(user_event("以后搜索代码优先使用 rg。", "evt-socket-agent"))
        thread = threading.Thread(
            target=serve_unix_socket,
            args=(socket_path, service, lambda: stop["value"]),
            daemon=True,
        )
        thread.start()
        for _ in range(100):
            if socket_path.exists():
                break
            time.sleep(0.01)

        with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as client:
            client.connect(str(socket_path))
            client.sendall(
                (
                    json.dumps(
                        {
                            "command": "build_agent_context",
                            "task": "搜索代码",
                            "stage": "tool_selection",
                            "agent_name": "openhands",
                            "session_id": "session-a",
                            "token_budget": 400,
                        },
                        ensure_ascii=False,
                    )
                    + "\n"
                ).encode("utf-8")
            )
            response = json.loads(client.recv(65536).decode("utf-8"))

        assert response["ok"] is True
        assert response["result"]["stage"] == "tool_selection"
        assert response["result"]["memories"][0]["preference_value"] == "rg"
    finally:
        stop["value"] = True
        if "thread" in locals():
            thread.join(timeout=2)
        service.close()
