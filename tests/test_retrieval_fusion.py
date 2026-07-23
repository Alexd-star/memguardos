import pytest

from memguard.retrieval.fusion import QueryIntent, classify_query_intent, rrf_fuse
from memguard.service import MemGuardService


def make_service(tmp_path, namespace):
    return MemGuardService(
        store_path=tmp_path / (namespace + ".jsonl"),
        db_path=tmp_path / (namespace + ".sqlite3"),
        namespace=namespace,
    )


def test_query_intent_router_is_deterministic():
    assert classify_query_intent("我上次明确说代码搜索用什么工具") == QueryIntent.PREFERENCE
    assert classify_query_intent("修复 systemd 服务失败的步骤是什么") == QueryIntent.PROCEDURAL
    assert classify_query_intent("项目 A 和工具 B 有什么关系") == QueryIntent.RELATIONAL
    assert classify_query_intent("上周更新后的配置") == QueryIntent.TEMPORAL
    assert classify_query_intent("memory_id mem-123") == QueryIntent.EXACT_LOOKUP
    assert classify_query_intent("openKylin 系统服务") == QueryIntent.GENERAL


def test_rrf_fuses_independent_rankings_without_score_calibration():
    fused = rrf_fuse(
        {"lexical": ["a", "b", "c"], "semantic": ["b", "d", "a"]},
        weights={"lexical": 1.0, "semantic": 1.0},
        k=60,
    )
    assert [item.memory_id for item in fused[:2]] == ["b", "a"]
    assert fused[0].sources == ("lexical", "semantic")


def test_exact_hit_protection_keeps_exact_match_first():
    fused = rrf_fuse(
        {"lexical": ["exact", "other"], "semantic": ["other", "exact"]},
        exact_hits={"exact"},
    )
    assert fused[0].memory_id == "exact"
    assert fused[0].protected is True


def test_rrf_deduplicates_each_source_and_handles_empty_rankings():
    fused = rrf_fuse({"lexical": ["a", "a", "b"], "semantic": []})
    assert [item.memory_id for item in fused] == ["a", "b"]
    assert rrf_fuse({}) == []


def test_rrf_rejects_invalid_configuration_and_empty_ids():
    assert [row.memory_id for row in rrf_fuse({"lexical": ["", "a"]})] == ["a"]
    with pytest.raises(ValueError, match="positive"):
        rrf_fuse({"lexical": ["a"]}, k=0)
    with pytest.raises(ValueError, match="non-negative"):
        rrf_fuse({"lexical": ["a"]}, weights={"lexical": -1.0})


def test_service_only_uses_graph_for_relational_intent(tmp_path, monkeypatch):
    service = make_service(tmp_path, "intent-router")
    try:
        service.ingest_text("项目 A 使用工具 B", source_kind="agent_interaction")
        calls = []

        def fake_graph_search(entity, limit=10):
            calls.append(entity)
            return {"layers": {"summary": [], "entity_relation": [], "chunk": []}}

        monkeypatch.setattr(service, "hierarchical_graph_search", fake_graph_search)
        service.search("项目 A 的摘要", limit=5)
        assert calls == []
        service.search("项目 A 和工具 B 有什么关系", limit=5)
        assert calls == ["项目 A 和工具 B 有什么关系"]
    finally:
        service.close()


def test_service_exposes_fusion_trace_and_protects_exact_phrase(tmp_path):
    service = make_service(tmp_path, "fusion-trace")
    try:
        exact = service.ingest_text(
            "用户明确要求回答时先给命令", source_kind="agent_interaction"
        )["record"]
        service.ingest_text(
            "回答应该详细解释背景信息", source_kind="agent_interaction"
        )
        results = service.search("回答时先给命令", limit=5)
        assert results[0]["memory_id"] == exact.memory_id
        explanation = results[0]["retrieval_explanation"]
        assert explanation["protected_exact_hit"] is True
        assert "lexical" in explanation["fusion_sources"]
        assert explanation["query_intent"] == "general"
    finally:
        service.close()


def test_general_query_result_is_unchanged_when_graph_has_no_signal(
    tmp_path, monkeypatch
):
    service = make_service(tmp_path, "graph-noise")
    try:
        expected = service.ingest_text(
            "代码搜索默认使用 rg", source_kind="agent_interaction"
        )["record"]
        monkeypatch.setattr(
            service,
            "hierarchical_graph_search",
            lambda entity, limit=10: {
                "layers": {
                    "summary": [{"memory_id": "unrelated-memory"}],
                    "entity_relation": [],
                    "chunk": [],
                }
            },
        )
        hits = service.search("代码搜索默认工具", limit=5)
        assert hits[0]["memory_id"] == expected.memory_id
        explanation = hits[0]["retrieval_explanation"]
        assert explanation["query_intent"] == "general"
        assert "graph" not in explanation["fusion_sources"]
    finally:
        service.close()


def test_relational_query_can_add_graph_only_candidate(tmp_path, monkeypatch):
    service = make_service(tmp_path, "graph-signal")
    try:
        related = service.ingest_text(
            "工具 B 属于项目 A", source_kind="agent_interaction"
        )["record"]
        monkeypatch.setattr(service, "_search_candidates", lambda *args, **kwargs: [])
        monkeypatch.setattr(
            service,
            "hierarchical_graph_search",
            lambda entity, limit=10: {
                "layers": {
                    "summary": [{"memory_id": related.memory_id}],
                    "entity_relation": [],
                    "chunk": [],
                }
            },
        )
        hits = service.search("项目 A 和工具 B 有什么关系", limit=5)
        row = next(item for item in hits if item["memory_id"] == related.memory_id)
        assert "graph" in row["retrieval_explanation"]["fusion_sources"]
    finally:
        service.close()
