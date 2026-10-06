"""Integration boundaries for external agents."""

from memguard.integrations.adapter_registry import (
    AdapterCapabilities,
    AgentAdapterRegistry,
    GenericAgentEventAdapter,
    builtin_agent_adapter_registry,
)
from memguard.integrations.events import AgentInteractionEvent

__all__ = [
    "AdapterCapabilities",
    "AgentAdapterRegistry",
    "AgentInteractionEvent",
    "GenericAgentEventAdapter",
    "builtin_agent_adapter_registry",
]
