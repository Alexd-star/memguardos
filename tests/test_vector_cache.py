import math
import sqlite3
import struct

import pytest

from memguard.retrieval.vector_cache import decode_vector, encode_vector, vector_content_hash
from memguard.models import MemorySource, make_memory
import memguard.store.sqlite_meta as sqlite_meta
from memguard.store.sqlite_meta import SQLiteMetaStore


def test_vector_round_trip_uses_little_endian_float32():
    vector = [1.5, -2.25, 0.125]

    blob = encode_vector(vector)

    assert blob == b"\x00\x00\xc0?\x00\x00\x10\xc0\x00\x00\x00>"
    assert decode_vector(memoryview(blob), dimension=3) == vector


@pytest.mark.parametrize("vector", [[], [math.nan], [math.inf], [-math.inf]])
def test_encode_vector_rejects_empty_or_non_finite_values(vector):
    with pytest.raises(ValueError):
        encode_vector(vector)


@pytest.mark.parametrize(
    ("blob", "dimension"),
    [
        (b"\x00\x00\x80?", 0),
        (b"\x00\x00\x80?", -1),
        (b"\x00\x00\x80?", 2),
        (b"\x00\x00\x80", 1),
    ],
)
def test_decode_vector_rejects_invalid_dimension_or_blob_length(blob, dimension):
    with pytest.raises(ValueError):
        decode_vector(blob, dimension)


@pytest.mark.parametrize("value", [math.nan, math.inf, -math.inf])
def test_decode_vector_rejects_non_finite_persisted_values(value):
    with pytest.raises(ValueError):
        decode_vector(struct.pack("<f", value), dimension=1)


def test_vector_content_hash_is_stable_and_content_sensitive():
    assert vector_content_hash("alpha", "beta") == (
        "4163834372780d81f2af66f279e9f9561be95535729c56c283fe298aee15582e"
    )
    assert vector_content_hash("alpha", "beta") != vector_content_hash("alpha", "bet a")


def test_embedding_schema_has_versioned_primary_key_status_check_and_index(tmp_path):
    store = SQLiteMetaStore(tmp_path / "meta.sqlite3")
    try:
        columns = {
            row["name"]: row for row in store.conn.execute("PRAGMA table_info(memory_embedding)")
        }
        assert set(columns) == {
            "memory_id",
            "content_hash",
            "backend",
            "model_revision",
            "dimension",
            "vector_blob",
            "status",
            "failure_reason",
            "created_at",
            "updated_at",
            "last_used_at",
            "attempt_count",
            "next_attempt_at",
        }
        assert [
            name
            for _, name in sorted(
                (row["pk"], row["name"]) for row in columns.values() if row["pk"]
            )
        ] == ["memory_id", "backend", "model_revision", "content_hash"]
        assert columns["vector_blob"]["notnull"] == 0
        assert columns["failure_reason"]["notnull"] == 1
        assert columns["failure_reason"]["dflt_value"] == "''"
        assert columns["attempt_count"]["notnull"] == 1
        assert columns["attempt_count"]["dflt_value"] == "0"
        assert columns["next_attempt_at"]["notnull"] == 1
        assert columns["next_attempt_at"]["dflt_value"] == "''"
        indexes = {
            row["name"]
            for row in store.conn.execute("PRAGMA index_list(memory_embedding)")
        }
        assert "idx_memory_embedding_cache_status" in indexes

        with pytest.raises(sqlite3.IntegrityError):
            store.conn.execute(
                """
                INSERT INTO memory_embedding (
                    memory_id, content_hash, backend, model_revision, dimension,
                    vector_blob, status, created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                ("m1", "hash", "sdk", "v1", 1, encode_vector([1.0]), "unknown", "now", "now"),
            )
    finally:
        store.close()


@pytest.mark.parametrize(
    ("dimension", "vector_blob"),
    [
        (1, None),
        (0, b""),
        (2, encode_vector([1.0])),
    ],
)
def test_ready_embedding_requires_complete_vector_blob(tmp_path, dimension, vector_blob):
    store = SQLiteMetaStore(tmp_path / "meta.sqlite3")
    try:
        with pytest.raises(sqlite3.IntegrityError):
            store.conn.execute(
                """
                INSERT INTO memory_embedding (
                    memory_id, content_hash, backend, model_revision, dimension,
                    vector_blob, status, created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    "m1",
                    "hash-a",
                    "kylin",
                    "v1",
                    dimension,
                    vector_blob,
                    "ready",
                    "created",
                    "updated",
                ),
            )
    finally:
        store.close()


