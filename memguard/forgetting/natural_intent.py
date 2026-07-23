from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional
import re


FORGET_VERBS = ("忘记", "忘掉", "遗忘", "删除", "清除")

MEMORY_TYPE_PATTERNS = [
    ("all", ("所有记忆", "全部记忆", "全部", "所有")),
    ("security_policy", ("安全策略", "高风险安全", "安全确认策略")),
    ("preference", ("工具偏好", "回答风格偏好", "风格偏好", "偏好")),
    ("knowledge", ("修复流程的知识", "知识", "流程")),
    ("tool_trace", ("工具轨迹", "工具执行", "工具结果")),
]

SENSITIVE_LABELS = [
    ("API Key", "api_key"),
    ("api key", "api_key"),
    ("Token", "token"),
    ("token", "token"),
    ("密码", "password"),
    ("私钥", "private_key"),
    ("身份证", "id_card_cn"),
    ("手机号", "phone_cn"),
    ("邮箱", "email"),
    ("姓名", "person_name_cn"),
]

PREFERENCE_TOPIC_ALIASES = {
    "high_risk_confirmation": (
        "高风险命令",
        "高风险操作",
        "危险操作",
        "二次确认",
        "操作确认",
        "安全确认",
        "高风险安全策略",
    ),
    "code_search_tool": ("查代码", "代码搜索", "rg", "grep", "ripgrep"),
    "response_order": ("先给命令", "先给结论", "先命令后解释", "回答顺序"),
    "response_language": ("中文回答", "用中文", "中文"),
    "response_verbosity": ("简洁回答", "简短", "少废话"),
    "test_runner_command": ("pytest", "测试命令", "单元测试"),
}


