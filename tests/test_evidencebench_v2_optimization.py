from __future__ import annotations

from pathlib import Path

from memguard.forgetting.natural_intent import ErasureIntentCompiler
from memguard.retrieval.hybrid import HybridRetriever, expand_query_aliases
from memguard.sensitivity.detector import SensitiveDetector
from memguard.service import MemGuardService
from scripts.audit_evidencebench_v2_dataset import audit_dataset


def test_lightweight_reranker_prioritizes_security_policy_over_audit_neighbor():
    retriever = HybridRetriever(embedding_backend="hash")
    records = [
        {
            "memory_id": "audit-neighbor",
            "type": "knowledge",
            "subtype": "fact",
            "summary": "auditd 记录用户态访问，BPF LSM 只拦截配置中的高风险路径。",
            "content": "auditd 记录用户态访问，BPF LSM 只拦截配置中的高风险路径。",
            "source_kind": "evidencebench_v2",
            "preference_topic": "",
            "entities": "",
        },
        {
            "memory_id": "security-policy",
            "type": "sensitive_policy",
            "subtype": "security_policy",
            "summary": "高风险命令需要二次确认，不因效率偏好绕过安全策略。",
            "content": "高风险命令需要二次确认，不因效率偏好绕过安全策略。",
            "source_kind": "evidencebench_v2",
            "preference_topic": "high_risk_confirmation",
            "entities": "高风险命令,二次确认,安全策略",
        },
    ]

    ranked = retriever.rank("高风险命令需要二次确认", records, limit=2)

    assert ranked[0][1]["memory_id"] == "security-policy"
    assert ranked[0][1]["_score_breakdown"]["topic_score"] > 0


def test_lightweight_reranker_prioritizes_tool_preference():
    retriever = HybridRetriever(embedding_backend="hash")
    records = [
        {
            "memory_id": "generic-rg",
            "type": "knowledge",
            "subtype": "fact",
            "summary": "ripgrep 是一个快速文本搜索工具。",
            "content": "rg 可用于代码库搜索。",
            "source_kind": "evidencebench_v2",
            "preference_topic": "",
        },
        {
            "memory_id": "tool-preference",
            "type": "preference",
            "subtype": "tool_preference",
            "summary": "代码搜索优先使用 rg，服务排障优先 systemctl status。",
            "content": "代码搜索优先使用 rg，服务排障优先 systemctl status。",
            "source_kind": "evidencebench_v2",
            "preference_topic": "code_search_tool",
        },
    ]

    ranked = retriever.rank("代码搜索优先使用 rg", records, limit=2)

    assert ranked[0][1]["memory_id"] == "tool-preference"
    assert ranked[0][1]["_score_breakdown"]["subtype_score"] > 0


def test_query_alias_expansion_records_trace_for_cross_language_terms():
    expanded = expand_query_aliases("precise forgetting should inspect managed storage and audit chain")

    assert "精准遗忘" in expanded["expanded_query"]
    assert "受管存储" in expanded["expanded_query"]
    assert "审计链" in expanded["expanded_query"]
    assert expanded["expansion_trace"]


def test_query_alias_expansion_covers_openkylin_service_resource_language():
    expanded = expand_query_aliases(
        "Which resource limits should be checked before restarting an openKylin service?"
    )

    assert "cgroup" in expanded["expanded_query"]
    canonicals = [item["canonical"] for item in expanded["expansion_trace"]]
    assert len(canonicals) == len(set(canonicals))
    assert expanded["expansion_trace"]


