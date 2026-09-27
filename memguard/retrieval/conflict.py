from typing import Dict, Iterable, List

from memguard.retrieval.hybrid import tokenize


SOURCE_PRIORITY = {
    "update": 6,
    "manual": 5,
    "rollback": 4,
    "benchmark": 3,
    "tool_result": 3,
    "os_event": 2,
    "inferred": 1,
}


def preference_topic(record: Dict[str, object]) -> str:
    text = "%s %s" % (record.get("summary", ""), record.get("content", ""))
    lowered_text = text.lower()
    if any(marker in lowered_text for marker in ["rg", "grep", "ripgrep", "代码", "搜索", "查找"]):
        return "code_search_tool"
    if any(marker in lowered_text for marker in ["删除", "覆盖", "密钥", "导出", "高风险", "确认"]):
        return "high_risk_confirmation"
    if any(marker in lowered_text for marker in ["linux", "命令", "command", "shell"]):
        return "linux_command_response"
    if any(marker in lowered_text for marker in ["中文", "英文", "language", "语言"]):
        return "response_language"
    if any(marker in lowered_text for marker in ["简洁", "详细", "背景", "最短", "风险提醒"]):
        return "response_verbosity"
    tokens = [token for token in tokenize(text) if len(token) > 1]
    markers = []
    for token in tokens:
        lowered = token.lower()
        if lowered in {"linux", "命令", "回答", "背景", "详细", "简洁", "brief", "style", "preference", "rg", "grep"}:
            markers.append(lowered)
    if markers:
        return "|".join(sorted(set(markers))[:4])
    return "|".join(sorted(set(tokens))[:3]) or str(record.get("memory_id", "unknown"))


class ConflictResolver:
    def resolve_preferences(self, records: Iterable[Dict[str, object]]) -> Dict[str, object]:
        groups: Dict[tuple, List[Dict[str, object]]] = {}
        for record in records:
            if record.get("type") not in {"preference", "sensitive_policy"}:
                continue
            key = (
                preference_topic(record),
                str(record.get("preference_scope") or "global"),
                str(record.get("agent_name") or ""),
                str(record.get("project_root") or ""),
                str(record.get("task_type") or ""),
                str(record.get("preference_session_id") or ""),
                str(record.get("retention_session_id") or ""),
            )
            groups.setdefault(key, []).append(record)

        resolutions = []
        for key, items in groups.items():
            if len(items) < 2:
                continue
            chosen = sorted(items, key=self._sort_key, reverse=True)[0]
            resolutions.append(
                {
                    "topic": key[0],
                    "chosen_memory_id": chosen["memory_id"],
                    "candidate_memory_ids": [item["memory_id"] for item in items],
                    "reason": "highest source priority, then latest updated_at",
                }
            )
        return {"conflict_groups": len(resolutions), "resolutions": resolutions}

    @staticmethod
    def _sort_key(record: Dict[str, object]) -> tuple:
        source_kind = str(record.get("source_kind", ""))
        return (SOURCE_PRIORITY.get(source_kind, 0), str(record.get("updated_at", "")))
