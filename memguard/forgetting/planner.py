from typing import Dict, Iterable, List, Optional, Set
import re

from memguard.forgetting.natural_intent import ErasureIntentCompiler
from memguard.retrieval.hybrid import HybridRetriever, tokenize


STOPWORDS = {
    "忘掉",
    "忘记",
    "删除",
    "清除",
    "所有",
    "全部",
    "关于",
    "但是",
    "保留",
    "的",
    "我",
    "你",
    "请",
    "把",
    "和",
    "或",
    "这条",
    "知识",
}

# 遗忘请求里普遍出现、不具区分度的词，不作为锚点参与覆盖率判定。
ANCHOR_STOPWORDS = {
    "and", "the", "for", "with", "from", "into", "all", "any", "please",
    "agent", "memory", "memories", "project", "session", "forget", "delete",
    "remove", "erase", "about", "related",
    "记忆", "遗忘", "忘掉", "忘记", "删除", "清除", "所有", "相关", "关于",
    "以及", "还有", "这些", "那些", "内容", "信息", "数据", "系统", "用户",
}

# 候选相对分数下限（偏好定向 / 有目标短语 / 纯关键词）。
# 只删"与最佳匹配同等接近"的记忆，是控制过度遗忘的主要杠杆；
# 数值由 data/benchmarks/forgetting.jsonl 上的扫描确定，不针对任何单条请求。
CANDIDATE_SCORE_FLOORS = (0.45, 0.75, 0.85)

GENERIC_MATCH_TERMS = {
    "回答",
    "偏好",
    "preference",
    "知识",
    "安全",
    "策略",
    "policy",
    "命令",
    "sdk",
    "记忆",
    "信息",
}

WEAK_MATCH_TERMS = GENERIC_MATCH_TERMS | {
    "服务",
    "知识",
    "流程",
}


