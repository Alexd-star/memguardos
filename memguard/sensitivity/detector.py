from __future__ import annotations

from dataclasses import dataclass
from typing import Dict, List, Pattern
import re

from memguard.models import Sensitivity


@dataclass
class SensitiveFinding:
    label: str
    value: str
    start: int
    end: int
    level: str


class SensitiveDetector:
    def __init__(self, custom_patterns: Dict[str, str] = None):
        self.patterns: Dict[str, Pattern[str]] = {
            "email": re.compile(r"\b[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}\b"),
            "phone_cn": re.compile(r"(?<!\d)1[3-9]\d{9}(?!\d)"),
            "id_card_cn": re.compile(r"(?<!\d)\d{17}[\dXx](?!\d)"),
            "bank_card": re.compile(r"(?<!\d)(?:\d{4}[- ]?){3,4}\d{3}(?!\d)"),
            "person_name_cn": re.compile(r"(?:姓名|名字|收件人|联系人)\s*(?:是|为|:|：)\s*[\u4e00-\u9fff]{2,4}"),
            "address_cn": re.compile(r"(?:地址|住址|收货地址)\s*(?:是|为|:|：)\s*[\u4e00-\u9fffA-Za-z0-9#_\-号栋单元室路街区县市省]{8,80}"),
            "api_key": re.compile(r"\b(?:(?:sk|ak|pk|hf|ghp|github_pat)[_-][A-Za-z0-9_\-]{16,}|sk-[A-Za-z0-9_\-]{16,})\b"),
            "aws_access_key_id": re.compile(r"\b(?:AKIA|ASIA)[A-Z0-9]{16}\b"),
            "token_assignment": re.compile(r"(?i)\b(?:[A-Z0-9_]*api[_-]?key|[A-Z0-9_]*token|[A-Z0-9_]*secret|[A-Z0-9_]*password)\s*[:=]\s*['\"]?[^'\"\s]{8,}"),
            "private_key": re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----"),
        }
        for name, pattern in (custom_patterns or {}).items():
            self.patterns[name] = re.compile(pattern)

    def detect(self, text: str, source_uri: str = "") -> List[SensitiveFinding]:
        findings: List[SensitiveFinding] = []
        for label, pattern in self.patterns.items():
            for match in pattern.finditer(text):
                if self._allowlist_hit(match.group(0), text):
                    continue
                findings.append(
                    SensitiveFinding(
                        label=label,
                        value=match.group(0),
                        start=match.start(),
                        end=match.end(),
                        level=self._level(label, source_uri),
                    )
                )
        if self._sensitive_path(source_uri):
            findings.append(
                SensitiveFinding(
                    label="sensitive_os_source",
                    value=source_uri,
                    start=0,
                    end=0,
                    level="high",
                )
            )
        return findings

    def classify(self, text: str, source_uri: str = "") -> Sensitivity:
        findings = self.detect(text, source_uri)
        reason = self.decision_reason(text, source_uri, findings)
        if not findings:
            return Sensitivity(level="none", labels=[], decision_reason=reason)
        levels = [finding.level for finding in findings]
        if "high" in levels:
            level = "high"
        elif "medium" in levels:
            level = "medium"
        else:
            level = "low"
        labels = sorted({finding.label for finding in findings})
        reason["final_action"] = self._action_for_level(level)
        return Sensitivity(level=level, labels=labels, decision_reason=reason)

    def redact(self, text: str, source_uri: str = "") -> str:
        result = text
        findings = sorted(self.detect(text, source_uri), key=lambda item: item.start, reverse=True)
        for finding in findings:
            if finding.start == finding.end:
                continue
            replacement = "[REDACTED:%s]" % finding.label
            result = result[: finding.start] + replacement + result[finding.end :]
        return result

    @staticmethod
    def _level(label: str, source_uri: str) -> str:
        if label in {"api_key", "aws_access_key_id", "token_assignment", "private_key"}:
            return "high"
        if label in {"id_card_cn", "phone_cn", "bank_card", "address_cn"}:
            return "medium"
        if SensitiveDetector._sensitive_path(source_uri):
            return "high"
        return "low"

    def decision_reason(self, text: str, source_uri: str = "", findings: List[SensitiveFinding] | None = None) -> Dict[str, object]:
        findings = findings if findings is not None else self.detect(text, source_uri)
        levels = [finding.level for finding in findings]
        if "high" in levels:
            level = "high"
        elif "medium" in levels:
            level = "medium"
        elif findings:
            level = "low"
        else:
            level = "none"
        allowlist_hits = self._allowlist_matches(text)
        return {
            "matched_pattern": sorted({finding.label for finding in findings}),
            "allowlist_hit": allowlist_hits,
            "source_uri_sensitive": self._sensitive_path(source_uri),
            "final_action": self._action_for_level(level),
        }

    @staticmethod
    def _action_for_level(level: str) -> str:
        if level == "high":
            return "block"
        if level == "medium":
            return "redact"
        if level == "low":
            return "allow_low_risk"
        return "allow"

    @staticmethod
    def _sensitive_path(source_uri: str) -> bool:
        lowered = source_uri.replace("\\", "/").lower()
        markers = [
            "/.ssh/",
            ".env",
            "/secrets/",
            "authorized_keys",
            "id_rsa",
            "id_ed25519",
            "credentials",
            "/.aws/",
            ".aws/credentials",
            "/.kube/",
            ".kube/config",
            "kubeconfig",
            "service_account",
            "service-account",
            "/etc/shadow",
            "/etc/gshadow",
            "/etc/ssh/",
            "/root/.ssh/",
        ]
        return any(marker in lowered for marker in markers)

    @staticmethod
    def _documentation_example_email(value: str) -> bool:
        lowered = value.lower()
        example_domains = ("@example.invalid",)
        return lowered.endswith(example_domains)

    @staticmethod
    def _allowlist_hit(value: str, context: str) -> bool:
        value_lower = value.lower()
        context_lower = context.lower()
        if value_lower.startswith("sk-demo-"):
            return True
        if value_lower.endswith("@example.invalid"):
            return True
        allow_terms = (
            "example.com",
            "token.service",
            "password_length",
            "map_private",
            "private_keywords",
        )
        if value_lower in allow_terms:
            return True
        if value_lower == "private key" and "private_keywords" in context_lower:
            return True
        return False

    @staticmethod
    def _allowlist_matches(text: str) -> List[str]:
        lowered = text.lower()
        hits = []
        for marker in ("sk-demo-", "example@example.invalid", "example.com", "token.service", "password_length", "map_private", "private_keywords"):
            if marker in lowered:
                hits.append(marker)
        if re.search(r"\b[A-Za-z0-9._%+-]+@example\.invalid\b", text, flags=re.IGNORECASE):
            hits.append("*.example.invalid")
        return sorted(set(hits))
