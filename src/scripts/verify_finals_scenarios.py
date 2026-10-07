"""Exercise product APIs and runtime guards in a disposable, offline workspace."""

from __future__ import annotations

import argparse
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import sys
from tempfile import TemporaryDirectory

ROOT = Path(__file__).resolve().parents[1]
if "--installed" not in sys.argv and str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from memguard.api.server import handle_request  # noqa: E402
from memguard.integrations.openhands_memory_hook import context_snapshot_status  # noqa: E402
from memguard.integrations.openhands_runner import OpenHandsMemoryRunner  # noqa: E402
from memguard.maintenance import MaintenancePolicy, maintenance_decision  # noqa: E402
from memguard.os_guard.cgroup import cgroup_status  # noqa: E402
from memguard.service import MemGuardService  # noqa: E402
import memguard.service as service_module  # noqa: E402


def run_scenarios(root: Path) -> dict:
    store, db = root / "memory.segment", root / "memory.sqlite3"
    project = str((root / "project").resolve())
    foreign = str((root / "other-project").resolve())
    checks = []
    service = MemGuardService(store, db, namespace="finals-validation")

    def check(name, passed, **details):
        item = {"name": name, "passed": bool(passed), **details}
        checks.append(item)
        print(("[通过] " if passed else "[失败] ") + name, flush=True)
        if not passed:
            raise AssertionError(name)

    def remember(text, session="history", project_root=project):
        response = handle_request(service, {
            "command": "ingest", "text": text, "natural": True,
            "session_id": session, "project_root": project_root,
        })
        if not response["ok"]:
            raise AssertionError(response)
        return response["result"]

    def context(session="new-session"):
        runner = OpenHandsMemoryRunner(
            service, session_id=session, workspace=Path(project),
            state_dir=root / "runtime" / session, project_root=project,
            task_type="code_assistance",
        )
        return runner.prepare_context("请搜索项目代码中配置加载函数的定义，并解释结果。")

    try:
        result = remember("以后搜索项目代码优先使用 grep，并且回答时先给可复制命令，再解释结果。")
        rows = service.list_memories()
        tools = [row for row in rows if row["preference_topic"] == "code_search_tool"]
        styles = [row for row in rows if row["preference_topic"] and row["preference_topic"] != "code_search_tool"]
        check("同一句要求形成独立工具偏好与回答偏好", result["stored"] and len(tools) == len(styles) == 1)
        tool_id, style_id = tools[0]["memory_id"], styles[0]["memory_id"]
        lifecycle = service.lifecycle_snapshot(tool_id)
        check("偏好有来源片段、情节与提交版本", lifecycle["counts"]["supports"] > 0 and lifecycle["storage_consistency"]["segment_matches_committed_version"])

        old = context()
        selected = [item for stage in old["stages"].values() for item in stage["memories"]]
        check("新会话自动复用历史偏好", {tool_id, style_id}.issubset({row["memory_id"] for row in selected}) and context_snapshot_status(old)["ok"])
        remember("纠正一下：以后搜索项目代码优先使用 rg。", session="correction")
        check("纠正偏好使已经构建的旧上下文失效", not context_snapshot_status(old)["ok"])
        current = context("after-update")
        values = [row["preference_value"] for stage in current["stages"].values() for row in stage["memories"] if row["preference_topic"] == "code_search_tool"]
        check("重新取上下文只采用更新后的工具偏好", bool(values) and set(values) == {"rg"} and context_snapshot_status(current)["ok"])

        first = remember("青山项目发布日是周三。")
        pending = remember("青山项目发布日是周五。")
        check("矛盾事实先进入核对队列，不直接覆盖", first["stored"] and not pending["stored"] and pending["reason"] == "fact_conflict_requires_review")
        accepted = service.resolve_fact_conflict(pending["conflict_id"], decision="accept_incoming")
        fact_id = first["memory_id"]
        check("确认新事实生成版本并关闭核对项", accepted["resolved"] and len(service.versions(fact_id)) == 2 and service.fact_conflicts()["count"] == 0)
        remember("青山项目发布日是周一。", project_root=foreign)
        own_hits = service.search("青山项目发布日", project_root=project, audit=False)
        check("相同事实键不跨项目覆盖或串入检索", own_hits and all(row["project_root"] in {"", project} for row in own_hits) and service.meta.get_memory(fact_id)["content"].endswith("周五"))

        temporary = remember("临时项目代号是蓝河，该信息只保留到本次会话结束。", session="temporary")
        temporary_id = temporary["memory_id"]
        temp_row = service.meta.get_memory(temporary_id)
        other_hits = service.search("蓝河", project_root=project, session_id="new-session", audit=False)
        check("临时事实按会话保存且不进入其他会话", temp_row["tier"] == "session" and temp_row["retention_session_id"] == "temporary" and temporary_id not in {row["memory_id"] for row in other_hits})
        rejected = remember("记住我的密码 11515。")
        check("中文短密码写入前拒存", not rejected["stored"])

        preview = service.forget_by_intent("忘记搜索代码使用 rg 的工具偏好", confirm=False)["result"]
        target_ids = [row["memory_id"] for row in preview["candidates"]]
        active_tool_ids = {row["memory_id"] for row in service.list_memories() if row["preference_topic"] == "code_search_tool"}
        check("自然语言遗忘准确定位工具偏好并保留回答偏好", bool(target_ids) and set(target_ids).issubset(active_tool_ids) and style_id not in target_ids)
        target = target_ids[0]
        receipt = service.forget_memory_ids(
            [target], "user selected one preview candidate", compact=True,
            expected_versions={target: service.meta.get_memory(target)["version_id"]},
        )
        check("单项遗忘清理受管存储且不删除另一项偏好", receipt["ok"] and not service.meta.get_memory(style_id)["deleted"], forgotten_ids=receipt["forgotten_memory_ids"])
        check("遗忘后旧 Agent 上下文被拒绝继续使用", not context_snapshot_status(current)["ok"])
        refreshed = context("after-forget")
        refreshed_ids = {row["memory_id"] for stage in refreshed["stages"].values() for row in stage["memories"]}
        check("重新构建上下文保留无关偏好且排除目标", target not in refreshed_ids and style_id in refreshed_ids and context_snapshot_status(refreshed)["ok"])
        check("压力调度推迟后台工作，空闲状态允许有界批处理", not maintenance_decision({"memory_budget_ratio": .9})["run"] and maintenance_decision({"memory_budget_ratio": .1})["run"], policy=MaintenancePolicy().__dict__)
        check("完整操作链审计可校验", service.verify_audit()["ok"])
    finally:
        service.close()
    reopened = MemGuardService(store, db, namespace="finals-validation")
    try:
        check("服务重开后已遗忘内容不复活", reopened.meta.get_memory(target)["deleted"] and not reopened.meta.get_memory(style_id)["deleted"] and reopened.verify_audit()["ok"])
    finally:
        reopened.close()
    module_path = Path(service_module.__file__).resolve()
    return {
        "schema": "memguardos-finals-scenarios-v1", "ok": all(item["passed"] for item in checks),
        "created_at": datetime.now(timezone.utc).isoformat(),
        "module_path": str(module_path), "module_sha256": hashlib.sha256(module_path.read_bytes()).hexdigest(),
        "evaluation_scope": "offline product API and runtime guard scenarios; no model-generated actions",
        "data_scope": "temporary synthetic workspace only", "checks": checks,
        "os_resources": cgroup_status(),
    }


def main() -> int:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path)
    parser.add_argument("--installed", action="store_true", help="Require modules from /opt/memguardos rather than checkout")
    args = parser.parse_args()
    if args.installed and not Path(service_module.__file__).resolve().is_relative_to(Path("/opt/memguardos").resolve()):
        parser.error("installed verification must use /opt/memguardos modules")
    previous_backend = os.environ.get("MEMGUARD_EMBEDDING_BACKEND")
    os.environ["MEMGUARD_EMBEDDING_BACKEND"] = "hash"
    try:
        with TemporaryDirectory(prefix="memguard-finals-") as directory:
            report = run_scenarios(Path(directory))
    finally:
        if previous_backend is None:
            os.environ.pop("MEMGUARD_EMBEDDING_BACKEND", None)
        else:
            os.environ["MEMGUARD_EMBEDDING_BACKEND"] = previous_backend
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print("完成 %s 项真实服务链路检查；数据位于临时目录，未修改正式记忆。" % len(report["checks"]))
    return 0 if report["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
