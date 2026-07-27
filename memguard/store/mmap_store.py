from contextlib import contextmanager
import mmap
import os
from pathlib import Path
from typing import Iterable, List, Optional

try:
    import fcntl
except ImportError:  # pragma: no cover - Windows development fallback.
    fcntl = None

from memguard.models import MemoryRecord, MemorySource, Sensitivity, VersionInfo, AuditInfo, new_id, stable_hash, utc_now
from memguard.store.permissions import ensure_private_directory, ensure_private_file


class AppendOnlyMemoryStore:
    """Append-only JSONL segment store.

    Reads use mmap so the storage layer can scan append-only segments with
    OS-backed memory mapping. Updates and deletions never overwrite old records;
    they append a new version or a tombstone.
    """

    def __init__(self, path: Path):
        self.path = Path(path)
        self.permission_corrections = []
        correction = ensure_private_directory(self.path.parent)
        if correction:
            self.permission_corrections.append(correction)
        self.path.touch(exist_ok=True)
        correction = ensure_private_file(self.path)
        if correction:
            self.permission_corrections.append(correction)
        self.lock_path = self.path.with_suffix(self.path.suffix + ".lock")

    @contextmanager
    def _locked(self):
        """Serialize readers, appenders and compact against other processes.

        The daemon and CLI intentionally share one append-only segment.  Without
        a process lock, compact can read a partially appended JSON line and either
        fail or rewrite the segment while another process is writing to it.
        """
        if fcntl is None:
            yield
            return
        with self.lock_path.open("a+b") as lock:
            ensure_private_file(self.lock_path)
            fcntl.flock(lock.fileno(), fcntl.LOCK_EX)
            try:
                yield
            finally:
                fcntl.flock(lock.fileno(), fcntl.LOCK_UN)

    def append(self, record: MemoryRecord) -> MemoryRecord:
        with self._locked():
            return self._append_unlocked(record)

    def scan(self) -> List[MemoryRecord]:
        with self._locked():
            return self._scan_unlocked()

    def _scan_unlocked(self) -> List[MemoryRecord]:
        self._repair_incomplete_tail_unlocked()
        if self.path.stat().st_size == 0:
            return []
        records: List[MemoryRecord] = []
        with self.path.open("rb") as fh:
            with mmap.mmap(fh.fileno(), 0, access=mmap.ACCESS_READ) as mm:
                for raw in iter(mm.readline, b""):
                    # 单行损坏（例如整理后残留的填充或截断行）只跳过该行；
                    # 不能让一行坏数据使列表、详情和版本读取全部失败。
                    try:
                        line = raw.decode("utf-8").strip().strip("\x00")
                        if line:
                            records.append(MemoryRecord.from_json(line))
                    except (UnicodeDecodeError, ValueError, KeyError, TypeError):
                        continue
        return records

    def latest_records(self, include_deleted: bool = False) -> List[MemoryRecord]:
        with self._locked():
            latest = {}
            for record in self._scan_unlocked():
                latest[record.memory_id] = record
            values = list(latest.values())
            if include_deleted:
                return values
            return [record for record in values if not record.deleted]

    def get_latest(self, memory_id: str, include_deleted: bool = False) -> Optional[MemoryRecord]:
        with self._locked():
            result = None
            for record in self._scan_unlocked():
                if record.memory_id == memory_id:
                    result = record
            if result is None:
                return None
            if result.deleted and not include_deleted:
                return None
            return result

    def get_versions(self, memory_id: str) -> List[MemoryRecord]:
        with self._locked():
            return [record for record in self._scan_unlocked() if record.memory_id == memory_id]

    def tombstone(self, record: MemoryRecord, reason: str) -> MemoryRecord:
        tombstone_version_id = new_id("ver")
        tomb = MemoryRecord(
            memory_id=record.memory_id,
            type=record.type,
            subtype=record.subtype,
            scope=record.scope,
            tier=record.tier,
            content="",
            summary="FORGOTTEN",
            source=MemorySource(kind="forget", uri=""),
            evidence=[],
            confidence=record.confidence,
            importance=record.importance,
            language=record.language,
            entities=[],
            relations=[],
            sensitivity=Sensitivity(level="none", labels=[]),
            version=VersionInfo(
                version_id=tombstone_version_id,
                parent_version_id=record.version.version_id,
                op="forget",
            ),
            audit=AuditInfo(
                created_by="memguard",
                created_at=utc_now(),
                hash=stable_hash(record.memory_id + stable_hash(reason) + utc_now()),
            ),
            preference_topic="",
            preference_value="",
            preference_scope="global",
            preference_explicit=False,
            agent_name="",
            preference_session_id="",
            project_root="",
            task_type="",
            trust_level="trusted",
            trust_score=1.0,
            governance_state="forgotten",
            governance_reason="",
            deleted=True,
        )
        record.invalidate(valid_to=tomb.temporal.valid_from, superseded_by=tombstone_version_id)
        with self._locked():
            self._append_unlocked(record)
            return self._append_unlocked(tomb)

    def _append_unlocked(self, record: MemoryRecord) -> MemoryRecord:
        self._repair_incomplete_tail_unlocked()
        with self.path.open("ab") as fh:
            fh.write((record.to_json() + "\n").encode("utf-8"))
        return record

    def _repair_incomplete_tail_unlocked(self) -> int:
        """Remove only a crash-truncated final JSONL record.

        A malformed line that already ends with a newline remains visible to the
        parser and raises normally; silently skipping mid-segment corruption
        would hide data-integrity failures. Only an unterminated tail can be
        proven to be an interrupted append from this store's write format.
        """

        original_size = self.path.stat().st_size
        if original_size == 0:
            return 0
        with self.path.open("r+b") as fh:
            fh.seek(-1, os.SEEK_END)
            if fh.read(1) == b"\n":
                return 0

            boundary = 0
            cursor = original_size
            while cursor > 0:
                block_size = min(8192, cursor)
                cursor -= block_size
                fh.seek(cursor)
                block = fh.read(block_size)
                newline = block.rfind(b"\n")
                if newline >= 0:
                    boundary = cursor + newline + 1
                    break

            fh.truncate(boundary)
            fh.flush()
            os.fsync(fh.fileno())
        return original_size - boundary

    def _replace_atomic_unlocked(self, records: Iterable[MemoryRecord]) -> None:
        tmp = self.path.with_suffix(self.path.suffix + ".tmp")
        try:
            with tmp.open("wb") as fh:
                for record in records:
                    fh.write((record.to_json() + "\n").encode("utf-8"))
                fh.flush()
                os.fsync(fh.fileno())
            tmp.replace(self.path)
            if os.name != "nt":
                directory_fd = os.open(str(self.path.parent), os.O_RDONLY)
                try:
                    os.fsync(directory_fd)
                finally:
                    os.close(directory_fd)
        finally:
            if tmp.exists():
                tmp.unlink()

    def compact(self) -> int:
        """Rewrite the segment with only latest non-deleted records.

        This is the irreversible purge step after forgetting. It removes old
        payload versions from the segment file while preserving live records.
        """

        with self._locked():
            latest = {}
            for record in self._scan_unlocked():
                latest[record.memory_id] = record
            live = [record for record in latest.values() if not record.deleted]
            self._replace_atomic_unlocked(live)
            return len(live)

    def raw_contains(self, text: str) -> bool:
        if not text:
            return False
        needle = text.encode("utf-8")
        with self._locked():
            self._repair_incomplete_tail_unlocked()
            if self.path.stat().st_size == 0:
                return False
            with self.path.open("rb") as fh:
                with mmap.mmap(fh.fileno(), 0, access=mmap.ACCESS_READ) as mm:
                    return mm.find(needle) != -1

    def replace_all(self, records: Iterable[MemoryRecord]) -> None:
        with self._locked():
            self._replace_atomic_unlocked(records)
