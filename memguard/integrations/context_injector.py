from typing import Dict, Set


AGENT_CONTEXT_STAGES = {"planning", "tool_selection", "response_generation"}

STAGE_SUBTYPES: Dict[str, Set[str]] = {
    "planning": {
        "case",
        "fact",
        "os_event",
        "procedural_trace",
        "template",
        "tool_failure_case",
        "workflow",
        "user_habit",
    },
    "tool_selection": {
        "behavior_risk",
        "procedural_trace",
        "security_policy",
        "tool_failure_case",
        "tool_preference",
        "tool_trace",
        "workflow",
        "user_habit",
    },
    "response_generation": {
        "case",
        "fact",
        "output_style",
        "security_policy",
        "template",
        "workflow",
        "user_habit",
    },
}

STAGE_PREFERENCE_TOPICS: Dict[str, Set[str]] = {
    "planning": set(),
    "tool_selection": {
        "code_search_tool",
        "high_risk_confirmation",
        "service_log_tool",
        "test_runner_command",
    },
    "response_generation": {
        "high_risk_confirmation",
        "response_language",
        "response_order",
        "response_verbosity",
        "service_log_tool",
        "test_runner_command",
    },
}


def validate_agent_context_stage(stage: str) -> str:
    clean = str(stage).strip()
    if clean not in AGENT_CONTEXT_STAGES:
        raise ValueError("unsupported agent context stage: %s" % clean)
    return clean


def stage_allows_memory(stage: str, memory: Dict[str, object]) -> bool:
    subtype = str(memory.get("subtype") or "")
    return subtype in STAGE_SUBTYPES[stage]


def stage_allows_preference(stage: str, memory: Dict[str, object]) -> bool:
    topic = str(memory.get("preference_topic") or memory.get("topic") or "")
    return topic in STAGE_PREFERENCE_TOPICS[stage]
