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
        # 显式声明仍可立刻生效（用户有权直接改变主意），但证据必须以正向为主：
        # "以后不要用 grep" 这类否定表达不能把 grep 反过来晋升为稳定偏好。
        if self.explicit_confirmation:
            return self.positive_ratio >= 0.5
        return (
            self.evidence_count >= 3
            and self.distinct_sessions >= 2
            and self.positive_ratio >= 0.8
            and self.contradiction_count == 0
        )
