from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
import math
import re
from typing import Dict, Iterable, List, Mapping, Optional, Set, Tuple


class QueryIntent(str, Enum):
    EXACT_LOOKUP = "exact_lookup"
    PREFERENCE = "preference"
    PROCEDURAL = "procedural"
    TEMPORAL = "temporal"
    RELATIONAL = "relational"
    GENERAL = "general"


@dataclass(frozen=True)
class FusedResult:
    memory_id: str
    score: float
    sources: Tuple[str, ...]
    protected: bool = False


def classify_query_intent(query: str) -> QueryIntent:
    text = str(query or "").strip().lower()
    if re.search(
        r"\bmem(?:ory)?[-_ ]?id\b|\bmem[-_][a-z0-9-]{8,}\b|\bmem-[a-z0-9-]+\b",
        text,
    ):
        return QueryIntent.EXACT_LOOKUP
    if any(term in text for term in ("偏好", "习惯", "明确说", "prefer", "preference")):
        return QueryIntent.PREFERENCE
    if any(
        term in text
        for term in ("步骤", "怎么修", "如何修", "失败后", "流程", "procedure", "fix")
    ):
        return QueryIntent.PROCEDURAL
    if any(
        term in text
        for term in ("上周", "昨天", "之前", "最新", "更新后", "when", "latest")
    ):
        return QueryIntent.TEMPORAL
    if any(
        term in text
        for term in ("关系", "关联", "依赖", "属于", "related", "relationship")
    ):
        return QueryIntent.RELATIONAL
    return QueryIntent.GENERAL


def rrf_fuse(
    rankings: Mapping[str, Iterable[str]],
    weights: Optional[Mapping[str, float]] = None,
    k: int = 60,
    exact_hits: Optional[Set[str]] = None,
) -> List[FusedResult]:
    if k <= 0:
        raise ValueError("RRF k must be positive")
    totals: Dict[str, float] = {}
    sources: Dict[str, List[str]] = {}
    for source, ranking in rankings.items():
        weight = float((weights or {}).get(source, 1.0))
        if not math.isfinite(weight) or weight < 0:
            raise ValueError("RRF weights must be finite and non-negative")
        if weight == 0:
            continue
        seen: Set[str] = set()
        for rank, raw_memory_id in enumerate(ranking, start=1):
            memory_id = str(raw_memory_id or "").strip()
            if not memory_id or memory_id in seen:
                continue
            seen.add(memory_id)
            totals[memory_id] = totals.get(memory_id, 0.0) + weight / float(k + rank)
            sources.setdefault(memory_id, []).append(source)
    protected = exact_hits or set()
    return sorted(
        (
            FusedResult(mid, score, tuple(sources[mid]), mid in protected)
            for mid, score in totals.items()
        ),
        key=lambda item: (not item.protected, -item.score, item.memory_id),
    )