class ForgetPlanner:
    def __init__(self):
        self.retriever = HybridRetriever()
        self.intent_compiler = ErasureIntentCompiler()

    def keywords(self, request: str) -> List[str]:
        rough = tokenize(request)
        words = []
        for token in rough:
            if token in STOPWORDS:
                continue
            if len(token.strip()) == 0:
                continue
            words.append(token)
        # Preserve longer Latin terms such as HuggingFace and API names.
        words.extend(re.findall(r"[A-Za-z][A-Za-z0-9_\-]{2,}", request))
        unique = []
        for word in words:
            lowered = word.lower()
            if lowered not in unique:
                unique.append(lowered)
        return unique[:12]

    def candidates(
        self,
        request: str,
        records: Iterable[Dict[str, str]],
        limit: int = 20,
        intent: Optional[Dict[str, object]] = None,
    ) -> List[Dict[str, object]]:
        # A caller on the destructive path passes the already compiled intent.
        # Recompiling generated text here would discard time/scope constraints.
        intent = dict(intent) if intent is not None else self.intent_compiler.compile(request)
        aliases = [str(alias).lower() for alias in intent.get("topic_aliases") or [] if str(alias).strip()]
        preference_topic = str(intent.get("normalized_preference_topic") or intent.get("preference_topic") or "")
        target_phrase = str(intent.get("target_phrase") or "")
        search_basis = " ".join(
            part
            for part in [
                _target_request_text(request),
                target_phrase,
                str(intent.get("topic") or ""),
                " ".join(aliases),
                preference_topic,
                " ".join(str(item) for item in intent.get("sensitive_labels") or []),
            ]
            if part
        )
        keywords = self.keywords(search_basis or request)
        distinctive_keywords = [keyword for keyword in keywords if keyword not in GENERIC_MATCH_TERMS]
        corpus = list(records)
        # A destructive "all" request must inspect the entire scoped corpus.
        # The UI may page the preview, but ranking must never silently cap it.
        ranked = self.retriever.rank(" ".join(keywords) or request, corpus, limit=len(corpus))
        ranked_ids = {str(record.get("memory_id") or "") for _, record in ranked}
        if preference_topic:
            for record in corpus:
                memory_id = str(record.get("memory_id") or "")
                if (
                    memory_id
                    and memory_id not in ranked_ids
                    and preference_topic == str(record.get("preference_topic") or "")
                ):
                    # Structured topic identity is stronger than lexical
                    # similarity. Keep it in the candidate pool even when the
                    # user's Chinese label does not occur in the stored text.
                    ranked.append((1.0, record))
                    ranked_ids.add(memory_id)
        if not ranked:
            return []
        # 锚点判别度按全语料的文档频次计算；只统计已排序的 top-N 会因查询相关而有偏。
        anchor_df, corpus_size = _anchor_document_frequency(distinctive_keywords, corpus)
        results = []
        for score, record in ranked:
            if score < 0.18:
                continue
            if not _record_type_matches(intent, record):
                continue
            text = "%s %s %s %s" % (
                record.get("memory_id", ""),
                record.get("type", ""),
                record.get("summary", ""),
                record.get("content", ""),
            )
            if not _required_qualifiers_match(request, text):
                continue
            if not _sensitive_labels_match(intent, text):
                continue
            matched = [keyword for keyword in keywords if keyword and keyword.lower() in text.lower()]
            matched_distinctive = [keyword for keyword in distinctive_keywords if keyword and keyword.lower() in text.lower()]
            preference_topic_match = bool(preference_topic) and (
                preference_topic == str(record.get("preference_topic") or "")
                or preference_topic in text.lower()
                or _preference_topic_matches(preference_topic, text)
            )
            matched_aliases: List[str] = []
            if aliases:
                matched_aliases = [
                    alias for alias in aliases if alias and alias in text.lower()
                ]
                matched_distinctive.extend(
                    alias
                    for alias in matched_aliases
                    if alias not in matched_distinctive
                )
                if not matched_aliases and not preference_topic_match and not _alias_fallback_passes(
                    intent,
                    target_phrase,
                    text,
                ):
                    continue
            if not matched and not matched_aliases and not preference_topic_match:
                continue
            if preference_topic_match:
                matched_distinctive.append(preference_topic)
            if distinctive_keywords and not matched_distinctive:
                continue
            strong_matches = [keyword for keyword in matched_distinctive if keyword not in WEAK_MATCH_TERMS and len(keyword) >= 2]
            if not _anchor_policy_passes(
                distinctive_keywords, matched_distinctive, strong_matches, aliases, anchor_df, corpus_size
            ):
                continue
            if _violates_retention_constraints(intent, record, text):
                continue
            results.append(
                {
                    "memory_id": record["memory_id"],
                    "version_id": record.get("version_id", ""),
                    "score": round(score + (0.35 if preference_topic_match else 0.0), 4),
                    "matched_keywords": list(dict.fromkeys(matched + matched_aliases)),
                    "matched_distinctive_keywords": matched_distinctive,
                    "preference_topic_match": preference_topic_match,
                    "summary": record.get("summary", ""),
                    "type": record.get("type", ""),
                    "subtype": record.get("subtype", ""),
                    "preference_topic": record.get("preference_topic", ""),
                    "preference_value": record.get("preference_value", ""),
                    "exact_topic_match": bool(
                        intent.get("topic")
                        and str(intent["topic"]).casefold() in text.casefold()
                    ),
                }
            )
        exhaustive_topic = bool(
            set(intent.get("memory_types") or []) == {"all"}
            and intent.get("topic")
            and re.search(r"所有|全部", request)
        )
        if exhaustive_topic:
            seen = {str(item["memory_id"]) for item in results}
            exact_topic = str(intent["topic"]).casefold()
            for record in corpus:
                memory_id = str(record.get("memory_id") or "")
                if not memory_id or memory_id in seen or not _record_type_matches(intent, record):
                    continue
                text = " ".join(str(record.get(key) or "") for key in ("summary", "content"))
                if exact_topic not in text.casefold():
                    continue
                if not _required_qualifiers_match(request, text):
                    continue
                if not _sensitive_labels_match(intent, text):
                    continue
                if _violates_retention_constraints(intent, record, text):
                    continue
                results.append({
                    "memory_id": memory_id,
                    "version_id": record.get("version_id", ""),
                    "score": 1.0,
                    "matched_keywords": [str(intent["topic"])],
                    "matched_distinctive_keywords": [str(intent["topic"])],
                    "preference_topic_match": False,
                    "summary": record.get("summary", ""),
                    "type": record.get("type", ""),
                    "subtype": record.get("subtype", ""),
                    "preference_topic": record.get("preference_topic", ""),
                    "preference_value": record.get("preference_value", ""),
                    "exact_topic_match": True,
                })
                seen.add(memory_id)
        if not results:
            return []
        top_score = max(float(item["score"]) for item in results)
        preference_floor, phrase_floor, plain_floor = CANDIDATE_SCORE_FLOORS
        relative_floor = preference_floor if preference_topic else phrase_floor if target_phrase else plain_floor
        score_floor = max(0.18, top_score * relative_floor)
        return [
            item for item in results
            if float(item["score"]) >= score_floor
            or (exhaustive_topic and item.get("exact_topic_match"))
        ]


