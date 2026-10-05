from pathlib import Path
import threading
from types import SimpleNamespace

import pytest

from memguard.maintenance import EmbeddingMaintenance, MaintenancePolicy, maintenance_decision, maintenance_tick
from memguard.os_guard.cgroup import cgroup_status
from memguard.service import MemGuardService


def test_cgroup_uses_process_membership_and_ancestor_limits(tmp_path):
    root, proc = tmp_path / "cgroup", tmp_path / "proc"
    leaf = root / "system.slice/memguardd.service"
    leaf.mkdir(parents=True)
    (proc / "self").mkdir(parents=True)
    (proc / "self/cgroup").write_text("0::/system.slice/memguardd.service\n")
    (root / "cgroup.controllers").write_text("cpu memory io")
    (root / "memory.current").write_text("10")
    (root / "memory.max").write_text("1000")
    (leaf / "memory.current").write_text("90")
    (leaf / "memory.max").write_text("100")
    (leaf / "memory.high").write_text("max")
    (leaf / "memory.pressure").write_text("some avg10=7.00 avg60=0.0 total=5\nfull avg10=1.0")
    (leaf.parent / "memory.current").write_text("180")
    (leaf.parent / "memory.high").write_text("200")
    status = cgroup_status(cgroup_root=root, proc_root=proc)
    assert status["cgroup_path"] == str(leaf.resolve())
    assert status["memory_current"] == "90"
    assert status["memory_budget_ratio"] == 0.9
    assert status["pressure_some_avg10"]["memory"] == 7
    assert not maintenance_decision(status)["run"]


def test_cgroup_rejects_escaping_membership_and_malformed_psi(tmp_path):
    root, proc = tmp_path / "cgroup", tmp_path / "proc"
    root.mkdir()
    (proc / "self").mkdir(parents=True)
    (proc / "self/cgroup").write_text("0::/../../outside")
    (root / "memory.pressure").write_text("some avg10=nan")
    status = cgroup_status(cgroup_root=root, proc_root=proc)
    assert status["cgroup_path"] == str(root.resolve())
    assert status["pressure_some_avg10"]["memory"] is None


@pytest.mark.parametrize("resource,value", [("cpu", 30), ("memory", 6), ("io", 12)])
def test_pressure_defers_work_without_claiming_job_failure(resource, value):
    service = SimpleNamespace(retriever=SimpleNamespace(kylin_provider=object()))
    result = maintenance_tick(service, {"pressure_some_avg10": {resource: value}}, MaintenancePolicy())
    assert result["status"] == "deferred" and result["processed"] == 0
    assert result["reason"] == resource + "_stall_pressure"


def test_bounded_maintenance_builds_real_persisted_vectors_and_respects_erasure(tmp_path):
    service = MemGuardService(tmp_path / "memory.segment", tmp_path / "memory.sqlite3")
    try:
        record = service.ingest_text("白鹭项目使用本地索引检索部署知识。")["record"]
        service.retriever.kylin_provider = SimpleNamespace(vector=lambda *args, **kwargs: [1.0, 0.0, 0.5])
        result = maintenance_tick(service, {}, MaintenancePolicy(batch_limit=2))
        assert result["status"] == "completed" and result["ready"] == 1
        assert service.embedding_cache_status()["ready"] == 1
        assert service.forget_memory_ids([record.memory_id], "user request", compact=True)["ok"]
        assert maintenance_tick(service, {}, MaintenancePolicy())["ready"] == 0
        assert service.embedding_cache_status()["ready"] == 0
    finally:
        service.close()


def test_worker_connection_lives_only_in_worker_thread(monkeypatch, tmp_path):
    import memguard.maintenance as module

    ids = []
    stopped = threading.Event()
    waits = iter([False, True])
    monkeypatch.setattr(stopped, "wait", lambda timeout: next(waits))

    def factory(*args, **kwargs):
        ids.append(threading.get_ident())
        return SimpleNamespace(close=lambda: ids.append(threading.get_ident()))

    monkeypatch.setattr(module, "MemGuardService", factory)
    monkeypatch.setattr(module, "maintenance_tick", lambda *args: {"status": "completed", "processed": 1})
    worker = EmbeddingMaintenance(Path(tmp_path / "m"), Path(tmp_path / "db"), "default", MaintenancePolicy(), stopped)
    worker.start()
    worker.join()
    assert len(ids) == 2 and ids[0] == ids[1] != threading.get_ident()
    assert worker.snapshot()["cycles"] == 1


@pytest.mark.parametrize("kwargs", [{"interval_seconds": float("nan")}, {"interval_seconds": 1}, {"budget_seconds": 10}, {"batch_limit": 100}])
def test_invalid_maintenance_budget_is_rejected(kwargs):
    with pytest.raises(ValueError):
        MaintenancePolicy(**kwargs)
