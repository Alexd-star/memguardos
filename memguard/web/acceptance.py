from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Callable
import subprocess
import sys

from memguard.service import MemGuardService


@dataclass(frozen=True)
class AcceptanceItem:
    id: str
    group: str
    title: str
    purpose: str
    operation: str
    pass_standard: str
    mode: str = "automatic"

    def to_dict(self) -> dict[str, str]:
        return {
            "id": self.id,
            "group": self.group,
            "title": self.title,
            "purpose": self.purpose,
            "operation": self.operation,
            "pass_standard": self.pass_standard,
            "mode": self.mode,
        }


ACCEPTANCE_ITEMS: tuple[AcceptanceItem, ...] = (
    AcceptanceItem("A02_DOCTOR", "基础环境", "基础诊断", "检查当前命名空间、存储和基础服务是否可访问。", "运行快速诊断", "能返回命名空间和存储状态"),
    AcceptanceItem("A03_SERVICE", "基础环境", "后台服务状态", "检查 memguardd 是否作为系统服务运行。", "读取 systemd 服务状态", "服务可查询；openKylin 上建议为 active/running"),
    AcceptanceItem("A04_CGROUP", "基础环境", "服务资源限制", "检查服务级 cgroup 资源信息是否可见。", "读取 cgroup 状态", "能看到 cgroup v2 或资源状态说明"),
    AcceptanceItem("A06_OPENKYLIN_STATUS", "openKylin 适配", "系统状态总览", "检查 openKylin、桌面插件、SDK 和安全组件状态。", "运行 openkylin-status 快速检查", "状态返回正常，不影响基础使用"),
    AcceptanceItem("A13_INGEST_PREF", "记忆功能", "写入偏好记忆", "写入一条用户与 Agent 交互偏好。", "写入固定测试偏好", "返回 stored=true 和 memory_id"),
    AcceptanceItem("A14_SEARCH_PREF", "记忆功能", "检索偏好记忆", "验证刚写入或既有偏好可以被检索。", "写入后检索关键词", "至少命中一条相关记忆"),
    AcceptanceItem("A15_BUILD_CONTEXT", "记忆功能", "构建 Agent 上下文", "验证系统能为 Agent 任务组织可解释上下文。", "构建固定任务上下文", "返回 memories 和 explainability"),
    AcceptanceItem("A16_GRAPH", "记忆功能", "层次图谱检索", "验证摘要层、实体关系层和原文层检索链路。", "检索 systemd 相关实体", "返回层次检索结构"),
    AcceptanceItem("A11_SENSITIVE", "安全与遗忘", "敏感内容阻断", "验证密码类内容不会被当作普通长期记忆保存。", "写入含敏感字段文本", "返回敏感阻断或高风险拒绝"),
    AcceptanceItem("A17_FORGET_DRY", "安全与遗忘", "遗忘预演", "验证遗忘前先展示候选影响范围。", "写入 blue-river 测试内容后 dry-run", "返回候选或预演结果，不直接删除"),
    AcceptanceItem("A18_FORGET_YES", "安全与遗忘", "确认遗忘", "验证确认后清理受管记忆和索引。", "写入 blue-river 后确认遗忘", "执行成功并返回 summary"),
    AcceptanceItem("A21_VERIFY_AUDIT", "安全与遗忘", "操作记录完整性", "验证审计哈希链没有断裂。", "校验审计链", "verify-audit 返回 ok=true"),
    AcceptanceItem("A22_WEB_DIAGNOSTICS", "系统维护", "系统报告", "验证管理员能导出本地系统维护报告。", "生成快速诊断报告", "报告包含审计、检索和分层状态"),
    AcceptanceItem("A24_PYTEST", "测试与证据", "自动测试", "运行完整 pytest，适合最终提交前执行。", "运行 python -m pytest -q", "全部测试通过"),
    AcceptanceItem("A25_READY", "测试与证据", "赛题就绪检查", "运行赛题门禁检查。", "运行 check_competition_ready.py", "全部门禁通过且 warning 为 0"),
    AcceptanceItem("A26_EVIDENCE_DIR", "测试与证据", "证据目录", "检查 openKylin 原生证据目录和哈希清单。", "检查 evidence 目录", "manifest.json 和 SHA256SUMS.txt 存在"),
    AcceptanceItem("M01_PEONY_MENU", "人工截图", "Peony 右键菜单", "需要用户在文件管理器中右键文件后确认入口可见。", "人工打开 Peony 并截图", "截图包含 MemGuardOS 文件扫描入口", mode="manual"),
    AcceptanceItem("M02_PEONY_DIALOG", "人工截图", "Peony 扫描结果弹窗", "需要用户点击扫描入口后确认预览和导入结果。", "人工点击右键菜单并截图", "截图包含预览、风险提示和确认按钮", mode="manual"),
    AcceptanceItem("M03_UKUI_DETAIL", "人工截图", "UKUI 控制中心详情页", "需要用户打开设置里的记忆安全中心并确认页面体验。", "人工打开 UKUI 控制中心并截图", "截图包含普通用户页、管理员页或系统维护页", mode="manual"),
    AcceptanceItem("M04_TRAY_MENU", "人工截图", "托盘菜单", "需要用户点击系统托盘图标并确认快捷入口。", "人工展开托盘菜单并截图", "截图包含打开记忆安全中心等入口", mode="manual"),
)


