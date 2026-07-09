from dataclasses import dataclass


@dataclass(frozen=True)
class PreferenceAggregate:
    evidence_count: int
    distinct_sessions: int
    positive_ratio: float
    contradiction_count: int
    explicit_confirmation: bool = False

    @property
    def stable(self) -> bool:
        if self.explicit_confirmation:
            return True
        return (
            self.evidence_count >= 3
            and self.distinct_sessions >= 2
            and self.positive_ratio >= 0.8
            and self.contradiction_count == 0
        )
