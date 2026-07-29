"""The optional preference enhancer may suggest; deterministic rules still decide."""

import json
import sys

from memguard.integrations.events import AgentInteractionEvent
from memguard.integrations.interaction_extractor import PreferenceObservation
from memguard.integrations.preference_enhancer import (
    ENHANCER_CONFIDENCE_CAP,
    PreferenceEnhancer,
)
from memguard.service import MemGuardService


def _event(text: str, event_id: str = "evt-enh-1") -> AgentInteractionEvent:
    return AgentInteractionEvent(
        agent_name="openhands",
        agent_version="test",
        session_id="session-enh",
        turn_id="turn-1",
        sequence=1,
        event_type="user_message",
        role="user",
        content=text,
        event_id=event_id,
    )


def _stub_command(payload: object, tmp_path) -> str:
    """A tiny stdin/stdout command that always answers with the given payload.

    Written to a script file so the command line stays free of Windows path
    escapes that shlex would mangle.
    """

    script = tmp_path / "stub_enhancer.py"
    script.write_text(
        "import sys, json\nsys.stdin.read()\nprint(json.dumps(%s))\n" % json.dumps(payload),
        encoding="utf-8",
    )
    return "%s %s" % (
        json.dumps(sys.executable).replace("\\\\", "/"),
        json.dumps(str(script)).replace("\\\\", "/"),
    )


def _failing_command(tmp_path) -> str:
    script = tmp_path / "failing_enhancer.py"
    script.write_text(
        "import sys\nsys.stdin.read()\nraise SystemExit(3)\n",
        encoding="utf-8",
    )
    return "%s %s" % (
        json.dumps(sys.executable).replace("\\\\", "/"),
        json.dumps(str(script)).replace("\\\\", "/"),
    )


def test_enhancer_is_disabled_unless_explicitly_configured():
    assert PreferenceEnhancer(command="").enabled is False
    assert PreferenceEnhancer(command="  ").enabled is False


def test_enhancer_accepts_only_whitelisted_high_confidence_suggestions(tmp_path):
    payload = {
        "preferences": [
            {"topic": "editor_command", "value": "nvim", "scope": "global", "polarity": "positive", "confidence": 0.9},
            {"topic": "not_a_real_topic", "value": "x", "scope": "global", "polarity": "positive", "confidence": 0.99},
            {"topic": "package_manager", "value": "apt", "scope": "bogus_scope", "polarity": "positive", "confidence": 0.9},
            {"topic": "commit_style", "value": "conventional", "scope": "global", "polarity": "positive", "confidence": 0.2},
        ]
    }
    enhancer = PreferenceEnhancer(command=_stub_command(payload, tmp_path), timeout=30)

    accepted, error = enhancer.suggest(_event("以后提交信息用 conventional commits，编辑器用 nvim"), [])

    assert error == ""
    assert [(item.topic, item.value) for item in accepted] == [("editor_command", "nvim")]
    # 模型建议一律按隐式证据计入，不能凭一条建议直接晋升为稳定偏好。
    assert accepted[0].explicit is False
    assert accepted[0].confidence <= ENHANCER_CONFIDENCE_CAP


def test_enhancer_does_not_duplicate_deterministic_observations(tmp_path):
    payload = {
        "preferences": [
            {"topic": "code_search_tool", "value": "rg", "scope": "global", "polarity": "positive", "confidence": 0.95}
        ]
    }
    enhancer = PreferenceEnhancer(command=_stub_command(payload, tmp_path), timeout=30)
    existing = [
        PreferenceObservation(
            topic="code_search_tool",
            value="rg",
            polarity="positive",
            scope="global",
            explicit=True,
            confidence=0.98,
            evidence_event_id="evt-enh-1",
        )
    ]

    accepted, error = enhancer.suggest(_event("以后查代码优先用 rg"), existing)

    assert error == ""
    assert accepted == []


def test_broken_enhancer_never_blocks_ingest(tmp_path, monkeypatch):
    monkeypatch.setenv("MEMGUARD_PREFERENCE_ENHANCER_COMMAND", _failing_command(tmp_path))
    service = MemGuardService(tmp_path / "memory.segment", tmp_path / "meta.sqlite3")
    try:
        result = service.ingest_interaction(_event("以后查代码优先用 rg", event_id="evt-enh-broken"))

        assert result["stored"] is True
        assert any(
            event["event_type"] == "preference_enhancer_skipped"
            for event in service.audit(limit=50)
        )
        # 确定性提取不受影响：偏好照常形成。
        topics = {
            str(row.get("preference_topic") or "")
            for row in service.list_memories()
        }
        assert "code_search_tool" in topics
    finally:
        service.close()


def test_high_risk_interaction_is_blocked_before_optional_enhancer(tmp_path, monkeypatch):
    service = MemGuardService(tmp_path / "memory.segment", tmp_path / "meta.sqlite3")
    called = []

    def should_not_run(*_args, **_kwargs):
        called.append(True)
        raise AssertionError("high-risk plaintext reached preference enhancer")

    monkeypatch.setattr(service.preference_enhancer, "command", "configured")
    monkeypatch.setattr(service.preference_enhancer, "suggest", should_not_run)
    try:
        result = service.ingest_interaction(_event("记住我的密码 11515", event_id="evt-secret-first"))

        assert result["sensitivity"]["level"] == "high"
        assert result["memory_ids"] == []
        assert called == []
    finally:
        service.close()


def test_medium_risk_interaction_is_redacted_before_optional_enhancer(tmp_path, monkeypatch):
    service = MemGuardService(tmp_path / "memory.segment", tmp_path / "meta.sqlite3")
    observed = []

    def capture(event, _existing):
        observed.append(event.content)
        return [], ""

    monkeypatch.setattr(service.preference_enhancer, "command", "configured")
    monkeypatch.setattr(service.preference_enhancer, "suggest", capture)
    try:
        result = service.ingest_interaction(_event("我的联系电话是 13800138000。", event_id="evt-redact-first"))

        assert result["sensitivity"]["level"] == "medium"
        assert observed and "13800138000" not in observed[0]
        assert "[REDACTED:phone_cn]" in observed[0]
    finally:
        service.close()
