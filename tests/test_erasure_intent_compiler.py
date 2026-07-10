from datetime import datetime, timezone

from memguard.forgetting.natural_intent import ErasureIntentCompiler
from memguard.service import MemGuardService


NOW = datetime(2026, 7, 9, 12, 0, 0, tzinfo=timezone.utc)


def compile_request(text: str, **context):
    return ErasureIntentCompiler(now=NOW).compile(text, context=context)


def test_compiler_handles_required_examples_with_dry_run_boundary():
    cases = [
        (
            "忘记我上周关于 X 项目的所有偏好",
            {"memory_types": ["preference"], "topic": "X 项目", "project_hint": "X", "time_source": "上周"},
        ),
        (
            "忘掉当前会话里关于 API Key 的所有记忆",
            {"memory_types": ["all"], "scope": "session", "sensitive_labels": ["api_key"]},
        ),
        (
            "删除昨天 OpenHands 中关于 pytest 的工具偏好",
            {"memory_types": ["preference"], "agent_name": "OpenHands", "preference_topic": "pytest", "time_source": "昨天"},
        ),
        (
            "遗忘这个项目里所有高风险安全策略",
            {"memory_types": ["security_policy"], "scope": "project", "topic": "高风险安全策略"},
        ),
        (
            "忘掉最近三天关于 systemd 修复流程的知识",
            {"memory_types": ["knowledge"], "topic": "systemd 修复流程", "time_source": "最近三天"},
        ),
        (
            "忘记当前 Agent 在本轮任务中学到的回答风格偏好",
            {"memory_types": ["preference"], "scope": "agent", "preference_topic": "回答风格"},
        ),
    ]
    for text, expected in cases:
        result = compile_request(text, session_id="session-a", agent_name="openhands", project_root="/repo/x")
        assert result["intent_type"] == "precise_forgetting"
        assert result["execution_mode"] == "dry_run"
        assert result["requires_confirm"] is True
        assert result["safety_note"].startswith("dry-run first")
        for key, value in expected.items():
            if key == "time_source":
                assert result["time_range"]["source_text"] == value
            elif key == "scope":
                assert result["scope"]["level"] == value
            else:
                assert result[key] == value
        assert result["confidence"] >= 0.68


def test_compiler_resolves_relative_time_ranges():
    yesterday = compile_request("忘记昨天的记忆")
    assert yesterday["time_range"]["source_text"] == "昨天"
    assert yesterday["time_range"]["start_time"].startswith("2026-07-08T00:00:00")
    assert yesterday["time_range"]["end_time"].startswith("2026-07-09T00:00:00")

    last_week = compile_request("忘记上周关于 UKUI 的偏好")
    assert last_week["time_range"]["source_text"] == "上周"
    assert last_week["time_range"]["start_time"].startswith("2026-06-29T00:00:00")
    assert last_week["time_range"]["end_time"].startswith("2026-07-06T00:00:00")

    recent_three_days = compile_request("忘掉最近三天关于 systemd 的知识")
    assert recent_three_days["time_range"]["source_text"] == "最近三天"
    assert recent_three_days["time_range"]["start_time"].startswith("2026-07-06T12:00:00")
    assert recent_three_days["time_range"]["end_time"].startswith("2026-07-09T12:00:00")


def test_compiler_stops_topic_before_counted_memory_phrase():
    result = compile_request("忘记上周记录中关于 journalctl 的两条记忆，但保留其他主题。")

    assert result["topic"] == "journalctl"
    assert result["target_phrase"] == "journalctl"
    assert result["topic_aliases"] == ["journalctl"]


def test_compiler_exposes_plain_language_target_phrase_without_command_words():
    template = compile_request("删除 systemd 服务单元模板记忆")
    raw_log = compile_request("忘掉 type=PATH name=/etc/shadow 的 auditd 原始日志样例")

    assert template["target_phrase"] == "systemd 服务单元模板"
    assert raw_log["target_phrase"] == "type=PATH name=/etc/shadow 的 auditd 原始日志样例"


