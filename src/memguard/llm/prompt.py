from __future__ import annotations

from typing import Dict


def render_generation_prompt(context_package: Dict[str, object]) -> str:
    query = str(context_package.get("query", ""))
    policy = str(context_package.get("generation_policy", ""))
    context = str(context_package.get("context", ""))
    budget = context_package.get("budget", {})
    if not isinstance(budget, dict):
        budget = {}
    token_line = "估算上下文 tokens: %s；预算: %s；已截断: %s" % (
        budget.get("estimated_context_tokens", "unknown"),
        budget.get("token_budget", "none"),
        budget.get("truncated", False),
    )
    return "\n".join(
        [
            "你是运行在 Linux/openKylin 上的 OS Agent。",
            "请严格遵守以下记忆使用策略：",
            policy,
            "",
            "可用记忆上下文如下。只允许使用这些已脱敏、未遗忘的记忆；不要猜测或复原被脱敏内容。",
            context or "(无可用记忆)",
            "",
            token_line,
            "",
            "用户当前请求：",
            query,
            "",
            "请基于当前请求和可用记忆给出回答；如果记忆不足，请明确说明依据不足。",
        ]
    )
