"""Provider-neutral execution plan built from memory-conditioned preflight."""

from __future__ import annotations

import json
from typing import Dict, Mapping

from memguard.models import stable_hash


def _canonical(value: object) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), default=str)


def build_action_plan(
    *,
    task: str,
    tool_name: str,
    tool_input: Mapping[str, object],
    preflight: Mapping[str, object],
) -> Dict[str, object]:
    """Turn a preflight result into a reviewable execution contract.

    The plan contains no command execution and no plaintext output.  It gives
    adapters one stable object to enforce before a tool call and to reference
    when recording the result afterwards.
    """

    receipt = preflight.get("context_receipt") if isinstance(preflight, Mapping) else None
    evidence = preflight.get("evidence") if isinstance(preflight, Mapping) else []
    decision = str(preflight.get("decision") or "deny") if isinstance(preflight, Mapping) else "deny"
    material = {
        "task": str(task or "").strip(),
        "tool_name": str(tool_name or "").strip(),
        "tool_input": dict(tool_input or {}),
        "context_fingerprint": str((receipt or {}).get("fingerprint") or "") if isinstance(receipt, Mapping) else "",
        "decision": decision,
        "evidence": [
            (str(item.get("memory_id") or ""), str(item.get("version_id") or ""))
            for item in evidence
            if isinstance(item, Mapping)
        ],
    }
    plan_id = "plan_" + stable_hash(_canonical(material))[:24]
    if decision == "allow":
        gate = "execute"
        next_step = "执行外部工具后，必须提交结构化结果以形成反馈闭环。"
    elif decision == "redirect":
        gate = "replace_then_execute"
        next_step = "采用 suggested_command 或等价参数重新预检，确认通过后再执行。"
    elif decision == "confirm":
        gate = "user_confirmation"
        next_step = "向用户展示风险与证据，获得明确确认后再执行。"
    else:
        gate = "stop"
        next_step = "停止工具调用，刷新上下文或处理安全策略后再试。"
    return {
        "schema": "memguardos-agent-action-plan-v1",
        "plan_id": plan_id,
        "decision": decision,
        "execution_gate": gate,
        "task": str(task or "").strip(),
        "tool": {"name": str(tool_name or "").strip(), "input_digest": stable_hash(_canonical(dict(tool_input or {})))},
        "policy": {
            "category": str(preflight.get("policy_category") or "") if isinstance(preflight, Mapping) else "",
            "reason": str(preflight.get("reason") or "") if isinstance(preflight, Mapping) else "",
            "suggested_command": str(preflight.get("suggested_command") or "") if isinstance(preflight, Mapping) else "",
            "confirmation_required": bool(preflight.get("confirmation_required", False)) if isinstance(preflight, Mapping) else False,
        },
        "evidence": [dict(item) for item in evidence if isinstance(item, Mapping)],
        "context": {
            "fingerprint": str((receipt or {}).get("fingerprint") or "") if isinstance(receipt, Mapping) else "",
            "memory_count": int(preflight.get("context_memory_count") or 0) if isinstance(preflight, Mapping) else 0,
        },
        "feedback": {
            "required": True,
            "command": "record_agent_action_outcome",
            "memory_evidence_count": len([item for item in evidence if isinstance(item, Mapping)]),
        },
        "next_step": next_step,
        "privacy": "仅保存输入摘要、记忆版本和结构化策略；不保存命令正文或工具输出。",
    }
