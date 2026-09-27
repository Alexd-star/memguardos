"""Product-level natural-language holdout matrix.

These cases exercise the public natural-memory entry point rather than calling
individual regex helpers.  Every family includes paraphrases and hard negatives
so a passing result proves the final write decision, not only a matched token.
"""

import pytest

from memguard.natural_language import interpret_user_input
from memguard.service import MemGuardService


def service(tmp_path):
    return MemGuardService(tmp_path / "memory.segment", tmp_path / "memory.sqlite3")


@pytest.mark.parametrize(
    ("text", "expected_action", "expected_payload"),
    [
        ("查看记忆", "memory_list", ""),
        ("你记得什么", "memory_list", ""),
        ("我的偏好有哪些", "preference_list", ""),
        ("系统状态", "status", ""),
        ("搜索关于蓝河的记忆", "search", "蓝河"),
        ("检索记忆：部署流程", "search", "部署流程"),
        ("麻烦帮我查一下关于蓝河的记忆", "search", "蓝河"),
        ("运行任务：查找服务定义", "run", "查找服务定义"),
        ("让 Agent：检查审计链", "run", "检查审计链"),
        ("请你帮我运行任务：检查服务状态", "run", "检查服务状态"),
        ("记住：项目代号是蓝河", "remember", "记住：项目代号是蓝河"),
        ("麻烦你记住：构建产物在 dist 目录", "remember", "麻烦你记住：构建产物在 dist 目录"),
        ("知识记忆：构建产物在 dist 目录", "remember", "知识记忆：构建产物在 dist 目录"),
        ("忘掉 grep 的习惯", "forget", "忘掉 grep 的习惯"),
        ("把关于 grep 的工具偏好删掉", "forget", "把关于 grep 的工具偏好删掉"),
        ("请你忘掉关于蓝河的记忆", "forget", "请你忘掉关于蓝河的记忆"),
        ("我想删除关于蓝河的记忆", "forget", "我想删除关于蓝河的记忆"),
        ("帮我保存一下，项目代号是蓝河", "remember", "帮我保存一下，项目代号是蓝河"),
        ("项目代号是蓝河", "", "项目代号是蓝河"),
        ("我忘记了 grep 的参数", "", "我忘记了 grep 的参数"),
        ("删除记忆功能怎么实现", "", "删除记忆功能怎么实现"),
        ("删除 build 目录", "", "删除 build 目录"),
        ("以后默认使用 rg 好吗", "", "以后默认使用 rg 好吗"),
    ],
)
def test_public_shell_intent_matrix(text, expected_action, expected_payload):
    decision = interpret_user_input(text)
    actual_action = "" if decision.action == "ambiguous" else decision.action
    assert (actual_action, decision.payload) == (expected_action, expected_payload)


@pytest.mark.parametrize(
    ("text", "expected_label"),
    [
        ("记住我的密码 11515", "password_secret"),
        ("我的解锁口令是 010609", "password_secret"),
        ("WiFi 密码: OpenKylin2026!", "password_secret"),
        ("登录 PIN 1234", "password_secret"),
        ("本次验证码 778899", "one_time_code"),
        ("动态码：246810", "one_time_code"),
        ("OPENAI_API_KEY=sk-test-only-1234567890", "api_key"),
    ],
)
def test_public_ingest_blocks_common_credential_phrases(tmp_path, text, expected_label):
    app = service(tmp_path)
    try:
        result = app.ingest_natural_memory(text)
        labels = set((result.get("sensitivity") or {}).get("labels") or [])
        assert result["stored"] is False
        assert result["reason"] == "sensitive_block"
        assert expected_label in labels
        assert app.list_memories() == []
    finally:
        app.close()


@pytest.mark.parametrize(
    "text",
    [
        "密码长度至少八位，并启用失败次数限制。",
        "请说明忘记密码后的重置流程。",
        "验证码接口需要限制重试次数。",
        "PIN 码策略由管理员统一配置。",
        "文档中的 password_length 是配置字段。",
    ],
)
def test_security_explanations_are_not_mistaken_for_real_credentials(tmp_path, text):
    app = service(tmp_path)
    try:
        result = app.ingest_natural_memory(text)
        assert result["stored"] is True
        assert result["record"].sensitivity.level == "none"
    finally:
        app.close()


