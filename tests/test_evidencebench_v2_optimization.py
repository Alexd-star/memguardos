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


def test_evidencebench_dataset_audit_reports_topic_acceptable_metadata():
    report = audit_dataset()

    assert report["summary"]["total_rows"] == 260
    assert report["summary"]["duplicate_query_count"] == 0
    assert report["summary"]["duplicate_seed_text_count"] == 0
    assert report["summary"]["multi_expected_query_count"] == 0
    assert report["summary"]["hard_negative_too_similar_count"] == 0
    assert report["summary"]["missing_acceptable_ids"] == 0
    assert report["summary"]["missing_evaluation_mode"] == 0
