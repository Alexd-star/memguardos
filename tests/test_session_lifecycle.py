import sqlite3
from datetime import datetime, timedelta, timezone

import pytest

from memguard.api.server import handle_request
from memguard.cli.memguard import main as user_cli_main
from memguard.cli.memctl import build_parser as memctl_parser
from memguard.models import MemoryRecord
from memguard.retrieval.vector_cache import vector_content_hash
from memguard.service import MemGuardService
from memguard.store.sqlite_meta import SQLiteMetaStore


def _service(tmp_path):
    return MemGuardService(tmp_path / "memory.segment", tmp_path / "meta.sqlite3")


def test_stale_session_review_is_read_only_and_requires_explicit_eviction(tmp_path):
    service = _service(tmp_path)
    try:
        old = service.ingest_text(
            "旧会话的临时构建记录。", tier="session", retention_session_id="abandoned",
        )["record"]
        recent = service.ingest_text(
            "当前会话的临时构建记录。", tier="session", retention_session_id="active",
        )["record"]
        stale_at = (datetime.now(timezone.utc) - timedelta(days=5)).isoformat()
        service.meta.conn.execute(
            "UPDATE memory SET updated_at = ? WHERE memory_id = ?", (stale_at, old.memory_id),
        )
        service.meta.conn.commit()
        with pytest.raises(ValueError):
            service.stale_memory_sessions(older_than_hours=1)
        report = service.stale_memory_sessions(older_than_hours=72)
        assert [row["session_id"] for row in report["sessions"]] == ["abandoned"]
        assert service.meta.get_memory(old.memory_id)["deleted"] == 0
        preview = service.evict_session("abandoned")
        assert preview["memory_ids"] == [old.memory_id]
        assert service.evict_session(
            "abandoned", confirm=True, expected_versions=preview["expected_versions"],
        )["evicted"]
        assert service.meta.get_memory(old.memory_id)["deleted"] == 1
        assert service.meta.get_memory(recent.memory_id)["deleted"] == 0
    finally:
        service.close()


def test_search_fallback_keeps_the_same_session_scope(tmp_path, monkeypatch):
    service = _service(tmp_path)
    try:
        record = service.ingest_text(
            "蓝河目录结构记录在本次会话。", tier="session", retention_session_id="owner",
        )["record"]
        monkeypatch.setattr(service.retriever, "rank", lambda *_args, **_kwargs: [])
        assert record.memory_id in {
            item["memory_id"] for item in service.search("蓝河目录结构", session_id="owner")
        }
        assert service.search("蓝河目录结构", session_id="stranger") == []
    finally:
        service.close()


def test_project_scope_applies_before_candidate_limit_and_to_dense_scan(tmp_path):
    service = _service(tmp_path)
    try:
        owner = str((tmp_path / "owner").resolve())
        other = str((tmp_path / "other").resolve())
        expected = service.ingest_text(
            "蓝河检索方案使用本地索引。", project_root=owner,
        )["record"]
        for index in range(25):
            service.ingest_text(
                "蓝河检索方案来自其他项目，编号 %s。" % index,
                project_root=other,
            )
        hits = service.search("蓝河检索方案", limit=1, project_root=owner)
        assert [item["memory_id"] for item in hits] == [expected.memory_id]
        assert all(item["project_root"] != other for item in service.search(
            "蓝河检索方案", limit=5, project_root=owner,
        ))
        for row in service.meta.list_memories():
            service.meta.upsert_memory_embedding(
                row["memory_id"], vector_content_hash(row["summary"], row["content"]),
                service.EMBEDDING_BACKEND, service.EMBEDDING_MODEL_REVISION, [1.0, 0.0],
            )
        vectors, complete = service.meta.list_dense_candidate_vectors(
            service.EMBEDDING_BACKEND, service.EMBEDDING_MODEL_REVISION,
            service.namespace, 1, project_root=owner,
        )
        assert complete and [memory_id for memory_id, _ in vectors] == [expected.memory_id]
    finally:
        service.close()


