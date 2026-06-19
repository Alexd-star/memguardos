#!/usr/bin/env python3
from __future__ import annotations

from collections import Counter
from pathlib import Path
import argparse
import json
import os
import shutil
import sys
import traceback

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from memguard.eval.real_agent_behavior import (  # noqa: E402
    aggregate_real_agent_results,
    evaluate_real_agent_run,
)
from memguard.ingestion.tool_result import ToolResult  # noqa: E402
from memguard.integrations.events import AgentInteractionEvent  # noqa: E402
from memguard.integrations.openhands_adapter import detect_openhands_runtime  # noqa: E402
from memguard.integrations.openhands_runner import OpenHandsMemoryRunner  # noqa: E402
from memguard.service import MemGuardService  # noqa: E402


def load_cases(path: Path):
    cases = []
    for line_number, line in enumerate(
        path.read_text(encoding="utf-8").splitlines(), start=1
    ):
        if not line.strip():
            continue
        item = json.loads(line)
        if not isinstance(item, dict):
            raise ValueError("line %s must be an object" % line_number)
        cases.append(item)
    return cases


def validate_cases(cases):
    errors = []
    expected_ids = ["R%02d" % index for index in range(1, 13)]
    case_ids = [str(case.get("case_id") or "") for case in cases]
    if case_ids != expected_ids:
        errors.append("case ids must be R01 through R12 in order")
    for case in cases:
        case_id = str(case.get("case_id") or "<missing>")
        for field in (
            "category",
            "title",
            "task",
            "tool_profile",
            "max_iterations",
            "workspace_fixture",
            "memory_assertions",
            "behavior_assertions",
            "safety_mode",
        ):
            if field not in case:
                errors.append("%s missing field %s" % (case_id, field))
        if str(case.get("safety_mode") or "") not in {
            "read_only",
            "workspace_only",
            "hook_only",
        }:
            errors.append("%s has unsupported safety_mode" % case_id)
        iterations = int(case.get("max_iterations") or 0)
        if iterations < 1 or iterations > 8:
            errors.append("%s max_iterations must be between 1 and 8" % case_id)
    return {
        "valid": not errors,
        "case_count": len(cases),
        "case_ids": case_ids,
        "category_distribution": dict(
            sorted(Counter(str(case.get("category") or "") for case in cases).items())
        ),
        "errors": errors,
    }


def prepare_workspace(workspace: Path, fixture):
    workspace.mkdir(parents=True, exist_ok=False)
    fixture = dict(fixture or {})
    for relative_path, content in dict(fixture.get("files") or {}).items():
        target = _safe_workspace_path(workspace, str(relative_path))
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(str(content), encoding="utf-8")
    for relative_path in fixture.get("copy_project_subset") or []:
        source = (ROOT / str(relative_path)).resolve()
        source.relative_to(ROOT.resolve())
        target = _safe_workspace_path(workspace, str(relative_path))
        if source.is_dir():
            shutil.copytree(source, target)
        elif source.is_file():
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(source, target)
        else:
            raise FileNotFoundError("workspace fixture source missing: %s" % source)


