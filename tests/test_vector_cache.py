import math
import sqlite3
import struct
import threading
from concurrent.futures import ThreadPoolExecutor

import pytest

from memguard.retrieval.vector_cache import decode_vector, encode_vector, vector_content_hash
from memguard.models import MemorySource, make_memory
import memguard.store.sqlite_meta as sqlite_meta
from memguard.store.sqlite_meta import SQLiteMetaStore


def embedding_job_token(
    store, memory_id, content_hash, backend="kylin", model_revision="v1"
):
    row = store.conn.execute(
        """
        SELECT job_token
        FROM memory_embedding
        WHERE memory_id = ? AND content_hash = ?
          AND backend = ? AND model_revision = ?
        """,
        (memory_id, content_hash, backend, model_revision),
    ).fetchone()
    assert row is not None
    return row["job_token"]


def insert_embedding_row(
    store,
    memory_id,
    content_hash,
    status,
    updated_at,
    job_token,
    *,
    backend="kylin",
    model_revision="v1",
):
    vector_blob = encode_vector([1.0]) if status == "ready" else None
    dimension = 1 if status == "ready" else 0
    store.conn.execute(
        """
        INSERT INTO memory_embedding (
            memory_id, content_hash, backend, model_revision, dimension,
            vector_blob, status, failure_reason, created_at, updated_at,
            job_token
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            memory_id,
            content_hash,
            backend,
            model_revision,
            dimension,
            vector_blob,
            status,
            "test failure" if status == "failed" else "",
            updated_at,
            updated_at,
            job_token,
        ),
    )


def add_eligible_memory(store, memory_id, label):
    record = make_memory(
        content=label + " content",
        memory_type="knowledge",
        source=MemorySource(kind="manual", uri="test"),
        summary=label + " summary",
    )
    record.memory_id = memory_id
    store.upsert_memory(record)
    return vector_content_hash(record.summary, record.content)


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
            "job_token",
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
        assert columns["job_token"]["notnull"] == 1
        cursor_columns = {
            row["name"]
            for row in store.conn.execute("PRAGMA table_info(embedding_scan_cursor)")
        }
        assert cursor_columns == {
            "backend",
            "model_revision",
            "last_memory_id",
            "updated_at",
        }
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
                    vector_blob, status, created_at, updated_at, job_token
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    "m1",
                    "hash",
                    "sdk",
                    "v1",
                    1,
                    encode_vector([1.0]),
                    "unknown",
                    "now",
                    "now",
                    "job-invalid",
                ),
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
                    vector_blob, status, created_at, updated_at, job_token
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
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
                    "job-ready-invalid",
                ),
            )
    finally:
        store.close()


def test_embedding_upsert_get_and_last_used_lifecycle(tmp_path):
    db_path = tmp_path / "meta.sqlite3"
    store = SQLiteMetaStore(db_path)
    try:
        content_hash = add_eligible_memory(store, "m1", "lifecycle")
        store.upsert_memory_embedding(
            memory_id="m1",
            content_hash=content_hash,
            backend="kylin",
            model_revision="v1",
            vector=[1.5, -2.25],
        )
        before = store.conn.execute(
            "SELECT last_used_at FROM memory_embedding WHERE memory_id = 'm1'"
        ).fetchone()
        assert before["last_used_at"] is None

        persisted = store.get_memory_embedding("m1", content_hash, "kylin", "v1")

        assert persisted is not None
        assert persisted.memory_id == "m1"
        assert persisted.content_hash == content_hash
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
        assert reopened.get_memory_embedding("m1", content_hash, "kylin", "v1") is not None
    finally:
        reopened.close()


def test_embedding_upsert_replaces_only_the_matching_versioned_key(tmp_path):
    store = SQLiteMetaStore(tmp_path / "meta.sqlite3")
    try:
        content_hash = add_eligible_memory(store, "m1", "versioned")
        store.upsert_memory_embedding("m1", content_hash, "kylin", "v1", [1.0])
        store.upsert_memory_embedding("m1", content_hash, "kylin", "v2", [2.0])
        store.upsert_memory_embedding("m1", content_hash, "kylin", "v1", [3.0, 4.0])

        v1 = store.get_memory_embedding("m1", content_hash, "kylin", "v1")
        v2 = store.get_memory_embedding("m1", content_hash, "kylin", "v2")
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
                vector_blob, status, created_at, updated_at, job_token
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                "m1",
                "hash-a",
                "kylin",
                "v1",
                2,
                None,
                status,
                "created",
                "updated",
                "job-unready",
            ),
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
                vector_blob, status, created_at, updated_at, job_token
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                "m1",
                "hash-a",
                "kylin",
                "v1",
                2,
                None,
                "ready",
                "created",
                "updated",
                "job-null-blob",
            ),
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
        content_hash = add_eligible_memory(store, "m1", "touch")
        store.upsert_memory_embedding("m1", content_hash, "kylin", "v1", [1.0])
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

        assert store.get_memory_embedding("m1", content_hash, "kylin", "v1") is not None
        first_used_at = store.conn.execute(
            "SELECT last_used_at FROM memory_embedding"
        ).fetchone()["last_used_at"]
        assert first_used_at == "2026-07-11T12:00:00+00:00"

        statements.clear()
        assert store.get_memory_embedding("m1", content_hash, "kylin", "v1") is not None
        assert not any(statement.lstrip().startswith("UPDATE") for statement in statements)
        assert "COMMIT" not in statements
        second_used_at = store.conn.execute(
            "SELECT last_used_at FROM memory_embedding"
        ).fetchone()["last_used_at"]
        assert second_used_at == first_used_at

        statements.clear()
        assert store.get_memory_embedding("m1", content_hash, "kylin", "v1") is not None
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
        content_hash = add_eligible_memory(store, "m1", "repair-touch")
        store.upsert_memory_embedding("m1", content_hash, "kylin", "v1", [1.0])
        store.conn.execute(
            "UPDATE memory_embedding SET last_used_at = ? WHERE memory_id = ?",
            ("not-an-iso-timestamp", "m1"),
        )
        store.conn.commit()
        monkeypatch.setattr(
            sqlite_meta, "utc_now", lambda: "2026-07-11T12:00:00+00:00"
        )

        assert store.get_memory_embedding("m1", content_hash, "kylin", "v1") is not None
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
                vector_blob, status, failure_reason, created_at, updated_at,
                job_token
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
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
                "job-promote",
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
            "due": 0,
            "retry_deferred": 0,
            "next_retry_at": None,
        }
        hashes = {
            memory_id: add_eligible_memory(store, memory_id, "status-" + memory_id)
            for memory_id in ("m1", "m2", "m3", "m4")
        }
        store.upsert_memory_embedding("m1", hashes["m1"], "kylin", "v1", [1.0])
        insert_embedding_row(
            store, "m2", hashes["m2"], "pending", "now", "job-status-pending"
        )
        insert_embedding_row(
            store, "m3", hashes["m3"], "failed", "now", "job-status-failed"
        )
        store.conn.commit()
        store.upsert_memory_embedding("m4", hashes["m4"], "other", "v1", [4.0])

        assert store.embedding_cache_status("kylin", "v1") == {
            "pending": 1,
            "ready": 1,
            "failed": 1,
            "due": 2,
            "retry_deferred": 0,
            "next_retry_at": None,
        }
        assert store.delete_memory_embeddings("m1") == 1
        assert store.delete_memory_embeddings("missing") == 0
        assert store.embedding_cache_status("kylin", "v1") == {
            "pending": 1,
            "ready": 0,
            "failed": 1,
            "due": 2,
            "retry_deferred": 0,
            "next_retry_at": None,
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
        content_hashes = []
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
            content_hash = vector_content_hash(record.summary, record.content)
            created = store.enqueue_memory_embedding(
                record.memory_id, content_hash, "kylin", "v1"
            )
            assert created is (index < 3)
            records.append(record)
            content_hashes.append(content_hash)

        store.mark_embedding_failed(
            records[1].memory_id,
            content_hashes[1],
            "kylin",
            "v1",
            "temporary failure" * 100,
            expected_attempt_count=0,
            expected_job_token=embedding_job_token(
                store, records[1].memory_id, content_hashes[1]
            ),
        )
        monkeypatch.setattr(
            sqlite_meta, "utc_now", lambda: "2026-07-11T12:00:02+00:00"
        )

        pending = store.list_pending_embeddings("kylin", "v1", limit=10)

        expected_pending = sorted(
            [
                (records[0].memory_id, content_hashes[0], "pending"),
                (records[2].memory_id, content_hashes[2], "pending"),
            ]
        )
        expected_failed = sorted(
            [
                (records[1].memory_id, content_hashes[1], "failed"),
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
            "pending": 2,
            "ready": 0,
            "failed": 1,
            "due": 3,
            "retry_deferred": 0,
            "next_retry_at": None,
        }

        assert not store.enqueue_memory_embedding(
            records[1].memory_id, content_hashes[1], "kylin", "v1"
        )
        retried = store.conn.execute(
            "SELECT status, failure_reason FROM memory_embedding WHERE memory_id = ?",
            (records[1].memory_id,),
        ).fetchone()
        assert retried["status"] == "failed"
        assert len(retried["failure_reason"]) == 500
    finally:
        store.close()


def test_enqueue_embedding_conflict_preserves_ready_vector(tmp_path):
    store = SQLiteMetaStore(tmp_path / "meta.sqlite3")
    record = make_memory(
        content="ready content",
        memory_type="knowledge",
        source=MemorySource(kind="manual", uri="test"),
        summary="ready summary",
    )
    content_hash = vector_content_hash(record.summary, record.content)
    try:
        store.upsert_memory(record)
        store.upsert_memory_embedding(
            record.memory_id, content_hash, "kylin", "v1", [1.0, 2.0]
        )
        token_before = store.conn.execute(
            "SELECT job_token FROM memory_embedding"
        ).fetchone()["job_token"]

        assert not store.enqueue_memory_embedding(
            record.memory_id, content_hash, "kylin", "v1"
        )

        row = store.conn.execute(
            "SELECT dimension, vector_blob, status, failure_reason, job_token "
            "FROM memory_embedding"
        ).fetchone()
        assert dict(row) == {
            "dimension": 2,
            "vector_blob": encode_vector([1.0, 2.0]),
            "status": "ready",
            "failure_reason": "",
            "job_token": token_before,
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
        ready_token = embedding_job_token(store, record.memory_id, current_hash)
        assert store.complete_memory_embedding(
            record.memory_id,
            current_hash,
            "kylin",
            "v1",
            [1.0, 2.0],
            expected_attempt_count=0,
            expected_job_token=ready_token,
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

        assert not store.enqueue_memory_embedding(
            record.memory_id, current_hash, "kylin", "v1"
        )
        store.upsert_memory_with_embedding_job(record, "kylin", "v1", current_hash)
        preserved = store.conn.execute(
            "SELECT status, attempt_count, next_attempt_at, job_token FROM memory_embedding"
        ).fetchone()
        assert dict(preserved) == {
            "status": "ready",
            "attempt_count": 0,
            "next_attempt_at": "",
            "job_token": ready_token,
        }
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
        old_job_token = embedding_job_token(store, record.memory_id, old_hash)

        assert store.complete_memory_embedding(
            record.memory_id,
            old_hash,
            "kylin",
            "v1",
            [1.0, 2.0],
            expected_attempt_count=0,
            expected_job_token=old_job_token,
        )
        assert not store.mark_embedding_failed(
            record.memory_id,
            old_hash,
            "kylin",
            "v1",
            "late worker failure",
            expected_attempt_count=0,
            expected_job_token=old_job_token,
        )
        ready = store.get_memory_embedding(record.memory_id, old_hash, "kylin", "v1")
        assert ready is not None
        assert ready.vector == [1.0, 2.0]

        record.content = "worker race updated"
        record.summary = "worker race updated"
        new_hash = vector_content_hash(record.summary, record.content)
        store.upsert_memory_with_embedding_job(record, "kylin", "v1", new_hash)
        new_job_token = embedding_job_token(store, record.memory_id, new_hash)
        assert not store.complete_memory_embedding(
            record.memory_id,
            old_hash,
            "kylin",
            "v1",
            [9.0],
            expected_attempt_count=0,
            expected_job_token=old_job_token,
        )
        assert store.conn.execute(
            "SELECT COUNT(*) FROM memory_embedding WHERE content_hash = ?", (old_hash,)
        ).fetchone()[0] == 0

        store.mark_deleted(record.memory_id)
        assert not store.complete_memory_embedding(
            record.memory_id,
            new_hash,
            "kylin",
            "v1",
            [9.0],
            expected_attempt_count=0,
            expected_job_token=new_job_token,
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
    content_hashes = []
    try:
        for index in range(5):
            record = make_memory(
                content="queue-content-%d" % index,
                memory_type="knowledge",
                source=MemorySource(kind="manual", uri="test"),
                summary="queue-summary-%d" % index,
            )
            store.upsert_memory(record)
            content_hash = vector_content_hash(record.summary, record.content)
            assert store.enqueue_memory_embedding(
                record.memory_id, content_hash, "kylin", "v1"
            )
            records.append(record)
            content_hashes.append(content_hash)

        failed_job_token = embedding_job_token(
            store, records[0].memory_id, content_hashes[0]
        )

        assert store.mark_embedding_failed(
            records[0].memory_id,
            content_hashes[0],
            "kylin",
            "v1",
            "temporary",
            expected_attempt_count=0,
            expected_job_token=failed_job_token,
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

        for expected_attempt_count in range(1, 10):
            assert store.mark_embedding_failed(
                records[0].memory_id,
                content_hashes[0],
                "kylin",
                "v1",
                "temporary",
                expected_attempt_count=expected_attempt_count,
                expected_job_token=failed_job_token,
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
        store.mark_embedding_failed(
            records[2].memory_id,
            current_hashes[2],
            "kylin",
            "v1",
            "x",
            expected_attempt_count=0,
            expected_job_token=embedding_job_token(
                store, records[2].memory_id, current_hashes[2]
            ),
        )
        store.upsert_memory_embedding(
            records[3].memory_id, current_hashes[3], "kylin", "v1", [1.0]
        )
        assert not store.enqueue_memory_embedding(
            records[0].memory_id, "stale-hash", "kylin", "v1"
        )
        before = store.conn.total_changes

        missing = store.list_memories_missing_embeddings("kylin", "v1", 10)

        assert [(row["memory_id"], row["content_hash"]) for row in missing] == [
            (records[0].memory_id, current_hashes[0])
        ]
        assert store.conn.total_changes == before
    finally:
        store.close()


def test_embedding_workers_require_the_attempt_token_they_observed(tmp_path):
    store = SQLiteMetaStore(tmp_path / "meta.sqlite3")
    record = make_memory(
        content="attempt token race",
        memory_type="knowledge",
        source=MemorySource(kind="manual", uri="test"),
        summary="attempt token race",
    )
    content_hash = vector_content_hash(record.summary, record.content)
    try:
        store.upsert_memory_with_embedding_job(record, "kylin", "v1", content_hash)
        observed = store.list_pending_embeddings("kylin", "v1", 1)[0]
        assert observed["attempt_count"] == 0

        assert store.mark_embedding_failed(
            record.memory_id,
            content_hash,
            "kylin",
            "v1",
            "worker A failed",
            expected_attempt_count=observed["attempt_count"],
            expected_job_token=observed["job_token"],
        )
        assert not store.mark_embedding_failed(
            record.memory_id,
            content_hash,
            "kylin",
            "v1",
            "worker B arrived late",
            expected_attempt_count=observed["attempt_count"],
            expected_job_token=observed["job_token"],
        )
        row = store.conn.execute(
            "SELECT attempt_count, failure_reason FROM memory_embedding"
        ).fetchone()
        assert dict(row) == {"attempt_count": 1, "failure_reason": "worker A failed"}

        assert not store.complete_memory_embedding(
            record.memory_id,
            content_hash,
            "kylin",
            "v1",
            [1.0],
            expected_attempt_count=observed["attempt_count"],
            expected_job_token=observed["job_token"],
        )
        assert store.complete_memory_embedding(
            record.memory_id,
            content_hash,
            "kylin",
            "v1",
            [1.0],
            expected_attempt_count=1,
            expected_job_token=observed["job_token"],
        )

        record.content = "attempt token replacement"
        record.summary = "attempt token replacement"
        replacement_hash = vector_content_hash(record.summary, record.content)
        store.upsert_memory_with_embedding_job(record, "kylin", "v1", replacement_hash)
        replacement_job_token = embedding_job_token(
            store, record.memory_id, replacement_hash
        )
        assert not store.complete_memory_embedding(
            record.memory_id,
            content_hash,
            "kylin",
            "v1",
            [2.0],
            expected_attempt_count=1,
            expected_job_token=observed["job_token"],
        )

        store.mark_deleted(record.memory_id)
        assert not store.mark_embedding_failed(
            record.memory_id,
            replacement_hash,
            "kylin",
            "v1",
            "late after deletion",
            expected_attempt_count=0,
            expected_job_token=replacement_job_token,
        )
    finally:
        store.close()


def test_pending_embedding_queue_uses_bounded_sql(tmp_path):
    store = SQLiteMetaStore(tmp_path / "meta.sqlite3")
    try:
        for index in range(8):
            record = make_memory(
                content="pending-bound-%d" % index,
                memory_type="knowledge",
                source=MemorySource(kind="manual", uri="test"),
                summary="pending-bound-%d" % index,
            )
            store.upsert_memory(record)
            assert store.enqueue_memory_embedding(
                record.memory_id,
                vector_content_hash(record.summary, record.content),
                "kylin",
                "v1",
            )

        statements = []
        store.conn.set_trace_callback(statements.append)
        rows = store.list_pending_embeddings("kylin", "v1", 3)
        store.conn.set_trace_callback(None)

        selects = [statement for statement in statements if statement.lstrip().upper().startswith("SELECT")]
        assert len(rows) == 3
        assert selects
        assert all("LIMIT" in statement.upper() for statement in selects)
    finally:
        store.close()


def test_missing_embedding_scan_uses_bounded_keyset_window(tmp_path):
    store = SQLiteMetaStore(tmp_path / "meta.sqlite3")
    records = []
    try:
        for index in range(7):
            record = make_memory(
                content="scan-bound-%d" % index,
                memory_type="knowledge",
                source=MemorySource(kind="manual", uri="test"),
                summary="scan-bound-%d" % index,
            )
            store.upsert_memory(record)
            records.append(record)

        store.conn.execute("UPDATE memory SET updated_at = ?", ("2026-07-12T00:00:00+00:00",))
        store.conn.commit()

        current_hashes = {
            record.memory_id: vector_content_hash(record.summary, record.content)
            for record in records
        }
        ordered = sorted(records, key=lambda record: record.memory_id)
        assert store.enqueue_memory_embedding(
            ordered[1].memory_id, current_hashes[ordered[1].memory_id], "kylin", "v1"
        )
        assert not store.enqueue_memory_embedding(
            ordered[2].memory_id, "stale", "kylin", "v1"
        )
        expected = [(ordered[0].memory_id, current_hashes[ordered[0].memory_id])]

        statements = []
        store.conn.set_trace_callback(statements.append)
        before = store.conn.total_changes
        missing = store.list_memories_missing_embeddings("kylin", "v1", 2)
        store.conn.set_trace_callback(None)

        selects = [statement for statement in statements if statement.lstrip().upper().startswith("SELECT")]
        assert [(row["memory_id"], row["content_hash"]) for row in missing] == expected
        assert store.conn.total_changes == before
        assert len(selects) <= 3
        assert "MEMORY_ID > ''" in selects[0].upper()
        assert "ORDER BY MEMORY_ID" in selects[0].upper()
        assert "LIMIT 2" in selects[0].upper()
        assert all("LIMIT" in statement.upper() for statement in selects)
    finally:
        store.close()


def test_existing_embedding_rows_receive_unique_job_tokens_on_reopen(tmp_path):
    path = tmp_path / "meta.sqlite3"
    store = SQLiteMetaStore(path)
    try:
        h1 = add_eligible_memory(store, "m1", "legacy-token-1")
        h2 = add_eligible_memory(store, "m2", "legacy-token-2")
        store.upsert_memory_embedding("m1", h1, "kylin", "v1", [1.0])
        store.upsert_memory_embedding("m2", h2, "kylin", "v1", [2.0])
        store.conn.execute("UPDATE memory_embedding SET job_token = ''")
        store.conn.commit()
    finally:
        store.close()

    reopened = SQLiteMetaStore(path)
    try:
        tokens = [
            row["job_token"]
            for row in reopened.conn.execute(
                "SELECT job_token FROM memory_embedding ORDER BY memory_id"
            )
        ]
        assert all(tokens)
        assert len(tokens) == len(set(tokens)) == 2
    finally:
        reopened.close()


def test_safe_enqueue_rejects_stale_memory_and_job_token_aba(tmp_path):
    store = SQLiteMetaStore(tmp_path / "meta.sqlite3")
    try:
        stale = make_memory(
            content="stale original",
            memory_type="knowledge",
            source=MemorySource(kind="manual", uri="test"),
            summary="stale original",
        )
        stale_hash = vector_content_hash(stale.summary, stale.content)
        store.upsert_memory(stale)
        assert store.enqueue_memory_embedding(
            stale.memory_id, stale_hash, "kylin", "v1"
        )
        stale_job = store.list_pending_embeddings("kylin", "v1", 1)[0]

        stale.summary = "stale updated"
        stale.content = "stale updated"
        store.upsert_memory(stale)
        assert not store.enqueue_memory_embedding(
            stale.memory_id, stale_hash, "kylin", "v1"
        )
        assert not store.complete_memory_embedding(
            stale.memory_id,
            stale_hash,
            "kylin",
            "v1",
            [1.0],
            expected_attempt_count=stale_job["attempt_count"],
            expected_job_token=stale_job["job_token"],
        )

        deleted = make_memory(
            content="deleted before enqueue",
            memory_type="knowledge",
            source=MemorySource(kind="manual", uri="test"),
            summary="deleted before enqueue",
        )
        deleted_hash = vector_content_hash(deleted.summary, deleted.content)
        store.upsert_memory(deleted)
        store.mark_deleted(deleted.memory_id)
        assert not store.enqueue_memory_embedding(
            deleted.memory_id, deleted_hash, "kylin", "v1"
        )

        aba = make_memory(
            content="same content",
            memory_type="knowledge",
            source=MemorySource(kind="manual", uri="test"),
            summary="same summary",
        )
        aba_hash = vector_content_hash(aba.summary, aba.content)
        store.upsert_memory_with_embedding_job(aba, "kylin", "v1", aba_hash)
        old_job = next(
            row
            for row in store.list_pending_embeddings("kylin", "v1", 10)
            if row["memory_id"] == aba.memory_id
        )
        store.mark_deleted(aba.memory_id)
        with pytest.raises(ValueError, match="cannot be reactivated"):
            store.upsert_memory_with_embedding_job(aba, "kylin", "v1", aba_hash)
        assert not store.complete_memory_embedding(
            aba.memory_id,
            aba_hash,
            "kylin",
            "v1",
            [9.0],
            expected_attempt_count=old_job["attempt_count"],
            expected_job_token=old_job["job_token"],
        )
    finally:
        store.close()


def test_atomic_missing_job_scan_is_keyset_bounded_and_wraps(tmp_path):
    store = SQLiteMetaStore(tmp_path / "meta.sqlite3")
    records = []
    try:
        for index in range(105):
            record = make_memory(
                content="cursor-content-%03d" % index,
                memory_type="knowledge",
                source=MemorySource(kind="manual", uri="test"),
                summary="cursor-summary-%03d" % index,
            )
            store.upsert_memory(record)
            records.append(record)

        ordered = sorted(records, key=lambda record: record.memory_id)
        late_missing = ordered[-1]
        for record in ordered[:-1]:
            assert store.enqueue_memory_embedding(
                record.memory_id,
                vector_content_hash(record.summary, record.content),
                "kylin",
                "v1",
            )

        reports = []
        for _ in range(12):
            report = store.enqueue_missing_embedding_jobs("kylin", "v1", 10)
            reports.append(report)
            if report["wrapped"]:
                break

        assert reports
        assert all(report["scanned"] <= 10 for report in reports)
        assert sum(report["enqueued"] for report in reports) == 1
        assert reports[-1]["wrapped"] is True
        assert reports[-1]["next_cursor"] == ""
        cursors = [report["next_cursor"] for report in reports[:-1]]
        assert cursors == sorted(cursors)
        assert store.conn.execute(
            "SELECT COUNT(*) FROM memory_embedding WHERE memory_id = ?",
            (late_missing.memory_id,),
        ).fetchone()[0] == 1

    finally:
        store.close()


def test_stale_embeddings_do_not_block_queue_status_or_reopen_cleanup(
    tmp_path, monkeypatch
):
    path = tmp_path / "meta.sqlite3"
    monkeypatch.setattr(sqlite_meta, "utc_now", lambda: "2026-07-23T12:00:00+00:00")
    store = SQLiteMetaStore(path)
    valid = {}
    try:
        for status in ("ready", "pending", "failed"):
            record = make_memory(
                content="valid %s content" % status,
                memory_type="knowledge",
                source=MemorySource(kind="manual", uri="test"),
                summary="valid %s summary" % status,
            )
            content_hash = vector_content_hash(record.summary, record.content)
            store.upsert_memory(record)
            assert store.enqueue_memory_embedding(
                record.memory_id, content_hash, "kylin", "v1"
            )
            valid[status] = {
                "record": record,
                "hash": content_hash,
                "token": embedding_job_token(store, record.memory_id, content_hash),
            }

        ready = valid["ready"]
        assert store.complete_memory_embedding(
            ready["record"].memory_id,
            ready["hash"],
            "kylin",
            "v1",
            [1.0],
            expected_attempt_count=0,
            expected_job_token=ready["token"],
        )
        store.conn.execute(
            """
            UPDATE memory_embedding
            SET status = 'failed', failure_reason = 'retry', next_attempt_at = '',
                updated_at = '2026-07-23T10:01:00+00:00'
            WHERE memory_id = ? AND content_hash = ?
            """,
            (valid["failed"]["record"].memory_id, valid["failed"]["hash"]),
        )
        store.conn.execute(
            """
            UPDATE memory_embedding
            SET updated_at = '2026-07-23T10:00:00+00:00'
            WHERE memory_id = ? AND content_hash = ?
            """,
            (valid["pending"]["record"].memory_id, valid["pending"]["hash"]),
        )

        stale_hashes = {}
        for status, timestamp in (
            ("pending", "2026-07-23T09:00:00+00:00"),
            ("failed", "2026-07-23T09:01:00+00:00"),
            ("ready", "2026-07-23T09:02:00+00:00"),
        ):
            content_hash = vector_content_hash("stale " + status, "stale " + status)
            stale_hashes[status] = content_hash
            insert_embedding_row(
                store,
                ready["record"].memory_id,
                content_hash,
                status,
                timestamp,
                "job-stale-" + status,
            )

        insert_embedding_row(
            store,
            "orphan-memory",
            "orphan-hash",
            "pending",
            "2026-07-23T09:03:00+00:00",
            "job-orphan",
        )
        invalid_records = []
        for label in ("deleted", "inactive", "untrusted"):
            record = make_memory(
                content=label + " content",
                memory_type="knowledge",
                source=MemorySource(kind="manual", uri="test"),
                summary=label + " summary",
            )
            content_hash = vector_content_hash(record.summary, record.content)
            store.upsert_memory(record)
            assert store.enqueue_memory_embedding(
                record.memory_id, content_hash, "kylin", "v1"
            )
            invalid_records.append(record)
        store.conn.execute(
            "UPDATE memory SET deleted = 1 WHERE memory_id = ?",
            (invalid_records[0].memory_id,),
        )
        store.conn.execute(
            "UPDATE memory SET governance_state = 'quarantined' WHERE memory_id = ?",
            (invalid_records[1].memory_id,),
        )
        store.conn.execute(
            "UPDATE memory SET trust_level = 'untrusted' WHERE memory_id = ?",
            (invalid_records[2].memory_id,),
        )
        store.conn.commit()

        limit_one = store.list_pending_embeddings("kylin", "v1", 1)
        assert [row["memory_id"] for row in limit_one] == [
            valid["pending"]["record"].memory_id
        ]
        all_due = store.list_pending_embeddings("kylin", "v1", 4)
        assert [
            (row["memory_id"], row["status"]) for row in all_due
        ] == [
            (valid["pending"]["record"].memory_id, "pending"),
            (valid["failed"]["record"].memory_id, "failed"),
        ]
        assert store.embedding_cache_status("kylin", "v1") == {
            "pending": 1,
            "ready": 1,
            "failed": 1,
            "due": 2,
            "retry_deferred": 0,
            "next_retry_at": None,
        }
        assert (
            store.get_memory_embedding(
                ready["record"].memory_id,
                stale_hashes["ready"],
                "kylin",
                "v1",
            )
            is None
        )
    finally:
        store.close()

    reopened = SQLiteMetaStore(path)
    try:
        rows = reopened.conn.execute(
            """
            SELECT memory_id, content_hash, status
            FROM memory_embedding
            ORDER BY status, memory_id
            """
        ).fetchall()
        expected = sorted(
            (
                item["record"].memory_id,
                item["hash"],
                status,
            )
            for status, item in valid.items()
        )
        assert sorted(
            (row["memory_id"], row["content_hash"], row["status"]) for row in rows
        ) == expected

        for status in ("pending", "failed"):
            item = valid[status]
            assert reopened.complete_memory_embedding(
                item["record"].memory_id,
                item["hash"],
                "kylin",
                "v1",
                [2.0],
                expected_attempt_count=0,
                expected_job_token=item["token"],
            )
        final_status = reopened.embedding_cache_status("kylin", "v1")
        assert final_status == {
            "pending": 0,
            "ready": 3,
            "failed": 0,
            "due": 0,
            "retry_deferred": 0,
            "next_retry_at": None,
        }
        assert final_status["pending"] + final_status["failed"] == 0
    finally:
        reopened.close()


LEGACY_MEMORY_EMBEDDING_DDL = """
CREATE TABLE memory_embedding (
    memory_id TEXT NOT NULL,
    content_hash TEXT NOT NULL,
    backend TEXT NOT NULL,
    model_revision TEXT NOT NULL,
    dimension INTEGER NOT NULL,
    vector_blob BLOB NOT NULL,
    status TEXT NOT NULL CHECK(status IN ('pending', 'ready', 'failed')),
    failure_reason TEXT,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    last_used_at TEXT,
    PRIMARY KEY(memory_id, backend, model_revision, content_hash)
)
"""


def create_legacy_embedding_database(path):
    bootstrap = SQLiteMetaStore(path)
    records = []
    try:
        for index in range(2):
            record = make_memory(
                content="legacy content %d" % index,
                memory_type="knowledge",
                source=MemorySource(kind="manual", uri="legacy"),
                summary="legacy summary %d" % index,
            )
            bootstrap.upsert_memory(record)
            records.append(record)
    finally:
        bootstrap.close()

    connection = sqlite3.connect(path)
    try:
        connection.execute("DROP TABLE memory_embedding")
        connection.execute(LEGACY_MEMORY_EMBEDDING_DDL)
        for index, record in enumerate(records):
            content_hash = vector_content_hash(record.summary, record.content)
            connection.execute(
                """
                INSERT INTO memory_embedding (
                    memory_id, content_hash, backend, model_revision, dimension,
                    vector_blob, status, failure_reason, created_at, updated_at
                ) VALUES (?, ?, 'kylin', 'v1', 1, ?, 'ready', ?, 'old', 'old')
                """,
                (
                    record.memory_id,
                    content_hash,
                    encode_vector([float(index + 1)]),
                    None if index == 0 else "",
                ),
            )
        connection.commit()
    finally:
        connection.close()
    return records


def test_real_legacy_embedding_ddl_migrates_columns_tokens_and_data(tmp_path):
    path = tmp_path / "legacy.sqlite3"
    records = create_legacy_embedding_database(path)

    store = SQLiteMetaStore(path)
    try:
        table_info = {
            row["name"]: row
            for row in store.conn.execute("PRAGMA table_info(memory_embedding)")
        }
        columns = list(table_info)
        assert columns.count("attempt_count") == 1
        assert columns.count("next_attempt_at") == 1
        assert columns.count("job_token") == 1
        assert table_info["vector_blob"]["notnull"] == 0
        assert table_info["failure_reason"]["notnull"] == 1
        assert table_info["failure_reason"]["dflt_value"] == "''"
        rows = store.conn.execute(
            """
            SELECT memory_id, status, vector_blob, failure_reason, job_token
            FROM memory_embedding
            ORDER BY memory_id
            """
        ).fetchall()
        assert {row["memory_id"] for row in rows} == {
            record.memory_id for record in records
        }
        assert all(row["status"] == "ready" and row["vector_blob"] for row in rows)
        assert all(row["failure_reason"] == "" for row in rows)
        tokens = [row["job_token"] for row in rows]
        assert all(tokens)
        assert len(tokens) == len(set(tokens)) == 2

        record = records[0]
        content_hash = vector_content_hash(record.summary, record.content)
        assert store.enqueue_memory_embedding(
            record.memory_id, content_hash, "kylin", "v2"
        )
        pending = store.conn.execute(
            """
            SELECT status, dimension, vector_blob, failure_reason,
                   attempt_count, job_token
            FROM memory_embedding
            WHERE memory_id = ? AND backend = 'kylin' AND model_revision = 'v2'
            """,
            (record.memory_id,),
        ).fetchone()
        assert pending["status"] == "pending"
        assert pending["dimension"] == 0
        assert pending["vector_blob"] is None
        assert pending["failure_reason"] == ""
        assert store.mark_embedding_failed(
            record.memory_id,
            content_hash,
            "kylin",
            "v2",
            "legacy retry",
            expected_attempt_count=pending["attempt_count"],
            expected_job_token=pending["job_token"],
        )
        failed = store.conn.execute(
            """
            SELECT status, attempt_count, job_token
            FROM memory_embedding
            WHERE memory_id = ? AND backend = 'kylin' AND model_revision = 'v2'
            """,
            (record.memory_id,),
        ).fetchone()
        assert failed["status"] == "failed"
        assert store.complete_memory_embedding(
            record.memory_id,
            content_hash,
            "kylin",
            "v2",
            [3.0, 4.0],
            expected_attempt_count=failed["attempt_count"],
            expected_job_token=failed["job_token"],
        )
        ready = store.get_memory_embedding(
            record.memory_id, content_hash, "kylin", "v2"
        )
        assert ready is not None
        assert ready.vector == [3.0, 4.0]
    finally:
        store.close()


def test_two_connections_concurrently_migrate_real_legacy_embedding_ddl(tmp_path):
    path = tmp_path / "legacy-concurrent.sqlite3"
    records = create_legacy_embedding_database(path)
    barrier = threading.Barrier(2)

    def initialize_store():
        barrier.wait()
        store = SQLiteMetaStore(path)
        try:
            return store.conn.execute("SELECT COUNT(*) FROM memory_embedding").fetchone()[0]
        finally:
            store.close()

    with ThreadPoolExecutor(max_workers=2) as executor:
        counts = list(executor.map(lambda _: initialize_store(), range(2)))

    assert counts == [len(records), len(records)]
    connection = sqlite3.connect(path)
    try:
        columns = [
            row[1] for row in connection.execute("PRAGMA table_info(memory_embedding)")
        ]
        assert columns.count("attempt_count") == 1
        assert columns.count("next_attempt_at") == 1
        assert columns.count("job_token") == 1
    finally:
        connection.close()
