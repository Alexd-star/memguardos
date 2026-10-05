import pytest

from memguard.sensitivity.detector import SensitiveDetector
from memguard.service import MemGuardService


SECRET_CASES = [
    "记住我的密码11515", "记住我的密码 11515", "保存密码ab", "登录PIN1234",
    "解锁PIN码12", "我的密码是12", "口令设置为7", "PIN为010609",
    "登录密码是春天来了", "WiFi 密码: OpenKylin2026!",
    "Remember my password 12", "Save my PIN 1234", "Store the passcode xy",
    "my password is ab", "PIN is 12", "PIN code equals 19", "password=7",
    "passwd: xy", "pwd=ab", "passcode is violet-fabric-77",
    'password is "required"', "password is 'not set'", "密码是“用于认证的字符串”",
    "文档里写着我的密码是11515。", "requirements: PIN is 1234",
    "The documentation says password is maple7.", "password=required",
    "PIN 是本任务必须复核的关键点；密码是ab。",
    "密码是用于认证的字符串，但我的PIN为12。",
    "Password is not configured, but the PIN is 1234.",
    "The PIN is a review point. Remember my password 12.",
    "PIN is 4 digits: 1234", "Password is required: maple7",
    "密码策略需要定期更新；密码=ab。",
    "Password is\nhunter2", "密码是\n11515", "DB_PASSWORD=ab", "device_pin=12",
]

NONVALUE_CASES = [
    "pin 是本任务必须复核的关键点。",
    "口令是该模块需要检查的配置项。",
    "密码是当前项目必须讨论的安全概念。",
    "PIN是用于设备认证的代码。", "密码是用来保护账户的字符串。",
    "密码是一个字段名称。", "口令是认证所用的凭据。",
    "密码是尚未设置的，不应写入值。", "PIN 是未配置的状态。",
    "密码不是实际值，只是接口说明。", "文档未提供PIN值。",
    "密码为至少八位字符。", "PIN为4位数字。", "密码需要定期更新。",
    "请记住密码策略需要定期更新。", "保存密码要求供复核。",
    "PIN is a required review item.", "Password is a string used for authentication.",
    "PIN is an authentication code.", "Password is the required field.",
    "Password is not set.", "PIN is not yet configured.",
    "Password is not provided in the documentation.", "Passcode is missing.",
    "Password is required for login.", "PIN is optional for this workflow.",
    "Password is used to authenticate users.", "PIN is needed for unlocking.",
    "Password is at least 12 characters long.", "PIN is 4 to 6 digits long.",
    "Password is not a secret in this conceptual explanation.",
    "Remember the password policy.", "Save the PIN requirements for review.",
    "Documentation: password_length=12; PIN must contain 4 digits.",
    "Password is required. PIN is not configured; no value is provided.",
    "PIN是关键概念。", "Password is\nrequired for login.",
]


@pytest.mark.parametrize("text", SECRET_CASES)
def test_actual_values_remain_high_risk_even_in_documentation(text):
    detector = SensitiveDetector()
    sensitivity = detector.classify(text)
    assert sensitivity.level == "high"
    assert "password_secret" in sensitivity.labels
    assert sensitivity.decision_reason["final_action"] == "block"


@pytest.mark.parametrize("text", NONVALUE_CASES)
def test_local_definitions_requirements_and_absent_values_are_not_secrets(text):
    detector = SensitiveDetector()
    assert detector.classify(text).labels == []
    assert detector.redact(text) == text


@pytest.mark.parametrize(
    "text",
    [
        "记住我的密码11515", "登录PIN1234", "Remember my password 12",
        "文档说明：密码是11515。", "PIN 是关键概念；password=ab",
    ],
)
def test_service_ingest_rejects_actual_values_via_sensitive_policy(tmp_path, monkeypatch, text):
    monkeypatch.setenv("MEMGUARD_EMBEDDING_BACKEND", "hash")
    service = MemGuardService(tmp_path / "memory.segment", tmp_path / "meta.sqlite3")
    try:
        result = service.ingest_text(text, source_kind="user_cli")
        assert result["stored"] is False
        assert result["reason"] == "sensitive_block"
        assert "password_secret" in result["sensitivity"].labels
        assert service.list_memories() == []
    finally:
        service.close()


@pytest.mark.parametrize(
    "text",
    [
        "pin 是本任务必须复核的关键点。", "口令是该模块需要检查的配置项。",
        "Password is a string used for authentication.",
        "Password is not provided in the documentation.",
        "PIN is 4 to 6 digits long.",
    ],
)
def test_service_ingest_stores_conceptual_text_without_redaction(tmp_path, monkeypatch, text):
    monkeypatch.setenv("MEMGUARD_EMBEDDING_BACKEND", "hash")
    service = MemGuardService(tmp_path / "memory.segment", tmp_path / "meta.sqlite3")
    try:
        result = service.ingest_text(text, source_kind="user_cli")
        assert result["stored"] is True
        assert result["record"].sensitivity.labels == []
        assert result["record"].content == text
    finally:
        service.close()


def test_nonvalue_clause_does_not_disable_other_sensitive_rules():
    detector = SensitiveDetector()
    text = "PIN is a review point; token=abcdefgh12345678"
    assert "token_assignment" in detector.classify(text).labels
    assert detector.classify(text).level == "high"
    assert detector.classify("PIN is not configured", "/home/demo/.env").level == "high"


def test_custom_password_pattern_is_not_semantically_filtered():
    detector = SensitiveDetector({"password_secret": {"regex": r"PIN is required", "level": "high"}})
    assert detector.classify("PIN is required").level == "high"


def test_clauses_and_newlines_do_not_supply_a_missing_password_value():
    detector = SensitiveDetector()
    text = "Password is\nrequired for login; PIN is\nnot configured."
    assert detector.classify(text).labels == []


def test_redaction_keeps_explanations_but_removes_adjacent_secret():
    text = "PIN 是本任务必须复核的关键点；记住我的密码11515。"
    redacted = SensitiveDetector().redact(text)
    assert "PIN 是本任务必须复核的关键点" in redacted
    assert "11515" not in redacted
    assert "[REDACTED:password_secret]" in redacted


@pytest.mark.parametrize(
    ("text", "expected_label"),
    [
        ("pwd=syntheticLong99", "token_assignment"),
        ("DB_PASSWORD=syntheticLong99", "token_assignment"),
        ("my_pwd: syntheticLong99", "token_assignment"),
        ("app_password='syntheticLong99'", "token_assignment"),
        ("pwd=12", "password_secret"),
        ("DB_PASSWORD=ab", "password_secret"),
    ],
)
def test_code_assignment_keeps_existing_label_but_short_values_still_block(text, expected_label):
    detector = SensitiveDetector()
    sensitivity = detector.classify(text)
    assert sensitivity.level == "high"
    assert sensitivity.labels == [expected_label]
    redacted = detector.redact(text)
    assert f"[REDACTED:{expected_label}]" in redacted
    assert "syntheticLong99" not in redacted
    assert "=12" not in redacted and "=ab" not in redacted


def test_lower_risk_custom_assignment_cannot_suppress_high_risk_password():
    detector = SensitiveDetector({"token_assignment": {"regex": r"pwd=.+", "level": "low"}})
    sensitivity = detector.classify("pwd=syntheticLong99")
    assert sensitivity.level == "high"
    assert "password_secret" in sensitivity.labels
