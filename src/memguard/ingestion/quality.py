from __future__ import annotations

from dataclasses import asdict, dataclass, field
import re
import unicodedata


MAX_TEXT_CHARS = 12000


@dataclass
class QualityReport:
    accepted: bool
    score: float
    issues: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    original_chars: int = 0
    normalized_chars: int = 0
    line_count: int = 0
    source_kind: str = ""

    def to_dict(self) -> dict[str, object]:
        return asdict(self)


def normalize_text(text: str, max_chars: int = MAX_TEXT_CHARS) -> str:
    value = unicodedata.normalize("NFKC", str(text or ""))
    value = value.replace("\x00", "")
    value = "".join(char if char in "\n\t" or not unicodedata.category(char).startswith("C") else " " for char in value)
    value = re.sub(r"[ \t\r\f\v]+", " ", value)
    value = re.sub(r"\n{3,}", "\n\n", value)
    value = value.strip()
    if len(value) > max_chars:
        value = value[:max_chars].rstrip()
    return value


def assess_text_quality(text: str, source_kind: str = "", max_chars: int = MAX_TEXT_CHARS) -> tuple[str, QualityReport]:
    original = str(text or "")
    normalized = normalize_text(original, max_chars=max_chars)
    report = QualityReport(
        accepted=True,
        score=1.0,
        original_chars=len(original),
        normalized_chars=len(normalized),
        line_count=normalized.count("\n") + 1 if normalized else 0,
        source_kind=source_kind,
    )

    if not normalized:
        report.accepted = False
        report.score = 0.0
        report.issues.append("empty_after_normalization")
        return normalized, report

    if len(normalized) < 4:
        report.warnings.append("very_short_text")
        report.score -= 0.25

    signal_chars = sum(1 for char in normalized if char.isalnum() or "\u4e00" <= char <= "\u9fff")
    signal_ratio = signal_chars / max(1, len(normalized))
    if signal_chars == 0:
        report.accepted = False
        report.score = 0.0
        report.issues.append("no_alphanumeric_or_cjk_signal")
        return normalized, report
    if signal_ratio < 0.25:
        report.warnings.append("low_signal_ratio")
        report.score -= 0.2

    if len(original) > max_chars:
        report.warnings.append("truncated_to_max_chars")
        report.score -= 0.1
    if normalized != original.strip():
        report.warnings.append("normalized_whitespace_or_control_chars")
        report.score -= 0.05

    lower = normalized.lower()
    if source_kind == "tool_result" and "command=" not in lower:
        report.warnings.append("tool_result_missing_command_field")
        report.score -= 0.1
    if source_kind in {"manual_config", "tool_result", "user_behavior"} and "=" not in normalized and ":" not in normalized:
        report.warnings.append("weak_structured_signal")
        report.score -= 0.05

    report.score = round(max(0.1, min(1.0, report.score)), 3)
    return normalized, report
