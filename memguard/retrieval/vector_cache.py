from dataclasses import dataclass
import hashlib
import math
import struct
from typing import List, Union


@dataclass(frozen=True)
class PersistedVector:
    memory_id: str
    content_hash: str
    backend: str
    model_revision: str
    dimension: int
    vector: List[float]
    status: str


def vector_content_hash(summary: str, content: str) -> str:
    payload = (summary + "\x00" + content).encode("utf-8")
    return hashlib.sha256(payload).hexdigest()


def encode_vector(vector: List[float]) -> bytes:
    if not vector or any(not math.isfinite(value) for value in vector):
        raise ValueError("vector must be non-empty and contain only finite values")
    try:
        return struct.pack("<%df" % len(vector), *vector)
    except (OverflowError, struct.error) as exc:
        raise ValueError("vector values must fit in float32") from exc


def decode_vector(blob: Union[bytes, bytearray, memoryview], dimension: int) -> List[float]:
    if dimension <= 0:
        raise ValueError("dimension must be positive")
    raw = bytes(blob)
    expected_length = dimension * 4
    if len(raw) != expected_length:
        raise ValueError("vector blob length does not match dimension")
    return list(struct.unpack("<%df" % dimension, raw))