def test_embedding_upsert_get_and_last_used_lifecycle(tmp_path):
    db_path = tmp_path / "meta.sqlite3"
    store = SQLiteMetaStore(db_path)
    try:
        store.upsert_memory_embedding(
            memory_id="m1",
            content_hash="hash-a",
            backend="kylin",
            model_revision="v1",
            vector=[1.5, -2.25],
        )
        before = store.conn.execute(
            "SELECT last_used_at FROM memory_embedding WHERE memory_id = 'm1'"
        ).fetchone()
        assert before["last_used_at"] is None

        persisted = store.get_memory_embedding("m1", "hash-a", "kylin", "v1")

        assert persisted is not None
        assert persisted.memory_id == "m1"
        assert persisted.content_hash == "hash-a"
        assert persisted.backend == "kylin"
        assert persisted.model_revision == "v1"
        assert persisted.dimension == 2
        assert persisted.vector == [1.5, -2.25]
        assert persisted.status == "ready"
        after = store.conn.execute(
            "SELECT last_used_at FROM memory_embedding WHERE memory_id = 'm1'"
        ).fetchone()
        assert after["last_used_at"] is not None
        assert store.get_memory_embedding("m1", "missing", "kylin", "v1") is None
    finally:
        store.close()

    reopened = SQLiteMetaStore(db_path)
    try:
        assert reopened.get_memory_embedding("m1", "hash-a", "kylin", "v1") is not None
    finally:
        reopened.close()


def test_embedding_upsert_replaces_only_the_matching_versioned_key(tmp_path):
    store = SQLiteMetaStore(tmp_path / "meta.sqlite3")
    try:
        store.upsert_memory_embedding("m1", "hash-a", "kylin", "v1", [1.0])
        store.upsert_memory_embedding("m1", "hash-a", "kylin", "v2", [2.0])
        store.upsert_memory_embedding("m1", "hash-a", "kylin", "v1", [3.0, 4.0])

        v1 = store.get_memory_embedding("m1", "hash-a", "kylin", "v1")
        v2 = store.get_memory_embedding("m1", "hash-a", "kylin", "v2")
        assert v1 is not None and v1.vector == [3.0, 4.0] and v1.dimension == 2
        assert v2 is not None and v2.vector == [2.0]
    finally:
        store.close()


