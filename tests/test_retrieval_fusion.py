import pytest

from memguard.retrieval.fusion import QueryIntent, classify_query_intent, rrf_fuse


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
