from __future__ import annotations

from dataclasses import dataclass, field
import json
import os
import shlex
import subprocess
from typing import Any

from memguard.natural_language import looks_like_question


ALLOWED_TYPES = {"preference", "knowledge", "sensitive_policy"}
ALLOWED_SUBTYPES = {
    "behavior_risk",
    "behavior_trace",
    "case",
    "fact",
    "general",
    "os_event",
    "output_style",
    "procedural_trace",
    "security_policy",
    "template",
    "tool_failure_case",
    "tool_preference",
    "tool_trace",
    "user_habit",
    "workflow",
}


@dataclass
class ExtractionDecision:
    memory_type: str
    subtype: str
    summary: str
    confidence: float = 1.0
    method: str = "deterministic_semantic_rules"
    signals: list[str] = field(default_factory=list)
    enhancer_used: bool = False
    enhancer_error: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "memory_type": self.memory_type,
            "subtype": self.subtype,
            "summary": self.summary,
            "confidence": round(float(self.confidence), 4),
            "method": self.method,
            "signals": list(self.signals),
            "enhancer_used": bool(self.enhancer_used),
            "enhancer_error": self.enhancer_error,
        }


class HybridMemoryExtractor:
    """Explainable memory extractor with an optional semantic enhancer.

    The default path is deterministic and dependency-free so openKylin tests
    remain reproducible. If MEMGUARD_EXTRACTOR_COMMAND is configured, the
    extractor sends a small JSON task to a local model/NER command and accepts
    only schema-valid decisions. Invalid or slow enhancers never block ingest.
    """

    def __init__(self, command: str | None = None, timeout: float | None = None):
        self.command = command if command is not None else os.environ.get("MEMGUARD_EXTRACTOR_COMMAND", "")
        if timeout is not None:
            self.timeout = timeout
        else:
            raw_timeout = os.environ.get("MEMGUARD_EXTRACTOR_TIMEOUT", "8")
            try:
                self.timeout = float(raw_timeout)
            except ValueError:
                self.timeout = 8.0

    def extract(self, text: str, source_kind: str = "manual") -> ExtractionDecision:
        base = self._deterministic_extract(text, source_kind)
        if source_kind == "tool_result_verified":
            return base
        if not self.command.strip():
            return base
        enhanced, error = self._call_enhancer(text, source_kind)
        if enhanced is None:
            if error:
                base.enhancer_error = error
                base.signals.append("semantic_enhancer_fallback")
            return base
        enhanced.enhancer_used = True
        enhanced.signals = _dedupe(enhanced.signals + base.signals)
        if enhanced.summary == "":
            enhanced.summary = base.summary
        return enhanced

    def _deterministic_extract(self, text: str, source_kind: str) -> ExtractionDecision:
        lowered = text.lower()
        signals = self._signals(text, source_kind)
        memory_type = "knowledge"
        if source_kind == "tool_result_verified":
            memory_type = "knowledge"
        elif "source_tool_result" in signals and "preference_intent" in signals and "tool_preference_intent" in signals:
            memory_type = "preference"
        elif "source_tool_result" in signals:
            memory_type = "knowledge"
        elif "security_policy_intent" in signals or "sensitive_secret_term" in signals:
            memory_type = "sensitive_policy"
        elif "preference_intent" in signals or "style_intent" in signals:
            memory_type = "preference"

        subtype = "fact"
        if source_kind == "tool_result_verified":
            subtype = "tool_trace"
        elif source_kind == "tool_result" and memory_type == "preference" and "tool_preference_intent" in signals:
            subtype = "tool_preference"
        elif source_kind == "tool_result" or "tool result:" in lowered:
            if "procedural_memory=state_action_result" in lowered:
                subtype = "tool_failure_case" if ("status=failure" in lowered or "exit_code=1" in lowered) else "procedural_trace"
            else:
                subtype = "tool_failure_case" if ("status=failure" in lowered or "exit_code=1" in lowered) else "tool_trace"
        elif source_kind == "os_event":
            subtype = "os_event"
        elif source_kind == "user_behavior":
            subtype = "behavior_risk" if "destructive_command" in signals else "behavior_trace"
        elif memory_type == "sensitive_policy":
            subtype = "security_policy"
        elif memory_type == "preference":
            if "evidence_boundary_intent" in signals or "口径" in lowered:
                subtype = "output_style"
            elif "style_intent" in signals and any(marker in lowered for marker in ["最短", "简洁", "concise", "risk", "风险提醒", "answer.style"]):
                subtype = "output_style"
            elif "tool_preference_intent" in signals:
                subtype = "tool_preference"
            elif "style_intent" in signals:
                subtype = "output_style"
            else:
                subtype = "user_habit"
        elif "template_signal" in signals:
            subtype = "template"
        elif "case_signal" in signals or "failure_signal" in signals:
            subtype = "case"
        elif "workflow_signal" in signals:
            subtype = "workflow"

        return ExtractionDecision(
            memory_type=memory_type,
            subtype=subtype,
            summary=self._summary(text, memory_type),
            confidence=self._confidence(signals),
            signals=signals,
        )

    def _call_enhancer(self, text: str, source_kind: str) -> tuple[ExtractionDecision | None, str]:
        payload = {
            "task": "memguardos_memory_extraction",
            "schema": {
                "type": sorted(ALLOWED_TYPES),
                "subtype": sorted(ALLOWED_SUBTYPES),
                "summary": "short Chinese summary preferred",
                "confidence": "0.0-1.0",
                "signals": "array of short evidence strings",
            },
            "source_kind": source_kind,
            "text": text,
        }
        try:
            argv = shlex.split(self.command)
            if not argv:
                return None, "semantic enhancer command is empty"
            completed = subprocess.run(
                argv,
                input=json.dumps(payload, ensure_ascii=False),
                text=True,
                capture_output=True,
                timeout=self.timeout,
                check=False,
            )
        except subprocess.TimeoutExpired:
            return None, "semantic enhancer timed out after %.1fs" % self.timeout
        if completed.returncode != 0:
            return None, "semantic enhancer exited %s: %s" % (completed.returncode, completed.stderr.strip()[:180])
        try:
            data = json.loads(completed.stdout)
        except json.JSONDecodeError as exc:
            return None, "semantic enhancer returned invalid JSON: %s" % exc
        try:
            memory_type = str(data.get("type") or data.get("memory_type") or "")
            subtype = str(data.get("subtype") or "")
            confidence = float(data.get("confidence", 0.0))
        except (TypeError, ValueError):
            return None, "semantic enhancer returned invalid scalar fields"
        if memory_type not in ALLOWED_TYPES or subtype not in ALLOWED_SUBTYPES:
            return None, "semantic enhancer returned unsupported type/subtype"
        if confidence < 0.55:
            return None, "semantic enhancer confidence below threshold"
        raw_signals = data.get("signals", [])
        if not isinstance(raw_signals, list):
            raw_signals = []
        return (
            ExtractionDecision(
                memory_type=memory_type,
                subtype=subtype,
                summary=str(data.get("summary", ""))[:180],
                confidence=max(0.0, min(1.0, confidence)),
                method="optional_semantic_enhancer",
                signals=[str(item)[:80] for item in raw_signals],
            ),
            "",
        )

    @staticmethod
    def _signals(text: str, source_kind: str) -> list[str]:
        lowered = text.lower()
        is_question = looks_like_question(text)
        signals: list[str] = []
        if source_kind:
            signals.append("source_%s" % source_kind)
        if "tool result:" in lowered:
            signals.append("source_tool_result")
        if "procedural_memory=state_action_result" in lowered or ("state_before=" in lowered and "state_after=" in lowered):
            signals.append("procedural_state_action_result")
        if not is_question and any(
            marker in text
            for marker in (
                "以后",
                "偏好",
                "请先",
                "请优先",
                "请总是",
                "必须",
                "不要",
                "记住",
                "喜欢",
                "习惯",
                "倾向",
                "希望",
                "常用",
            )
        ):
            signals.append("preference_intent")
        if not is_question and any(marker in lowered for marker in ["prefer", "always", "never", "style"]):
            signals.append("preference_intent")
        if not is_question and any(marker in lowered for marker in ["rg", "grep", "pytest", "tool", "工具", "命令", "shell"]):
            signals.append("tool_preference_intent")
        style_directive = any(
            marker in lowered
            for marker in (
                "以后",
                "今后",
                "默认",
                "偏好",
                "请",
                "必须",
                "应当",
                "应该",
                "不要",
                "不应",
                "一律",
                "统一",
                "先给",
                "再给",
                "放在前面",
                "规则",
                "规范",
                "answer.style",
            )
        )
        if not is_question and style_directive and any(marker in lowered for marker in ["简洁", "详细", "中文", "英文", "回答", "最短", "风险提醒", "证据边界", "口径", "材料"]):
            signals.append("style_intent")
        if is_question:
            signals.append("question_content")
        if any(marker in lowered for marker in ["安全策略", "不要保存", "脱敏", "访问令牌"]):
            signals.append("security_policy_intent")
        token_budget_context = "token 预算" in lowered or "token budget" in lowered or "tokens" in lowered
        if any(marker in lowered for marker in ["secret", "password", "api key", "私钥", "密钥"]) or ("token" in lowered and not token_budget_context):
            signals.append("sensitive_secret_term")
        if any(marker in lowered for marker in ["template", "模板"]):
            signals.append("template_signal")
        if any(marker in lowered for marker in ["案例", "case", "修复", "failure", "stderr", "incident"]):
            signals.append("case_signal")
        if any(marker in lowered for marker in ["error", "failed", "exit_code=1", "status=failure"]):
            signals.append("failure_signal")
        if any(marker in lowered for marker in ["workflow", "流程", "步骤", "runbook", "验证", "use ", "run ", "应至少"]):
            signals.append("workflow_signal")
        if "可以通过 systemd" in lowered or "查看运行状态" in lowered:
            signals.append("workflow_signal")
        if any(marker in lowered for marker in ["systemd", "cgroup", "auditd", "journald", "bpf", "lsm", "mmap", "ukui", "openkylin"]):
            signals.append("os_lifecycle_signal")
        if any(marker in lowered for marker in ["rm ", "sudo", "chmod", "chown", "dd ", "mkfs", "git reset"]):
            signals.append("destructive_command")
        if "默认大模型依赖" in text or "不能夸大" in text:
            signals.append("evidence_boundary_intent")
        return _dedupe(signals)

    @staticmethod
    def _summary(text: str, memory_type: str) -> str:
        prefix = {
            "preference": "用户偏好",
            "knowledge": "知识记忆",
            "sensitive_policy": "安全策略",
        }.get(memory_type, "记忆")
        clean = " ".join(text.strip().split())
        for existing in ("知识记忆：", "知识记忆:", "偏好记忆：", "偏好记忆:", "用户偏好：", "用户偏好:", "安全策略：", "安全策略:", "操作经验：", "操作经验:", "流程记忆：", "流程记忆:"):
            if clean.startswith(existing):
                clean = clean[len(existing):].strip()
                break
        return "%s：%s" % (prefix, clean[:140])

    @staticmethod
    def _confidence(signals: list[str]) -> float:
        if not signals:
            return 0.72
        score = 0.80 + min(0.18, 0.025 * len(signals))
        if "semantic_enhancer_fallback" in signals:
            score -= 0.08
        return max(0.55, min(0.98, score))


def _dedupe(values: list[str]) -> list[str]:
    seen = set()
    result = []
    for value in values:
        if value in seen:
            continue
        seen.add(value)
        result.append(value)
    return result