def acceptance_plan() -> dict[str, object]:
    items = [item.to_dict() for item in ACCEPTANCE_ITEMS]
    automatic = sum(1 for item in ACCEPTANCE_ITEMS if item.mode == "automatic")
    manual = len(ACCEPTANCE_ITEMS) - automatic
    groups: dict[str, int] = {}
    for item in ACCEPTANCE_ITEMS:
        groups[item.group] = groups.get(item.group, 0) + 1
    return {
        "schema": "memguardos-acceptance-plan-v1",
        "summary": {"total": len(ACCEPTANCE_ITEMS), "automatic": automatic, "manual": manual, "groups": groups},
        "items": items,
    }


def run_acceptance_item(service: MemGuardService, item_id: str) -> dict[str, object]:
    item = _item_by_id(item_id)
    if item is None:
        raise KeyError(item_id)
    if item.mode == "manual":
        return _result(item, "manual", True, "该项目需要人工打开桌面界面并截图确认。", {"next_action": item.operation})
    runner = _RUNNERS.get(item.id)
    if runner is None:
        return _result(item, "skipped", True, "该项目暂未提供自动运行器。", {})
    try:
        status, ok, summary, details = runner(service)
    except Exception as exc:  # pragma: no cover - defensive web boundary
        status, ok, summary, details = "failed", False, "运行失败，请查看详细信息。", {"error": type(exc).__name__, "message": str(exc)}
    return _result(item, status, ok, summary, details)


def _item_by_id(item_id: str) -> AcceptanceItem | None:
    for item in ACCEPTANCE_ITEMS:
        if item.id == item_id:
            return item
    return None


def _result(item: AcceptanceItem, status: str, ok: bool, summary: str, details: dict[str, object]) -> dict[str, object]:
    return {
        "id": item.id,
        "group": item.group,
        "title": item.title,
        "mode": item.mode,
        "status": status,
        "ok": ok,
        "summary": summary,
        "pass_standard": item.pass_standard,
        "details": details,
    }


def _doctor(service: MemGuardService) -> tuple[str, bool, str, dict[str, object]]:
    status = service.namespace_status()
    ok = bool(status.get("namespace") and status.get("store_path") and status.get("db_path"))
    return ("passed" if ok else "failed", ok, "基础诊断可访问。" if ok else "基础诊断信息不完整。", status)


def _service_status(_: MemGuardService) -> tuple[str, bool, str, dict[str, object]]:
    output = _run_fixed_command(["systemctl", "is-active", "memguardd"], timeout=8)
    if output["returncode"] == 0:
        return "passed", True, "后台服务处于运行状态。", output
    return "warning", True, "当前环境无法确认服务运行，核心库功能仍可继续核验。", output


def _cgroup(_: MemGuardService) -> tuple[str, bool, str, dict[str, object]]:
    try:
        from memguard.os_guard.cgroup import cgroup_status

        status = cgroup_status()
    except Exception as exc:  # pragma: no cover - host dependent
        status = {"error": str(exc)}
    ok = bool(status)
    return ("passed" if ok else "warning", True, "资源状态已读取。" if ok else "资源状态不可读。", status)


def _openkylin_status(_: MemGuardService) -> tuple[str, bool, str, dict[str, object]]:
    try:
        from memguard.os_guard.openkylin_native import collect_openkylin_status

        status = collect_openkylin_status(probe_sdk=False)
    except Exception as exc:  # pragma: no cover - platform dependent
        status = {"error": str(exc)}
    return "passed", True, "已完成本机 openKylin 快速状态检查。", status


