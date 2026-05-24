from pathlib import Path
from typing import Iterable, List, Optional
import mmap

from memguard.models import MemoryRecord, MemorySource, Sensitivity, VersionInfo, AuditInfo, new_id, stable_hash, utc_now


class AppendOnlyMemoryStore:
    """Append-only JSONL segment store.

    Reads use mmap so the storage layer can scan append-only segments with
    OS-backed memory mapping. Updates and deletions never overwrite old records;
    they append a new version or a tombstone.
    """

    def __init__(self, path: Path):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.path.touch(exist_ok=True)

    def append(self, record: MemoryRecord) -> MemoryRecord:
        payload = record.to_json() + "\n"
        with self.path.open("ab") as fh:
            fh.write(payload.encode("utf-8"))
        return record

    def scan(self) -> List[MemoryRecord]:
        if self.path.stat().st_size == 0:
            return []
        records: List[MemoryRecord] = []
        with self.path.open("rb") as fh:
            with mmap.mmap(fh.fileno(), 0, access=mmap.ACCESS_READ) as mm:
                for raw in iter(mm.readline, b""):
                    line = raw.decode("utf-8").strip()
                    if line:
                        records.append(MemoryRecord.from_json(line))
        return records

    def latest_records(self, include_deleted: bool = False) -> List[MemoryRecord]:
        latest = {}
        for record in self.scan():
            latest[record.memory_id] = record
        values = list(latest.values())
        if include_deleted:
            return values
        return [record for record in values if not record.deleted]

    def get_latest(self, memory_id: str, include_deleted: bool = False) -> Optional[MemoryRecord]:
        result = None
        for record in self.scan():
            if record.memory_id == memory_id:
                result = record
        if result is None:
            return None
        if result.deleted and not include_deleted:
            return None
        return result

    def get_versions(self, memory_id: str) -> List[MemoryRecord]:
        return [record for record in self.scan() if record.memory_id == memory_id]

    def tombstone(self, record: MemoryRecord, reason: str) -> MemoryRecord:
        tomb = MemoryRecord(
            memory_id=record.memory_id,
            type=record.type,
            subtype=record.subtype,
            scope=record.scope,
            tier=record.tier,
            content="",
            summary="FORGOTTEN: " + reason[:120],
            source=MemorySource(kind="forget", uri=record.memory_id),
            evidence=[],
            confidence=record.confidence,
            importance=record.importance,
            language=record.language,
            entities=[],
            relations=[],
            sensitivity=Sensitivity(level="none", labels=[]),
            version=VersionInfo(
                version_id=new_id("ver"),
                parent_version_id=record.version.version_id,
                op="forget",
            ),
            audit=AuditInfo(
                created_by="memguard",
                created_at=utc_now(),
                hash=stable_hash(record.memory_id + reason + utc_now()),
            ),
            deleted=True,
        )
        return self.append(tomb)

    def compact(self) -> int:
        """Rewrite the segment with only latest non-deleted records.

        This is the irreversible purge step after forgetting. It removes old
        payload versions from the segment file while preserving live records.
        """

        live = self.latest_records(include_deleted=False)
        tmp = self.path.with_suffix(self.path.suffix + ".tmp")
        with tmp.open("wb") as fh:
            for record in live:
                fh.write((record.to_json() + "\n").encode("utf-8"))
        tmp.replace(self.path)
        return len(live)

    def raw_contains(self, text: str) -> bool:
        if not text:
            return False
        needle = text.encode("utf-8")
        if self.path.stat().st_size == 0:
            return False
        with self.path.open("rb") as fh:
            with mmap.mmap(fh.fileno(), 0, access=mmap.ACCESS_READ) as mm:
                return mm.find(needle) != -1

    def replace_all(self, records: Iterable[MemoryRecord]) -> None:
        with self.path.open("wb") as fh:
            for record in records:
                fh.write((record.to_json() + "\n").encode("utf-8"))
