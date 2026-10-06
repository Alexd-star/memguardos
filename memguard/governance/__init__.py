"""Memory source trust and lifecycle governance."""

from memguard.governance.state import (
    GOVERNANCE_STATES,
    TERMINAL_GOVERNANCE_STATES,
    GovernanceStateError,
    GovernanceTransition,
    InvalidGovernanceTransition,
    UnknownGovernanceState,
    allowed_governance_transitions,
    governance_state_catalog,
    validate_governance_state,
    validate_governance_transition,
)

__all__ = [
    "GOVERNANCE_STATES",
    "TERMINAL_GOVERNANCE_STATES",
    "GovernanceStateError",
    "GovernanceTransition",
    "InvalidGovernanceTransition",
    "UnknownGovernanceState",
    "allowed_governance_transitions",
    "governance_state_catalog",
    "validate_governance_state",
    "validate_governance_transition",
]
