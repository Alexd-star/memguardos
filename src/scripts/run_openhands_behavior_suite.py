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
    aggregate_real_agent_paired_results,
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
    expected_ids = ["R%02d" % index for index in range(1, len(cases) + 1)]
    case_ids = [str(case.get("case_id") or "") for case in cases]
    if case_ids != expected_ids:
        errors.append("case ids must be contiguous R01 through R%02d in order" % len(cases))
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


def ingest_setup_events(service, case, workspace: Path | None = None):
    last_commands = {}
    stored = []
    for sequence, raw in enumerate(case.get("setup_events") or [], start=1):
        item = dict(raw)
        session_id = str(item.get("session_id") or "%s-history" % case["case_id"])
        tool_args = dict(item.get("tool_args") or {})
        project_root = _resolve_setup_project_root(item.get("project_root"), workspace)
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
            project_root=project_root,
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
                    cwd=project_root,
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
                session_id=session_id,
            )
    return stored


def run_case(case, output_dir: Path, args, memory_mode: str = "on", paired_layout: bool = False):
    if memory_mode not in {"on", "off"}:
        raise ValueError("case memory_mode must be on or off")
    case_id = str(case["case_id"])
    case_dir = output_dir / "cases" / case_id
    if paired_layout:
        case_dir = case_dir / ("memory_on" if memory_mode == "on" else "memory_off")
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
    runner = None
    api_key = os.environ.get(args.api_key_env, "")
    safe_task = service.detector.redact(str(case["task"]))
    setup_events = []
    try:
        setup_results = (
            ingest_setup_events(service, case, workspace=workspace)
            if memory_mode == "on"
            else []
        )
        forget_result = None
        forget_action = dict(case.get("forget_action") or {})
        if forget_action and memory_mode == "on":
            forget_result = service.forget_agent_interactions(
                dict(forget_action.get("filters") or {}),
                confirm=bool(forget_action.get("confirm")),
                compact=True,
                external_persistence=False,
            )
        model = str(case.get("model") or args.model).strip()
        setup_events = service.meta.list_agent_events(limit=10000) if memory_mode == "on" else []
        runner = OpenHandsMemoryRunner(
            service=service,
            session_id="%s-live" % case_id.lower(),
            workspace=workspace,
            state_dir=state_dir / "openhands-state",
            project_root=str(workspace),
            task_type=str(case.get("task_type") or "code_assistance"),
            model=model,
            api_key=api_key,
            base_url=args.base_url,
            max_iterations=int(case.get("max_iterations") or 6),
            tool_profile=str(case.get("tool_profile") or "terminal"),
            memory_enabled=memory_mode == "on",
        )
        run_result = runner.run(str(case["task"]))
        events = [*setup_events, *runner.recorded_events()]
        context = flatten_context(
            run_result.get("context") or {},
            adherence=run_result.get("adherence") or {},
        )
        evaluation_case = dict(case)
        if memory_mode == "off":
            evaluation_case["memory_assertions"] = {}
        evaluation = evaluate_real_agent_run(
            case=evaluation_case,
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
            "task": safe_task,
            "memory_mode": memory_mode,
            "arm_source": "real_agent_run",
            "expected_tool": str(case.get("expected_tool") or ""),
            "setup_event_count": len(setup_results),
            "recording_mode": "redacted_sdk_transcript_independent_of_memory_storage",
            "forget_result": forget_result,
            "run": run_result,
            "evaluation": evaluation,
            "events": events,
            "workspace_before": before_manifest,
            "workspace_after": workspace_manifest(workspace),
        }
    except Exception as exc:
        events = [*setup_events, *(runner.recorded_events() if runner is not None else [])]
        result = {
            "case_id": case_id,
            "title": case["title"],
            "category": case["category"],
            "model": str(case.get("model") or args.model),
            "task": safe_task,
            "memory_mode": memory_mode,
            "arm_source": "real_agent_run",
            "recording_mode": "redacted_sdk_transcript_independent_of_memory_storage",
            "expected_tool": str(case.get("expected_tool") or ""),
            "run_exception": {
                "type": exc.__class__.__name__,
                "message": service.detector.redact(str(exc)).replace(api_key, "[REDACTED:credential]") if api_key else service.detector.redact(str(exc)),
                "traceback": service.detector.redact(traceback.format_exc()).replace(api_key, "[REDACTED:credential]") if api_key else service.detector.redact(traceback.format_exc()),
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
    finalize_case_artifacts(
        case_dir,
        result,
        retain_runtime=bool(args.retain_runtime),
    )
    result_file.write_text(
        json.dumps(result, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    return result


def finalize_case_artifacts(case_dir: Path, result, retain_runtime: bool):
    if retain_runtime:
        return
    run = dict(result.get("run") or {})
    context_path = Path(str(run.get("context_file") or ""))
    if context_path.is_file():
        target = case_dir / "context.json"
        shutil.copy2(context_path, target)
        result["run"]["context_file"] = "context.json"
    for name in ("workspace", "state"):
        target = case_dir / name
        if target.is_dir():
            shutil.rmtree(target)


def flatten_context(context, adherence=None):
    flattened = {}
    for key in ("selected_topics", "selected_values", "selected_subtypes"):
        values = []
        for stage_values in dict(context.get(key) or {}).values():
            values.extend(str(item) for item in stage_values if item)
        flattened[key] = sorted(set(values))
    if isinstance(adherence, dict) and "passed" in adherence:
        flattened["adherence_passed"] = bool(adherence["passed"])
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


def render_paired_report(metrics, runtime):
    off = metrics["arms"]["memory_off"]
    on = metrics["arms"]["memory_on"]
    completion_test = metrics["paired_tests"]["task_completion"]
    tool_test = metrics["paired_tests"]["first_tool_accuracy"]
    return "\n".join(
        [
            "# MemGuardOS 真实 OpenHands 有/无记忆配对评测",
            "",
            "每个案例在相同模型、相同工作区夹具和相同任务下独立运行两次："
            "`memory_off` 不注入历史记忆，`memory_on` 启用 MemGuardOS。失败运行原样保留。",
            "",
            "- 配对案例数：%s" % metrics["case_count"],
            "- 无记忆任务完成率：%s" % _format_real_interval(off["task_completion"]),
            "- 启用记忆任务完成率：%s" % _format_real_interval(on["task_completion"]),
            "- 任务完成率 McNemar p：%s" % completion_test["p_value"],
            "- 无记忆首选工具准确率：%s" % _format_real_interval(off["first_tool_accuracy"]),
            "- 启用记忆首选工具准确率：%s" % _format_real_interval(on["first_tool_accuracy"]),
            "- 工具准确率 McNemar p：%s" % tool_test["p_value"],
            "- OpenHands 运行时：`%s`" % json.dumps(runtime, ensure_ascii=False),
            "",
            "错误命令和重复命令同时报告配对 bootstrap 95% 置信区间与 Wilcoxon 检验，"
            "完整逐案例双臂结果见 `metrics.json` 和 `cases/*/memory_{on,off}/result.json`。",
            "",
        ]
    )


def _format_real_interval(value):
    if value.get("point") is None:
        return "不适用"
    return "%.4f [%.4f, %.4f], n=%s" % (
        value["point"], value["ci_low"], value["ci_high"], value["n"]
    )


def _safe_workspace_path(workspace: Path, relative_path: str) -> Path:
    root = workspace.resolve()
    target = (root / relative_path).resolve()
    target.relative_to(root)
    return target


def _resolve_setup_project_root(value: object, workspace: Path | None) -> str:
    text = str(value or "").strip()
    if text in {"$WORKSPACE", "{workspace}", "<workspace>"}:
        return str(workspace.resolve()) if workspace is not None else ""
    return text


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
    parser.add_argument(
        "--memory-mode",
        choices=("on", "off", "paired"),
        default="on",
        help="启用记忆、关闭记忆，或对每个案例运行有/无记忆双臂",
    )
    parser.add_argument("--resume", action="store_true")
    parser.add_argument("--validate-only", action="store_true")
    parser.add_argument(
        "--retain-runtime",
        action="store_true",
        help="保留逐案例 workspace 和状态目录，仅用于本地调试",
    )
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
    if args.memory_mode == "paired":
        results = []
        for case in selected:
            memory_on = run_case(case, output_dir, args, memory_mode="on", paired_layout=True)
            memory_off = run_case(case, output_dir, args, memory_mode="off", paired_layout=True)
            results.append(
                {
                    "case_id": str(case["case_id"]),
                    "expected_tool": str(case.get("expected_tool") or ""),
                    "arms": {"memory_on": memory_on, "memory_off": memory_off},
                }
            )
        metrics = aggregate_real_agent_paired_results(results)
    else:
        results = [
            run_case(case, output_dir, args, memory_mode=args.memory_mode)
            for case in selected
        ]
        metrics = aggregate_real_agent_results(results)
        metrics["memory_mode"] = args.memory_mode
    metrics["runtime"] = runtime
    (output_dir / "metrics.json").write_text(
        json.dumps(metrics, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    (output_dir / "report.md").write_text(
        render_paired_report(metrics, runtime)
        if args.memory_mode == "paired"
        else render_report(metrics, runtime),
        encoding="utf-8",
    )
    print(json.dumps(metrics, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
