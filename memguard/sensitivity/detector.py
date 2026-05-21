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
            "person_name_cn": re.compile(r"(?:姓名|名字|收件人|联系人)\s*(?:是|为|:|：)?\s*[\u4e00-\u9fff]{2,4}"),
            "address_cn": re.compile(r"(?:地址|住址|收货地址)\s*(?:是|为|:|：)?\s*[\u4e00-\u9fffA-Za-z0-9#_\-号栋单元室路街区县市省]{8,80}"),
            "api_key": re.compile(r"\b(?:sk|ak|pk|hf|ghp|github_pat)_[A-Za-z0-9_\-]{16,}\b"),
            "aws_access_key_id": re.compile(r"\b(?:AKIA|ASIA)[A-Z0-9]{16}\b"),
            "token_assignment": re.compile(r"(?i)\b(?:api[_-]?key|token|secret|password)\s*[:=]\s*['\"]?[^'\"\s]{8,}"),
            "private_key": re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----"),
        }
        for name, pattern in (custom_patterns or {}).items():
            self.patterns[name] = re.compile(pattern)

    def detect(self, text: str, source_uri: str = "") -> List[SensitiveFinding]:
        findings: List[SensitiveFinding] = []
        for label, pattern in self.patterns.items():
            for match in pattern.finditer(text):
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
        if not findings:
            return Sensitivity(level="none", labels=[])
        levels = [finding.level for finding in findings]
        if "high" in levels:
            level = "high"
        elif "medium" in levels:
            level = "medium"
        else:
            level = "low"
        labels = sorted({finding.label for finding in findings})
        return Sensitivity(level=level, labels=labels)

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
            "/etc/shadow",
            "/etc/gshadow",
            "/etc/ssh/",
            "/root/.ssh/",
        ]
        return any(marker in lowered for marker in markers)
