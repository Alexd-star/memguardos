from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass
from typing import Iterable
import shlex

from memguard.ingestion.tool_result import ToolResult, summarize_stream


DOMAIN_LABELS = {
    "code_search": "代码搜索",
    "file_search": "文件查找",
    "test_runner": "测试验证",
    "service_management": "服务管理",
    "general_shell": "命令执行",
}


@dataclass
class ToolPreferenceCandidate:
    domain: str
    preferred_tool: str
    success_count: int
    failure_count: int
    avoid_tools: list[str]
    avg_duration_ms: float
    evidence: list[str]

    def to_memory_text(self) -> str:
        domain_label = DOMAIN_LABELS.get(self.domain, self.domain)
        avoid = "、".join(self.avoid_tools) if self.avoid_tools else "无明确失败工具"
        evidence = "；".join(self.evidence[:4])
        return (
            "以后进行%s时优先使用 %s。"
            "工具结果统计显示 preferred_tool=%s success_count=%s failure_count=%s "
            "avg_duration_ms=%.3f avoid_tools=%s evidence=%s"
            % (
                domain_label,
                self.preferred_tool,
                self.preferred_tool,
                self.success_count,
                self.failure_count,
                self.avg_duration_ms,
                avoid,
                evidence,
            )
        )


class ToolPreferenceMiner:
    """Promote repeated tool outcomes into stable, auditable preferences."""

    def mine(self, results: Iterable[ToolResult]) -> list[ToolPreferenceCandidate]:
        grouped: dict[str, dict[str, list[ToolResult]]] = defaultdict(lambda: defaultdict(list))
        for result in results:
            tool = _tool_name(result)
            if not tool:
                continue
            grouped[_domain(result.command, tool)][tool].append(result)

        candidates: list[ToolPreferenceCandidate] = []
        for domain, by_tool in grouped.items():
            candidate = self._candidate_for_domain(domain, by_tool)
            if candidate is not None:
                candidates.append(candidate)
        return candidates

    @staticmethod
    def _candidate_for_domain(domain: str, by_tool: dict[str, list[ToolResult]]) -> ToolPreferenceCandidate | None:
        stats = {}
        for tool, rows in by_tool.items():
            successes = [item for item in rows if item.exit_code == 0]
            failures = [item for item in rows if item.exit_code != 0]
            avg_duration = sum(max(0.0, item.duration_ms) for item in successes or rows) / max(1, len(successes or rows))
            stats[tool] = {
                "success": len(successes),
                "failure": len(failures),
                "avg_duration": avg_duration,
                "rows": rows,
            }
        preferred_tool, preferred = max(
            stats.items(),
            key=lambda item: (item[1]["success"], -item[1]["failure"], -item[1]["avg_duration"]),
        )
        avoid_tools = [
            tool
            for tool, stat in sorted(stats.items())
            if tool != preferred_tool and stat["failure"] > 0 and stat["success"] <= preferred["success"]
        ]
        if preferred["success"] < 2 and not avoid_tools:
            return None
        if preferred["success"] == 0:
            return None
        evidence = []
        for tool, stat in sorted(stats.items()):
            evidence.append(
                "%s success=%s failure=%s avg_ms=%.1f"
                % (tool, stat["success"], stat["failure"], stat["avg_duration"])
            )
        for row in preferred["rows"][:2]:
            evidence.append("%s -> %s" % (summarize_stream(row.command, 80), "success" if row.exit_code == 0 else "failure"))
        return ToolPreferenceCandidate(
            domain=domain,
            preferred_tool=preferred_tool,
            success_count=int(preferred["success"]),
            failure_count=int(preferred["failure"]),
            avoid_tools=avoid_tools,
            avg_duration_ms=float(preferred["avg_duration"]),
            evidence=evidence,
        )


def _tool_name(result: ToolResult) -> str:
    if result.tool_name:
        return result.tool_name.strip()
    try:
        parts = shlex.split(result.command)
    except ValueError:
        parts = result.command.split()
    return parts[0] if parts else ""


def _domain(command: str, tool: str) -> str:
    lowered = command.lower()
    if tool in {"rg", "grep", "ack", "ag"}:
        return "code_search"
    if tool in {"find", "fd", "locate"}:
        return "file_search"
    if tool in {"pytest", "tox", "nox"} or "pytest" in lowered:
        return "test_runner"
    if tool == "systemctl" or "systemctl" in lowered or "journalctl" in lowered:
        return "service_management"
    return "general_shell"
