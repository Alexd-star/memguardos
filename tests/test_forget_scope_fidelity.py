"""A scoped forget request must not delete outside the scope it asked for.

Deleting more than the user authorised is the worst failure mode for precise
forgetting: the user reads a preview that says "最近三天", confirms it, and
loses memories from months ago. These tests pin the boundary.
"""

from datetime import datetime, timedelta, timezone

from memguard.service import MemGuardService


def _age(service: MemGuardService, memory_id: str, days: int) -> None:
    stamp = (datetime.now(timezone.utc) - timedelta(days=days)).isoformat()
    service.meta.conn.execute(
        "UPDATE memory SET valid_from = ?, updated_at = ? WHERE memory_id = ?",
        (stamp, stamp, memory_id),
    )
    service.meta.conn.commit()


def test_relative_time_window_excludes_older_memories(tmp_path):
    service = MemGuardService(tmp_path / "memory.segment", tmp_path / "meta.sqlite3")
    try:
        ids = {}
        for label, days in (("recent", 1), ("mid", 5), ("old", 40)):
            record = service.ingest_text(
                "systemd 服务修复流程：先查看 journalctl 再重启单元（样本 %s）" % label,
                source_kind="manual",
            )["record"]
            ids[label] = record.memory_id
            _age(service, record.memory_id, days)

        plan = service.forget("忘掉最近三天关于 systemd 修复流程的知识", confirm=False)
        hit = {item["memory_id"] for item in plan["candidates"]}

        assert ids["recent"] in hit
        assert ids["mid"] not in hit, "5 天前的记忆超出了'最近三天'的授权范围"
        assert ids["old"] not in hit, "40 天前的记忆超出了'最近三天'的授权范围"
        # 预览必须告诉用户实际生效的时间边界，否则用户无法判断范围是否正确。
        window = plan["time_window"]
        assert window["applied"] is True
        assert window["start_time"] and window["end_time"]
        assert window["records_after"] < window["records_before"]
    finally:
        service.close()


def test_request_without_time_range_is_not_silently_narrowed(tmp_path):
    service = MemGuardService(tmp_path / "memory.segment", tmp_path / "meta.sqlite3")
    try:
        record = service.ingest_text(
            "systemd 服务修复流程：先查看 journalctl 再重启单元。",
            source_kind="manual",
        )["record"]
        _age(service, record.memory_id, 90)

        plan = service.forget("忘掉 systemd 修复流程的知识", confirm=False)
        hit = {item["memory_id"] for item in plan["candidates"]}

        assert record.memory_id in hit, "没有时间条件的请求不应被时间窗口过滤掉"
        assert plan["time_window"]["applied"] is False
    finally:
        service.close()


def test_confirmed_forget_reports_the_window_it_enforced(tmp_path):
    service = MemGuardService(tmp_path / "memory.segment", tmp_path / "meta.sqlite3")
    try:
        recent = service.ingest_text(
            "systemd 服务修复流程：先查看 journalctl 再重启单元（近期）。",
            source_kind="manual",
        )["record"]
        old = service.ingest_text(
            "systemd 服务修复流程：先查看 journalctl 再重启单元（历史）。",
            source_kind="manual",
        )["record"]
        _age(service, recent.memory_id, 1)
        _age(service, old.memory_id, 60)

        result = service.forget("忘掉最近三天关于 systemd 修复流程的知识", confirm=True)

        assert result["time_window"]["applied"] is True
        assert old.memory_id not in result["forgotten"]
        assert service.meta.get_memory(old.memory_id)["deleted"] == 0
    finally:
        service.close()


def test_forget_by_intent_keeps_compiled_time_window(tmp_path):
    service = MemGuardService(tmp_path / "memory.segment", tmp_path / "meta.sqlite3")
    try:
        recent = service.ingest_text(
            "systemd 修复流程：检查 journalctl 后重启服务（意图入口近期）。",
            source_kind="manual",
        )["record"]
        old = service.ingest_text(
            "systemd 修复流程：检查 journalctl 后重启服务（意图入口历史）。",
            source_kind="manual",
        )["record"]
        _age(service, recent.memory_id, 1)
        _age(service, old.memory_id, 40)

        preview = service.forget_by_intent(
            "忘掉最近三天关于 systemd 修复流程的知识",
            confirm=False,
        )
        candidates = {item["memory_id"] for item in preview["result"]["candidates"]}

        assert preview["route"] == "managed_memory"
        assert preview["result"]["time_window"]["applied"] is True
        assert recent.memory_id in candidates
        assert old.memory_id not in candidates

        confirmed = service.forget_by_intent(
            "忘掉最近三天关于 systemd 修复流程的知识",
            confirm=True,
            compact=True,
        )
        assert confirmed["result"]["forgotten"] == list(candidates)
        assert service.meta.get_memory(old.memory_id)["deleted"] == 0
    finally:
        service.close()


def test_unresolved_project_scope_stops_instead_of_broadening(tmp_path):
    service = MemGuardService(tmp_path / "memory.segment", tmp_path / "meta.sqlite3")
    try:
        record = service.ingest_text(
            "当前项目的 systemd 部署流程需要先检查日志。",
            source_kind="manual",
        )["record"]

        preview = service.forget_by_intent(
            "忘掉当前项目关于 systemd 部署流程的知识",
            context={},
            confirm=False,
        )["result"]

        assert preview["erasure_plan"]["unresolved_scope"] is True
        assert preview["time_window"]["scope_blocked"] is True
        assert preview["candidates"] == []
        assert service.meta.get_memory(record.memory_id)["deleted"] == 0
    finally:
        service.close()


def test_project_scope_is_applied_before_semantic_ranking(tmp_path):
    service = MemGuardService(tmp_path / "memory.segment", tmp_path / "meta.sqlite3")
    try:
        project_a = service.ingest_text(
            "systemd 部署流程：项目 A 先检查日志再重启。",
            source_kind="manual",
        )["record"]
        project_b = service.ingest_text(
            "systemd 部署流程：项目 B 先检查日志再重启。",
            source_kind="manual",
        )["record"]
        service.meta.conn.execute(
            "UPDATE memory SET project_root = ? WHERE memory_id = ?",
            ("/workspace/a", project_a.memory_id),
        )
        service.meta.conn.execute(
            "UPDATE memory SET project_root = ? WHERE memory_id = ?",
            ("/workspace/b", project_b.memory_id),
        )
        service.meta.conn.commit()

        preview = service.forget_by_intent(
            "忘掉当前项目关于 systemd 部署流程的知识",
            context={"project_root": "/workspace/a"},
            confirm=False,
        )["result"]
        hit = {item["memory_id"] for item in preview["candidates"]}

        assert project_a.memory_id in hit
        assert project_b.memory_id not in hit
        assert preview["erasure_plan"]["scope"]["level"] == "project"
    finally:
        service.close()


def test_time_words_inside_record_reference_are_not_timestamp_filters(tmp_path):
    service = MemGuardService(tmp_path / "memory.segment", tmp_path / "meta.sqlite3")
    try:
        record = service.ingest_text(
            "上周记录：journalctl 是服务重启前必须检查的日志。",
            source_kind="manual",
        )["record"]
        _age(service, record.memory_id, 45)

        preview = service.forget(
            "忘记上周记录中关于 journalctl 的记忆",
            confirm=False,
        )

        assert preview["time_window"]["applied"] is False
        assert record.memory_id in {item["memory_id"] for item in preview["candidates"]}
    finally:
        service.close()
