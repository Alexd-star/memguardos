"""Verify procedure reuse/revocation through product APIs in disposable data.

File actions below are ordinary scripted setup, NOT autonomous Agent actions.
No LLM, external service, formal memories or arbitrary procedure execution.
"""

from __future__ import annotations

import argparse
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import platform
import sys
from tempfile import TemporaryDirectory

ROOT = Path(__file__).resolve().parents[1]
if "--installed" not in sys.argv and str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from memguard.api.server import handle_request  # noqa: E402
from memguard.integrations.openhands_memory_hook import context_snapshot_status  # noqa: E402
from memguard.integrations.openhands_runner import OpenHandsMemoryRunner  # noqa: E402
from memguard.service import MemGuardService  # noqa: E402


def run_scenarios(root: Path) -> dict:
    project = root / "project"
    project.mkdir()
    checks = []
    service = MemGuardService(root / "memory.segment", root / "memory.sqlite3",
                              namespace="procedure-validation")

    def check(name, passed, **details):
        checks.append({"name": name, "passed": bool(passed), **details})
        print(("[通过] " if passed else "[失败] ") + name, flush=True)
        if not passed:
            raise AssertionError(name)

    def request(command, _allow_failure=False, **payload):
        response = handle_request(service, {"command": command, **payload})
        if "result" not in response or (not response["ok"] and not _allow_failure):
            raise AssertionError(response)
        return response["result"]

    try:
        source = request("ingest", text="项目发布说明在提交前需要复核，并保留检查结果。",
                         natural=True, session_id="learning", project_root=str(project))
        source_id = source["memory_id"]
        independent = request("ingest", text="项目文档归档使用 Markdown 格式。",
                              natural=True, session_id="learning", project_root=str(project))
        independent_id = independent["memory_id"]
        spec = {
            "goal": "复核项目发布说明并保留检查结果",
            "steps": ["在 $file 中整理发布说明。", "核对版本与变更后，在 $file 中记录 REVIEWED。"],
            "failure_guards": ["缺少复核记录时不可宣称发布成功。", "不修改其他项目的文件。"],
            "parameters": {"file": None},
            "postconditions": [
                {"type": "path_exists", "path": "$file"},
                {"type": "file_contains", "path": "$file", "value": "REVIEWED"},
                {"type": "file_not_contains", "path": "$file", "value": "TODO"},
            ],
            "environment": {"platform_system": platform.system()},
        }
        (project / "release-a.md").write_text("Version 1\nTODO review\n", encoding="utf-8")
        rejected = request("learn_procedure", _allow_failure=True, spec=spec, workspace=str(project),
                           source_memory_ids=[source_id], session_id="learning",
                           arguments={"file": "release-a.md"})
        check("完成检查未通过时拒绝形成可复用流程", not rejected["stored"] and rejected["overall"] == "fail")
        (project / "release-a.md").write_text("Version 1\nREVIEWED\n", encoding="utf-8")
        learned = request("learn_procedure", spec=spec, workspace=str(project),
                          source_memory_ids=[source_id], session_id="learning",
                          arguments={"file": "release-a.md"})
        procedure_id = learned["memory_id"]
        check("实际文件检查通过后形成带来源版本的流程", learned["stored"] and learned["verified"]
              and learned["dependencies"][0]["memory_id"] == source_id)
        for index, filename in enumerate(("release-b.md", "发布复核.md", "report-final.txt")):
            recalled = request("recall_procedures", task=spec["goal"], workspace=str(project),
                               session_id=f"reuse-{index}", arguments={"file": filename})
            check(f"新会话绑定不同文件参数：{filename}", bool(recalled)
                  and recalled[0]["status"] == "ready" and filename in recalled[0]["steps"][0])
            before = request("verify_procedure", _allow_failure=True, memory_id=procedure_id, workspace=str(project),
                             session_id=f"reuse-{index}", use_id="before-action", arguments={"file": filename})
            check(f"尚未完成的工作不计成功：{filename}", before["overall"] == "fail" and not before["verified"])
            (project / filename).write_text("Version 2\nREVIEWED\n", encoding="utf-8")
            completed = request("verify_procedure", memory_id=procedure_id, workspace=str(project),
                                session_id=f"reuse-{index}", use_id="after-action", arguments={"file": filename})
            check(f"后续任务重新检查实际结果：{filename}", completed["verified"] and completed["recorded"])
        duplicate = request("verify_procedure", memory_id=procedure_id, workspace=str(project),
                            session_id="reuse-2", use_id="after-action", arguments={"file": "report-final.txt"})
        check("重复同次反馈不增加成功计数", duplicate["duplicate"] and not duplicate["recorded"]
              and service.meta.procedure_stats(procedure_id)["pass"] == 3)
        scoped = request("recall_procedures", task=spec["goal"], workspace=str(project), session_id="new-session")
        check("未提供参数时明确提示而不虚构步骤", scoped[0]["status"] == "requires_arguments"
              and scoped[0]["required_arguments"] == ["file"])
        context = service.build_agent_context(task=spec["goal"], stage="planning", agent_name="procedure-validator", project_root=str(project),
                                              session_id="context", token_budget=2000, max_chars=12000)
        selected = {item["memory_id"] for item in context["memories"]}
        check("Agent 上下文同时携带步骤、约束与完成条件", procedure_id in selected
              and "failure_guards=" in context["context"] and "postconditions=" in context["context"])
        small = service.build_agent_context(task=spec["goal"], stage="planning", agent_name="procedure-validator", project_root=str(project),
                                            session_id="context-small", token_budget=5, max_chars=60)
        check("窗口不足时整体跳过流程，不裁掉安全约束", procedure_id not in {item["memory_id"] for item in small["memories"]})
        detail = request("memory_detail", memory_id=procedure_id, light=True)
        check("控制台详情可查看流程来源及实测反馈", len(detail["dependencies"]) == 1
              and detail["procedure_feedback"]["pass"] == 3 and detail["procedure_feedback"]["fail"] == 3)
        runner = OpenHandsMemoryRunner(service, session_id="checkpoint", workspace=project,
            state_dir=root / "runtime", project_root=str(project), task_type="documentation")
        snapshot = runner.prepare_context(spec["goal"])
        check("流程进入接入的 Agent 上下文检查点", context_snapshot_status(snapshot)["ok"]
              and any(item["memory_id"] == procedure_id for stage in snapshot["stages"].values() for item in stage["memories"]))
        updated = request("update", memory_id=source_id, text="项目发布说明必须由两名维护者复核。")
        check("来源更新后派生流程立即失效", updated["updated"]
              and service.meta.get_memory(procedure_id)["governance_state"] == "source_invalidated")
        check("已发出的旧上下文在下一检查点拒用", not context_snapshot_status(snapshot)["ok"])
        after = request("recall_procedures", task=spec["goal"], workspace=str(project),
                        arguments={"file": "release-a.md"}, session_id="after-update")
        check("新任务不再收到过时流程", not after)
        impact = request("dependency_impact", memory_ids=[source_id])
        check("撤回预览列出确切派生影响", [item["memory_id"] for item in impact["derived_memories"]] == [procedure_id])
        erased = request("forget_memory_ids", memory_ids=[source_id], reason="撤回来源与派生流程", compact=True,
                         expected_versions={source_id: service.meta.get_memory(source_id)["version_id"], **impact["expected_versions"]})
        check("源记录、流程、依赖关系和反馈一起清理", erased["ok"]
              and set(erased["forgotten_memory_ids"]) == {source_id, procedure_id}
              and service.meta.memory_dependencies(procedure_id) == []
              and service.meta.procedure_stats(procedure_id)["measured"] == 0)
        check("独立文档偏好保留，审计链完整", not service.meta.get_memory(independent_id)["deleted"]
              and service.verify_audit()["ok"])
        return {"schema": "memguardos-procedure-validation-v1", "generated_at": datetime.now(timezone.utc).isoformat(),
                "mode": "offline_product_api_with_scripted_file_actions", "external_llm_used": False,
                "passed": all(item["passed"] for item in checks), "checks": checks,
                "environment": {"system": platform.system(), "python": platform.python_version()},
                "modules": {name: {"path": str(Path(sys.modules[name].__file__).resolve()),
                                   "sha256": hashlib.sha256(Path(sys.modules[name].__file__).read_bytes()).hexdigest()}
                            for name in ("memguard.procedural", "memguard.procedure_service",
                                         "memguard.store.dependencies", "memguard.service")}}
    except Exception as exc:
        return {"schema": "memguardos-procedure-validation-v1", "passed": False,
                "mode": "offline_product_api_with_scripted_file_actions", "external_llm_used": False,
                "checks": checks, "error": {"type": type(exc).__name__, "message": str(exc)}}
    finally:
        service.close()


def main():
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, default=ROOT / "output/procedure_showcase.json")
    parser.add_argument("--installed", action="store_true", help="Use installed product rather than adding source to sys.path")
    args = parser.parse_args()
    if args.installed:
        module = Path(sys.modules["memguard.service"].__file__).resolve()
        if Path("/opt/memguardos").resolve() not in module.parents:
            parser.error("--installed requires the product from /opt/memguardos")
    with TemporaryDirectory(prefix="memguard-procedure-") as directory:
        result = run_scenarios(Path(directory).resolve())
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"结果已保存：{args.output}")
    return 0 if result["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
