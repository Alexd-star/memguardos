import multiprocessing
import time
from pathlib import Path

import pytest

import memguard.store.mmap_store as mmap_store_module
from memguard.models import MemorySource, make_memory
from memguard.store.mmap_store import AppendOnlyMemoryStore


def _hold_store_lock(path: str, ready) -> None:
    store = AppendOnlyMemoryStore(Path(path))
    with store._locked():
        ready.set()
        time.sleep(0.25)


@pytest.mark.skipif(mmap_store_module.fcntl is None, reason="process locking is Linux-specific")
def test_append_waits_for_another_process_using_the_same_segment(tmp_path):
    path = tmp_path / "memory.segment"
    context = multiprocessing.get_context("spawn")
    ready = context.Event()
    holder = context.Process(target=_hold_store_lock, args=(str(path), ready))
    holder.start()
    try:
        assert ready.wait(timeout=5)
        store = AppendOnlyMemoryStore(path)
        record = make_memory(
            "跨进程并发写入测试记录",
            memory_type="knowledge",
            subtype="test",
            source=MemorySource(kind="test", uri="concurrency"),
        )
        started = time.monotonic()
        store.append(record)
        elapsed = time.monotonic() - started

        assert elapsed >= 0.15
        assert [item.memory_id for item in store.scan()] == [record.memory_id]
    finally:
        holder.join(timeout=5)
        if holder.is_alive():
            holder.terminate()
        assert holder.exitcode == 0