def test_compiler_handles_broader_field_test_phrases():
    project = compile_request(
        "清除近7天 OpenHands 当前项目里关于 rg 工具偏好的记忆",
        agent_name="OpenHands",
        session_id="sess-9",
        project_root="/repo/memguardos",
    )
    assert project["agent_name"] == "OpenHands"
    assert project["scope"] == {"level": "project", "project_root": "/repo/memguardos"}
    assert project["memory_types"] == ["preference"]
    assert project["topic"] == "rg"
    assert project["preference_topic"] == "rg"
    assert project["time_range"]["source_text"] == "近7天"
    assert project["time_range"]["start_time"].startswith("2026-07-02T12:00:00")

    explicit_session = compile_request("删除会话 sess-42 中关于 API Key 的所有记忆")
    assert explicit_session["scope"]["level"] == "session"
    assert explicit_session["scope"]["session_id"] == "sess-42"
    assert explicit_session["sensitive_labels"] == ["api_key"]

    explicit_dates = compile_request("遗忘 2026-07-01 到 2026-07-03 关于部署脚本的知识")
    assert explicit_dates["memory_types"] == ["knowledge"]
    assert explicit_dates["topic"] == "部署脚本"
    assert explicit_dates["time_range"]["source_text"] == "2026-07-01 到 2026-07-03"
    assert explicit_dates["time_range"]["start_time"].startswith("2026-07-01T00:00:00")
    assert explicit_dates["time_range"]["end_time"].startswith("2026-07-04T00:00:00")


def test_compiler_extracts_sensitive_labels_and_scope_context():
    result = compile_request(
        "忘掉当前会话里关于 Token、密码、邮箱和手机号的所有记忆",
        session_id="sess-7",
        agent_name="openhands",
    )
    assert result["scope"] == {"level": "session", "session_id": "sess-7", "agent_name": "openhands"}
    assert result["sensitive_labels"] == ["token", "password", "email", "phone_cn"]
    assert result["memory_types"] == ["all"]


def test_compiler_records_retention_constraints_for_hard_negatives():
    result = compile_request("忘记 X 项目的偏好，但保留 X 项目的知识")
    assert result["memory_types"] == ["preference"]
    assert result["retention_constraints"] == [{"memory_types": ["knowledge"], "topic": "X 项目"}]

    today = compile_request("忘记昨天的记忆，但保留今天的记忆")
    assert today["time_range"]["source_text"] == "昨天"
    assert today["retention_constraints"] == [{"time_range": "今天"}]

    agent = compile_request("忘记当前 Agent，但保留其他 Agent")
    assert agent["scope"]["level"] == "agent"
    assert agent["retention_constraints"] == [{"scope": "other_agents"}]

    style = compile_request("忘记回答风格，但保留安全确认策略")
    assert style["preference_topic"] == "回答风格"
    assert style["retention_constraints"] == [{"memory_types": ["security_policy"], "topic": "安全确认策略"}]


def test_compiler_marks_ambiguous_or_non_erasure_text_as_unresolved():
    result = compile_request("我不想再纠结这个问题")
    assert result["confidence"] < 0.5
    assert "forget_verb" in result["unresolved_slots"]
    assert result["requires_confirm"] is True


def test_service_exposes_intent_compiler_and_routes_to_agent_forgetting(tmp_path):
    service = MemGuardService(store_path=tmp_path / "memory.segment", db_path=tmp_path / "memguard.sqlite3")
    try:
        compiled = service.compile_erasure_intent(
            "忘记当前 Agent 在本轮任务中学到的回答风格偏好",
            context={"agent_name": "OpenHands", "session_id": "sess-intent"},
        )
        assert compiled["scope"]["level"] == "agent"
        assert compiled["preference_topic"] == "回答风格"

        plan = service.forget_by_intent(
            "忘记当前 Agent 在本轮任务中学到的回答风格偏好",
            context={"agent_name": "OpenHands", "session_id": "sess-intent"},
            confirm=False,
        )
        assert plan["compiled_intent"]["execution_mode"] == "dry_run"
        assert plan["route"] == "agent_interactions"
        assert plan["result"]["confirmed"] is False
    finally:
        service.close()
