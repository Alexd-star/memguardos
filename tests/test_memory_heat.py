"""Retrieval hits feed memory heat, and eviction prefers cold memories."""

from memguard.service import MemGuardService


def test_search_hits_increment_memory_heat_without_faking_freshness(tmp_path):
    service = MemGuardService(tmp_path / "memory.segment", tmp_path / "meta.sqlite3")
    try:
        record = service.ingest_text(
            "openKylin 服务重启前先查看 journalctl 日志与 cgroup 配额。",
            source_kind="manual",
        )["record"]
        before = service.meta.get_memory(record.memory_id)

        assert int(before.get("hit_count") or 0) == 0

        service.search("journalctl cgroup 配额", limit=5)
        # 热度按批落盘以免每次检索都 fsync；显式 flush 后即可核对。
        service.flush_memory_hits()
        after = service.meta.get_memory(record.memory_id)

        assert int(after.get("hit_count") or 0) >= 1
        assert after.get("last_hit_at")
        # 读取不得伪装成写入，否则新鲜度信号会被检索反复重置。
        assert after.get("updated_at") == before.get("updated_at")
    finally:
        service.close()


def test_eviction_candidates_prefer_cold_memories_over_recalled_ones(tmp_path):
    service = MemGuardService(tmp_path / "memory.segment", tmp_path / "meta.sqlite3")
    try:
        hot = service.ingest_text(
            "排查 systemd 服务失败时优先查看 journalctl 输出。",
            source_kind="manual",
            tier="working",
        )["record"]
        cold = service.ingest_text(
            "临时记录：演示环境的壁纸切换脚本存放位置。",
            source_kind="manual",
            tier="working",
        )["record"]

        for _ in range(3):
            service.search("journalctl systemd 服务失败", limit=5)

        policy = service.memory_policy_status()
        candidates = policy["eviction_candidates"]
        order = [item["memory_id"] for item in candidates]

        assert cold.memory_id in order, "未被召回的记忆应作为淘汰候选"
        if hot.memory_id in order:
            assert order.index(cold.memory_id) < order.index(hot.memory_id)
        hot_row = service.meta.get_memory(hot.memory_id)
        cold_row = service.meta.get_memory(cold.memory_id)
        assert int(hot_row.get("hit_count") or 0) > int(cold_row.get("hit_count") or 0)
    finally:
        service.close()