def test_session_facts_are_isolated_across_retrieval_and_updates(tmp_path):
    service = _service(tmp_path)
    try:
        first = service.ingest_text(
            "蓝河项目发布日是周三。", tier="session", retention_session_id="session-a",
        )["record"]
        second = service.ingest_text(
            "蓝河项目发布日是周三。", tier="session", retention_session_id="session-b",
        )["record"]
        assert first.memory_id != second.memory_id
        duplicate = service.ingest_text(
            "蓝河项目发布日是周三。", tier="session", retention_session_id="session-a",
        )
        assert duplicate["duplicate"] is True
        corrected = service.ingest_text(
            "蓝河项目发布日改为周四。", tier="session", retention_session_id="session-a",
        )
        assert corrected["updated"] is True
        assert corrected["record"].memory_id == first.memory_id
        assert "周四" in service.meta.get_memory(first.memory_id)["content"]
        assert "周三" in service.meta.get_memory(second.memory_id)["content"]

        for session_id, expected_id, excluded_id in (
            ("session-a", first.memory_id, second.memory_id),
            ("session-b", second.memory_id, first.memory_id),
        ):
            hits = service.search("蓝河项目发布日", session_id=session_id, limit=10)
            ids = {hit["memory_id"] for hit in hits}
            assert expected_id in ids
            assert excluded_id not in ids
            context = service.build_agent_context(
                "蓝河项目发布日", stage="planning", agent_name="test",
                session_id=session_id,
            )
            assert excluded_id not in str(context.get("selected_memory_ids"))
        assert service.search("蓝河项目发布日", session_id="session-c") == []
        assert service.search("蓝河项目发布日") == []
        assert first.memory_id not in {row["memory_id"] for row in service.graph_search("蓝河项目", session_id="session-b")}
        assert second.memory_id not in {row["memory_id"] for row in service.meta.list_recent_memories(10, session_id="session-a")}
        for record in (first, second):
            current = service.meta.get_memory(record.memory_id)
            service.meta.upsert_memory_embedding(
                record.memory_id, vector_content_hash(current["summary"], current["content"]),
                service.EMBEDDING_BACKEND, service.EMBEDDING_MODEL_REVISION, [1.0, 0.0],
            )
        vectors, complete = service.meta.list_dense_candidate_vectors(
            service.EMBEDDING_BACKEND, service.EMBEDDING_MODEL_REVISION,
            service.namespace, 10, session_id="session-a",
        )
        assert complete and {memory_id for memory_id, _ in vectors} == {first.memory_id}
    finally:
        service.close()


def test_session_end_preserves_other_sessions_and_promoted_memory(tmp_path):
    service = _service(tmp_path)
    try:
        disposable = service.ingest_text(
            "当前会话暂存蓝河环境日志。", tier="working", retention_session_id="session-a",
        )["record"]
        promoted = service.ingest_text(
            "蓝河项目使用本地构建脚本。", tier="session", retention_session_id="session-a",
        )["record"]
        other = service.ingest_text(
            "另一个会话暂存白鹭环境日志。", tier="working", retention_session_id="session-b",
        )["record"]
        long_term = service.ingest_text("长期保留：白鹭项目采用增量测试。", tier="long_term")["record"]
        assert service.promote_memory(promoted.memory_id, "long_term", session_id="session-b")["reason"] == "session_mismatch"
        assert service.promote_memory(promoted.memory_id, "long_term")["record"].retention_session_id == ""
        preview = service.evict_session("session-a")
        assert preview["memory_ids"] == [disposable.memory_id]
        assert not preview["evicted"]
        result = service.evict_session("session-a", confirm=True)
        assert result["evicted"] is True
        assert result["memory_ids"] == [disposable.memory_id]
        assert not service.search("蓝河环境日志", session_id="session-a")
        assert service.meta.get_memory(other.memory_id)["deleted"] == 0
        assert service.meta.get_memory(promoted.memory_id)["deleted"] == 0
        assert service.meta.get_memory(long_term.memory_id)["deleted"] == 0
        assert service.search("蓝河项目使用本地构建脚本", session_id="session-b")
    finally:
        service.close()