def test_hash_rank_drops_records_supported_only_by_hash_collision(monkeypatch):
    retriever = HybridRetriever(embedding_backend="hash")
    monkeypatch.setattr(
        retriever,
        "_score_with_vectors",
        lambda query, record, query_vector, text_vector: 0.35,
    )
    monkeypatch.setattr(
        retriever,
        "lightweight_score_breakdown",
        lambda query, record: {
            "summary_score": 0.0,
            "content_score": 0.0,
            "entity_score": 0.0,
            "topic_score": 0.0,
            "subtype_score": 0.0,
            "exact_phrase_score": 0.0,
            "hard_negative_penalty": 0.0,
            "scope_penalty": 0.0,
            "alias_expansion": {
                "query": query,
                "expanded_query": query,
                "aliases": [],
                "expansion_trace": [],
            },
            "total": 0.0,
        },
    )

    ranked = retriever.rank(
        "openKylin service memory limits",
        [
            {
                "memory_id": "unrelated",
                "type": "knowledge",
                "summary": "桌面壁纸轮换计划",
                "content": "只记录主题颜色。",
            }
        ],
        limit=5,
    )

    assert ranked == []


def test_query_aliases_do_not_turn_a_document_false_friend_into_a_match():
    retriever = HybridRetriever(embedding_backend="hash")

    ranked = retriever.rank(
        "How is managed plaintext absence verified after segment compaction?",
        [
            {
                "memory_id": "mmap-tutorial",
                "type": "knowledge",
                "summary": "mmap 教程介绍 MAP_PRIVATE 的写时复制语义。",
                "content": "mmap 教程介绍 MAP_PRIVATE 的写时复制语义。",
            }
        ],
        limit=5,
    )

    assert ranked == []


def test_latin_alias_matching_does_not_treat_compact_as_compaction():
    expanded = expand_query_aliases(
        "How is managed plaintext absence verified after segment compaction?"
    )

    assert "compact" in expanded["aliases"]


def test_sensitivity_allowlist_distinguishes_demo_tokens_and_code_terms():
    detector = SensitiveDetector()

    demo = detector.classify("文档示例 sk-demo-0000000000000000 和 token.service 均不是真实凭据。")
    mmap = detector.classify("mmap 教程里 MAP_PRIVATE 和 PRIVATE_KEYWORDS 是普通符号。")
    real = detector.classify("生产 API_TOKEN=sk-live-1234567890abcdef1234567890abcd")

    assert demo.level in {"none", "low"}
    assert mmap.level in {"none", "low"}
    assert real.level == "high"
    assert real.decision_reason["final_action"] == "block"
    assert demo.decision_reason["allowlist_hit"]


def test_erasure_intent_outputs_standard_topic_aliases():
    intent = ErasureIntentCompiler().compile("忘记高风险命令需要二次确认，但保留不相关记忆")

    assert intent["preference_topic"] == "high_risk_confirmation"
    assert "高风险命令" in intent["topic_aliases"]
    assert "二次确认" in intent["topic_aliases"]


def test_forget_planner_recalls_high_risk_confirmation_without_hitting_keep_text(tmp_path: Path):
    service = MemGuardService(store_path=tmp_path / "memory.segment", db_path=tmp_path / "memguard.sqlite3")
    try:
        delete = service.ingest_text("高风险命令需要二次确认，不因效率偏好绕过安全策略。", source_uri="delete")
        keep = service.ingest_text("不应遗忘：其他 Agent 的偏好不属于当前 Agent 范围。", source_uri="keep")
        plan = service.forget("忘记高风险命令需要二次确认，但保留不相关记忆")
        candidate_ids = {item["memory_id"] for item in plan["candidates"]}

        assert delete["record"].memory_id in candidate_ids
        assert keep["record"].memory_id not in candidate_ids
    finally:
        service.close()


