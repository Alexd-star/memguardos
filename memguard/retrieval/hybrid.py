from functools import lru_cache
from typing import Dict, Iterable, List, Optional, Tuple
import hashlib
import math
import os
import re

from memguard.retrieval.kylin_sdk import KylinEmbeddingBackend


TRANSLATION_HINTS = {
    "内存": ["memory", "MemoryMax", "ram"],
    "限制": ["limit", "quota"],
    "服务": ["service", "systemd"],
    "审计": ["audit", "journal", "journald"],
    "忘记": ["forget", "delete"],
    "密钥": ["key", "token", "secret"],
    "偏好": ["preference"],
}


def tokenize(text: str) -> List[str]:
    return list(_tokenize_cached(text))


@lru_cache(maxsize=8192)
def _tokenize_cached(text: str) -> Tuple[str, ...]:
    latin = re.findall(r"[A-Za-z0-9_\-]+", text.lower())
    cjk_sequences = re.findall(r"[\u4e00-\u9fff]+", text)
    cjk = []
    for seq in cjk_sequences:
        cjk.append(seq)
        if len(seq) > 1:
            cjk.extend(seq[idx : idx + 2] for idx in range(len(seq) - 1))
    tokens = latin + cjk
    for zh, hints in TRANSLATION_HINTS.items():
        if zh in text:
            tokens.extend(item.lower() for item in hints)
    return tuple(tokens)


def hashed_vector(tokens: Iterable[str], dim: int = 128) -> List[float]:
    return list(_hashed_vector_cached(tuple(tokens), dim))


@lru_cache(maxsize=16384)
def _hashed_vector_cached(tokens: Tuple[str, ...], dim: int = 128) -> Tuple[float, ...]:
    vector = [0.0] * dim
    for token in tokens:
        digest = hashlib.sha256(token.encode("utf-8")).digest()
        idx = int.from_bytes(digest[:4], "big") % dim
        vector[idx] += 1.0
    norm = math.sqrt(sum(value * value for value in vector)) or 1.0
    return tuple(value / norm for value in vector)


def normalize_vector(vector: Iterable[float]) -> List[float]:
    values = [float(value) for value in vector]
    norm = math.sqrt(sum(value * value for value in values)) or 1.0
    return [value / norm for value in values]


def cosine(left: List[float], right: List[float]) -> float:
    return sum(a * b for a, b in zip(left, right))


class HashEmbeddingProvider:
    name = "hash"

    def __init__(self, dim: int = 128):
        self.dim = dim

    def vector(self, text: str) -> List[float]:
        return hashed_vector(tokenize(text), dim=self.dim)

    def status(self) -> Dict[str, object]:
        return {
            "name": self.name,
            "available": True,
            "dim": self.dim,
            "fallback": False,
            "token_cache": _tokenize_cached.cache_info()._asdict(),
            "vector_cache": _hashed_vector_cached.cache_info()._asdict(),
        }


class KylinEmbeddingProvider:
    name = "kylin_sdk"

    def __init__(self, timeout_seconds: float = 8.0, max_failures: int = 1):
        self.timeout_seconds = max(1.0, timeout_seconds)
        self.max_failures = max(1, max_failures)
        self.failures = 0
        self.disabled_reason = ""
        self.cache: Dict[str, List[float]] = {}
        self.sdk = KylinEmbeddingBackend()

    def vector(self, text: str) -> Optional[List[float]]:
        if self.disabled_reason:
            return None
        key = hashlib.sha256(text.encode("utf-8")).hexdigest()
        if key in self.cache:
            return self.cache[key]
        result = self.sdk.embed_in_subprocess(text, self.timeout_seconds, include_vector=True)
        if result.get("ok") and isinstance(result.get("vector"), list) and result["vector"]:
            vector = normalize_vector(result["vector"])
            self.cache[key] = vector
            return vector
        self.failures += 1
        self.disabled_reason = str(result.get("error") or result.get("error_message") or "kylin embedding unavailable")
        if self.failures < self.max_failures:
            self.disabled_reason = ""
        return None

    def status(self) -> Dict[str, object]:
        return {
            "name": self.name,
            "available": not bool(self.disabled_reason),
            "timeout_seconds": self.timeout_seconds,
            "failures": self.failures,
            "disabled_reason": self.disabled_reason,
            "cache_entries": len(self.cache),
            "fallback": "hash",
        }