def ingest_setup_events(service, case):
    last_commands = {}
    stored = []
    for sequence, raw in enumerate(case.get("setup_events") or [], start=1):
        item = dict(raw)
        session_id = str(item.get("session_id") or "%s-history" % case["case_id"])
        tool_args = dict(item.get("tool_args") or {})
        event = AgentInteractionEvent(
            agent_name=str(item.get("agent_name") or "openhands"),
            agent_version=str(item.get("agent_version") or "1.28.1"),
            session_id=session_id,
            turn_id=str(item.get("turn_id") or "turn-%s" % sequence),
            sequence=sequence,
            event_type=str(item.get("event_type") or "user_message"),
            role=str(item.get("role") or "user"),
            content=str(item.get("content") or ""),
            tool_name=str(item.get("tool_name") or ""),
            tool_args=tool_args,
            tool_result=str(item.get("tool_result") or ""),
            exit_code=item.get("exit_code"),
            project_root=str(item.get("project_root") or ""),
            task_type=str(item.get("task_type") or "code_assistance"),
            source_uri="memory-value-real://%s/setup/%s"
            % (case["case_id"], sequence),
            event_id="evt-%s-setup-%s" % (str(case["case_id"]).lower(), sequence),
        )
        stored.append(service.ingest_interaction(event))
        if event.event_type == "tool_call":
            last_commands[session_id] = str(tool_args.get("command") or "")
        elif event.event_type == "tool_result":
            exit_code = int(event.exit_code or 0)
            service.ingest_tool_result(
                ToolResult(
                    command=last_commands.get(session_id, ""),
                    cwd=str(item.get("project_root") or ""),
                    exit_code=exit_code,
                    stdout=event.tool_result if exit_code == 0 else "",
                    stderr=event.tool_result if exit_code != 0 else "",
                    tool_name=event.tool_name or "terminal",
                    state_before="历史 Agent 正在处理同类任务",
                    state_after="历史工具成功" if exit_code == 0 else "历史工具失败",
                    lesson=(
                        "后续优先复用已验证成功的替代命令"
                        if exit_code == 0
                        else "后续避免重复该失败命令"
                    ),
                ),
                source_uri="memory-value-real-tool://%s/setup/%s"
                % (case["case_id"], sequence),
            )
    return stored


