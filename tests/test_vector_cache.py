import math
import sqlite3
import struct

import pytest

from memguard.retrieval.vector_cache import decode_vector, encode_vector, vector_content_hash
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
