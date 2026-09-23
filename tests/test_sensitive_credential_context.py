import pytest

from memguard.sensitivity.detector import SensitiveDetector
from memguard.service import MemGuardService
from memguard.ingestion.tool_result import ToolResult
from memguard.integrations.events import AgentInteractionEvent


@pytest.mark.parametrize(
    "text",
    [
        "记住我的密码 11515",
        "我的密码是 123",
        "登录密码是 春天来了",
        "password is violet-fabric-77",
        "PIN码为 010609",
    ],
)
def test_short_and_natural_language_passwords_are_blocked(tmp_path, text):
    service = MemGuardService(tmp_path / "memory.segment", tmp_path / "meta.sqlite3")
    try:
        result = service.ingest_natural_memory(text)
        assert result["stored"] is False
        sensitivity = result["sensitivity"]
        level = sensitivity["level"] if isinstance(sensitivity, dict) else sensitivity.level
        assert level == "high"
        assert service.list_memories() == []
        assert all(text not in str(item) for item in service.meta.list_agent_events())
    finally:
        service.close()


@pytest.mark.parametrize(
    "text",
    [
        "密码长度至少 12 位。",
        "password_length=12",
        "密码策略需要定期更新。",
    ],
)
def test_password_policy_descriptions_are_not_treated_as_password_values(text):
    assert SensitiveDetector().classify(text).level != "high"


def test_credentials_are_blocked_consistently_on_update_and_tool_results(tmp_path):
    service = MemGuardService(tmp_path / "memory.segment", tmp_path / "meta.sqlite3")
    try:
        record = service.ingest_text("星桥项目会议时间是周四下午。")["record"]
        update = service.update_memory(record.memory_id, "星桥项目密码是 春天来了")
        tool = service.ingest_tool_result(
            ToolResult(command="printenv", stdout="password is violet-fabric-77")
        )

        assert update["updated"] is False
        assert update["reason"] == "sensitive_block"
        assert tool["stored"] is False
        assert tool["reason"] == "sensitive_block"
        assert "春天来了" not in str(service.list_memories())
        assert "violet-fabric-77" not in str(service.list_memories())
    finally:
        service.close()


def test_sensitive_source_uri_never_enters_memory_event_or_audit(tmp_path):
    service = MemGuardService(tmp_path / "memory.segment", tmp_path / "meta.sqlite3")
    secret = "ultraPass123"
    source_uri = "https://demo:%s@host.example/path" % secret
    try:
        record = service.ingest_text(
            "白鹭项目的维护窗口为周四。", source_uri=source_uri,
        )["record"]
        assert record.source.uri.startswith("redacted:sha256:")
        assert secret not in str(service.meta.get_memory(record.memory_id))

        event = AgentInteractionEvent(
            agent_name="test-agent", agent_version="1", session_id="s1",
            turn_id="t1", sequence=1, event_type="user_message", role="user",
            content="请检查白鹭项目的维护窗口。", source_uri=source_uri,
        )
        service.ingest_interaction(event)
        assert service.meta.list_agent_events()[0]["source_uri"].startswith("redacted:sha256:")

        blocked = service.ingest_text(
            "这是一个普通句子。", source_uri="/home/demo/.env",
        )
        assert blocked["reason"] == "sensitive_block"
        service._audit(
            "sanitization_probe", None,
            {"command": "password is violet-fabric-77", "path": "/home/demo/.env"},
        )
        assert secret not in str(service.audit())
        assert "/home/demo/.env" not in str(service.audit())
        assert "violet-fabric-77" not in str(service.audit())
    finally:
        service.close()