def run_case(case, output_dir: Path, args):
    case_id = str(case["case_id"])
    case_dir = output_dir / "cases" / case_id
    result_file = case_dir / "result.json"
    if result_file.exists():
        if args.resume:
            return json.loads(result_file.read_text(encoding="utf-8"))
        raise FileExistsError(
            "%s already exists; use a new output directory or --resume" % result_file
        )
    case_dir.mkdir(parents=True, exist_ok=False)
    workspace = case_dir / "workspace"
    state_dir = case_dir / "state"
    prepare_workspace(workspace, case.get("workspace_fixture"))
    before_manifest = workspace_manifest(workspace)
    service = MemGuardService(
        store_path=state_dir / "memory.segment",
        db_path=state_dir / "memory.sqlite3",
    )
    try:
        setup_results = ingest_setup_events(service, case)
        forget_result = None
        forget_action = dict(case.get("forget_action") or {})
        if forget_action:
            forget_result = service.forget_agent_interactions(
                dict(forget_action.get("filters") or {}),
                confirm=bool(forget_action.get("confirm")),
                compact=True,
                external_persistence=False,
            )
        model = str(case.get("model") or args.model).strip()
        runner = OpenHandsMemoryRunner(
            service=service,
            session_id="%s-live" % case_id.lower(),
            workspace=workspace,
            state_dir=state_dir / "openhands-state",
            project_root=str(workspace),
            task_type=str(case.get("task_type") or "code_assistance"),
            model=model,
            api_key=os.environ.get(args.api_key_env, ""),
            base_url=args.base_url,
            max_iterations=int(case.get("max_iterations") or 6),
            tool_profile=str(case.get("tool_profile") or "terminal"),
        )
        run_result = runner.run(str(case["task"]))
        events = service.meta.list_agent_events(limit=10000)
        context = flatten_context(run_result.get("context") or {})
        evaluation = evaluate_real_agent_run(
            case=case,
            context=context,
            events=events,
            workspace=workspace,
            session_id="%s-live" % case_id.lower(),
        )
        result = {
            "case_id": case_id,
            "title": case["title"],
            "category": case["category"],
            "model": model,
            "setup_event_count": len(setup_results),
            "forget_result": forget_result,
            "run": run_result,
            "evaluation": evaluation,
            "events": events,
            "workspace_before": before_manifest,
            "workspace_after": workspace_manifest(workspace),
        }
    except Exception as exc:
        events = service.meta.list_agent_events(limit=10000)
        result = {
            "case_id": case_id,
            "title": case["title"],
            "category": case["category"],
            "model": str(case.get("model") or args.model),
            "run_exception": {
                "type": exc.__class__.__name__,
                "message": str(exc),
                "traceback": traceback.format_exc(),
            },
            "evaluation": {
                "status": "fail",
                "memory_system": {"passed": False, "failures": ["run exception"]},
                "agent_behavior": {"passed": False, "task_completed": False},
                "error_classification": ["environment_or_runner_error"],
            },
            "events": events,
            "workspace_before": before_manifest,
            "workspace_after": workspace_manifest(workspace),
        }
    finally:
        service.close()
    result_file.write_text(
        json.dumps(result, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    return result


def flatten_context(context):
    flattened = {}
    for key in ("selected_topics", "selected_values", "selected_subtypes"):
        values = []
        for stage_values in dict(context.get(key) or {}).values():
            values.extend(str(item) for item in stage_values if item)
        flattened[key] = sorted(set(values))
    return flattened


def workspace_manifest(workspace: Path):
    return [
        {
            "path": str(path.relative_to(workspace)).replace("\\", "/"),
            "size": path.stat().st_size,
        }
        for path in sorted(workspace.rglob("*"))
        if path.is_file()
    ]


def render_report(metrics, runtime):
    return "\n".join(
        [
            "# MemGuardOS 真实 OpenHands 记忆价值评测",
            "",
            "本报告记录全部正式运行案例。失败案例不会被删除或择优替换。",
            "",
            "- 案例数：%s" % metrics["case_count"],
            "- 记忆系统层通过率：%.4f" % metrics["memory_system_pass_rate"],
            "- Agent 行为层通过率：%.4f" % metrics["agent_behavior_pass_rate"],
            "- 状态分布：`%s`"
            % json.dumps(metrics["status_distribution"], ensure_ascii=False),
            "- 失败案例：`%s`"
            % json.dumps(metrics["failed_case_ids"], ensure_ascii=False),
            "- OpenHands 运行时：`%s`"
            % json.dumps(runtime, ensure_ascii=False),
            "",
            "记忆系统层与 Agent 行为层分开统计。本地小模型执行失败不自动等同于"
            "记忆检索失败，反之亦然。",
            "",
        ]
    )


def _safe_workspace_path(workspace: Path, relative_path: str) -> Path:
    root = workspace.resolve()
    target = (root / relative_path).resolve()
    target.relative_to(root)
    return target


def build_parser():
    parser = argparse.ArgumentParser(
        description="运行 openKylin/OpenHands 真实智能体记忆价值套件"
    )
    parser.add_argument(
        "--dataset",
        default=str(ROOT / "data" / "benchmarks" / "real_agent_behavior.jsonl"),
    )
    parser.add_argument(
        "--output-dir",
        default=str(ROOT / "outputs" / "openhands_behavior_suite"),
    )
    parser.add_argument(
        "--model",
        default=os.environ.get("OPENHANDS_MODEL", "ollama/qwen3:1.7b"),
    )
    parser.add_argument(
        "--base-url",
        default=os.environ.get("OPENHANDS_BASE_URL", "http://127.0.0.1:11434"),
    )
    parser.add_argument("--api-key-env", default="OPENHANDS_API_KEY")
    parser.add_argument("--case-id", action="append", default=[])
    parser.add_argument("--resume", action="store_true")
    parser.add_argument("--validate-only", action="store_true")
    return parser


def main():
    args = build_parser().parse_args()
    cases = load_cases(Path(args.dataset).resolve())
    validation = validate_cases(cases)
    output_dir = Path(args.output_dir).resolve()
    output_dir.mkdir(parents=True, exist_ok=True)
    (output_dir / "dataset_validation.json").write_text(
        json.dumps(validation, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    if not validation["valid"]:
        print(json.dumps(validation, ensure_ascii=False, indent=2))
        return 2
    if args.validate_only:
        print(json.dumps(validation, ensure_ascii=False, indent=2))
        return 0
    runtime = detect_openhands_runtime()
    if not runtime["available"]:
        (output_dir / "runtime.json").write_text(
            json.dumps(runtime, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        print(json.dumps(runtime, ensure_ascii=False, indent=2))
        return 3
    selected_ids = set(args.case_id)
    selected = [
        case for case in cases if not selected_ids or case["case_id"] in selected_ids
    ]
    results = [run_case(case, output_dir, args) for case in selected]
    metrics = aggregate_real_agent_results(results)
    metrics["runtime"] = runtime
    (output_dir / "metrics.json").write_text(
        json.dumps(metrics, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    (output_dir / "report.md").write_text(
        render_report(metrics, runtime),
        encoding="utf-8",
    )
    print(json.dumps(metrics, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
