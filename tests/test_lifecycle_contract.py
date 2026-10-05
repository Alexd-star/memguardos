from memguard.api.server import handle_request
from memguard.cli.memguard import main as user_cli_main
from memguard.cli.memctl import build_parser
from memguard.integrations.events import AgentInteractionEvent
from memguard.service import MemGuardService


def _service(tmp_path):
    return MemGuardService(tmp_path / "memory.segment", tmp_path / "memory.sqlite3")


def _preference_event(session_id="lifecycle-session"):
    return AgentInteractionEvent(
        agent_name="openhands",
        agent_version="test",
        session_id=session_id,
        turn_id="turn-1",
        sequence=1,
        event_type="user_message",
        role="user",
        content="以后搜索项目代码优先使用 rg。",
        project_root="/tmp/project",
        task_type="code_search",
        source_uri="test:lifecycle",
    )


def test_lifecycle_projection_explains_atom_episode_support_and_operations(tmp_path):
    service = _service(tmp_path)
    try:
        result = service.ingest_interaction(_preference_event())
        memory_id = result["memory_ids"][0]

        snapshot = service.lifecycle_snapshot(memory_id)

        assert snapshot["found"] is True
        assert snapshot["schema"] == "memguardos-lifecycle-v1"
        assert snapshot["atom"]["object_type"] == "atom"
        assert snapshot["atom"]["kind"] == "preference"
        assert snapshot["episodes"]
        assert snapshot["episodes"][0]["object_type"] == "episode"
        assert snapshot["supports"]
        assert snapshot["supports"][0]["object_type"] == "support"
        assert snapshot["supports"][0]["excerpt"]
        assert snapshot["operations"]
        assert snapshot["counts"]["context_uses"] == 0
        assert "OpenHands 原始日志" in snapshot["forgetting_boundary"]["external_surfaces"]
    finally:
        service.close()


def test_lifecycle_snapshot_is_redacted_after_precise_forgetting(tmp_path):
    service = _service(tmp_path)
    try:
        result = service.ingest_interaction(_preference_event())
        memory_id = result["memory_ids"][0]
        assert service.forget_memory_ids([memory_id], reason="测试遗忘", compact=True)["ok"] is True

        snapshot = service.lifecycle_snapshot(memory_id)

        assert snapshot["found"] is True
        assert snapshot["atom"]["lifecycle_state"] == "forgotten"
        assert all(not item["excerpt"] for item in snapshot["supports"])
        assert all(not item.get("summary") for episode in snapshot["episodes"] for item in episode["event_summaries"])
        assert snapshot["forgetting_boundary"]["state"] == "已遗忘"
    finally:
        service.close()


def test_lifecycle_overview_and_api_are_read_only_product_contracts(tmp_path):
    service = _service(tmp_path)
    try:
        record = service.ingest_text("长期保留：openKylin 服务由 systemd 托管。") ["record"]
        overview = service.lifecycle_overview()
        assert overview["schema"] == "memguardos-lifecycle-v1"
        assert overview["counts"]["active_atoms"] == 1
        assert overview["tiers"]["long_term"] == 1

        api_overview = handle_request(service, {"command": "lifecycle_overview"})
        api_snapshot = handle_request(service, {"command": "lifecycle_snapshot", "memory_id": record.memory_id})
        assert api_overview["ok"] is True
        assert api_snapshot["ok"] is True
        assert api_snapshot["result"]["atom"]["memory_id"] == record.memory_id
        assert service.meta.get_memory(record.memory_id)["deleted"] == 0
    finally:
        service.close()


def test_cli_exposes_lifecycle_projection_without_special_demo_data(tmp_path, capsys):
    common = ["--store", str(tmp_path / "memory.segment"), "--db", str(tmp_path / "memory.sqlite3")]
    assert user_cli_main(common + ["remember", "长期保存：项目构建使用 python -m pytest。"] ) == 0
    output = capsys.readouterr().out
    memory_id = output.split("记忆编号：", 1)[1].strip().splitlines()[0]

    assert user_cli_main(common + ["lifecycle", memory_id]) == 0
    lifecycle_output = capsys.readouterr().out
    assert "记忆生命周期证据" in lifecycle_output
    assert "原子类型" in lifecycle_output

    parser = build_parser()
    parsed = parser.parse_args(["lifecycle", "--overview"])
    assert parsed.overview is True


def test_natural_knowledge_has_source_episode_and_scoped_erasure(tmp_path):
    service = _service(tmp_path)
    try:
        record = service.ingest_natural_memory(
            "蓝河项目部署前检查 journalctl 服务日志。", session_id="knowledge-session",
        )["record"]
        snapshot = service.lifecycle_snapshot(record.memory_id)
        assert snapshot["counts"]["episodes"] == 1
        assert snapshot["counts"]["supports"] == 1
        assert snapshot["supports"][0]["excerpt"] == record.content
        preview = service.forget_by_intent(
            "忘掉当前会话关于蓝河项目的知识", context={"session_id": "knowledge-session"},
        )["result"]
        assert [row["memory_id"] for row in preview["candidates"]] == [record.memory_id]
        foreign = service.forget_by_intent(
            "忘掉当前会话关于蓝河项目的知识", context={"session_id": "other-session"},
        )["result"]
        assert foreign["candidates"] == []
        erased = service.forget_memory_ids([record.memory_id], "user request", compact=True)
        assert erased["ok"]
        assert service.lifecycle_snapshot(record.memory_id)["supports"][0]["excerpt"] == ""
    finally:
        service.close()


def test_mixed_untyped_preference_is_not_silently_discarded(tmp_path):
    service = _service(tmp_path)
    try:
        result = service.ingest_natural_memory(
            "代码搜索优先使用 rg，服务排障优先 systemctl status。", session_id="mixed",
        )
        assert result["stored"]
        assert len(result["records"]) == 2
        fallback = next(row for row in result["records"] if not row["preference_topic"])
        assert "systemctl status" in fallback["content"]
        assert service.lifecycle_snapshot(fallback["memory_id"])["counts"]["supports"] == 1
        changes = result["feedback"]["new_memories"]
        assert next(row for row in changes if row["memory_id"] == fallback["memory_id"])["application_status"] == "retrieval_only"
        preview = service.forget_by_intent("忘记关于 systemctl status 的记忆")["result"]
        assert {row["memory_id"] for row in preview["candidates"]} == {fallback["memory_id"]}
    finally:
        service.close()