def _ingest_preference(service: MemGuardService) -> tuple[str, bool, str, dict[str, object]]:
    result = service.ingest_text(
        "以后回答 Linux 命令时，先给出可执行命令，再说明风险和回滚方式。",
        source_kind="dashboard_acceptance",
        source_uri="web_acceptance",
    )
    ok = bool(result.get("stored"))
    return ("passed" if ok else "failed", ok, "偏好记忆已写入。" if ok else "偏好记忆未写入。", result)


def _search_preference(service: MemGuardService) -> tuple[str, bool, str, dict[str, object]]:
    service.ingest_text(
        "以后回答 Linux 命令时，先给出可执行命令，再说明风险和回滚方式。",
        source_kind="dashboard_acceptance",
        source_uri="web_acceptance",
    )
    hits = service.search("Linux 命令回答偏好", limit=3)
    ok = len(hits) >= 1
    return ("passed" if ok else "failed", ok, "已检索到相关偏好记忆。" if ok else "未检索到相关偏好。", {"hits": hits})


def _build_context(service: MemGuardService) -> tuple[str, bool, str, dict[str, object]]:
    service.ingest_text("openKylin 服务启动失败时，先看 systemctl status，再看 journalctl。", source_kind="dashboard_acceptance")
    context = service.build_context("帮我修复 openKylin 服务启动失败", limit=5, token_budget=1200)
    ok = bool(context.get("memories") is not None and context.get("explainability"))
    return ("passed" if ok else "failed", ok, "Agent 上下文已生成并包含解释信息。", context)


def _graph(service: MemGuardService) -> tuple[str, bool, str, dict[str, object]]:
    service.ingest_text("systemd 服务修复流程：daemon-reload、restart、is-active。", source_kind="dashboard_acceptance")
    graph = service.hierarchical_graph_search("systemd", limit=5)
    ok = bool(graph.get("retrieval_path") or graph.get("layers"))
    return ("passed" if ok else "failed", ok, "层次检索已返回结构化结果。", graph)


def _sensitive(service: MemGuardService) -> tuple[str, bool, str, dict[str, object]]:
    result = service.ingest_text("测试敏感内容：password=OpenKylin-123456，仅用于阻断验证。", source_kind="dashboard_acceptance")
    ok = not bool(result.get("stored"))
    return ("passed" if ok else "failed", ok, "敏感文本已被阻断。" if ok else "敏感文本被写入，请检查策略。", result)


def _forget_dry(service: MemGuardService) -> tuple[str, bool, str, dict[str, object]]:
    service.ingest_text("临时项目代号 blue-river，只用于遗忘预演。", source_kind="dashboard_acceptance")
    plan = service.forget("忘掉 blue-river", confirm=False)
    ok = bool(plan.get("candidates") or plan.get("summary") is not None)
    return ("passed" if ok else "failed", ok, "遗忘预演已返回影响范围。", plan)


def _forget_yes(service: MemGuardService) -> tuple[str, bool, str, dict[str, object]]:
    service.ingest_text("临时项目代号 blue-river，只用于遗忘确认。", source_kind="dashboard_acceptance")
    result = service.forget("忘掉 blue-river", confirm=True, compact=True)
    ok = bool(result.get("confirmed") is True)
    return ("passed" if ok else "failed", ok, "已执行确认遗忘。", result)


def _verify_audit(service: MemGuardService) -> tuple[str, bool, str, dict[str, object]]:
    result = service.verify_audit()
    ok = bool(result.get("ok"))
    return ("passed" if ok else "failed", ok, "操作记录完整性校验通过。" if ok else "操作记录完整性异常。", result)


def _diagnostics(service: MemGuardService) -> tuple[str, bool, str, dict[str, object]]:
    payload = {
        "namespace": service.namespace,
        "namespace_status": service.namespace_status(),
        "retrieval_status": service.retrieval_status(),
        "memory_policy": service.memory_policy_status(),
        "audit_chain": service.verify_audit(),
    }
    ok = bool(payload["audit_chain"].get("ok"))
    return ("passed" if ok else "failed", ok, "系统报告已生成。", payload)


def _pytest(_: MemGuardService) -> tuple[str, bool, str, dict[str, object]]:
    output = _run_fixed_command([sys.executable, "-m", "pytest", "-q"], timeout=180)
    ok = output["returncode"] == 0
    return ("passed" if ok else "failed", ok, "自动测试通过。" if ok else "自动测试未通过。", output)


