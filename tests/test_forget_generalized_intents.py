import json
from pathlib import Path

import pytest

from memguard.forgetting.natural_intent import ErasureIntentCompiler
from memguard.forgetting.planner import ForgetPlanner
from memguard.service import MemGuardService


def _record(memory_id, text, memory_type="knowledge", **fields):
    return {
        "memory_id": memory_id,
        "version_id": "version-" + memory_id,
        "type": memory_type,
        "subtype": "fact",
        "summary": text,
        "content": text,
        **fields,
    }


@pytest.mark.parametrize("topic", ["systemctl status", "python -m pytest", "git diff", "cargo test"])
def test_explicit_topic_peers_do_not_depend_on_retrieval_scores(monkeypatch, topic):
    planner = ForgetPlanner()
    records = [
        _record("long", "巡检记录：" + topic + "，另有终端配色记录。"),
        _record("short", "执行 " + topic),
        _record("value", "结构化工具选择", "preference", preference_value=topic),
        _record("unknown", "本任务必须复核的关键点。"),
    ]
    monkeypatch.setattr(planner.retriever, "rank", lambda *args, **kwargs: [(2.0, records[0])])

    candidates = planner.candidates("忘记巡检记录中关于 " + topic + " 的三条记忆", records)

    assert {item["memory_id"] for item in candidates} == {"long", "short", "value"}
    assert all(item["exact_topic_match"] for item in candidates)


@pytest.mark.parametrize(
    "topic, near_matches",
    [
        ("systemctl status", ["systemctl restart", "systemctl status-other", "mysystemctl status"]),
        ("python -m pytest", ["python pytest", "python -v pytest", "python -m pytest_extra"]),
        ("git diff", ["git show", "git diff-tree", "legit diff"]),
    ],
)
def test_concrete_command_does_not_authorize_related_or_partial_commands(topic, near_matches):
    records = [_record("target", topic)]
    records += [_record("near-" + str(index), text) for index, text in enumerate(near_matches)]
    records.append(_record(topic, "桌面配色与壁纸"))

    candidates = ForgetPlanner().candidates("忘记关于 " + topic + " 的记忆", records)

    assert {item["memory_id"] for item in candidates} == {"target"}


@pytest.mark.parametrize("exception_prefix", ["除了", "除去"])
def test_exact_topic_recall_still_intersects_types_qualifiers_and_retention(monkeypatch, exception_prefix):
    planner = ForgetPlanner()
    request = exception_prefix + " git diff 模板案例以外，忘记关于 git diff 模板的知识"
    records = [
        _record("target", "git diff 模板"),
        _record("wrong-type", "git diff 模板", "preference"),
        _record("wrong-qualifier", "git diff"),
        _record("protected", "git diff 模板案例"),
    ]
    monkeypatch.setattr(planner.retriever, "rank", lambda *args, **kwargs: [])

    candidates = planner.candidates(request, records)

    assert {item["memory_id"] for item in candidates} == {"target"}


def test_exact_topic_recall_still_requires_sensitive_label(monkeypatch):
    planner = ForgetPlanner()
    intent = planner.intent_compiler.compile("忘记关于部署配置的记忆")
    intent["sensitive_labels"] = ["password"]
    records = [_record("target", "部署配置 password 字段"), _record("unknown", "部署配置版本号")]
    monkeypatch.setattr(planner.retriever, "rank", lambda *args, **kwargs: [])

    candidates = planner.candidates(intent["source_text"], records, intent=intent)

    assert {item["memory_id"] for item in candidates} == {"target"}


def test_generic_topic_does_not_enable_exhaustive_exact_recall(monkeypatch):
    planner = ForgetPlanner()
    monkeypatch.setattr(planner.retriever, "rank", lambda *args, **kwargs: [])

    assert planner.candidates("忘记关于命令的记忆", [_record("unknown", "运行命令记录")]) == []


@pytest.mark.parametrize("subject", ["星港部署回滚流程", "蓝溪网络恢复流程"])
def test_partial_compound_topic_does_not_cover_the_authorized_anchor(subject):
    records = [
        _record("target", subject + "：先检查状态，再复核结果。"),
        _record("neighbor", subject[:4] + "说明：只介绍状态展示，不描述流程。"),
        _record("other", "独立主题：缓存清理不会替代版本检查。"),
    ]
    candidates = ForgetPlanner().candidates("忘记" + subject + "这条知识", records)
    assert {item["memory_id"] for item in candidates} == {"target"}