@dataclass
class ErasureIntentCompiler:
    now: Optional[datetime] = None

    def compile(self, request: str, context: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
        context = dict(context or {})
        text = " ".join(str(request or "").strip().split())
        current_time = self._now()
        has_forget_verb = any(verb in text for verb in FORGET_VERBS)
        memory_types = self._memory_types(text)
        sensitive_labels = self._sensitive_labels(text)
        time_range = self._time_range(text, current_time)
        scope = self._scope(text, context)
        agent_name = self._agent_name(text, context)
        topic = self._topic(text, memory_types)
        target_phrase = self._target_phrase(text, topic)
        preference_topic = self._preference_topic(text, topic)
        normalized_preference_topic = self._normalized_preference_topic(text, preference_topic)
        topic_aliases = self._topic_aliases(text, topic, normalized_preference_topic or preference_topic)
        project_hint = self._project_hint(text, context, topic)
        unresolved_slots = []
        if not has_forget_verb:
            unresolved_slots.append("forget_verb")
        if not topic and not sensitive_labels and memory_types == ["all"] and scope["level"] == "global":
            unresolved_slots.append("scope_or_topic")

        confidence = 0.34
        if has_forget_verb:
            confidence += 0.22
        if topic:
            confidence += 0.16
        if memory_types != ["all"]:
            confidence += 0.12
        if scope["level"] != "global":
            confidence += 0.10
        if time_range["source_text"]:
            confidence += 0.08
        if sensitive_labels:
            confidence += 0.08
        confidence = min(0.96, round(confidence, 2))
        if unresolved_slots:
            confidence = min(confidence, 0.46)

        return {
            "schema": "memguardos-erasure-intent-v1",
            "intent_type": "precise_forgetting",
            "source_text": text,
            "execution_mode": "dry_run",
            "memory_types": memory_types,
            "topic": topic,
            "target_phrase": target_phrase,
            "topic_aliases": topic_aliases,
            "normalized_preference_topic": normalized_preference_topic,
            "agent_name": agent_name,
            "session_id": str(context.get("session_id") or ""),
            "project_hint": project_hint,
            "scope": scope,
            "time_range": time_range,
            "sensitive_labels": sensitive_labels,
            "preference_topic": preference_topic,
            "retention_constraints": self._retention_constraints(text, topic),
            "requires_confirm": True,
            "confidence": confidence,
            "unresolved_slots": unresolved_slots,
            "safety_note": "dry-run first; confirmation required before irreversible compact within MemGuardOS-managed storage",
        }

    def to_agent_filters(self, intent: Dict[str, Any]) -> Dict[str, str]:
        filters: Dict[str, str] = {}
        scope = intent.get("scope") or {}
        if intent.get("agent_name"):
            filters["agent_name"] = str(intent["agent_name"])
        if scope.get("session_id"):
            filters["session_id"] = str(scope["session_id"])
        elif intent.get("session_id") and scope.get("level") in {"session", "agent"}:
            filters["session_id"] = str(intent["session_id"])
        if intent.get("preference_topic"):
            filters["preference_topic"] = str(intent["preference_topic"])
        time_range = intent.get("time_range") or {}
        if time_range.get("start_time"):
            filters["start_time"] = str(time_range["start_time"])
        if time_range.get("end_time"):
            filters["end_time"] = str(time_range["end_time"])
        return filters

    def to_forget_request(self, intent: Dict[str, Any]) -> str:
        topic = str(intent.get("topic") or intent.get("preference_topic") or "").strip()
        aliases = [str(alias) for alias in intent.get("topic_aliases") or [] if str(alias).strip()]
        labels = " ".join(str(label) for label in intent.get("sensitive_labels") or [])
        text = " ".join(part for part in [topic, " ".join(aliases), labels] if part)
        return "忘掉 %s" % text if text else str(intent.get("source_text") or "忘掉目标记忆")

    def _now(self) -> datetime:
        value = self.now or datetime.now(timezone.utc)
        if value.tzinfo is None:
            return value.replace(tzinfo=timezone.utc)
        return value.astimezone(timezone.utc)

    @staticmethod
    def _memory_types(text: str) -> List[str]:
        target_text, _ = _split_retention_clause(text)
        selected: List[str] = []
        for memory_type, markers in MEMORY_TYPE_PATTERNS:
            if any(marker in target_text for marker in markers):
                selected.append(memory_type)
        if "工具偏好" in target_text and "preference" not in selected:
            selected.append("preference")
        if "all" in selected and len(selected) > 1:
            selected.remove("all")
        return selected or ["all"]

    @staticmethod
    def _sensitive_labels(text: str) -> List[str]:
        located: List[tuple[int, str]] = []
        lowered = text.lower()
        for marker, label in SENSITIVE_LABELS:
            haystack = lowered if marker.lower() == marker else text
            needle = marker.lower() if marker.lower() == marker else marker
            pos = haystack.find(needle)
            if pos >= 0:
                located.append((pos, label))
        labels: List[str] = []
        for _, label in sorted(located, key=lambda item: item[0]):
            if label not in labels:
                labels.append(label)
        return labels

    @staticmethod
    def _scope(text: str, context: Dict[str, Any]) -> Dict[str, str]:
        agent_name = str(context.get("agent_name") or "")
        session_id = str(context.get("session_id") or "")
        project_root = str(context.get("project_root") or "")
        explicit_session = re.search(r"(?:会话|session)\s*([A-Za-z0-9_.:-]{2,64})", text, flags=re.IGNORECASE)
        if explicit_session:
            return {"level": "session", "session_id": explicit_session.group(1), "agent_name": agent_name}
        if "当前 Agent" in text or "当前Agent" in text or "当前智能体" in text:
            return {"level": "agent", "agent_name": agent_name, "session_id": session_id}
        if "当前会话" in text or "本轮任务" in text:
            return {"level": "session", "session_id": session_id, "agent_name": agent_name}
        if "这个项目" in text or "当前项目" in text or "项目里" in text:
            return {"level": "project", "project_root": project_root}
        return {"level": "global"}

    @staticmethod
    def _agent_name(text: str, context: Dict[str, Any]) -> str:
        if "OpenHands" in text or "openhands" in text.lower():
            return "OpenHands"
        return str(context.get("agent_name") or "")

    @staticmethod
    def _topic(text: str, memory_types: List[str]) -> str:
        match = re.search(
            r"关于\s*([^，。,；;]+?)(?:的)?"
            r"(?:(?:所有|全部|若干|[一二两三四五六七八九十\d]+)条)?"
            r"(?:所有|全部|工具偏好|偏好|安全策略|策略|知识|记忆|$)",
            text,
        )
        if match:
            topic = match.group(1).strip()
            topic = re.sub(
                r"\s*的\s*(?:所有|全部|若干|[一二两三四五六七八九十\d]+)条?$",
                "",
                topic,
            ).strip()
            return topic
        project_match = re.search(r"忘(?:记|掉)?\s*([^，。,；;]+?项目)的偏好", text)
        if project_match:
            return project_match.group(1).strip()
        if (
            "高风险命令" in text
            or "高风险操作" in text
            or "危险操作" in text
            or "二次确认" in text
            or "操作确认" in text
        ):
            return "高风险命令需要二次确认"
        if "高风险安全策略" in text:
            return "高风险安全策略"
        if "安全确认策略" in text:
            return "安全确认策略"
        if "回答风格" in text:
            return "回答风格"
        if "systemd 修复流程" in text:
            return "systemd 修复流程"
        if "pytest" in text:
            return "pytest"
        if "API Key" in text or "api key" in text.lower():
            return "API Key"
        if memory_types == ["all"]:
            return ""
        return ""

    @staticmethod
    def _target_phrase(text: str, topic: str) -> str:
        target, _ = _split_retention_clause(text)
        target = target.strip(" ，。,；;")
        target = re.sub(r"^(?:请\s*)?(?:帮我\s*)?(?:把\s*)?(?:忘记|忘掉|遗忘|删除|清除)\s*", "", target)
        if "关于" in target:
            target = target.split("关于", 1)[1].strip()
        target = re.sub(
            r"\s*(?:的)?"
            r"(?:(?:所有|全部|若干|[一二两三四五六七八九十\d]+)条)?"
            r"(?:所有|全部)?(?:记忆|这条知识|知识)$",
            "",
            target,
        ).strip(" ，。,；;")
        if topic and topic.lower() in target.lower():
            return topic
        return target or topic

    @staticmethod
    def _preference_topic(text: str, topic: str) -> str:
        target_text, _ = _split_retention_clause(text)
        lowered = target_text.lower()
        if "回答风格" in target_text:
            return "回答风格"
        if "pytest" in lowered and "偏好" in target_text:
            return "pytest"
        if (
            "rg" in lowered
            and "工具偏好" in target_text
            and "代码搜索" not in target_text
            and "查代码" not in target_text
        ):
            return "rg"
        if (
            "高风险命令" in target_text
            or "高风险操作" in target_text
            or "危险操作" in target_text
            or "二次确认" in target_text
            or "操作确认" in target_text
        ):
            return "high_risk_confirmation"
        if "代码搜索" in target_text or "查代码" in target_text:
            return "code_search_tool"
        if "先给命令" in target_text or "先给结论" in target_text or "先命令后解释" in target_text:
            return "response_order"
        if "中文回答" in target_text or "用中文" in target_text:
            return "response_language"
        if "简洁回答" in target_text or "简短" in target_text or "少废话" in target_text:
            return "response_verbosity"
        if "测试命令" in target_text or "单元测试" in target_text:
            return "test_runner_command"
        if "偏好" in target_text:
            return topic
        return ""

    @staticmethod
    def _normalized_preference_topic(text: str, preference_topic: str) -> str:
        if preference_topic in PREFERENCE_TOPIC_ALIASES:
            return preference_topic
        if not preference_topic:
            return ""
        target_text, _ = _split_retention_clause(text)
        lowered = target_text.lower()
        if preference_topic == "回答风格":
            return "response_order"
        if preference_topic == "pytest":
            return "test_runner_command"
        if preference_topic == "rg":
            return "code_search_tool"
        for canonical, aliases in PREFERENCE_TOPIC_ALIASES.items():
            if any(alias.lower() in lowered for alias in aliases):
                return canonical
        return ""

    @staticmethod
    def _topic_aliases(text: str, topic: str, preference_topic: str) -> List[str]:
        aliases: List[str] = []
        if topic:
            aliases.append(topic)
        if preference_topic and preference_topic in PREFERENCE_TOPIC_ALIASES:
            aliases.extend(PREFERENCE_TOPIC_ALIASES[preference_topic])
        lowered = text.lower()
        for canonical, values in PREFERENCE_TOPIC_ALIASES.items():
            if canonical == preference_topic:
                continue
            if any(value.lower() in lowered for value in values):
                aliases.extend(values)
        unique: List[str] = []
        for alias in aliases:
            if alias and alias not in unique:
                unique.append(alias)
        return unique

    @staticmethod
    def _project_hint(text: str, context: Dict[str, Any], topic: str) -> str:
        match = re.search(r"([A-Za-z0-9_\-\u4e00-\u9fff]+)\s*项目", topic or text)
        if match:
            return match.group(1)
        return str(context.get("project_hint") or "")

    @staticmethod
    def _time_range(text: str, now: datetime) -> Dict[str, str]:
        start: Optional[datetime] = None
        end: Optional[datetime] = None
        source = ""
        today = datetime(now.year, now.month, now.day, tzinfo=timezone.utc)
        explicit = re.search(
            r"(20\d{2}-\d{1,2}-\d{1,2})\s*(?:到|至|-|~)\s*(20\d{2}-\d{1,2}-\d{1,2})",
            text,
        )
        recent_days = re.search(r"(?:最近|近|过去)\s*(\d{1,2})\s*天", text)
        if explicit:
            source = "%s 到 %s" % (explicit.group(1), explicit.group(2))
            start = ErasureIntentCompiler._parse_date(explicit.group(1))
            end = ErasureIntentCompiler._parse_date(explicit.group(2)) + timedelta(days=1)
        elif recent_days:
            days = max(1, min(90, int(recent_days.group(1))))
            source = "近%s天" % days
            start = now - timedelta(days=days)
            end = now
        elif "昨天" in text:
            source = "昨天"
            start = today - timedelta(days=1)
            end = today
        elif "今天" in text:
            source = "今天"
            start = today
            end = today + timedelta(days=1)
        elif "上周" in text:
            source = "上周"
            this_week_start = today - timedelta(days=today.weekday())
            start = this_week_start - timedelta(days=7)
            end = this_week_start
        elif "本周" in text:
            source = "本周"
            start = today - timedelta(days=today.weekday())
            end = start + timedelta(days=7)
        elif "最近三天" in text:
            source = "最近三天"
            start = now - timedelta(days=3)
            end = now
        elif "最近一周" in text:
            source = "最近一周"
            start = now - timedelta(days=7)
            end = now
        return {
            "start_time": start.isoformat() if start else "",
            "end_time": end.isoformat() if end else "",
            "source_text": source,
        }

    @staticmethod
    def _parse_date(value: str) -> datetime:
        year, month, day = [int(part) for part in value.split("-")]
        return datetime(year, month, day, tzinfo=timezone.utc)

    @staticmethod
    def _retention_constraints(text: str, topic: str) -> List[Dict[str, Any]]:
        _, keep_text = _split_retention_clause(text)
        if not keep_text:
            return []
        constraints: List[Dict[str, Any]] = []
        if "知识" in keep_text:
            constraints.append({"memory_types": ["knowledge"], "topic": topic})
        if "今天" in keep_text:
            constraints.append({"time_range": "今天"})
        if "其他 Agent" in keep_text or "其他Agent" in keep_text:
            constraints.append({"scope": "other_agents"})
        if "安全确认策略" in keep_text:
            constraints.append({"memory_types": ["security_policy"], "topic": "安全确认策略"})
        return constraints


def _split_retention_clause(text: str) -> tuple[str, str]:
    match = re.search(
        r"(?:[，,；;]|\s+(?:但是|但))\s*(?:但是|但)?\s*保留",
        text,
    )
    if not match:
        return text, ""
    return text[: match.start()], text[match.end() :]