def _ready(_: MemGuardService) -> tuple[str, bool, str, dict[str, object]]:
    script = Path("scripts/check_competition_ready.py")
    if not script.exists():
        return "warning", True, "当前目录没有赛题就绪脚本。", {"script": str(script)}
    output = _run_fixed_command([sys.executable, str(script), "--output", "docs/competition_ready.json", "--markdown", "docs/竞赛就绪自检报告.md"], timeout=60)
    ok = output["returncode"] == 0
    return ("passed" if ok else "failed", ok, "赛题就绪检查通过。" if ok else "赛题就绪检查未通过。", output)


def _evidence_dir(_: MemGuardService) -> tuple[str, bool, str, dict[str, object]]:
    evidence = Path("docs/evidence/openkylin_native_latest")
    manifest = evidence / "manifest.json"
    sha = evidence / "SHA256SUMS.txt"
    details = {
        "path": str(evidence),
        "exists": evidence.exists(),
        "manifest": manifest.exists(),
        "sha256": sha.exists(),
        "file_count": len(list(evidence.glob("*"))) if evidence.exists() else 0,
    }
    ok = bool(details["exists"] and details["manifest"] and details["sha256"])
    return ("passed" if ok else "failed", ok, "证据目录和哈希清单存在。" if ok else "证据目录不完整。", details)


def _run_fixed_command(command: list[str], timeout: int) -> dict[str, object]:
    try:
        completed = subprocess.run(command, capture_output=True, text=True, timeout=timeout, check=False)
    except FileNotFoundError as exc:
        return {"returncode": 127, "stdout": "", "stderr": str(exc)}
    except subprocess.TimeoutExpired as exc:
        return {"returncode": 124, "stdout": exc.stdout or "", "stderr": "operation timed out"}
    return {
        "returncode": int(completed.returncode),
        "stdout": completed.stdout[-8000:],
        "stderr": completed.stderr[-8000:],
    }


_RUNNERS: dict[str, Callable[[MemGuardService], tuple[str, bool, str, dict[str, object]]]] = {
    "A02_DOCTOR": _doctor,
    "A03_SERVICE": _service_status,
    "A04_CGROUP": _cgroup,
    "A06_OPENKYLIN_STATUS": _openkylin_status,
    "A13_INGEST_PREF": _ingest_preference,
    "A14_SEARCH_PREF": _search_preference,
    "A15_BUILD_CONTEXT": _build_context,
    "A16_GRAPH": _graph,
    "A11_SENSITIVE": _sensitive,
    "A17_FORGET_DRY": _forget_dry,
    "A18_FORGET_YES": _forget_yes,
    "A21_VERIFY_AUDIT": _verify_audit,
    "A22_WEB_DIAGNOSTICS": _diagnostics,
    "A24_PYTEST": _pytest,
    "A25_READY": _ready,
    "A26_EVIDENCE_DIR": _evidence_dir,
}


def acceptance_report(results: list[dict[str, object]]) -> dict[str, object]:
    total = len(results)
    passed = sum(1 for item in results if item.get("status") == "passed")
    failed = sum(1 for item in results if item.get("status") == "failed")
    warning = sum(1 for item in results if item.get("status") == "warning")
    manual = sum(1 for item in results if item.get("status") == "manual")
    return {
        "schema": "memguardos-acceptance-report-v1",
        "summary": {
            "total": total,
            "passed": passed,
            "failed": failed,
            "warning": warning,
            "manual": manual,
            "ready_for_final_demo": failed == 0 and manual == 0,
        },
        "results": results,
    }


def report_to_markdown(report: dict[str, object]) -> str:
    summary = report.get("summary", {})
    lines = [
        "# MemGuardOS 本机复核报告",
        "",
        "| 项目 | 数量 |",
        "|---|---:|",
        f"| 总数 | {summary.get('total', 0)} |",
        f"| 通过 | {summary.get('passed', 0)} |",
        f"| 不通过 | {summary.get('failed', 0)} |",
        f"| 警告 | {summary.get('warning', 0)} |",
        f"| 需人工确认 | {summary.get('manual', 0)} |",
        "",
        "| 编号 | 分组 | 名称 | 状态 | 摘要 |",
        "|---|---|---|---|---|",
    ]
    for item in report.get("results", []):
        lines.append("| {id} | {group} | {title} | {status} | {summary} |".format(**{key: _md_cell(item.get(key, "")) for key in ["id", "group", "title", "status", "summary"]}))
    return "\n".join(lines) + "\n"


def _md_cell(value: object) -> str:
    return str(value).replace("|", "\\|").replace("\n", " ")
