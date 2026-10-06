"""Explicit lifecycle rules for memory governance states.

The memory table stores the current state, while version history and audit
records explain how that state was reached.  Keeping the transition policy in
one small module prevents callers from inventing a new state or reviving a
forgotten record through an accidental update.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Dict, FrozenSet


GOVERNANCE_STATES: FrozenSet[str] = frozenset({
    "active",
    "quarantined",
    "rejected",
    "source_invalidated",
    "superseded",
    "forgotten",
})

TERMINAL_GOVERNANCE_STATES: FrozenSet[str] = frozenset({
    "forgotten",
})

# A state change is a policy event, not a free-form field update.  ``active``
# is intentionally the only state that can become superseded or invalidated;
# a replacement version or a revalidation is required to make new content
# usable again.
_ALLOWED_TRANSITIONS: Dict[str, FrozenSet[str]] = {
    "active": frozenset({
        "active", "quarantined", "superseded", "source_invalidated", "forgotten",
    }),
    "quarantined": frozenset({
        "quarantined", "rejected", "source_invalidated", "forgotten",
    }),
    "rejected": frozenset({"rejected", "forgotten"}),
    "source_invalidated": frozenset({"source_invalidated", "forgotten"}),
    "superseded": frozenset({"superseded", "forgotten"}),
    "forgotten": frozenset({"forgotten"}),
}


class GovernanceStateError(ValueError):
    """Base error for invalid governance state data or transitions."""


class UnknownGovernanceState(GovernanceStateError):
    """Raised when storage contains a state outside the contract."""


class InvalidGovernanceTransition(GovernanceStateError):
    """Raised when a caller attempts to bypass the lifecycle policy."""

    def __init__(self, current: str, target: str):
        self.current = current
        self.target = target
        super().__init__(
            "invalid governance transition: %s -> %s" % (current, target)
        )


@dataclass(frozen=True)
class GovernanceTransition:
    """A validated state change suitable for audit and API responses."""

    current: str
    target: str
    changed: bool

    def to_dict(self) -> Dict[str, object]:
        return {
            "from": self.current,
            "to": self.target,
            "changed": self.changed,
        }


def validate_governance_state(state: str) -> str:
    """Normalize and validate a persisted governance state."""

    normalized = str(state or "").strip().lower()
    if normalized not in GOVERNANCE_STATES:
        raise UnknownGovernanceState("unknown governance state: %s" % (normalized or "<empty>"))
    return normalized


def allowed_governance_transitions(current: str) -> FrozenSet[str]:
    """Return the immutable set of legal targets for ``current``."""

    normalized = validate_governance_state(current)
    return _ALLOWED_TRANSITIONS[normalized]


def validate_governance_transition(current: str, target: str) -> GovernanceTransition:
    """Validate one lifecycle transition and return its normalized form."""

    current_state = validate_governance_state(current)
    target_state = validate_governance_state(target)
    if target_state not in _ALLOWED_TRANSITIONS[current_state]:
        raise InvalidGovernanceTransition(current_state, target_state)
    return GovernanceTransition(
        current=current_state,
        target=target_state,
        changed=current_state != target_state,
    )


def governance_state_catalog() -> Dict[str, object]:
    """Return a stable, presentation-friendly lifecycle catalog."""

    return {
        "states": sorted(GOVERNANCE_STATES),
        "terminal_states": sorted(TERMINAL_GOVERNANCE_STATES),
        "transitions": {
            state: sorted(targets)
            for state, targets in sorted(_ALLOWED_TRANSITIONS.items())
        },
    }