def _target_request_text(request: str) -> str:
    """Remove protected clauses while retaining useful target context."""

    text = re.sub(
        r"^\s*除了\s*.+?\s*(?:以外|之外)\s*[，,；;]?\s*",
        "",
        str(request or ""),
    )
    text = re.split(
        r"(?:[，,；;]|\s+(?:但是|但))\s*(?:但是|但)?\s*保留",
        text,
        maxsplit=1,
    )[0]
    return text.strip()


def _record_type_matches(intent: Dict[str, object], record: Dict[str, str]) -> bool:
    requested = {str(item) for item in intent.get("memory_types") or []}
    record_type = str(record.get("type") or "")
    subtype = str(record.get("subtype") or "")
    preference_topic = str(intent.get("normalized_preference_topic") or intent.get("preference_topic") or "")
    if preference_topic and requested in (set(), {"all"}):
        return record_type in {"preference", "sensitive_policy"}
    if not requested or "all" in requested:
        return True
    for memory_type in requested:
        if memory_type == "preference" and record_type in {"preference", "sensitive_policy"}:
            return True
        if memory_type == "security_policy" and record_type in {"preference", "sensitive_policy"}:
            return True
        if memory_type == "knowledge" and record_type == "knowledge":
            return True
        if (
            memory_type == "tool_trace"
            and record_type == "knowledge"
            and subtype in {"tool_trace", "procedural_trace", "tool_failure_case"}
        ):
            return True
    return False


def _required_qualifiers_match(request: str, text: str) -> bool:
    for qualifier in ("模板", "案例", "原始日志"):
        if qualifier in request and qualifier not in text:
            return False
    polarity_markers = ("不等于", "不能替代", "并非", "不是")
    if any(marker in request for marker in polarity_markers) and not any(
        marker in text for marker in polarity_markers
    ):
        return False
    request_lower = request.lower()
    text_lower = text.lower()
    command_patterns = {
        "rm_rf": r"\brm\s*-?\s*rf\b",
        "mkfs": r"\bmkfs\b",
        "dd": r"\bdd\b",
    }
    requested_commands = {
        name
        for name, pattern in command_patterns.items()
        if re.search(pattern, request_lower)
    }
    if requested_commands and not all(
        re.search(command_patterns[name], text_lower)
        for name in requested_commands
    ):
        return False
    return True


def _sensitive_labels_match(intent: Dict[str, object], text: str) -> bool:
    labels = {str(item) for item in intent.get("sensitive_labels") or []}
    if not labels:
        return True
    lowered = text.lower()
    marker_groups = {
        "api_key": ("api key", "apikey", "api_key"),
        "token": ("token",),
        "password": ("密码", "password", "passwd"),
        "private_key": ("私钥", "private key"),
        "id_card_cn": ("身份证",),
        "phone_cn": ("手机号", "手机号码"),
        "email": ("邮箱", "email"),
        "person_name_cn": ("姓名",),
    }
    return any(any(marker in lowered for marker in marker_groups.get(label, (label,))) for label in labels)


def _alias_fallback_passes(intent: Dict[str, object], target_phrase: str, text: str) -> bool:
    if intent.get("sensitive_labels") and _sensitive_labels_match(intent, text):
        return True
    requested_terms = {
        term.lower()
        for term in re.findall(r"[A-Za-z][A-Za-z0-9_\-]{2,}", target_phrase)
        if term.lower() not in {"agent", "memory", "project", "session"}
    }
    if not requested_terms:
        return False
    matched_terms = {term for term in requested_terms if term in text.lower()}
    required = max(1, int(len(requested_terms) * 0.6 + 0.999))
    return len(matched_terms) >= required


def _preference_topic_matches(preference_topic: str, text: str) -> bool:
    lowered = text.lower()
    mapping = {
        "high_risk_confirmation": ["高风险命令", "危险操作", "二次确认", "安全策略"],
        "code_search_tool": ["代码搜索", "查代码", " rg", "grep", "ripgrep"],
        "response_order": ["先给命令", "先给结论", "回答顺序", "输出顺序", "回复顺序"],
        "response_language": ["中文回答", "英文回答", "用中文", "使用中文", "使用英文", "回答语言", "回复语言"],
        "response_verbosity": ["简洁回答", "详细回答", "简短", "回答长度", "详细程度"],
        "test_runner_command": ["pytest", "测试命令", "单元测试", "测试工具"],
        "service_log_tool": ["journalctl", "服务日志", "日志工具", "日志命令"],
    }
    return any(marker.lower() in lowered for marker in mapping.get(preference_topic, []))