def test_workflow_anchor_cannot_be_satisfied_only_by_shared_audit_topic():
    records = [
        _record("target", "审计链失败处理流程：停止服务后恢复完整备份，再验证。"),
        _record("neighbor", "审计链说明：普通缓存清理不能替代完整性证明。"),
    ]
    candidates = ForgetPlanner().candidates("忘掉审计链失败恢复流程", records)
    assert {item["memory_id"] for item in candidates} == {"target"}


def test_generic_security_policy_is_not_high_risk_confirmation():
    records = [
        _record("target", "安全策略：高风险操作前先让我确认。", "sensitive_policy"),
        _record("privacy", "安全策略：不要把生产密钥写入长期记忆。", "sensitive_policy"),
        _record("presentation", "安全策略：展示脱敏后的来源分布。", "sensitive_policy"),
    ]
    candidates = ForgetPlanner().candidates("删除高风险操作确认策略", records)
    assert {item["memory_id"] for item in candidates} == {"target"}


@pytest.mark.parametrize("verb", ["删除", "清除"])
def test_delete_verb_and_external_target_are_not_an_exception_clause(verb):
    intent = ErasureIntentCompiler().compile(verb + "默认不依赖外部 LLM API 的答辩口径")
    assert intent["unresolved_slots"] == []
    assert intent["target_phrase"] == "默认不依赖外部 LLM API 的答辩口径"
    assert intent["retention_constraints"] == []


@pytest.mark.parametrize("verb", ["forget", "Forget", "FORGET", "delete", "Remove", "ERASE", "clear"])
def test_english_forget_commands_are_case_insensitive_and_strip_the_verb(verb):
    intent = ErasureIntentCompiler().compile(verb + " invoice inventory")
    assert intent["unresolved_slots"] == []
    assert intent["target_phrase"] == "invoice inventory"
    assert intent["memory_types"] == ["all"]
    assert intent["scope"] == {"level": "global"}
    assert intent["requires_confirm"] is True
    assert intent["execution_mode"] == "dry_run"


@pytest.mark.parametrize("request_text", [
    "forgetfulness about invoice inventory", "Do not forget invoice inventory",
    "Please do not delete invoice inventory", "I might forget invoice inventory",
    "Forget nothing about invoice inventory", "DELETE no invoice inventory",
    "The word forget describes invoice inventory", "invoice inventory",
])
def test_english_word_mentions_or_negation_do_not_authorize_erasure(request_text):
    assert "forget_verb" in ErasureIntentCompiler().compile(request_text)["unresolved_slots"]


def test_polite_english_forgetting_recalls_only_target_content(tmp_path):
    service = MemGuardService(tmp_path / "memory.segment", tmp_path / "meta.sqlite3")
    try:
        target = service.ingest_text("Invoice inventory source.")["record"]
        keep = service.ingest_text("Invoice payment receipt.")["record"]
        preview = service.forget("Please FORGET invoice inventory")
        assert {item["memory_id"] for item in preview["candidates"]} == {target.memory_id}
        assert not service.meta.get_memory(keep.memory_id)["deleted"]
        blocked = service.forget("Do not forget invoice inventory", confirm=True)
        assert blocked["forgotten"] == []
    finally:
        service.close()


def test_retained_all_memories_cannot_expand_default_tool_authorization():
    intent = ErasureIntentCompiler().compile("忘记使用 grep，但保留所有其他记忆")

    assert intent["memory_types"] == ["preference"]
    assert intent["normalized_preference_topic"] == "code_search_tool"
    assert intent["execution_mode"] == "dry_run"
    assert intent["requires_confirm"] is True


@pytest.mark.parametrize(
    "protected",
    ["昨天 OpenHands 当前项目的 API Key 记忆", "当前会话关于 pytest 的模板案例知识"],
)
def test_protected_clause_cannot_supply_delete_filters(protected):
    compiler = ErasureIntentCompiler()
    expected = compiler.compile("忘记关于 git diff 的知识")
    actual = compiler.compile("忘记关于 git diff 的知识，但保留 " + protected)

    for key in ("topic", "target_phrase", "memory_types", "scope", "agent_name", "project_hint", "time_range", "sensitive_labels"):
        assert actual[key] == expected[key]
    assert actual["source_text"].endswith(protected)


