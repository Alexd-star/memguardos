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
    assert classify_query_intent("代码搜索应该优先用什么工具") == QueryIntent.PREFERENCE
    assert classify_query_intent("修复 systemd 服务失败的步骤是什么") == QueryIntent.PROCEDURAL
    assert classify_query_intent("cgroup 的直接操作要求") == QueryIntent.PROCEDURAL
    assert classify_query_intent("项目 A 和工具 B 有什么关系") == QueryIntent.RELATIONAL
    assert classify_query_intent("上周更新后的配置") == QueryIntent.TEMPORAL
    assert classify_query_intent("本周 cgroup") == QueryIntent.TEMPORAL
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


def test_query_routing_prefers_matching_language_time_and_relation_sources(tmp_path):
    service = make_service(tmp_path, "route-suitability")
    try:
        # 跨语言命中必须靠真实术语重叠决定，不能依赖任何基准生成器标记短语：
        # 两条记录的技术术语必须真的不同，否则排序没有可解释依据。
        cross = service.ingest_text(
            "服务重启前先查看 journalctl 日志与 cgroup resource limits 配额。",
            source_kind="agent_interaction",
        )["record"]
        service.ingest_text(
            "本周有效记录：cgroup 配额需要每周复核一次。",
            source_kind="agent_interaction",
        )
        hits = service.search(
            "Which logs and resource limits should be checked before restarting a service?",
            limit=5,
        )
        assert hits[0]["memory_id"] == cross.memory_id
        assert "cross_language_term_overlap" in hits[0]["score_breakdown"]["routing_reasons"]

        temporal = service.search("本周 cgroup", limit=5)[0]
        assert "temporal_scope_match" in temporal["score_breakdown"]["routing_reasons"]
        assert "cross_language_term_overlap" not in temporal["score_breakdown"]["routing_reasons"]

        relation = service.ingest_text(
            "关联图记录：namespace 与 cgroup 共同约束服务。",
            source_kind="agent_interaction",
        )["record"]
        relational_hits = service.search("namespace 与 cgroup 有什么关联", limit=5)
        assert relational_hits[0]["memory_id"] == relation.memory_id
        assert "relational_source" in relational_hits[0]["score_breakdown"]["routing_reasons"]
    finally:
        service.close()


def test_single_distinctive_technical_term_is_protected_from_generic_tie(tmp_path):
    service = make_service(tmp_path, "distinctive-protection")
    try:
        expected = service.ingest_text(
            "批量删除确认后执行 compact。", source_kind="agent_interaction"
        )["record"]
        service.ingest_text(
            "操作证据应保留完整上下文。", source_kind="agent_interaction"
        )
        hits = service.search("请检索与 compact 直接相关的操作证据", limit=5)
        assert hits[0]["memory_id"] == expected.memory_id
        assert hits[0]["retrieval_explanation"]["protected_exact_hit"] is True
    finally:
        service.close()


def test_multi_term_command_signature_is_protected_from_semantic_noise(tmp_path):
    service = make_service(tmp_path, "command-signature-protection")
    try:
        expected = service.ingest_text(
            "User behavior: shell_command source=.bash_history command=sudo "
            "systemctl restart memguardd category=maintenance "
            "risk=service_disruption",
            source_kind="user_behavior",
        )["record"]
        service.ingest_text(
            "systemctl status memguardd 用于读取服务状态，不会重启服务。",
            source_kind="agent_interaction",
        )
        hits = service.search(
            "bash history 中 sudo systemctl restart memguardd 属于什么行为风险？",
            limit=5,
        )
        assert hits[0]["memory_id"] == expected.memory_id
        assert hits[0]["retrieval_explanation"]["protected_exact_hit"] is True
    finally:
        service.close()


def test_command_context_words_do_not_hide_destructive_action_anchor(tmp_path):
    service = make_service(tmp_path, "destructive-action-anchor")
    try:
        expected = service.ingest_text(
            "用户行为偏好：shell history 出现 rm -rf、dd、mkfs 时，"
            "Agent 必须先二次确认。",
            source_kind="user_behavior",
        )["record"]
        service.ingest_text(
            "真实 shell history 难例：history -d 只清理记录，不等于 "
            "sudo rm -rf 破坏性删除。",
            source_kind="agent_interaction",
        )

        hits = service.search(
            "shell history 出现 sudo rm -rf 或 mkfs 时要不要先确认？",
            limit=5,
        )

        assert hits[0]["memory_id"] == expected.memory_id
        assert hits[0]["retrieval_explanation"]["protected_exact_hit"] is True
        assert hits[0]["score_breakdown"]["distinctive_latin_token_count"] == 1
    finally:
        service.close()


def test_common_latin_term_is_not_misclassified_as_rare_exact_hit(tmp_path):
    service = make_service(tmp_path, "common-term-protection")
    try:
        for text in (
            "openKylin systemd 服务内存限制。",
            "openKylin UKUI 桌面入口。",
            "openKylin Peony 文件扫描。",
        ):
            service.ingest_text(text, source_kind="agent_interaction")
        hits = service.search("openKylin 系统能力", limit=5)
        assert len(hits) == 3
        assert all(
            item["retrieval_explanation"]["protected_exact_hit"] is False
            for item in hits
        )
    finally:
        service.close()