def _violates_retention_constraints(intent: Dict[str, object], record: Dict[str, str], text: str) -> bool:
    """A record is protected only when it matches every dimension of a constraint.

    Disjunctive matching would make "忘记 X 的偏好，但保留 X 的知识" protect
    everything mentioning X — including the preference the user explicitly asked
    to delete — silently turning the whole request into a no-op.
    """

    lowered = text.lower()
    for constraint in intent.get("retention_constraints") or []:
        if not isinstance(constraint, dict):
            continue
        conditions = []
        memory_types = {str(item) for item in constraint.get("memory_types") or []}
        if memory_types:
            conditions.append(_record_type_in(record, memory_types))
        if constraint.get("scope") == "other_agents":
            conditions.append("其他 agent" in lowered or "其他agent" in lowered)
        topic = str(constraint.get("topic") or "")
        if topic:
            conditions.append(topic in text)
        terms = [str(term) for term in constraint.get("terms") or [] if str(term).strip()]
        if terms:
            conditions.append(any(term.lower() in lowered for term in terms))
        if conditions and all(conditions):
            return True
    return False


def _record_type_in(record: Dict[str, str], memory_types: Set[str]) -> bool:
    record_type = str(record.get("type") or "")
    subtype = str(record.get("subtype") or "")
    for memory_type in memory_types:
        if memory_type == record_type:
            return True
        if memory_type in {"preference", "security_policy"} and record_type in {"preference", "sensitive_policy"}:
            return True
        if memory_type == "knowledge" and record_type == "knowledge":
            return True
        if memory_type == "tool_trace" and subtype in {"tool_trace", "procedural_trace", "tool_failure_case"}:
            return True
    return False


def _anchor_document_frequency(
    keywords: List[str],
    corpus: Iterable[Dict[str, str]],
) -> tuple:
    """Document frequency of each keyword across the whole managed corpus."""

    texts = []
    for record in corpus:
        texts.append(
            ("%s %s %s" % (
                record.get("summary", ""),
                record.get("content", ""),
                record.get("preference_topic", ""),
            )).lower()
        )
    frequency = {}
    for keyword in keywords:
        lowered = str(keyword or "").lower()
        if not lowered:
            continue
        frequency[lowered] = sum(1 for text in texts if lowered in text)
    return frequency, len(texts)


def _anchor_policy_passes(
    distinctive_keywords: List[str],
    matched_distinctive: List[str],
    strong_matches: List[str],
    aliases: List[str],
    anchor_df: Dict[str, int],
    corpus_size: int,
) -> bool:
    """Require the request's most discriminative anchor to be matched.

    Never inspect benchmark-specific literals: a rule that recognises particular
    evaluation requests makes forgetting precision/recall self-fulfilling.
    Discriminativeness is derived from the candidate corpus at run time — the
    rarer a term is across candidates, the more it defines what this request
    is actually about.
    """

    anchors = {
        keyword.lower()
        for keyword in distinctive_keywords
        if keyword and keyword.lower() not in ANCHOR_STOPWORDS
    }
    matched = {str(item).lower() for item in matched_distinctive if item}
    if not anchors:
        return bool(strong_matches) if matched_distinctive else True
    if not matched:
        return False

    alias_pool = {str(alias).lower() for alias in aliases if str(alias).strip()}
    covered = {
        anchor
        for anchor in anchors
        if _anchor_is_covered(anchor, matched, alias_pool)
    }
    if not covered:
        return False

    # 判别度阈值：出现在超过一半候选里的词太普通，不能独自代表遗忘目标。
    common_ceiling = max(1, corpus_size // 2)
    discriminative = {
        anchor for anchor in anchors if anchor_df.get(anchor, corpus_size) <= common_ceiling
    }
    if discriminative and not (covered & discriminative):
        return False
    return bool(strong_matches) or bool(covered - WEAK_MATCH_TERMS)


def _anchor_is_covered(anchor: str, matched: Set[str], alias_pool: Set[str]) -> bool:
    """Covered when a matched keyword overlaps the anchor, or an alias bridges it.

    The alias bridge is what lets a Chinese record satisfy an English anchor
    (and vice versa) without hardcoding either side.
    """

    if anchor in matched:
        return True
    if any(keyword and (anchor in keyword or keyword in anchor) for keyword in matched):
        return True
    if anchor in alias_pool and matched & alias_pool:
        return True
    return False
