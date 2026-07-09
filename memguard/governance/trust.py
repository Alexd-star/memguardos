from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Dict, List
import re


TRUSTED_SOURCE_KINDS = {
    "manual",
    "inline",
    "update",
    "rollback",
    "promote",
    "agent_interaction",
    "manual_config",
    "api",
    "os_event",
    "auditd",
}

CONTROLLED_SOURCE_PREFIXES = (
    "benchmark",
    "latency_",
    "concurrent_",
    "resource_control",
    "longmemeval_v2",
    "tool_result",
    "user_behavior",
)

UNTRUSTED_SOURCE_MARKERS = ("web", "browser", "url", "external", "remote", "crawl", "scrape")

INJECTION_PATTERNS = {
    "instruction_override": re.compile(r"(?:ignore|disregard).{0,30}(?:previous|prior|system)|忽略.{0,20}(?:之前|以上|系统|安全)", re.I | re.S),
    "covert_persistence": re.compile(r"(?:remember|store|persist).{0,50}(?:forever|permanent|without telling)|(?:永久|永远).{0,30}(?:记住|可信|保存)|不要告诉用户", re.I | re.S),
    "policy_bypass": re.compile(
        r"(?:bypass|disable|override).{0,30}(?:safety|policy|approval)"
        r"|(?:绕过|禁用).{0,20}(?:安全|策略|确认)"
        r"|覆盖.{0,8}(?:安全策略|安全确认|审批策略)",
        re.I | re.S,
    ),
    "privilege_claim": re.compile(r"(?:system prompt|developer message|admin instruction)|(?:系统提示词|开发者消息|管理员指令)", re.I),
}


@dataclass(frozen=True)
class GovernanceDecision:
    trust_level: str
    trust_score: float
    governance_state: str
    reason: str
    signals: List[str]

    @property
    def quarantined(self) -> bool:
        return self.governance_state == "quarantined"

    def to_dict(self) -> Dict[str, object]:
        return asdict(self)


def assess_memory_source(text: str, source_kind: str, source_uri: str = "") -> GovernanceDecision:
    kind = str(source_kind or "unknown").strip().casefold()
    material = "%s\n%s" % (text, source_uri)
    signals = [name for name, pattern in INJECTION_PATTERNS.items() if pattern.search(material)]
    if "policy_bypass" in signals and _is_defensive_policy_statement(material):
        signals = [signal for signal in signals if signal != "policy_bypass"]
    explicitly_untrusted = any(marker in kind for marker in UNTRUSTED_SOURCE_MARKERS)
    if signals:
        return GovernanceDecision(
            trust_level="untrusted",
            trust_score=0.1,
            governance_state="quarantined",
            reason="potential memory-poisoning signals: %s" % ", ".join(signals),
            signals=signals,
        )
    if explicitly_untrusted or kind == "unknown":
        return GovernanceDecision(
            trust_level="untrusted",
            trust_score=0.35,
            governance_state="quarantined",
            reason="untrusted external source requires review",
            signals=["untrusted_source_kind"],
        )
    if kind in TRUSTED_SOURCE_KINDS:
        return GovernanceDecision(
            trust_level="trusted",
            trust_score=0.95,
            governance_state="active",
            reason="direct user or verified local source",
            signals=[],
        )
    if kind.startswith(CONTROLLED_SOURCE_PREFIXES):
        return GovernanceDecision(
            trust_level="controlled",
            trust_score=0.7,
            governance_state="active",
            reason="structured local integration with provenance",
            signals=[],
        )
    return GovernanceDecision(
        trust_level="limited",
        trust_score=0.55,
        governance_state="active",
        reason="local source accepted with limited trust",
        signals=[],
    )


def _is_defensive_policy_statement(text: str) -> bool:
    lowered = text.lower()
    defensive_markers = (
        "不因",
        "不得",
        "不能",
        "不要",
        "需要二次确认",
        "require confirmation",
        "requires confirmation",
        "must not bypass",
        "do not bypass",
    )
    return ("绕过" in text or "bypass" in lowered) and any(marker in lowered or marker in text for marker in defensive_markers)
