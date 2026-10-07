"""Optional preference enhancer: a local model may *suggest*, rules always decide.

The deterministic extractor in :mod:`interaction_extractor` covers a fixed set of
topics. When ``MEMGUARD_PREFERENCE_ENHANCER_COMMAND`` is configured, this module
sends the interaction text to that command and accepts only schema-valid,
topic-whitelisted, high-confidence suggestions. Anything malformed, slow, or
unknown is dropped: the enhancer can add evidence but can never override a
deterministic decision, and a broken enhancer never blocks ingest.
"""

from __future__ import annotations

from typing import List, Optional, Tuple
import json
import os
import shlex
import subprocess

from memguard.integrations.events import AgentInteractionEvent
from memguard.integrations.interaction_extractor import PreferenceObservation

# 只接受已知 topic，防止外部命令注入任意偏好维度。
ALLOWED_TOPICS = {
    "code_search_tool",
    "test_runner_command",
    "service_log_tool",
    "response_order",
    "response_language",
    "response_verbosity",
    "high_risk_confirmation",
    "editor_command",
    "package_manager",
    "commit_style",
    "indent_style",
}
ALLOWED_SCOPES = {"global", "session", "project", "agent", "task_type"}
MIN_CONFIDENCE = 0.6
# 增强器提出的偏好一律按隐式证据计入，必须靠多轮观察才能晋升为稳定偏好。
ENHANCER_CONFIDENCE_CAP = 0.72


class PreferenceEnhancer:
    def __init__(self, command: Optional[str] = None, timeout: Optional[float] = None):
        self.command = (
            command if command is not None else os.environ.get("MEMGUARD_PREFERENCE_ENHANCER_COMMAND", "")
        )
        if timeout is not None:
            self.timeout = float(timeout)
        else:
            try:
                self.timeout = float(os.environ.get("MEMGUARD_PREFERENCE_ENHANCER_TIMEOUT", "8"))
            except ValueError:
                self.timeout = 8.0

    @property
    def enabled(self) -> bool:
        return bool(self.command.strip())

    def suggest(
        self,
        event: AgentInteractionEvent,
        existing: List[PreferenceObservation],
    ) -> Tuple[List[PreferenceObservation], str]:
        """Return extra observations the deterministic pass missed."""

        if not self.enabled:
            return [], ""
        text = " ".join(part for part in (event.content, event.user_feedback) if part).strip()
        if not text:
            return [], ""
        payload = {
            "task": "memguardos_preference_extraction",
            "schema": {
                "preferences": [
                    {
                        "topic": sorted(ALLOWED_TOPICS),
                        "value": "short stable value",
                        "scope": sorted(ALLOWED_SCOPES),
                        "polarity": ["positive", "negative"],
                        "confidence": "0.0-1.0",
                    }
                ]
            },
            "already_extracted": [
                {"topic": item.topic, "value": item.value} for item in existing
            ],
            "text": text,
        }
        try:
            argv = shlex.split(self.command)
            if not argv:
                return [], "preference enhancer command is empty"
            completed = subprocess.run(
                argv,
                input=json.dumps(payload, ensure_ascii=False),
                text=True,
                capture_output=True,
                timeout=self.timeout,
                check=False,
            )
        except subprocess.TimeoutExpired:
            return [], "preference enhancer timed out after %.1fs" % self.timeout
        except (OSError, ValueError) as exc:
            return [], "preference enhancer could not start: %s" % str(exc)[:120]
        if completed.returncode != 0:
            return [], "preference enhancer exited %s: %s" % (
                completed.returncode,
                completed.stderr.strip()[:180],
            )
        try:
            data = json.loads(completed.stdout)
        except json.JSONDecodeError as exc:
            return [], "preference enhancer returned invalid JSON: %s" % exc

        raw_items = data.get("preferences") if isinstance(data, dict) else None
        if not isinstance(raw_items, list):
            return [], "preference enhancer returned no preference list"

        known = {(item.topic, item.value) for item in existing}
        accepted: List[PreferenceObservation] = []
        for raw in raw_items[:8]:
            observation = _validate_suggestion(raw, event)
            if observation is None:
                continue
            if (observation.topic, observation.value) in known:
                continue
            known.add((observation.topic, observation.value))
            accepted.append(observation)
        return accepted, ""


def _validate_suggestion(raw: object, event: AgentInteractionEvent) -> Optional[PreferenceObservation]:
    if not isinstance(raw, dict):
        return None
    topic = str(raw.get("topic") or "").strip()
    value = str(raw.get("value") or "").strip()
    scope = str(raw.get("scope") or "global").strip()
    polarity = str(raw.get("polarity") or "positive").strip()
    if topic not in ALLOWED_TOPICS or not value or len(value) > 120:
        return None
    if scope not in ALLOWED_SCOPES or polarity not in {"positive", "negative"}:
        return None
    try:
        confidence = float(raw.get("confidence", 0.0))
    except (TypeError, ValueError):
        return None
    if confidence < MIN_CONFIDENCE:
        return None
    return PreferenceObservation(
        topic=topic,
        value=value,
        polarity=polarity,
        scope=scope,
        explicit=False,
        confidence=min(confidence, ENHANCER_CONFIDENCE_CAP),
        evidence_event_id=event.event_id,
    )
