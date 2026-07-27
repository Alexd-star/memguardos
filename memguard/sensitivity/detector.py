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


_CUSTOM_LEVELS = {"high", "medium", "low"}


def _normalize_custom_pattern(spec: object) -> tuple[str, str]:
    """Accept both the legacy ``name -> regex`` form and ``name -> {regex, level}``."""

    if isinstance(spec, dict):
        regex = str(spec.get("regex", ""))
        level = str(spec.get("level", "")).strip().lower()
        return regex, level if level in _CUSTOM_LEVELS else ""
    return str(spec), ""


class SensitiveDetector:
    def __init__(self, custom_patterns: Dict[str, object] = None):
        self.patterns: Dict[str, Pattern[str]] = {
            "email": re.compile(r"\b[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}\b"),
            "phone_cn": re.compile(r"(?<![\dA-Za-z])(?:(?:\+|00)?86[-\s]?)?1[3-9]\d{9}(?!\d)"),
            "id_card_cn": re.compile(r"(?<!\d)\d{17}[\dXx](?!\d)"),
            "id_card_cn_legacy": re.compile(r"(?<!\d)[1-9]\d{7}(?:0[1-9]|1[0-2])(?:[0-2]\d|3[01])\d{3}(?!\d)"),
            # 前置边界排除 sk-demo-0000000000000000 这类 token 尾部的连续数字。
            "bank_card": re.compile(r"(?<![-_\dA-Za-z])(?:\d{16,19}|\d{4}(?:[ -]\d{4}){3}(?:[ -]\d{1,3})?)(?![-\d])"),
            "person_name_cn": re.compile(r"(?:姓名|名字|收件人|联系人)\s*(?:是|为|:|：)\s*[\u4e00-\u9fff]{2,4}"),
            "address_cn": re.compile(r"(?:地址|住址|收货地址)\s*(?:是|为|:|：)\s*[\u4e00-\u9fffA-Za-z0-9#_\-号栋单元室路街区县市省]{8,80}"),
            "api_key": re.compile(r"\b(?:(?:sk|ak|pk|hf|ghp|gho|ghu|ghs|ghr|github_pat|glpat|xox[baprs])[_-][A-Za-z0-9_\-]{16,}|sk-[A-Za-z0-9_\-]{16,}|AIza[0-9A-Za-z_\-]{35})\b"),
            "aws_access_key_id": re.compile(r"\b(?:AKIA|ASIA)[A-Z0-9]{16}\b"),
            "aws_secret_access_key": re.compile(r"(?i)\baws[-A-Za-z0-9 _.]{0,32}?(?:secret|access[_-]?key)[-A-Za-z0-9 _.]{0,12}?[:=]\s*['\"]?[A-Za-z0-9/+=]{40}(?![A-Za-z0-9/+=])"),
            "jwt": re.compile(r"\beyJ[A-Za-z0-9_\-]{10,}\.[A-Za-z0-9_\-]{10,}\.[A-Za-z0-9_\-]{16,}\b"),
            "url_credentials": re.compile(r"[A-Za-z][A-Za-z0-9+.\-]*://[^/\s:@]{1,64}:[^/\s@]{1,128}@[^\s/]+"),
            # 复合键名（aws_secret_access_key= / gitlab_access_key=）也要命中，
            # 不能依赖凭据长度恰好等于某个固定值。
            "token_assignment": re.compile(r"(?i)\b[A-Z0-9_]*(?:api[_-]?key|access[_-]?key|token|secret|passwd|pwd|password)\s*[:=]\s*['\"]?[^'\"\s]{6,}"),
            "weak_password_hint_cn": re.compile(r"(?:口令|密码)\s*(?:是|为|[:=：])\s*\S{6,64}"),
            "private_key": re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----"),
        }
        self.custom_levels: Dict[str, str] = {}
        for name, spec in (custom_patterns or {}).items():
            regex, level = _normalize_custom_pattern(spec)
            self.patterns[name] = re.compile(regex)
            if level:
                self.custom_levels[name] = level

    def detect(self, text: str, source_uri: str = "") -> List[SensitiveFinding]:
        findings: List[SensitiveFinding] = []
        id_spans = [
            (match.start(), match.end())
            for label in ("id_card_cn", "id_card_cn_legacy")
            if label in self.patterns
            for match in self.patterns[label].finditer(text)
        ]
        for label, pattern in self.patterns.items():
            for match in pattern.finditer(text):
                if self._allowlist_hit(match.group(0), text):
                    continue
                if label == "bank_card":
                    # 身份证号也满足银行卡的位数形状，保留更精确的标签，避免张冠李戴。
                    if any(start <= match.start() and match.end() <= end for start, end in id_spans):
                        continue
                    digits = re.sub(r"\D", "", match.group(0))
                    if len(set(digits)) <= 1:
                        continue  # 全同数字是文档占位符，不是真实卡号
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
        spans: List[tuple[int, int, str]] = []
        for finding in self.detect(text, source_uri):
            if finding.start < finding.end:
                spans.append((finding.start, finding.end, finding.label))
        if not spans:
            return text
        # 合并重叠区间后再一次性替换，避免嵌套/交叠命中在逐段替换时产生残字或吞字。
        spans.sort(key=lambda item: (item[0], -item[1]))
        merged: List[tuple[int, int, str]] = []
        for start, end, label in spans:
            if merged and start < merged[-1][1]:
                prev_start, prev_end, prev_label = merged[-1]
                merged[-1] = (prev_start, max(prev_end, end), prev_label)
            else:
                merged.append((start, end, label))
        result = text
        for start, end, label in sorted(merged, key=lambda item: item[0], reverse=True):
            result = result[:start] + ("[REDACTED:%s]" % label) + result[end:]
        return result

    def _level(self, label: str, source_uri: str) -> str:
        custom = self.custom_levels.get(label)
        if custom:
            return custom
        if label in {
            "api_key",
            "aws_access_key_id",
            "aws_secret_access_key",
            "token_assignment",
            "jwt",
            "url_credentials",
            "private_key",
        }:
            return "high"
        if label in {"id_card_cn", "id_card_cn_legacy", "phone_cn", "bank_card", "address_cn", "weak_password_hint_cn"}:
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