def test_exception_clause_cannot_supply_delete_topic_or_labels():
    intent = ErasureIntentCompiler().compile("除了昨天 OpenHands 的 API Key 以外，忘记 git diff 的知识")

    assert intent["topic"] == ""
    assert intent["target_phrase"] == "git diff"
    assert intent["sensitive_labels"] == []
    assert intent["agent_name"] == ""
    assert intent["time_range"]["source_text"] == ""
    assert intent["retention_constraints"]


def test_exact_topic_selection_uses_only_the_caller_scoped_corpus(tmp_path, monkeypatch):
    service = MemGuardService(tmp_path / "memory.segment", tmp_path / "meta.sqlite3")
    context = {"project_root": str(tmp_path / "project-a")}
    try:
        target = service.ingest_natural_memory(
            "git diff 检查当前变更。", session_id="session-a", project_root=context["project_root"],
        )["record"]
        service.ingest_natural_memory(
            "git diff 检查其他项目变更。", session_id="session-b", project_root=str(tmp_path / "project-b"),
        )
        monkeypatch.setattr(service.forget_planner.retriever, "rank", lambda *args, **kwargs: [])
        for request, scoped_context in [
            ("忘记当前项目关于 git diff 的知识", context),
            ("忘记会话 session-a 中关于 git diff 的知识", {}),
        ]:
            preview = service.forget_by_intent(request, context=scoped_context)["result"]
            assert {item["memory_id"] for item in preview["candidates"]} == {target.memory_id}

        unresolved = service.forget_by_intent("忘记当前项目关于 git diff 的知识", context={})["result"]
        assert unresolved["candidates"] == []
        assert unresolved["time_window"]["scope_blocked"] is True
    finally:
        service.close()


def test_exact_topic_selection_cannot_resurrect_out_of_window_records(tmp_path, monkeypatch):
    service = MemGuardService(tmp_path / "memory.segment", tmp_path / "meta.sqlite3")
    try:
        recent = service.ingest_text("git diff 检查当前变更。")["record"]
        old = service.ingest_text("git diff 检查历史变更。")["record"]
        service.meta.conn.execute(
            "UPDATE memory SET valid_from = ?, updated_at = ? WHERE memory_id = ?",
            ("2020-01-01T00:00:00+00:00", "2020-01-01T00:00:00+00:00", old.memory_id),
        )
        service.meta.conn.commit()
        monkeypatch.setattr(service.forget_planner.retriever, "rank", lambda *args, **kwargs: [])

        preview = service.forget("忘记最近三天关于 git diff 的知识")

        assert {item["memory_id"] for item in preview["candidates"]} == {recent.memory_id}
        assert preview["time_window"]["applied"] is True
    finally:
        service.close()


@pytest.mark.parametrize(
    "relative_path",
    [
        "forgetting/natural_language_erasure.jsonl",
        "forgetting/scoped_agent_forgetting.jsonl",
        "forgetting/time_range_forgetting.jsonl",
        "forgetting/forget_keep_hard_negatives.jsonl",
        "holdout/holdout_forgetting.jsonl",
    ],
)
def test_reported_misses_recall_exact_atoms_without_related_preference(tmp_path, relative_path):
    root = Path(__file__).resolve().parents[1]
    cases = [json.loads(line) for line in (root / "data/benchmarks_v2" / relative_path).read_text(encoding="utf-8").splitlines()]
    case = next(item for item in cases if item["id"].endswith("_08"))
    service = MemGuardService(tmp_path / "memory.segment", tmp_path / "meta.sqlite3")
    context = {"agent_name": "memguardos", "session_id": "isolated-case", "project_root": str(tmp_path / "project")}
    try:
        labels = {}
        for candidate in case["candidates"]:
            ingested = service.ingest_natural_memory(
                candidate["text"], source_kind="evidencebench_v2",
                session_id=context["session_id"], project_root=context["project_root"],
            )
            for record in ingested.get("records") or [ingested["record"]]:
                memory_id = record["memory_id"] if isinstance(record, dict) else record.memory_id
                labels[memory_id] = candidate["id"]

        preview = service.forget_by_intent(case["request"], context=context, confirm=False)
        candidates = preview["result"]["candidates"]

        assert {labels[item["memory_id"]] for item in candidates} == set(case["expected_delete"])
        assert all(item["preference_topic"] != "code_search_tool" for item in candidates)
        assert preview["route"] == "managed_memory"
    finally:
        service.close()
