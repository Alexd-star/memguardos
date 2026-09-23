from dataclasses import dataclass, replace
import re
from typing import Optional

from memguard.integrations.events import AgentInteractionEvent
from memguard.integrations.interaction_extractor import (
    PreferenceObservation,
    extract_interaction_preferences,
)

_MAX_SOURCE_CHARS = 4000
_MAX_CANDIDATES = 96


@dataclass(frozen=True)
class EvidenceSpan:
    field: str
    start: int
    end: int
    excerpt: str

    def to_dict(self) -> dict:
        return {
            "source_field": self.field,
            "source_start": self.start,
            "source_end": self.end,
            "source_excerpt": self.excerpt,
        }


def preference_source_span(
    event: AgentInteractionEvent, observation: PreferenceObservation,
) -> Optional[EvidenceSpan]:
    target = (
        observation.topic, observation.value,
        observation.polarity, observation.scope,
    )
    matches = []
    for field in ("content", "user_feedback"):
        value = getattr(event, field)
        if len(value) > _MAX_SOURCE_CHARS:
            return None
        candidates = _candidate_spans(value)
        if len(candidates) > _MAX_CANDIDATES:
            return None
        for start, end in candidates:
            excerpt = value[start:end]
            if len(excerpt) > 500:
                continue
            candidate = replace(
                event,
                content=excerpt if field == "content" else "",
                user_feedback=excerpt if field == "user_feedback" else "",
                provenance_hash="",
            )
            observed = {
                (item.topic, item.value, item.polarity, item.scope)
                for item in extract_interaction_preferences(candidate).observations
            }
            if observed == {target}:
                matches.append(EvidenceSpan(field, start, end, excerpt))
    if not matches:
        return None
    shortest = min(len(item.excerpt) for item in matches)
    selected = {
        (item.field, item.start, item.end): item
        for item in matches if len(item.excerpt) == shortest
    }
    return next(iter(selected.values())) if len(selected) == 1 else None


def exact_source_span(
    event: AgentInteractionEvent, excerpt: str,
) -> Optional[EvidenceSpan]:
    if not excerpt or len(excerpt) > 500:
        return None
    matches = []
    for field in ("content", "user_feedback"):
        value = getattr(event, field)
        if len(value) > _MAX_SOURCE_CHARS:
            return None
        start = value.find(excerpt)
        if start >= 0 and value.find(excerpt, start + 1) < 0:
            matches.append(EvidenceSpan(field, start, start + len(excerpt), excerpt))
    return matches[0] if len(matches) == 1 else None


def _candidate_spans(value: str) -> list[tuple[int, int]]:
    spans = set()
    for strong in re.finditer(r"[^。！？!?；;\n]+", value):
        parts = list(re.finditer(r"[^，,]+", strong.group()))
        windows = [(part.start(), part.end()) for part in parts]
        windows.extend(
            (parts[index].start(), parts[index + 1].end())
            for index in range(len(parts) - 1)
        )
        windows.append((0, len(strong.group())))
        for relative_start, relative_end in windows:
            start = strong.start() + relative_start
            end = strong.start() + relative_end
            while start < end and value[start].isspace():
                start += 1
            while end > start and value[end - 1].isspace():
                end -= 1
            if start < end:
                spans.add((start, end))
    return sorted(spans, key=lambda item: (item[1] - item[0], item[0]))