@pytest.mark.parametrize(
    ("text", "expected_tier"),
    [
        ("项目代号是蓝河，只保留到本次演示结束。", "session"),
        ("这次演示结束后删除项目代号蓝河。", "session"),
        ("临时记一下：发布窗口是今晚十点。", "session"),
        ("只在这次对话使用测试分支 feature/demo。", "session"),
        ("只在当前任务使用临时目录 /tmp/demo-output。", "working"),
        ("当前任务结束后删除这条构建备注。", "working"),
        ("长期保存：服务由 systemd 用户单元托管。", "long_term"),
        ("永久保存项目的中文回答规范。", "long_term"),
    ],
)
def test_public_ingest_honors_natural_retention_phrases(tmp_path, text, expected_tier):
    app = service(tmp_path)
    try:
        result = app.ingest_natural_memory(text, session_id="retention-test")
        assert result["stored"] is True
        assert result["record"].tier == expected_tier
    finally:
        app.close()


@pytest.mark.parametrize(
    "text",
    [
        "不要保存这条：项目代号是蓝河。",
        "不要记住这条：本次构建目录是 /tmp/build。",
        "别保存：当前演示账号是 guest。",
        "不用记住：这次临时选择 debug 模式。",
    ],
)
def test_public_ingest_respects_explicit_no_store_phrases(tmp_path, text):
    app = service(tmp_path)
    try:
        result = app.ingest_natural_memory(text)
        assert result["stored"] is False
        assert result["reason"] == "user_requested_no_memory"
        assert app.list_memories() == []
    finally:
        app.close()


@pytest.mark.parametrize(
    ("text", "topics"),
    [
        ("记住：以后搜索项目代码优先使用 rg。", {"code_search_tool"}),
        ("以后运行 Python 测试请使用 python -m pytest。", {"test_runner_command"}),
        ("记住：以后用中文简洁回答。", {"response_language", "response_verbosity"}),
        ("回答时先给可复制命令，再解释结果。", {"response_order"}),
        (
            "纠正一下：不要再用 grep，改用 rg，并且先告诉我怎么做，然后说明原因。",
            {"code_search_tool", "response_order"},
        ),
    ],
)
def test_public_ingest_creates_actionable_structured_preferences(tmp_path, text, topics):
    app = service(tmp_path)
    try:
        result = app.ingest_natural_memory(text)
        active = app.list_memories(governance_state="active")
        assert result["stored"] is True
        assert {row["preference_topic"] for row in active} == topics
        assert all(row["preference_value"] for row in active)
    finally:
        app.close()


@pytest.mark.parametrize(
    "text",
    [
        "默认用 rg 还是 grep 好？",
        "以后默认使用 rg 好吗",
        "我忘记了 grep 的参数，请告诉我怎么用。",
        "解释为什么 grep 没有找到结果。",
        "这份文档比较了中文和英文回答的差异。",
        "删除名为 response_order.txt 的测试文件。",
    ],
)
def test_public_ingest_hard_negatives_do_not_create_structured_preferences(tmp_path, text):
    app = service(tmp_path)
    try:
        result = app.ingest_natural_memory(text)
        assert result.get("natural_intent") == "memory_content"
        assert result["record"].type != "preference"
        assert not [row for row in app.list_memories() if row.get("preference_topic")]
    finally:
        app.close()


def test_one_time_tool_choice_is_visible_as_candidate_not_silent_or_stable(tmp_path):
    app = service(tmp_path)
    try:
        result = app.ingest_natural_memory("请用 rg 查找服务定义。")
        observed = result["feedback"]["observed_preferences"]

        assert result["stored"] is False
        assert observed[0]["category_label"] == "工具偏好"
        assert observed[0]["status"] == "candidate"
        assert observed[0]["status_label"] == "正在观察"
        assert app.list_memories() == []
    finally:
        app.close()


def test_temporary_structured_preference_is_scoped_to_current_session(tmp_path):
    app = service(tmp_path)
    try:
        result = app.ingest_natural_memory(
            "记住：本次演示搜索项目代码优先使用 grep。",
            tier="session",
            session_id="demo-session",
        )
        record = result["record"]

        assert result["stored"] is True
        assert record["preference_topic"] == "code_search_tool"
        assert record["preference_scope"] == "session"
        assert record["tier"] == "session"
        assert record["preference_session_id"] == "demo-session"
    finally:
        app.close()


def test_open_domain_user_habit_is_retrievable_by_agent_context(tmp_path):
    app = service(tmp_path)
    try:
        result = app.ingest_natural_memory("记住：我喜欢深色模式，通知保持静默。")
        record = result["record"]
        context = app.build_agent_context(
            task="请按照我的界面和通知习惯给出设置建议",
            stage="response_generation",
            agent_name="openhands",
            session_id="new-session",
        )

        assert result["stored"] is True
        assert record.type == "preference"
        assert record.subtype == "user_habit"
        assert any(item["memory_id"] == record.memory_id for item in context["memories"])
        assert app.search("我的命令行颜色偏好") == []
    finally:
        app.close()