def test_temporary_write_requires_explicit_session_and_api_routes_it(tmp_path):
    service = _service(tmp_path)
    try:
        assert service.ingest_text("本轮调试只看白鹭日志。", tier="working")["reason"] == "session_required"
        assert service.ingest_natural_memory("本次会话临时记录白鹭日志。", tier="session")["reason"] == "session_required"
        request = {"command": "ingest", "text": "本轮只保存白鹭构建日志。", "tier": "session", "session_id": "web-a"}
        stored = handle_request(service, request)
        assert stored["ok"] is True and stored["result"]["stored"] is True
        memory_id = stored["result"]["memory_id"]
        own = handle_request(service, {"command": "search", "query": "白鹭构建日志", "session_id": "web-a"})
        foreign = handle_request(service, {"command": "search", "query": "白鹭构建日志", "session_id": "web-b"})
        assert memory_id in {item["memory_id"] for item in own["result"]}
        assert memory_id not in {item["memory_id"] for item in foreign["result"]}
        ended = handle_request(service, {"command": "evict_session", "session_id": "web-a", "confirm": True})
        assert ended["ok"] is True
        assert memory_id in ended["result"]["memory_ids"]
    finally:
        service.close()


def test_legacy_record_deserialization_has_unbound_session():
    record = MemoryRecord.from_dict({
        "memory_id": "mem_legacy", "type": "knowledge", "scope": "default",
        "source": {"kind": "manual"},
    })
    assert record.retention_session_id == ""


def test_existing_database_adds_retention_session_column(tmp_path):
    path = tmp_path / "legacy.sqlite3"
    original = SQLiteMetaStore(path)
    original.close()
    with sqlite3.connect(path) as connection:
        connection.execute("DROP INDEX idx_memory_retention_session")
        connection.execute("ALTER TABLE memory DROP COLUMN retention_session_id")
    upgraded = SQLiteMetaStore(path)
    try:
        columns = {row["name"] for row in upgraded.conn.execute("PRAGMA table_info(memory)")}
        assert "retention_session_id" in columns
    finally:
        upgraded.close()


def test_preference_conflicts_do_not_merge_distinct_sessions(tmp_path):
    service = _service(tmp_path)
    try:
        one = service.ingest_natural_memory("本次会话搜索代码优先用 rg。", session_id="session-a")
        two = service.ingest_natural_memory("本次会话搜索代码优先用 grep。", session_id="session-b")
        assert one["stored"] and two["stored"]
        resolved = service.resolve_conflicts()
        assert not any(
            {one["record"]["memory_id"], two["record"]["memory_id"]}
            <= set(item["candidate_memory_ids"])
            for item in resolved["resolutions"]
        )
    finally:
        service.close()


def test_public_cli_session_flow(tmp_path, capsys):
    common = ["--store", str(tmp_path / "cli.segment"), "--db", str(tmp_path / "cli.sqlite3")]
    assert user_cli_main(common + ["remember", "本次会话临时记录蓝河构建策略。", "--session-id", "cli-a"]) == 0
    capsys.readouterr()
    assert user_cli_main(common + ["search", "蓝河构建策略", "--session-id", "cli-a"]) == 0
    assert "记忆检索" in capsys.readouterr().out
    assert user_cli_main(common + ["search", "蓝河构建策略", "--session-id", "cli-b"]) == 1
    capsys.readouterr()
    assert user_cli_main(common + ["end-session", "--session-id", "cli-a", "--yes"]) == 0
    assert "临时记忆" in capsys.readouterr().out
    assert user_cli_main(common + ["search", "蓝河构建策略", "--session-id", "cli-a"]) == 1
    assert memctl_parser().parse_args(["search", "蓝河", "--session-id", "cli-a"]).session_id == "cli-a"
    assert memctl_parser().parse_args(["stale-sessions", "--older-than-hours", "96"]).older_than_hours == 96


def test_session_end_rejects_changed_preview(tmp_path):
    service = _service(tmp_path)
    try:
        first = service.ingest_text("本轮仅记录蓝河构建失败。", tier="session", retention_session_id="web-a")["record"]
        preview = handle_request(service, {"command": "evict_session", "session_id": "web-a"})["result"]
        second = service.ingest_text("本轮仅记录白鹭构建失败。", tier="session", retention_session_id="web-a")["record"]
        rejected = handle_request(service, {
            "command": "evict_session", "session_id": "web-a", "confirm": True,
            "expected_versions": preview["expected_versions"],
        })
        assert rejected["ok"] is False and rejected["error"] == "stale_preview"
        assert service.meta.get_memory(first.memory_id)["deleted"] == 0
        assert service.meta.get_memory(second.memory_id)["deleted"] == 0
    finally:
        service.close()