def test_forget_planner_keeps_all_topic_peers_and_excludes_other_topics(tmp_path: Path):
    service = MemGuardService(store_path=tmp_path / "memory.segment", db_path=tmp_path / "memguard.sqlite3")
    try:
        primary = service.ingest_text("自然语言请求：高风险命令需要二次确认，不因效率偏好绕过安全策略。")
        peer = service.ingest_text("自然语言请求补充：二次确认是本任务必须复核的关键点。")
        other_agent = service.ingest_text("另一个 Agent 偏好在回答前先列出假设。")
        unrelated = service.ingest_text("独立主题记录：API 文档使用保留域名作为占位邮箱。")

        plan = service.forget("忘记自然语言请求中关于二次确认的两条记忆，但保留其他主题。")
        candidate_ids = {item["memory_id"] for item in plan["candidates"]}

        assert candidate_ids == {
            primary["record"].memory_id,
            peer["record"].memory_id,
        }
        assert other_agent["record"].memory_id not in candidate_ids
        assert unrelated["record"].memory_id not in candidate_ids
    finally:
        service.close()


def test_forget_planner_requires_the_requested_latin_anchor(tmp_path: Path):
    service = MemGuardService(store_path=tmp_path / "memory.segment", db_path=tmp_path / "memguard.sqlite3")
    try:
        primary = service.ingest_text("上周记录：服务重启前先查看 journalctl 和 cgroup 限制。")
        peer = service.ingest_text("上周记录补充：journalctl 是本任务必须复核的关键点。")
        unrelated = service.ingest_text("独立主题记录：systemd 单元 token.service 当前处于 active 状态。")

        plan = service.forget("忘记上周记录中关于 journalctl 的两条记忆，但保留其他主题。")
        candidate_ids = {item["memory_id"] for item in plan["candidates"]}

        assert candidate_ids == {
            primary["record"].memory_id,
            peer["record"].memory_id,
        }
        assert unrelated["record"].memory_id not in candidate_ids
    finally:
        service.close()


def test_forget_planner_keeps_short_procedural_peer_without_unrelated_records(
    tmp_path: Path,
):
    service = MemGuardService(
        store_path=tmp_path / "memory.segment", db_path=tmp_path / "memguard.sqlite3"
    )
    try:
        primary = service.ingest_text(
            "自然语言请求：pytest 失败后先定位最小失败用例，再修复并回归全量测试。"
        )
        peer = service.ingest_text(
            "自然语言请求补充：最小失败用例是本任务必须复核的关键点。"
        )
        unrelated = service.ingest_text("BPF 教程解释 verifier 的基本检查流程。")

        plan = service.forget(
            "忘记自然语言请求中关于最小失败用例的两条记忆，但保留其他主题。"
        )
        candidate_ids = {item["memory_id"] for item in plan["candidates"]}

        assert candidate_ids == {
            primary["record"].memory_id,
            peer["record"].memory_id,
        }
        assert unrelated["record"].memory_id not in candidate_ids
    finally:
        service.close()


def test_erasure_intent_does_not_treat_retention_prefix_or_plain_pytest_as_preference():
    compiler = ErasureIntentCompiler()
    retained_prefix = compiler.compile(
        "忘记保留约束中关于二次确认的两条记忆，但保留其他主题。"
    )
    plain_pytest = compiler.compile(
        "忘记独立留出任务中关于 python -m pytest 的两条记忆，但保留其他主题。"
    )

    assert retained_prefix["target_phrase"] == "二次确认"
    assert retained_prefix["preference_topic"] == "high_risk_confirmation"
    assert retained_prefix["normalized_preference_topic"] == "high_risk_confirmation"
    assert plain_pytest["target_phrase"] == "python -m pytest"
    assert plain_pytest["preference_topic"] == ""
    assert plain_pytest["normalized_preference_topic"] == ""


def test_evidencebench_dataset_audit_reports_topic_acceptable_metadata():
    report = audit_dataset()

    assert report["summary"]["total_rows"] == 260
    assert report["summary"]["duplicate_query_count"] == 0
    assert report["summary"]["duplicate_seed_text_count"] == 0
    assert report["summary"]["multi_expected_query_count"] == 0
    assert report["summary"]["hard_negative_too_similar_count"] == 0
    assert report["summary"]["missing_acceptable_ids"] == 0
    assert report["summary"]["missing_evaluation_mode"] == 0
