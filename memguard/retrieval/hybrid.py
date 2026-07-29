from __future__ import annotations

from functools import lru_cache
from collections import OrderedDict
from importlib.resources import files
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Tuple
import hashlib
import json
import math
import os
import re
import time

from memguard.retrieval.kylin_sdk import KylinEmbeddingBackend


TRANSLATION_HINTS = {
    "内存": ["memory", "MemoryMax", "ram"],
    "限制": ["limit", "quota"],
    "服务": ["service", "systemd"],
    "审计": ["audit", "journal", "journald"],
    "审计链": ["audit chain", "audit_chain"],
    "忘记": ["forget", "delete"],
    "精准遗忘": ["precise forgetting", "managed erase"],
    "明文探针": ["plaintext probe"],
    "受管存储": ["managed storage"],
    "密钥": ["key", "token", "secret"],
    "偏好": ["preference"],
    "工具偏好": ["tool preference"],
    "高风险命令": ["high risk command", "dangerous command"],
    "二次确认": ["confirmation", "double confirmation"],
    "代码搜索": ["code search", "rg", "ripgrep"],
    "回答风格": ["response style"],
    "先给命令": ["command first"],
}

@lru_cache(maxsize=1)
def _technical_aliases() -> Dict[str, List[str]]:
    try:
        resource = files("memguard.resources").joinpath("technical_aliases.json")
        data = json.loads(resource.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        data = {}
    aliases: Dict[str, List[str]] = {}
    for key, values in data.items():
        if not isinstance(values, list):
            continue
        clean = [str(value).strip() for value in values if str(value).strip()]
        if clean:
            aliases[str(key).strip()] = clean
    return aliases


def expand_query_aliases(query: str) -> Dict[str, Any]:
    """Expand query with a small offline lexicon and explain every expansion."""

    text = str(query or "")
    lowered = text.lower()
    additions: List[str] = []
    trace: List[Dict[str, str]] = []
    aliases = _technical_aliases()
    for canonical, values in aliases.items():
        terms = [canonical] + values
        matched = [term for term in terms if term and _term_in_text(term, lowered)]
        if not matched:
            continue
        for term in terms:
            if term and term not in additions and not _term_in_text(term, lowered):
                additions.append(term)
        trace.append({"canonical": canonical, "matched": matched[0], "added": ", ".join(additions[-max(1, len(terms)) :])})
    expanded = " ".join([text] + additions)
    return {"query": text, "expanded_query": expanded, "aliases": additions, "expansion_trace": trace}


def _term_in_text(term: str, lowered_text: str) -> bool:
    lowered_term = str(term or "").lower()
    if not lowered_term:
        return False
    if re.search(r"[a-z0-9_]", lowered_term):
        pattern = r"(?<![a-z0-9_])%s(?![a-z0-9_])" % re.escape(lowered_term)
        return re.search(pattern, lowered_text) is not None
    return lowered_term in lowered_text


def tokenize(text: str) -> List[str]:
    return list(_tokenize_cached(text))


@lru_cache(maxsize=8192)
def _tokenize_cached(text: str) -> Tuple[str, ...]:
    expanded_info = expand_query_aliases(text)
    return _tokenize_literal_cached(str(expanded_info["expanded_query"]))


def tokenize_literal(text: str) -> List[str]:
    return list(_tokenize_literal_cached(str(text or "")))


@lru_cache(maxsize=8192)
def _tokenize_literal_cached(text: str) -> Tuple[str, ...]:
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

    def vector_literal(self, text: str) -> List[float]:
        return hashed_vector(tokenize_literal(text), dim=self.dim)

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

    def __init__(
        self,
        timeout_seconds: float = 8.0,
        max_failures: int = 1,
        cooldown_seconds: float = 60.0,
        max_cache_entries: int = 1024,
        clock=time.monotonic,
    ):
        self.timeout_seconds = max(0.01, timeout_seconds)
        self.max_failures = max(1, max_failures)
        self.cooldown_seconds = max(1.0, cooldown_seconds)
        self.max_cache_entries = max(1, max_cache_entries)
        self.clock = clock
        self.failures = 0
        self.disabled_reason = ""
        self.disabled_until = 0.0
        self.last_error_at: float | None = None
        self.last_success_at: float | None = None
        self.cache: OrderedDict[str, List[float]] = OrderedDict()
        self.sdk = KylinEmbeddingBackend()

    def vector(self, text: str, timeout_seconds: Optional[float] = None) -> Optional[List[float]]:
        now = float(self.clock())
        if self.disabled_reason and now < self.disabled_until:
            return None
        if self.disabled_reason:
            self.disabled_reason = ""
        key = hashlib.sha256(("kylin-sdk-v1\0" + text).encode("utf-8")).hexdigest()
        if key in self.cache:
            vector = self.cache.pop(key)
            self.cache[key] = vector
            return vector
        effective_timeout = self.timeout_seconds
        if timeout_seconds is not None:
            effective_timeout = max(0.01, min(self.timeout_seconds, float(timeout_seconds)))
        result = self.sdk.embed_in_subprocess(text, effective_timeout, include_vector=True)
        if result.get("ok") and isinstance(result.get("vector"), list) and result["vector"]:
            vector = normalize_vector(result["vector"])
            self.cache[key] = vector
            while len(self.cache) > self.max_cache_entries:
                self.cache.popitem(last=False)
            self.failures = 0
            self.disabled_reason = ""
            self.disabled_until = 0.0
            self.last_success_at = now
            return vector
        self.failures += 1
        self.disabled_reason = str(result.get("error") or result.get("error_message") or "kylin embedding unavailable")
        self.last_error_at = now
        if self.failures < self.max_failures:
            self.disabled_reason = ""
        else:
            self.disabled_until = now + self.cooldown_seconds
        return None

    def status(self) -> Dict[str, object]:
        return {
            "name": self.name,
            "available": not bool(self.disabled_reason),
            "timeout_seconds": self.timeout_seconds,
            "failures": self.failures,
            "disabled_reason": self.disabled_reason,
            "degraded": bool(self.disabled_reason),
            "disabled_until": self.disabled_until or None,
            "last_error_at": self.last_error_at,
            "last_success_at": self.last_success_at,
            "cache_entries": len(self.cache),
            "cache_capacity": self.max_cache_entries,
            "cooldown_seconds": self.cooldown_seconds,
            "fallback": "hash",
        }


def _configured_timeout() -> float:
    raw = os.environ.get("MEMGUARD_KYLIN_EMBEDDING_TIMEOUT", "8")
    try:
        return float(raw)
    except ValueError:
        return 8.0


def _configured_rerank_budget_ms() -> float:
    raw = os.environ.get("MEMGUARD_KYLIN_RERANK_BUDGET_MS", "160")
    try:
        return max(0.0, float(raw))
    except ValueError:
        return 160.0


def _is_openkylin() -> bool:
    try:
        text = Path("/etc/os-release").read_text(encoding="utf-8", errors="replace").lower()
    except OSError:
        return False
    return "openkylin" in text


def _platform_default_backend() -> str:
    return "auto" if _is_openkylin() else "hash"


class HybridRetriever:
    def __init__(self, embedding_backend: Optional[str] = None):
        backend = (embedding_backend or os.environ.get("MEMGUARD_EMBEDDING_BACKEND") or _platform_default_backend()).strip().lower()
        if backend not in {"hash", "auto", "kylin", "kylin_sdk"}:
            backend = "hash"
        self.requested_backend = backend
        self.hash_provider = HashEmbeddingProvider()
        self.kylin_provider = KylinEmbeddingProvider(timeout_seconds=_configured_timeout()) if backend in {"auto", "kylin", "kylin_sdk"} else None
        self.kylin_rerank_budget_ms = _configured_rerank_budget_ms()
        self.last_backend_used = "hash"
        self.persisted_vector_hits = 0
        self.persisted_vector_misses = 0

    def status(self) -> Dict[str, object]:
        return {
            "requested_backend": self.requested_backend,
            "last_backend_used": self.last_backend_used,
            "hash": self.hash_provider.status(),
            "kylin": self.kylin_provider.status() if self.kylin_provider else {"enabled": False},
            "kylin_rerank_budget_ms": self.kylin_rerank_budget_ms,
            "persisted_vector_hits": self.persisted_vector_hits,
            "persisted_vector_misses": self.persisted_vector_misses,
        }

    def _score_with_vectors(self, query: str, record: Dict[str, str], query_vector: List[float], text_vector: List[float]) -> float:
        text = "%s %s %s" % (record.get("type", ""), record.get("summary", ""), record.get("content", ""))
        query_tokens = tokenize_literal(query)
        text_tokens = tokenize_literal(text)
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

    def lightweight_score_breakdown(self, query: str, record: Dict[str, Any]) -> Dict[str, Any]:
        expanded = expand_query_aliases(query)
        query_text = str(expanded["expanded_query"])
        query_lower = query_text.lower()
        summary = str(record.get("summary") or "")
        content = str(record.get("content") or "")
        record_type = str(record.get("type") or "")
        subtype = str(record.get("subtype") or "")
        preference_topic = str(record.get("preference_topic") or "")
        source_kind = str(record.get("source_kind") or "")
        combined = " ".join([record_type, subtype, preference_topic, summary, content])
        combined_lower = combined.lower()
        original_query_tokens = set(tokenize_literal(str(query or "")))
        query_tokens = set(tokenize_literal(query_text))
        summary_tokens = set(tokenize_literal(summary))
        content_tokens = set(tokenize_literal(content))
        lexical_hits = query_tokens & (summary_tokens | content_tokens)
        generic_preference_tokens = {
            "偏好",
            "习惯",
            "喜欢",
            "我的",
            "用户",
            "设置",
            "建议",
            "按照",
            "prefer",
            "preference",
        }
        preference_grounded_hits = lexical_hits - generic_preference_tokens
        record_tokens = summary_tokens | content_tokens
        lexical_precision = len(lexical_hits) / float(len(record_tokens) or 1)
        original_lexical_hits = original_query_tokens & record_tokens
        original_lexical_precision = len(original_lexical_hits) / float(
            len(record_tokens) or 1
        )
        distinctive_latin_terms = {
            term
            for term in re.findall(r"[A-Za-z][A-Za-z0-9_\-]{2,}", str(query or "").lower())
            if term
            not in {
                "after",
                "agent",
                "and",
                "are",
                "bash",
                "before",
                "can",
                "command",
                "directly",
                "for",
                "from",
                "history",
                "how",
                "is",
                "related",
                "shell",
                "should",
                "sudo",
                "the",
                "user",
                "what",
                "when",
                "where",
                "which",
                "with",
            }
        }
        distinctive_term_hits = {
            term for term in distinctive_latin_terms if term in combined_lower
        }
        distinctive_term_coverage = len(distinctive_term_hits) / float(
            len(distinctive_latin_terms) or 1
        )
        summary_score = min(0.28, 0.04 * len(query_tokens & summary_tokens))
        content_score = min(0.24, 0.035 * len(query_tokens & content_tokens))
        exact_phrase_score = 0.0
        original = str(query or "").strip()
        if original and original in summary:
            exact_phrase_score = 0.42
        elif original and original in content:
            exact_phrase_score = 0.32
        entity_terms = _technical_entities(query_text)
        entity_hits = [term for term in entity_terms if term.lower() in combined_lower]
        entity_score = min(0.30, 0.10 * len(entity_hits))
        topic_score = _topic_score(query_lower, combined_lower, record_type, subtype, preference_topic)
        subtype_score = _subtype_score(query_lower, record_type, subtype, preference_topic)
        hard_negative_penalty = _hard_negative_penalty(query_lower, summary, content, source_kind, record)
        scope_penalty = _scope_penalty(query_lower, combined_lower)
        routing_score, routing_reasons = _query_routing_score(
            str(query or ""), combined
        )
        total = (
            summary_score
            + content_score
            + entity_score
            + topic_score
            + subtype_score
            + exact_phrase_score
            + routing_score
            + min(0.15, lexical_precision * 0.50)
            + min(0.20, original_lexical_precision * 0.80)
            - hard_negative_penalty
            - scope_penalty
        )
        return {
            "summary_score": round(summary_score, 4),
            "content_score": round(content_score, 4),
            "query_token_count": len(query_tokens),
            "lexical_hit_count": len(lexical_hits),
            "preference_grounded_hit_count": len(preference_grounded_hits),
            "lexical_precision": round(lexical_precision, 4),
            "original_lexical_hit_count": len(original_lexical_hits),
            "original_lexical_precision": round(original_lexical_precision, 4),
            "distinctive_latin_token_count": len(distinctive_latin_terms),
            "distinctive_term_hit_count": len(distinctive_term_hits),
            "distinctive_term_coverage": round(distinctive_term_coverage, 4),
            "entity_score": round(entity_score, 4),
            "topic_score": round(topic_score, 4),
            "subtype_score": round(subtype_score, 4),
            "exact_phrase_score": round(exact_phrase_score, 4),
            "hard_negative_penalty": round(hard_negative_penalty, 4),
            "scope_penalty": round(scope_penalty, 4),
            "routing_score": round(routing_score, 4),
            "routing_reasons": routing_reasons,
            "preference_intent": _contains_any(
                query_lower,
                ["偏好", "习惯", "优先", "喜欢", "prefer", "preference"],
            ),
            "record_is_preference": record_type == "preference",
            "alias_expansion": expanded,
            "total": round(total, 4),
        }

    def score(self, query: str, record: Dict[str, str]) -> float:
        text = "%s %s %s" % (record.get("type", ""), record.get("summary", ""), record.get("content", ""))
        return self._score_with_vectors(query, record, self.hash_provider.vector(query), self.hash_provider.vector(text))

    def rank(
        self,
        query: str,
        records: Iterable[Dict[str, str]],
        limit: int = 10,
        persisted_vectors: Optional[Dict[str, List[float]]] = None,
    ) -> List[Tuple[float, Dict[str, str]]]:
        records = list(records)
        expanded_info = expand_query_aliases(query)
        expanded_query = str(expanded_info["expanded_query"])
        hash_query_vector = self.hash_provider.vector_literal(expanded_query)
        scored = []
        semantic_pool = []
        for record in records:
            text = "%s %s %s" % (record.get("type", ""), record.get("summary", ""), record.get("content", ""))
            score = self._score_with_vectors(
                expanded_query,
                record,
                hash_query_vector,
                self.hash_provider.vector_literal(text),
            )
            breakdown = self.lightweight_score_breakdown(query, record)
            if self.requested_backend == "hash" and not _has_grounded_relevance(breakdown):
                continue
            score += float(breakdown["total"])
            item = dict(record)
            item["_hash_score"] = round(score, 6)
            item["_score_breakdown"] = breakdown
            item["_expansion_trace"] = expanded_info["expansion_trace"]
            if score > 0:
                semantic_pool.append((score, item))
            if score > 0 and self._hash_evidence_is_sufficient(breakdown):
                scored.append((score, item))
        scored = [item for item in scored if item[0] > 0]
        scored.sort(key=lambda item: item[0], reverse=True)
        semantic_pool.sort(key=lambda item: item[0], reverse=True)
        self.last_backend_used = "hash"

        if self.kylin_provider is None or not semantic_pool:
            return self._tag_backend(scored[:limit], "hash")

        sdk_started = time.perf_counter()
        query_timeout = self._remaining_kylin_timeout(sdk_started)
        if query_timeout == 0.0:
            return self._tag_backend(scored[:limit], "hash")
        kylin_query_vector = self.kylin_provider.vector(expanded_query, timeout_seconds=query_timeout)
        if kylin_query_vector is None:
            return self._tag_backend(scored[:limit], "hash")
        if self._kylin_budget_exceeded(sdk_started):
            return self._tag_backend(scored[:limit], "hash")

        vectors = persisted_vectors or {}
        rerank_pool = semantic_pool[: max(30, limit * 6)]
        reranked = []
        hit_count = 0
        for base_score, record in rerank_pool:
            item = dict(record)
            memory_id = str(item.get("memory_id") or "")
            candidate_vector = vectors.get(memory_id)
            if candidate_vector and len(candidate_vector) == len(kylin_query_vector):
                kylin_score = self._score_with_vectors(
                    expanded_query, item, kylin_query_vector, candidate_vector
                )
                item["_kylin_score"] = round(kylin_score, 6)
                item["_vector_backend"] = "kylin_sdk_persisted"
                reranked.append((base_score + kylin_score, item))
                self.persisted_vector_hits += 1
                hit_count += 1
            else:
                item["_kylin_score"] = 0.0
                item["_vector_backend"] = "hash"
                reranked.append((base_score, item))
                self.persisted_vector_misses += 1
        reranked.sort(
            key=lambda pair: (-pair[0], str(pair[1].get("memory_id") or ""))
        )
        if hit_count:
            self.last_backend_used = "kylin_sdk_persisted"
            return reranked[:limit]
        self.last_backend_used = "hash"
        # A Kylin query vector without a matching persisted candidate vector
        # provides no semantic evidence for that candidate. Returning the
        # broader semantic pool here would bypass the grounded hash threshold
        # and surface unrelated memories only on openKylin. Fall back to the
        # already-qualified hash results until vector persistence catches up.
        return self._tag_backend(scored[:limit], "hash")

    @staticmethod
    def _hash_evidence_is_sufficient(breakdown: Dict[str, float | dict]) -> bool:
        if float(breakdown.get("total", 0.0)) <= 0:
            return False
        if any(
            float(breakdown.get(key, 0.0)) > 0
            for key in ("exact_phrase_score", "topic_score")
        ):
            return True
        if (
            float(breakdown.get("subtype_score", 0.0)) > 0
            and int(breakdown.get("preference_grounded_hit_count", 0)) >= 1
        ):
            return True
        lexical_hits = int(breakdown.get("lexical_hit_count", 0))
        query_tokens = int(breakdown.get("query_token_count", 0))
        if bool(breakdown.get("preference_intent")) and bool(
            breakdown.get("record_is_preference")
        ):
            return int(breakdown.get("preference_grounded_hit_count", 0)) >= 1
        if lexical_hits >= 2:
            return True
        distinctive_latin_tokens = int(breakdown.get("distinctive_latin_token_count", 0))
        if lexical_hits == 1 and (query_tokens <= 2 or distinctive_latin_tokens == 1):
            return True
        return float(breakdown.get("entity_score", 0.0)) >= 0.2

    def _kylin_budget_exceeded(self, started: float) -> bool:
        if self.requested_backend in {"kylin", "kylin_sdk"} or self.kylin_rerank_budget_ms <= 0:
            return False
        elapsed_ms = (time.perf_counter() - started) * 1000.0
        if elapsed_ms <= self.kylin_rerank_budget_ms:
            return False
        if self.kylin_provider is not None:
            self.kylin_provider.disabled_reason = "Kylin rerank exceeded latency budget; using stable fallback"
            self.kylin_provider.disabled_until = float(self.kylin_provider.clock()) + self.kylin_provider.cooldown_seconds
        return True

    def _remaining_kylin_timeout(self, started: float) -> Optional[float]:
        if self.requested_backend in {"kylin", "kylin_sdk"} or self.kylin_rerank_budget_ms <= 0:
            return None
        remaining = (self.kylin_rerank_budget_ms / 1000.0) - (time.perf_counter() - started)
        return max(0.0, remaining)

    @staticmethod
    def _tag_backend(scored: List[Tuple[float, Dict[str, str]]], backend: str) -> List[Tuple[float, Dict[str, str]]]:
        tagged = []
        for score, record in scored:
            item = dict(record)
            item["_vector_backend"] = backend
            tagged.append((score, item))
        return tagged


def _technical_entities(text: str) -> List[str]:
    return re.findall(r"[A-Za-z][A-Za-z0-9_\-.]{1,}|[\u4e00-\u9fff]{2,}", text)


def _query_routing_score(query: str, combined: str) -> Tuple[float, List[str]]:
    query_lower = query.lower()
    combined_lower = combined.lower()
    score = 0.0
    reasons: List[str] = []
    cjk_chars = len(re.findall(r"[\u4e00-\u9fff]", query))
    latin_words = re.findall(r"[A-Za-z][A-Za-z0-9_-]*", query)
    if cjk_chars == 0 and len(latin_words) >= 3 and _shares_latin_terms(latin_words, combined):
        score += 0.35
        reasons.append("cross_language_term_overlap")

    temporal_markers = ("本周", "上周", "昨天", "今天", "最新", "更新后")
    matched_temporal = [marker for marker in temporal_markers if marker in query]
    if matched_temporal and any(marker in combined for marker in matched_temporal):
        score += 0.40
        reasons.append("temporal_scope_match")

    relational_query = any(
        marker in query_lower
        for marker in ("关系", "关联", "依赖", "属于", "related", "relationship")
    )
    if relational_query and any(
        marker in combined_lower for marker in ("关联图记录", "关系记录", "relationship")
    ):
        score += 0.40
        reasons.append("relational_source")

    procedural_query = any(
        marker in query_lower
        for marker in (
            "步骤",
            "操作要求",
            "怎么修",
            "如何修",
            "失败后",
            "procedure",
            "first step",
            "how to",
        )
    )
    procedural_markers = {
        marker
        for marker in ("任务", "步骤", "流程", "操作要求", "失败后")
        if marker in combined_lower
    }
    if procedural_query and procedural_markers:
        score += min(0.50, 0.25 * len(procedural_markers))
        reasons.append("procedural_source")
    return score, reasons


def _has_grounded_relevance(breakdown: Dict[str, Any]) -> bool:
    return any(
        float(breakdown.get(key, 0.0)) > 0.0
        for key in (
            "summary_score",
            "content_score",
            "entity_score",
            "topic_score",
            "subtype_score",
            "exact_phrase_score",
        )
    )


def _topic_score(query_lower: str, combined_lower: str, record_type: str, subtype: str, preference_topic: str) -> float:
    score = 0.0
    if _contains_any(query_lower, ["高风险命令", "危险操作", "二次确认", "high risk", "confirmation"]):
        if subtype == "security_policy" or preference_topic == "high_risk_confirmation":
            score += 0.72
        elif record_type == "knowledge":
            score -= 0.28
    if _contains_any(query_lower, ["代码搜索", "查代码", " rg", "ripgrep", "code search"]):
        if subtype == "tool_preference" or preference_topic == "code_search_tool":
            score += 0.60
    if _contains_any(query_lower, ["回答风格", "先给命令", "先给结论", "response style", "command first"]):
        if subtype == "output_style" or preference_topic in {"response_order", "response_verbosity", "response_language"}:
            score += 0.50
    if _contains_any(query_lower, ["精准遗忘", "precise forgetting"]):
        if _contains_any(combined_lower, ["精准遗忘", "forget", "managed"]):
            score += 0.35
    return score


def _subtype_score(query_lower: str, record_type: str, subtype: str, preference_topic: str) -> float:
    score = 0.0
    if _contains_any(query_lower, ["偏好", "优先", "prefer", "preference"]) and record_type == "preference":
        score += 0.26
    if _contains_any(query_lower, ["安全策略", "确认", "confirmation"]) and subtype == "security_policy":
        score += 0.30
    if preference_topic and preference_topic in query_lower:
        score += 0.20
    return score


def _hard_negative_penalty(query_lower: str, summary: str, content: str, source_kind: str, record: Dict[str, Any]) -> float:
    """Penalise lexically-similar but semantically wrong hits.

    Only content-derived signals are allowed here. Never inspect benchmark
    bookkeeping such as dataset labels or generated id prefixes: doing so would
    let the scorer recognise its own test set and make published metrics
    meaningless.
    """

    text = (summary + " " + content).strip()
    penalty = 0.0
    if _contains_any(query_lower, ["token", "令牌", "凭据"]) and "token.service" in text:
        penalty += 0.50
    if _contains_any(query_lower, ["private key", "私钥"]) and "MAP_PRIVATE" in text:
        penalty += 0.50
    return penalty


def _scope_penalty(query_lower: str, combined_lower: str) -> float:
    if _contains_any(query_lower, ["当前 agent", "当前agent"]) and _contains_any(combined_lower, ["其他 agent", "其他agent"]):
        return 0.45
    if "上周" in query_lower and "今天" in combined_lower:
        return 0.30
    return 0.0


def _contains_any(text: str, needles: Iterable[str]) -> bool:
    return any(needle.lower() in text for needle in needles)


def _shares_latin_terms(latin_words: List[str], combined: str) -> bool:
    """Latin query against a record that carries the same technical terms.

    Used for cross-language routing: a English/pinyin query still matches a
    Chinese record when both mention the same identifiers, without relying on
    any dataset-specific marker text.
    """

    combined_lower = combined.lower()
    meaningful = [word.lower() for word in latin_words if len(word) >= 3]
    if not meaningful:
        return False
    hits = sum(1 for word in meaningful if word in combined_lower)
    return hits >= 2 or (hits == 1 and len(meaningful) <= 2)
