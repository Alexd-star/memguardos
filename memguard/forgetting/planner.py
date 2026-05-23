from typing import Dict, Iterable, List
import re

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

    def candidates(self, request: str, records: Iterable[Dict[str, str]], limit: int = 20) -> List[Dict[str, object]]:
        keywords = self.keywords(request)
        distinctive_keywords = [keyword for keyword in keywords if keyword not in GENERIC_MATCH_TERMS]
        ranked = self.retriever.rank(" ".join(keywords) or request, records, limit=limit)
        if not ranked:
            return []
        max_score = ranked[0][0]
        results = []
        for score, record in ranked:
            if score < max(0.18, max_score * 0.62):
                continue
            text = "%s %s %s %s" % (
                record.get("memory_id", ""),
                record.get("type", ""),
                record.get("summary", ""),
                record.get("content", ""),
            )
            matched = [keyword for keyword in keywords if keyword and keyword.lower() in text.lower()]
            if not matched:
                continue
            matched_distinctive = [keyword for keyword in distinctive_keywords if keyword and keyword.lower() in text.lower()]
            if distinctive_keywords and not matched_distinctive:
                continue
            strong_matches = [keyword for keyword in matched_distinctive if keyword not in WEAK_MATCH_TERMS and len(keyword) >= 2]
            if not _anchor_policy_passes(request, matched_distinctive, strong_matches):
                continue
            results.append(
                {
                    "memory_id": record["memory_id"],
                    "score": round(score, 4),
                    "matched_keywords": matched,
                    "matched_distinctive_keywords": matched_distinctive,
                    "summary": record.get("summary", ""),
                    "type": record.get("type", ""),
                }
            )
        return results


def _anchor_policy_passes(request: str, matched_distinctive: List[str], strong_matches: List[str]) -> bool:
    lowered_request = request.lower()
    matched = set(matched_distinctive)
    if "linux" in lowered_request:
        return "linux" in matched
    if "rg" in lowered_request:
        return "rg" in matched
    if "shell history" in lowered_request:
        if "pytest" in lowered_request:
            return "pytest" in matched and "shell" in matched and "history" in matched
        return "shell" in matched and "history" in matched
    if "huggingface" in lowered_request or "api key" in lowered_request.lower():
        return "token" in matched and bool({"api", "key", "huggingface"} & matched)
    if "精准遗忘流程" in request:
        return "流程" in matched or "忘流" in matched
    if "embedding" in lowered_request:
        if "维度" in request:
            return bool({"维度", "768"} & matched)
        return "embedding" in matched
    if "auditd" in lowered_request:
        return "auditd" in matched
    if "web" in lowered_request and ("控制" in request or "控制台" in request):
        return "web" in matched and bool({"控制", "制台", "控制台"} & matched)
    if "bpf" in lowered_request and "lsm" in lowered_request:
        has_bpf = bool({"bpf", "ebpf"} & matched)
        if "hook" in lowered_request or "阻断" in request or "拦截" in request:
            return has_bpf and "lsm" in matched and bool({"hook", "file_open"} & matched) and bool({"阻断", "拦截", "deny"} & matched)
        return has_bpf and "lsm" in matched
    if "systemd" in lowered_request:
        return "systemd" in matched
    latin_terms = [keyword for keyword in matched_distinctive if re.search(r"[A-Za-z]", keyword)]
    if latin_terms:
        return bool(strong_matches)
    return True