@pytest.mark.parametrize("status", ["pending", "failed"])
def test_unready_embedding_without_vector_is_not_returned_or_touched(tmp_path, status):
    store = SQLiteMetaStore(tmp_path / "meta.sqlite3")
    try:
        store.conn.execute(
            """
            INSERT INTO memory_embedding (
                memory_id, content_hash, backend, model_revision, dimension,
                vector_blob, status, created_at, updated_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            ("m1", "hash-a", "kylin", "v1", 2, None, status, "created", "updated"),
        )
        store.conn.commit()

        row = store.conn.execute(
            "SELECT vector_blob, failure_reason, last_used_at FROM memory_embedding"
        ).fetchone()
        assert row["vector_blob"] is None
        assert row["failure_reason"] == ""
        assert store.get_memory_embedding("m1", "hash-a", "kylin", "v1") is None
        assert row["last_used_at"] is None
        untouched = store.conn.execute(
            "SELECT last_used_at FROM memory_embedding"
        ).fetchone()
        assert untouched["last_used_at"] is None
    finally:
        store.close()


def test_ready_embedding_with_null_blob_is_not_returned_or_touched(tmp_path):
    store = SQLiteMetaStore(tmp_path / "meta.sqlite3")
    try:
        store.conn.execute("PRAGMA ignore_check_constraints = ON")
        store.conn.execute(
            """
            INSERT INTO memory_embedding (
                memory_id, content_hash, backend, model_revision, dimension,
                vector_blob, status, created_at, updated_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            ("m1", "hash-a", "kylin", "v1", 2, None, "ready", "created", "updated"),
        )
        store.conn.commit()
        store.conn.execute("PRAGMA ignore_check_constraints = OFF")

        assert store.get_memory_embedding("m1", "hash-a", "kylin", "v1") is None
        row = store.conn.execute(
            "SELECT last_used_at FROM memory_embedding"
        ).fetchone()
        assert row["last_used_at"] is None
    finally:
        store.close()


def test_embedding_last_used_at_updates_at_most_once_per_minute(tmp_path, monkeypatch):
    store = SQLiteMetaStore(tmp_path / "meta.sqlite3")
    try:
        store.upsert_memory_embedding("m1", "hash-a", "kylin", "v1", [1.0])
        timestamps = iter(
            [
                "2026-07-11T12:00:00+00:00",
                "2026-07-11T12:00:30+00:00",
                "2026-07-11T12:01:01+00:00",
            ]
        )
        monkeypatch.setattr(sqlite_meta, "utc_now", lambda: next(timestamps))
        statements = []
        store.conn.set_trace_callback(statements.append)

        assert store.get_memory_embedding("m1", "hash-a", "kylin", "v1") is not None
        first_used_at = store.conn.execute(
            "SELECT last_used_at FROM memory_embedding"
        ).fetchone()["last_used_at"]
        assert first_used_at == "2026-07-11T12:00:00+00:00"

        statements.clear()
        assert store.get_memory_embedding("m1", "hash-a", "kylin", "v1") is not None
        assert not any(statement.lstrip().startswith("UPDATE") for statement in statements)
        assert "COMMIT" not in statements
        second_used_at = store.conn.execute(
            "SELECT last_used_at FROM memory_embedding"
        ).fetchone()["last_used_at"]
        assert second_used_at == first_used_at

        statements.clear()
        assert store.get_memory_embedding("m1", "hash-a", "kylin", "v1") is not None
        assert any(statement.lstrip().startswith("UPDATE") for statement in statements)
        assert "COMMIT" in statements
        third_used_at = store.conn.execute(
            "SELECT last_used_at FROM memory_embedding"
        ).fetchone()["last_used_at"]
        assert third_used_at == "2026-07-11T12:01:01+00:00"
    finally:
        store.close()


def test_embedding_last_used_at_repairs_invalid_timestamp(tmp_path, monkeypatch):
    store = SQLiteMetaStore(tmp_path / "meta.sqlite3")
    try:
        store.upsert_memory_embedding("m1", "hash-a", "kylin", "v1", [1.0])
        store.conn.execute(
            "UPDATE memory_embedding SET last_used_at = ? WHERE memory_id = ?",
            ("not-an-iso-timestamp", "m1"),
        )
        store.conn.commit()
        monkeypatch.setattr(
            sqlite_meta, "utc_now", lambda: "2026-07-11T12:00:00+00:00"
        )

        assert store.get_memory_embedding("m1", "hash-a", "kylin", "v1") is not None
        repaired = store.conn.execute(
            "SELECT last_used_at FROM memory_embedding"
        ).fetchone()["last_used_at"]
        assert repaired == "2026-07-11T12:00:00+00:00"
    finally:
        store.close()


def test_embedding_upsert_promotes_pending_row_to_ready(tmp_path):
    store = SQLiteMetaStore(tmp_path / "meta.sqlite3")
    try:
        store.conn.execute(
            """
            INSERT INTO memory_embedding (
                memory_id, content_hash, backend, model_revision, dimension,
                vector_blob, status, failure_reason, created_at, updated_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                "m1",
                "hash-a",
                "kylin",
                "v1",
                2,
                None,
                "pending",
                "waiting",
                "created",
                "updated",
            ),
        )
        store.conn.commit()

        store.upsert_memory_embedding("m1", "hash-a", "kylin", "v1", [1.0, 2.0])

        row = store.conn.execute(
            "SELECT status, failure_reason, vector_blob FROM memory_embedding"
        ).fetchone()
        assert row["status"] == "ready"
        assert row["failure_reason"] == ""
        assert row["vector_blob"] == encode_vector([1.0, 2.0])
        with pytest.raises(TypeError):
            store.upsert_memory_embedding(
                "m2", "hash-b", "kylin", "v1", [3.0], status="pending"
            )
    finally:
        store.close()


def test_embedding_status_counts_and_delete_lifecycle(tmp_path):
    store = SQLiteMetaStore(tmp_path / "meta.sqlite3")
    try:
        assert store.embedding_cache_status("kylin", "v1") == {
            "pending": 0,
            "ready": 0,
            "failed": 0,
        }
        store.upsert_memory_embedding("m1", "h1", "kylin", "v1", [1.0])
        store.conn.executemany(
            """
            INSERT INTO memory_embedding (
                memory_id, content_hash, backend, model_revision, dimension,
                vector_blob, status, created_at, updated_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            [
                ("m1", "h2", "kylin", "v1", 1, None, "pending", "now", "now"),
                ("m2", "h3", "kylin", "v1", 1, None, "failed", "now", "now"),
            ],
        )
        store.conn.commit()
        store.upsert_memory_embedding("m3", "h4", "other", "v1", [4.0])

        assert store.embedding_cache_status("kylin", "v1") == {
            "pending": 1,
            "ready": 1,
            "failed": 1,
        }
        assert store.delete_memory_embeddings("m1") == 2
        assert store.delete_memory_embeddings("missing") == 0
        assert store.embedding_cache_status("kylin", "v1") == {
            "pending": 0,
            "ready": 0,
            "failed": 1,
        }
    finally:
        store.close()


def test_pending_embedding_queue_filters_governance_and_retries_failures(
    tmp_path, monkeypatch
):
    store = SQLiteMetaStore(tmp_path / "meta.sqlite3")
    monkeypatch.setattr(sqlite_meta, "utc_now", lambda: "2026-07-11T12:00:00+00:00")
    try:
        states = [
            ("trusted", "active", False),
            ("reviewed", "active", False),
            ("verified", "active", False),
            ("untrusted", "quarantined", False),
            ("rejected", "rejected", False),
            ("trusted", "active", True),
            ("untrusted", "active", False),
        ]
        records = []
        for index, (trust_level, governance_state, deleted) in enumerate(states):
            record = make_memory(
                content="content-%d" % index,
                memory_type="knowledge",
                source=MemorySource(kind="manual", uri="test"),
                summary="summary-%d" % index,
                trust_level=trust_level,
                governance_state=governance_state,
            )
            record.deleted = deleted
            store.upsert_memory(record)
            store.enqueue_memory_embedding(record.memory_id, "hash-%d" % index, "kylin", "v1")
            records.append(record)

        store.mark_embedding_failed(
            records[1].memory_id,
            "hash-1",
            "kylin",
            "v1",
            "temporary failure" * 100,
        )
        store.enqueue_memory_embedding(
            records[2].memory_id,
            "hash-verified-failed",
            "kylin",
            "v1",
        )
        store.mark_embedding_failed(
            records[2].memory_id,
            "hash-verified-failed",
            "kylin",
            "v1",
            "retry verified memory",
        )
        monkeypatch.setattr(
            sqlite_meta, "utc_now", lambda: "2026-07-11T12:00:02+00:00"
        )

        pending = store.list_pending_embeddings("kylin", "v1", limit=10)

        expected_pending = sorted(
            [
                (records[0].memory_id, "hash-0", "pending"),
                (records[2].memory_id, "hash-2", "pending"),
            ]
        )
        expected_failed = sorted(
            [
                (records[1].memory_id, "hash-1", "failed"),
                (records[2].memory_id, "hash-verified-failed", "failed"),
            ]
        )
        assert [
            (row["memory_id"], row["content_hash"], row["status"]) for row in pending
        ] == expected_pending + expected_failed
        assert pending[0]["status"] == "pending"
        trusted = next(row for row in pending if row["memory_id"] == records[0].memory_id)
        assert trusted["summary"] == "summary-0"
        assert trusted["content"] == "content-0"
        assert store.list_pending_embeddings("kylin", "v1", limit=0) == []
        failed = store.conn.execute(
            "SELECT dimension, vector_blob, failure_reason FROM memory_embedding "
            "WHERE memory_id = ?",
            (records[1].memory_id,),
        ).fetchone()
        assert failed["dimension"] == 0
        assert failed["vector_blob"] is None
        assert len(failed["failure_reason"]) == 500
        assert store.embedding_cache_status("kylin", "v1") == {
            "pending": 6,
            "ready": 0,
            "failed": 2,
        }

        store.enqueue_memory_embedding(records[1].memory_id, "hash-1", "kylin", "v1")
        retried = store.conn.execute(
            "SELECT status, failure_reason FROM memory_embedding WHERE memory_id = ?",
            (records[1].memory_id,),
        ).fetchone()
        assert dict(retried) == {"status": "pending", "failure_reason": ""}
    finally:
        store.close()


def test_enqueue_embedding_conflict_clears_ready_vector(tmp_path):
    store = SQLiteMetaStore(tmp_path / "meta.sqlite3")
    try:
        store.upsert_memory_embedding("m1", "hash-a", "kylin", "v1", [1.0, 2.0])

        store.enqueue_memory_embedding("m1", "hash-a", "kylin", "v1")

        row = store.conn.execute(
            "SELECT dimension, vector_blob, status, failure_reason FROM memory_embedding"
        ).fetchone()
        assert dict(row) == {
            "dimension": 0,
            "vector_blob": None,
            "status": "pending",
            "failure_reason": "",
        }
    finally:
        store.close()


def test_atomic_memory_job_rolls_back_when_embedding_insert_fails(tmp_path):
    store = SQLiteMetaStore(tmp_path / "meta.sqlite3")
    statements = []
    store.conn.set_trace_callback(statements.append)
    record = make_memory(
        content="atomic memory",
        memory_type="knowledge",
        source=MemorySource(kind="manual", uri="test"),
        summary="atomic summary",
    )
    try:
        store.conn.execute(
            """
            CREATE TRIGGER fail_embedding_enqueue
            BEFORE INSERT ON memory_embedding
            BEGIN
                SELECT RAISE(ABORT, 'injected embedding enqueue failure');
            END
            """
        )
        store.conn.commit()
        statements.clear()

        with pytest.raises(sqlite3.IntegrityError, match="injected embedding enqueue failure"):
            store.upsert_memory_with_embedding_job(
                record, "kylin", "v1", vector_content_hash(record.summary, record.content)
            )

        assert store.get_memory(record.memory_id) is None
        assert store.conn.execute("SELECT COUNT(*) FROM memory_embedding").fetchone()[0] == 0
        assert any(statement == "ROLLBACK" for statement in statements)
    finally:
        store.close()


def test_atomic_memory_job_preserves_exact_ready_and_replaces_stale_rows(tmp_path):
    store = SQLiteMetaStore(tmp_path / "meta.sqlite3")
    record = make_memory(
        content="current content",
        memory_type="knowledge",
        source=MemorySource(kind="manual", uri="test"),
        summary="current summary",
    )
    current_hash = vector_content_hash(record.summary, record.content)
    try:
        store.upsert_memory_with_embedding_job(record, "kylin", "v1", current_hash)
        assert store.complete_memory_embedding(
            record.memory_id, current_hash, "kylin", "v1", [1.0, 2.0]
        )
        ready_blob = encode_vector([1.0, 2.0])
        store.upsert_memory_embedding(record.memory_id, "stale", "other", "old", [3.0])

        store.upsert_memory_with_embedding_job(record, "kylin", "v1", current_hash)

        rows = store.conn.execute(
            "SELECT content_hash, backend, model_revision, status, vector_blob "
            "FROM memory_embedding WHERE memory_id = ?",
            (record.memory_id,),
        ).fetchall()
        assert [dict(row) for row in rows] == [
            {
                "content_hash": current_hash,
                "backend": "kylin",
                "model_revision": "v1",
                "status": "ready",
                "vector_blob": ready_blob,
            }
        ]

        store.enqueue_memory_embedding(record.memory_id, current_hash, "kylin", "v1")
        store.upsert_memory_with_embedding_job(record, "kylin", "v1", current_hash)
        pending = store.conn.execute(
            "SELECT status, attempt_count, next_attempt_at FROM memory_embedding"
        ).fetchone()
        assert dict(pending) == {
            "status": "pending",
            "attempt_count": 0,
            "next_attempt_at": "",
        }

        assert store.mark_embedding_failed(
            record.memory_id, current_hash, "kylin", "v1", "preserve retry state"
        )
        failed_before = dict(
            store.conn.execute(
                """
                SELECT status, failure_reason, attempt_count, next_attempt_at
                FROM memory_embedding
                """
            ).fetchone()
        )
        store.upsert_memory_with_embedding_job(record, "kylin", "v1", current_hash)
        failed_after = dict(
            store.conn.execute(
                """
                SELECT status, failure_reason, attempt_count, next_attempt_at
                FROM memory_embedding
                """
            ).fetchone()
        )
        assert failed_after == failed_before
    finally:
        store.close()


def test_mark_deleted_rolls_back_on_embedding_delete_failure_then_deletes_atomically(tmp_path):
    store = SQLiteMetaStore(tmp_path / "meta.sqlite3")
    record = make_memory(
        content="forget atomically",
        memory_type="knowledge",
        source=MemorySource(kind="manual", uri="test"),
        summary="forget atomically",
    )
    content_hash = vector_content_hash(record.summary, record.content)
    try:
        store.upsert_memory_with_embedding_job(record, "kylin", "v1", content_hash)
        store.conn.execute(
            """
            CREATE TRIGGER fail_embedding_delete
            BEFORE DELETE ON memory_embedding
            BEGIN
                SELECT RAISE(ABORT, 'injected embedding delete failure');
            END
            """
        )
        store.conn.commit()

        with pytest.raises(sqlite3.IntegrityError, match="injected embedding delete failure"):
            store.mark_deleted(record.memory_id)

        assert store.get_memory(record.memory_id)["deleted"] == 0
        assert store.conn.execute(
            "SELECT COUNT(*) FROM memory_embedding WHERE memory_id = ?", (record.memory_id,)
        ).fetchone()[0] == 1

        store.conn.execute("DROP TRIGGER fail_embedding_delete")
        store.conn.commit()
        store.mark_deleted(record.memory_id)
        assert store.get_memory(record.memory_id)["deleted"] == 1
        assert store.conn.execute(
            "SELECT COUNT(*) FROM memory_embedding WHERE memory_id = ?", (record.memory_id,)
        ).fetchone()[0] == 0
    finally:
        store.close()


def test_embedding_completion_and_failure_use_compare_and_swap(tmp_path):
    store = SQLiteMetaStore(tmp_path / "meta.sqlite3")
    record = make_memory(
        content="worker race",
        memory_type="knowledge",
        source=MemorySource(kind="manual", uri="test"),
        summary="worker race",
    )
    old_hash = vector_content_hash(record.summary, record.content)
    try:
        store.upsert_memory_with_embedding_job(record, "kylin", "v1", old_hash)

        assert store.complete_memory_embedding(
            record.memory_id, old_hash, "kylin", "v1", [1.0, 2.0]
        )
        assert not store.mark_embedding_failed(
            record.memory_id, old_hash, "kylin", "v1", "late worker failure"
        )
        ready = store.get_memory_embedding(record.memory_id, old_hash, "kylin", "v1")
        assert ready is not None
        assert ready.vector == [1.0, 2.0]

        record.content = "worker race updated"
        record.summary = "worker race updated"
        new_hash = vector_content_hash(record.summary, record.content)
        store.upsert_memory_with_embedding_job(record, "kylin", "v1", new_hash)
        assert not store.complete_memory_embedding(
            record.memory_id, old_hash, "kylin", "v1", [9.0]
        )
        assert store.conn.execute(
            "SELECT COUNT(*) FROM memory_embedding WHERE content_hash = ?", (old_hash,)
        ).fetchone()[0] == 0

        store.mark_deleted(record.memory_id)
        assert not store.complete_memory_embedding(
            record.memory_id, new_hash, "kylin", "v1", [9.0]
        )
        assert store.conn.execute(
            "SELECT COUNT(*) FROM memory_embedding WHERE memory_id = ?", (record.memory_id,)
        ).fetchone()[0] == 0
    finally:
        store.close()


def test_failed_embedding_backoff_due_filter_fairness_and_stable_order(tmp_path, monkeypatch):
    store = SQLiteMetaStore(tmp_path / "meta.sqlite3")
    monkeypatch.setattr(sqlite_meta, "utc_now", lambda: "2026-07-11T12:00:00+00:00")
    records = []
    try:
        for index in range(5):
            record = make_memory(
                content="queue-content-%d" % index,
                memory_type="knowledge",
                source=MemorySource(kind="manual", uri="test"),
                summary="queue-summary-%d" % index,
            )
            store.upsert_memory(record)
            store.enqueue_memory_embedding(record.memory_id, "hash-%d" % index, "kylin", "v1")
            records.append(record)

        assert store.mark_embedding_failed(
            records[0].memory_id, "hash-0", "kylin", "v1", "temporary"
        )
        failed = store.conn.execute(
            "SELECT attempt_count, next_attempt_at FROM memory_embedding WHERE memory_id = ?",
            (records[0].memory_id,),
        ).fetchone()
        assert dict(failed) == {
            "attempt_count": 1,
            "next_attempt_at": "2026-07-11T12:00:01+00:00",
        }
        assert records[0].memory_id not in {
            row["memory_id"] for row in store.list_pending_embeddings("kylin", "v1", 10)
        }

        for _ in range(9):
            assert store.mark_embedding_failed(
                records[0].memory_id, "hash-0", "kylin", "v1", "temporary"
            )
        capped = store.conn.execute(
            "SELECT attempt_count, next_attempt_at FROM memory_embedding WHERE memory_id = ?",
            (records[0].memory_id,),
        ).fetchone()
        assert dict(capped) == {
            "attempt_count": 10,
            "next_attempt_at": "2026-07-11T12:05:00+00:00",
        }

        store.conn.execute(
            "UPDATE memory_embedding SET updated_at = ? WHERE memory_id = ?",
            ("2026-07-11T11:00:00+00:00", records[0].memory_id),
        )
        store.conn.commit()
        monkeypatch.setattr(sqlite_meta, "utc_now", lambda: "2026-07-11T12:05:01+00:00")

        globally_oldest = store.list_pending_embeddings("kylin", "v1", 1)
        assert [(row["memory_id"], row["status"]) for row in globally_oldest] == [
            (records[0].memory_id, "failed")
        ]
        fair = store.list_pending_embeddings("kylin", "v1", 2)
        assert len(fair) == 2
        assert [row["status"] for row in fair] == ["pending", "failed"]
        assert fair[1]["memory_id"] == records[0].memory_id

        stable_first = store.list_pending_embeddings("kylin", "v1", 10)
        stable_second = store.list_pending_embeddings("kylin", "v1", 10)
        assert [
            (row["status"], row["updated_at"], row["memory_id"], row["content_hash"])
            for row in stable_first
        ] == [
            (row["status"], row["updated_at"], row["memory_id"], row["content_hash"])
            for row in stable_second
        ]
    finally:
        store.close()


def test_missing_embedding_scan_is_read_only_and_requires_exact_current_hash(tmp_path):
    store = SQLiteMetaStore(tmp_path / "meta.sqlite3")
    records = []
    try:
        for index in range(4):
            record = make_memory(
                content="missing-content-%d" % index,
                memory_type="knowledge",
                source=MemorySource(kind="manual", uri="test"),
                summary="missing-summary-%d" % index,
            )
            store.upsert_memory(record)
            records.append(record)

        current_hashes = [
            vector_content_hash(record.summary, record.content) for record in records
        ]
        store.enqueue_memory_embedding(records[1].memory_id, current_hashes[1], "kylin", "v1")
        store.enqueue_memory_embedding(records[2].memory_id, current_hashes[2], "kylin", "v1")
        store.mark_embedding_failed(records[2].memory_id, current_hashes[2], "kylin", "v1", "x")
        store.upsert_memory_embedding(
            records[3].memory_id, current_hashes[3], "kylin", "v1", [1.0]
        )
        store.enqueue_memory_embedding(records[0].memory_id, "stale-hash", "kylin", "v1")
        before = store.conn.total_changes

        missing = store.list_memories_missing_embeddings("kylin", "v1", 10)

        assert [(row["memory_id"], row["content_hash"]) for row in missing] == [
            (records[0].memory_id, current_hashes[0])
        ]
        assert store.conn.total_changes == before
    finally:
        store.close()
