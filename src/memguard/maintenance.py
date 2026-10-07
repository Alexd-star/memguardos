"""Bounded embedding maintenance, isolated from foreground API connections."""

from __future__ import annotations

from dataclasses import dataclass
import math
from pathlib import Path
import threading
from typing import Callable

from memguard.os_guard.cgroup import cgroup_status
from memguard.service import MemGuardService


@dataclass(frozen=True)
class MaintenancePolicy:
    interval_seconds: float = 30.0
    budget_seconds: float = 2.0
    batch_limit: int = 8

    def __post_init__(self):
        if not math.isfinite(self.interval_seconds) or self.interval_seconds < 5:
            raise ValueError("maintenance interval must be finite and at least 5 seconds")
        if not math.isfinite(self.budget_seconds) or not 0.05 <= self.budget_seconds <= 5:
            raise ValueError("maintenance budget must be between 0.05 and 5 seconds")
        if not 1 <= self.batch_limit <= 32:
            raise ValueError("maintenance batch limit must be between 1 and 32")


def maintenance_decision(resources: dict) -> dict:
    ratio = resources.get("memory_budget_ratio")
    pressures = resources.get("pressure_some_avg10") or {}
    if ratio is not None and ratio >= 0.85:
        return {"run": False, "reason": "memory_budget_pressure"}
    for resource, threshold in (("memory", 5.0), ("io", 10.0), ("cpu", 25.0)):
        value = pressures.get(resource)
        if value is not None and value >= threshold:
            return {"run": False, "reason": resource + "_stall_pressure"}
    observed = ratio is not None or any(value is not None for value in pressures.values())
    return {"run": True, "reason": "resources_available" if observed else "pressure_unavailable_bounded_mode"}


def maintenance_tick(service: MemGuardService, resources: dict, policy: MaintenancePolicy) -> dict:
    decision = maintenance_decision(resources)
    if not decision["run"]:
        return {"status": "deferred", **decision, "processed": 0}
    if service.retriever.kylin_provider is None:
        return {"status": "disabled", "reason": "embedding_provider_unavailable", "processed": 0}
    result = service.backfill_embeddings(limit=policy.batch_limit, timeout_seconds=policy.budget_seconds)
    return {
        "status": "completed", "reason": decision["reason"],
        **{key: result[key] for key in (
            "processed", "ready", "failed", "queue_remaining", "due_remaining",
            "retry_deferred", "elapsed_ms", "has_more",
        )},
    }


class EmbeddingMaintenance:
    def __init__(
        self, store_path: Path, db_path: Path, namespace: str,
        policy: MaintenancePolicy, stopping: threading.Event,
        resource_probe: Callable[[], dict] = cgroup_status,
    ):
        self.store_path, self.db_path, self.namespace = store_path, db_path, namespace
        self.policy, self.stopping, self.resource_probe = policy, stopping, resource_probe
        self._lock = threading.Lock()
        self._status: dict = {"status": "waiting", "processed": 0, "cycles": 0}
        self._thread = threading.Thread(target=self._run, name="memory-embedding-maintenance", daemon=True)

    def start(self):
        self._thread.start()

    def snapshot(self) -> dict:
        with self._lock:
            return dict(self._status)

    def join(self):
        self._thread.join(timeout=self.policy.budget_seconds + 3)

    def _run(self):
        service = None
        try:
            # This connection is created and used only by its owning thread.
            service = MemGuardService(self.store_path, self.db_path, namespace=self.namespace)
            while not self.stopping.wait(self.policy.interval_seconds):
                try:
                    result = maintenance_tick(service, self.resource_probe(), self.policy)
                except Exception as exc:
                    result = {"status": "failed", "reason": type(exc).__name__, "processed": 0}
                with self._lock:
                    cycles = int(self._status["cycles"]) + 1
                    self._status = {**result, "cycles": cycles}
        except Exception as exc:
            with self._lock:
                self._status.update(status="failed", reason=type(exc).__name__)
        finally:
            if service is not None:
                service.close()
