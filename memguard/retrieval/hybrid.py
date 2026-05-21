from typing import Dict, Iterable, List, Tuple
import hashlib
import math
import re


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
    return tokens


def hashed_vector(tokens: Iterable[str], dim: int = 128) -> List[float]:
    vector = [0.0] * dim
    for token in tokens:
        digest = hashlib.sha256(token.encode("utf-8")).digest()
        idx = int.from_bytes(digest[:4], "big") % dim
        vector[idx] += 1.0
    norm = math.sqrt(sum(value * value for value in vector)) or 1.0
    return [value / norm for value in vector]


def cosine(left: List[float], right: List[float]) -> float:
    return sum(a * b for a, b in zip(left, right))


class HybridRetriever:
    def score(self, query: str, record: Dict[str, str]) -> float:
        text = "%s %s %s" % (record.get("type", ""), record.get("summary", ""), record.get("content", ""))
        query_tokens = tokenize(query)
        text_tokens = tokenize(text)
        if not text_tokens:
            return 0.0
        query_set = set(query_tokens)
        text_set = set(text_tokens)
        overlap = len(query_set & text_set) / float(len(query_set) or 1)
        vector_score = cosine(hashed_vector(query_tokens), hashed_vector(text_tokens))
        recency_score = 0.05
        sensitivity_penalty = 0.2 if record.get("sensitivity_level") == "high" else 0.0
        return 0.55 * vector_score + 0.40 * overlap + recency_score - sensitivity_penalty

    def rank(self, query: str, records: Iterable[Dict[str, str]], limit: int = 10) -> List[Tuple[float, Dict[str, str]]]:
        scored = [(self.score(query, record), record) for record in records]
        scored = [item for item in scored if item[0] > 0]
        scored.sort(key=lambda item: item[0], reverse=True)
        return scored[:limit]