def _configured_timeout() -> float:
    raw = os.environ.get("MEMGUARD_KYLIN_EMBEDDING_TIMEOUT", "8")
    try:
        return float(raw)
    except ValueError:
        return 8.0


class HybridRetriever:
    def __init__(self, embedding_backend: Optional[str] = None):
        backend = (embedding_backend or os.environ.get("MEMGUARD_EMBEDDING_BACKEND") or "hash").strip().lower()
        if backend not in {"hash", "auto", "kylin", "kylin_sdk"}:
            backend = "hash"
        self.requested_backend = backend
        self.hash_provider = HashEmbeddingProvider()
        self.kylin_provider = KylinEmbeddingProvider(timeout_seconds=_configured_timeout()) if backend in {"auto", "kylin", "kylin_sdk"} else None
        self.last_backend_used = "hash"

    def status(self) -> Dict[str, object]:
        return {
            "requested_backend": self.requested_backend,
            "last_backend_used": self.last_backend_used,
            "hash": self.hash_provider.status(),
            "kylin": self.kylin_provider.status() if self.kylin_provider else {"enabled": False},
        }

    def _score_with_vectors(self, query: str, record: Dict[str, str], query_vector: List[float], text_vector: List[float]) -> float:
        text = "%s %s %s" % (record.get("type", ""), record.get("summary", ""), record.get("content", ""))
        query_tokens = tokenize(query)
        text_tokens = tokenize(text)
        if not text_tokens:
            return 0.0
        query_set = set(query_tokens)
        text_set = set(text_tokens)
        overlap = len(query_set & text_set) / float(len(query_set) or 1)
        vector_score = cosine(query_vector, text_vector)
        relevance_score = 0.55 * vector_score + 0.40 * overlap
        if overlap <= 0.0 and relevance_score < 0.08:
            return 0.0
        recency_score = 0.05
        sensitivity_penalty = 0.2 if record.get("sensitivity_level") == "high" else 0.0
        return relevance_score + recency_score - sensitivity_penalty

    def score(self, query: str, record: Dict[str, str]) -> float:
        text = "%s %s %s" % (record.get("type", ""), record.get("summary", ""), record.get("content", ""))
        return self._score_with_vectors(query, record, self.hash_provider.vector(query), self.hash_provider.vector(text))

    def rank(self, query: str, records: Iterable[Dict[str, str]], limit: int = 10) -> List[Tuple[float, Dict[str, str]]]:
        records = list(records)
        hash_query_vector = self.hash_provider.vector(query)
        scored = []
        for record in records:
            text = "%s %s %s" % (record.get("type", ""), record.get("summary", ""), record.get("content", ""))
            score = self._score_with_vectors(query, record, hash_query_vector, self.hash_provider.vector(text))
            scored.append((score, record))
        scored = [item for item in scored if item[0] > 0]
        scored.sort(key=lambda item: item[0], reverse=True)
        self.last_backend_used = "hash"

        if self.kylin_provider is None or not scored:
            return self._tag_backend(scored[:limit], "hash")

        kylin_query_vector = self.kylin_provider.vector(query)
        if kylin_query_vector is None:
            return self._tag_backend(scored[:limit], "hash")

        rerank_pool = scored[: max(limit * 2, min(10, len(scored)))]
        reranked = []
        for _, record in rerank_pool:
            text = "%s %s %s" % (record.get("type", ""), record.get("summary", ""), record.get("content", ""))
            kylin_text_vector = self.kylin_provider.vector(text)
            if kylin_text_vector is None or len(kylin_text_vector) != len(kylin_query_vector):
                return self._tag_backend(scored[:limit], "hash")
            reranked.append((self._score_with_vectors(query, record, kylin_query_vector, kylin_text_vector), record))
        reranked.sort(key=lambda item: item[0], reverse=True)
        self.last_backend_used = "kylin_sdk"
        return self._tag_backend(reranked[:limit], "kylin_sdk")

    @staticmethod
    def _tag_backend(scored: List[Tuple[float, Dict[str, str]]], backend: str) -> List[Tuple[float, Dict[str, str]]]:
        tagged = []
        for score, record in scored:
            item = dict(record)
            item["_vector_backend"] = backend
            tagged.append((score, item))
        return tagged
