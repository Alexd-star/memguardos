"""Seven real Agent sessions; client-managed learning is not model extraction.

Only sanitized evidence is exported. Stores, projects and SDK caches are disposable.
"""

from __future__ import annotations

import argparse
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import platform
import sys
from tempfile import TemporaryDirectory
import traceback

ROOT = Path(__file__).resolve().parents[1]
if "--installed" not in sys.argv and str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from memguard.api.server import handle_request  # noqa: E402
from memguard.eval.real_agent_behavior import evaluate_real_agent_run  # noqa: E402
from memguard.integrations.adherence import (  # noqa: E402
    shell_command_executables,
    detect_adherence_issues,
    final_visible_output,
    successful_terminal_commands,
)
from memguard.integrations.openhands_memory_hook import context_snapshot_status  # noqa: E402
from memguard.integrations.openhands_runner import OpenHandsMemoryRunner  # noqa: E402
from memguard.json_support import jsonable  # noqa: E402
from memguard.procedural import ProcedureSpec, verify_postconditions  # noqa: E402
from memguard.service import MemGuardService  # noqa: E402

PROJECT_FILES = {
    "settings.py": 'import json\n\ndef load_settings(path):\n    """Read a JSON settings file."""\n'
    '    with open(path, encoding="utf-8") as stream:\n        return json.load(stream)\n',
    "README.md": "# Settings utility\nA small JSON configuration loader.\n",
}
SEARCH_TASK = (
    "搜索项目代码中 load_settings 函数的定义，给出文件位置并解释返回值，不修改文件。"
)
PREFERENCE = "以后搜索项目代码优先使用 rg，并且回答时先给可复制命令，再解释结果。"
GOAL = "复核项目发布说明并保留检查结果"
SPEC = {
    "goal": GOAL,
    "steps": [
        "从任务文件读取 file 参数，打开 $file 并核对版本与变更。",
        "完成复核后将 $file 中的 TODO 改为 REVIEWED，保留版本与变更信息。",
    ],
    "failure_guards": ["检查未通过时不可宣称完成。", "只修改任务指定的发布说明。"],
    "parameters": {"file": None},
    "postconditions": [
        {"type": "path_exists", "path": "$file"},
        {"type": "file_contains", "path": "$file", "value": "REVIEWED"},
        {"type": "file_not_contains", "path": "$file", "value": "TODO"},
    ],
    "environment": {"platform_system": platform.system()},
}
MODULE_NAMES = (
    "memguard.feedback",
    "memguard.extraction.semantic",
    "memguard.integrations.adherence",
    "memguard.service",
    "memguard.procedural",
    "memguard.procedure_service",
    "memguard.api.server",
    "memguard.integrations.openhands_runner",
    "memguard.integrations.openhands_memory_hook",
    "memguard.integrations.openhands_adapter",
    "memguard.eval.real_agent_behavior",
)


def now():
    return datetime.now(timezone.utc).isoformat()


def module_manifest(installed=False):
    modules = {}
    for name in MODULE_NAMES:
        path = Path(sys.modules[name].__file__).resolve()
        if installed and not path.is_relative_to(Path("/opt/memguardos").resolve()):
            raise ValueError(
                "--installed requires all product modules from /opt/memguardos"
            )
        modules[name] = {
            "path": str(path),
            "sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
        }
    modules["showcase_driver"] = {
        "path": str(Path(__file__).resolve()),
        "sha256": hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
    }
    return modules


def redact(value, service, secret):
    value = jsonable(value)
    if isinstance(value, dict):
        return {
            redact(key, service, secret): redact(item, service, secret)
            for key, item in value.items()
        }
    if isinstance(value, list):
        return [redact(item, service, secret) for item in value]
    if isinstance(value, str):
        if secret:
            value = value.replace(secret, "[REDACTED:credential]")
        return service.detector.redact(value)
    return value


def write_json(path, value):
    with path.open("w", encoding="utf-8", newline="\n") as stream:
        stream.write(json.dumps(value, ensure_ascii=False, indent=2) + "\n")


def visible_events(events):
    """Use SDK display text for layout checks, without discarding original evidence."""
    result = []
    for event in events:
        item = dict(event)
        for field in ("content", "tool_result", "tool_args"):
            if "display_" + field in item and isinstance(
                item["display_" + field], type(item.get(field))
            ):
                item[field] = item["display_" + field]
        if not dict(item.get("tool_args") or {}).get("execution_origin") and not str(
            item.get("source_uri") or ""
        ).startswith("memguard-recovery://"):
            result.append(item)
    return result


class Showcase:
    def __init__(self, output, private, args, modules, runner_factory):
        self.output, self.private, self.args = output, private, args
        self.modules, self.runner_factory = modules, runner_factory
        self.project = (private / "project").resolve()
        self.project.mkdir(parents=True)
        for name, content in PROJECT_FILES.items():
            (self.project / name).write_text(content, encoding="utf-8", newline="\n")
        self.service = MemGuardService(
            private / "memory.segment",
            private / "memory.sqlite3",
            namespace="real-showcase",
        )
        self.secret = os.environ.get(args.api_key_env, "")
        self.results = []
        self.current = None

    def save(self):
        self.current["generated_at"] = now()
        write_json(
            self.output / self.current["result_path"],
            redact(self.current, self.service, self.secret),
        )

    def check(self, name, passed, **details):
        self.current["product_checks"].append(
            {"name": name, "passed": bool(passed), **details}
        )
        self.save()

    def api(self, command, **payload):
        request = {"command": command, **payload}
        response = handle_request(self.service, request)
        self.current["setup_results"].append(
            {
                "origin": "client_product_api",
                "at": now(),
                "request": request,
                "response": response,
            }
        )
        if not response.get("ok") and "result" not in response:
            self.check("API " + command, False)
        self.save()
        return response.get("result") or {}

    def remember(self, text, session="learning"):
        return self.api(
            "ingest",
            text=text,
            natural=True,
            session_id=session,
            project_root=str(self.project),
        )

    def error(self, phase):
        self.current["errors"].append(
            {"phase": phase, "traceback": traceback.format_exc()}
        )
        self.check(phase + " completed without exception", False)

    def case(
        self,
        case_id,
        title,
        task,
        *,
        enabled=True,
        paired=False,
        setup=None,
        after=None,
        memory=None,
        behavior=None,
        expected_tool="",
        task_type="code_assistance",
    ):
        relative = Path("cases") / case_id
        if paired:
            relative /= "memory_on" if enabled else "memory_off"
        (self.output / relative).mkdir(parents=True, exist_ok=False)
        session = case_id.lower() + ("-on" if enabled else "-off")
        self.current = {
            "case_id": case_id,
            "title": title,
            "task": task,
            "started_at": now(),
            "model": self.args.model,
            "model_config": {"base_url": self.args.base_url},
            "modules": self.modules,
            "session_id": session,
            "project_root": str(self.project),
            "memory_mode": "on" if enabled else "off",
            "arm_source": "real_agent_run",
            "result_path": (relative / "result.json").as_posix(),
            "setup_results": [],
            "product_checks": [],
            "errors": [],
            "events": [],
            "run": {"memory_enabled": enabled},
            "evaluation": {"status": "incomplete"},
            "workspace_before": self.files(),
            "memory_assertions": memory or {},
            "behavior_assertions": behavior or {},
            "expected_tool": expected_tool,
        }
        self.results.append(self.current)
        self.save()
        if setup:
            try:
                setup(self)
            except Exception:
                self.error("setup")
        self.current["workspace_before"] = self.files()
        runner = None
        state = self.private / "runtime" / session
        try:
            runner = self.runner_factory(
                self.service,
                session_id=session,
                workspace=self.project,
                state_dir=state,
                project_root=str(self.project),
                task_type=task_type,
                model=self.args.model,
                base_url=self.args.base_url,
                api_key=self.secret,
                context_token_budget_per_stage=2000,
                max_iterations=getattr(self.args, "max_iterations", 10),
                tool_profile="terminal",
                memory_enabled=enabled,
            )
            self.current["run"] = runner.run(task)
        except Exception:
            self.error("agent_run")
        finally:
            if runner is not None:
                self.current["events"] = runner.recorded_events()
            contexts = sorted(
                state.glob("ctx*.json"), key=lambda path: path.stat().st_mtime_ns
            )
            self.current["context"] = (
                json.loads(contexts[-1].read_text(encoding="utf-8")) if contexts else {}
            )
            self.current["workspace_after"] = self.files()
            self.save()
        if after:
            try:
                after(self)
            except Exception:
                self.error("postchecks")
        self.evaluate(expected_tool, behavior or {}, memory or {}, enabled)
        self.save()
        print(
            case_id
            + (" on: " if enabled else " off: ")
            + self.current["evaluation"]["status"],
            flush=True,
        )
        return self.current

    def files(self):
        return {
            path.name: {
                "text": path.read_text(encoding="utf-8"),
                "sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
            }
            for path in self.project.iterdir()
            if path.is_file()
        }

    def evaluate(self, expected_tool, behavior, memory, enabled):
        snapshot = self.current.get("context") or {}
        stages = snapshot.get("stages") or {}
        flat = {
            key: [
                str(row.get(field) or "")
                for stage in stages.values()
                for row in stage.get("memories") or []
            ]
            for key, field in (
                ("selected_topics", "preference_topic"),
                ("selected_values", "preference_value"),
                ("selected_subtypes", "subtype"),
            )
        }
        native = visible_events(self.current["events"])
        evaluation = evaluate_real_agent_run(
            {"memory_assertions": memory, "behavior_assertions": behavior},
            flat,
            native,
            workspace=self.project,
            session_id=self.current["session_id"],
        )
        commands = successful_terminal_commands(native)
        failures = evaluation["agent_behavior"]["failures"]
        if self.current["errors"] or not self.current["run"].get("ok"):
            failures.append("runner failed or did not verify completion")
        if self.current["run"].get("memory_enabled") is not enabled:
            self.check("实际记忆开关与请求一致", False)
        self.check(
            "上下文绑定当前会话、项目与记忆开关",
            snapshot.get("session_id") == self.current["session_id"]
            and snapshot.get("project_root") == str(self.project)
            and (snapshot.get("memory_snapshot") or {}).get("memory_enabled")
            is enabled,
        )
        if not enabled:
            self.check(
                "关闭记忆后各阶段没有注入历史内容",
                bool(stages)
                and not any(stage.get("memories") for stage in stages.values()),
            )
        if expected_tool and not any(
            expected_tool in shell_command_executables(command) for command in commands
        ):
            failures.append("no successful native preferred search tool")
        if self.current["task"] == SEARCH_TASK and not commands:
            failures.append("no successful native project inspection")
        if enabled and detect_adherence_issues(
            stages, native, task=self.current["task"]
        ):
            failures.append("native model did not follow selected preferences")
        evaluation["agent_behavior"].update(
            passed=not failures,
            task_completed=not failures,
            native_successful_commands=commands,
            final_output=final_visible_output(native),
        )
        product_ok = all(check["passed"] for check in self.current["product_checks"])
        evaluation["memory_system"]["passed"] &= product_ok
        evaluation["memory_system"]["product_checks"] = self.current["product_checks"]
        evaluation["status"] = (
            "pass" if not failures and evaluation["memory_system"]["passed"] else "fail"
        )
        self.current["evaluation"] = evaluation


def preferences(d):
    learned = d.remember(PREFERENCE)
    rows = d.service.list_memories(limit=100)
    d.style_ids = {
        row["memory_id"] for row in rows if row["preference_topic"] == "response_order"
    }
    d.check(
        "一句话拆成工具与回答两条独立偏好",
        learned.get("stored")
        and bool(d.style_ids)
        and any(row["preference_topic"] == "code_search_tool" for row in rows),
    )


def correct_tool(d):
    result = d.remember("纠正一下：以后搜索项目代码优先使用 grep。", "correction")
    d.check(
        "纠正工具偏好后保留原有回答偏好",
        result.get("stored")
        and all(not d.service.meta.get_memory(mid)["deleted"] for mid in d.style_ids),
    )


def forget_tool(d):
    preview = d.service.forget_by_intent(
        "忘记搜索代码使用 grep 的工具偏好", confirm=False
    )
    d.current["setup_results"].append(
        {
            "origin": "client_product_api",
            "at": now(),
            "request": {"forget_by_intent": "tool preference", "confirm": False},
            "response": preview,
        }
    )
    candidates = preview["result"].get("candidates") or []
    ids = [row["memory_id"] for row in candidates]
    safe = (
        bool(ids)
        and not d.style_ids.intersection(ids)
        and all(
            d.service.meta.get_memory(mid)["preference_topic"] == "code_search_tool"
            for mid in ids
        )
    )
    d.check("遗忘预览只选工具偏好", safe, preview=preview)
    if safe:
        versions = {mid: d.service.meta.get_memory(mid)["version_id"] for mid in ids}
        receipt = d.api(
            "forget_memory_ids",
            memory_ids=ids,
            reason="user selected preview candidates",
            compact=True,
            expected_versions=versions,
        )
        d.check(
            "按预览版本遗忘工具偏好，保留回答偏好",
            receipt.get("ok")
            and all(
                not d.service.meta.get_memory(mid)["deleted"] for mid in d.style_ids
            ),
            receipt=receipt,
        )


def facts(d):
    independent = d.remember("青山项目文档归档使用 Markdown 格式。")
    first = d.remember("青山项目发布日是周三。")
    pending = d.remember("青山项目发布日是周五。")
    d.check(
        "矛盾事实进入核对队列，确认前没有覆盖旧值",
        not pending.get("stored")
        and pending.get("reason") == "fact_conflict_requires_review",
    )
    accepted = d.api(
        "resolve_fact_conflict",
        conflict_id=pending.get("conflict_id", ""),
        decision="accept_incoming",
    )
    d.check(
        "接受周五新值形成版本链，并保留独立知识",
        accepted.get("resolved")
        and len(d.service.versions(first["memory_id"])) == 2
        and not d.service.meta.get_memory(independent["memory_id"])["deleted"],
    )


def fact_answer(d):
    answer = final_visible_output(visible_events(d.current["events"]))
    d.current["model_answer_check"] = {
        "passed": "周五" in answer and "周三" not in answer,
        "answer": answer,
    }
    if not d.current["model_answer_check"]["passed"]:
        d.current["errors"].append(
            {
                "phase": "model_answer",
                "message": "final answer did not use accepted Friday",
            }
        )


def release_setup(d):
    d.source = d.remember("项目发布说明在提交前需要复核，并保留检查结果。")
    d.independent = d.remember("项目文档归档使用 Markdown 格式。")
    for suffix in ("a", "b"):
        (d.project / f"release-{suffix}.md").write_text(
            f"Version {1 if suffix == 'a' else 2}\nChanges: configuration loader\nTODO\n",
            encoding="utf-8",
            newline="\n",
        )
        (d.project / f"task-{suffix}.json").write_text(
            json.dumps(
                {"goal": GOAL, "file": f"release-{suffix}.md"}, ensure_ascii=False
            )
            + "\n",
            encoding="utf-8",
            newline="\n",
        )
    before = verify_postconditions(
        ProcedureSpec.from_dict(SPEC), d.project, {"file": "release-a.md"}
    )
    d.check(
        "未复核的文件尚不满足完成条件",
        before["overall"] == "fail",
        verification=before,
    )
    d.current["client_supplied_procedure_spec"] = SPEC


def learn_release(d):
    arguments = json.loads((d.project / "task-a.json").read_text(encoding="utf-8"))
    arguments = {"file": arguments["file"]}
    verified = verify_postconditions(
        ProcedureSpec.from_dict(SPEC), d.project, arguments
    )
    d.check(
        "Agent 实际复核后的文件满足独立完成条件",
        verified["overall"] == "pass",
        verification=verified,
    )
    d.procedure_id = ""
    if verified["overall"] == "pass":
        learned = d.api(
            "learn_procedure",
            spec=SPEC,
            workspace=str(d.project),
            source_memory_ids=[d.source["memory_id"]],
            session_id="a05-on",
            arguments=arguments,
        )
        d.procedure_id = learned.get("memory_id", "")
        d.check(
            "验证通过后学习客户端流程规范，并绑定来源版本",
            learned.get("verified")
            and learned.get("stored")
            and learned.get("dependencies")
            == [
                {
                    "memory_id": d.source["memory_id"],
                    "version_id": d.service.meta.get_memory(d.source["memory_id"])[
                        "version_id"
                    ],
                    "relation": "derived_from",
                }
            ],
        )


def reuse_setup(d):
    d.arguments = {
        "file": json.loads((d.project / "task-b.json").read_text(encoding="utf-8"))[
            "file"
        ]
    }
    recalled = d.api(
        "recall_procedures",
        task=GOAL,
        workspace=str(d.project),
        session_id="a06-on",
        arguments=d.arguments,
    )
    before = d.api(
        "verify_procedure",
        memory_id=d.procedure_id,
        workspace=str(d.project),
        session_id="a06-on",
        use_id="before",
        arguments=d.arguments,
    )
    d.check(
        "新任务绑定不同文件参数，执行前检查为未完成",
        before.get("overall") == "fail"
        and bool(recalled)
        and "release-b.md" in recalled[0]["steps"][1],
        verification=before,
    )


def revoke_release(d):
    completed = d.api(
        "verify_procedure",
        memory_id=d.procedure_id,
        workspace=str(d.project),
        session_id="a06-on",
        use_id="after",
        arguments=d.arguments,
    )
    old = d.current["context"]
    selected = {
        row["memory_id"]
        for stage in old.get("stages", {}).values()
        for row in stage.get("memories", [])
    }
    d.check(
        "Agent 采用召回流程完成新文件，检查由失败转为通过",
        completed.get("verified") and d.procedure_id in selected,
        verification=completed,
    )
    updated = d.api(
        "update",
        memory_id=d.source["memory_id"],
        text="项目发布说明必须由两名维护者复核。",
    )
    stale = context_snapshot_status(old)
    fresh = OpenHandsMemoryRunner(
        d.service,
        session_id="after-source-update",
        workspace=d.project,
        state_dir=d.private / "runtime" / "after-source-update",
        project_root=str(d.project),
        context_token_budget_per_stage=2000,
        max_iterations=6,
    ).prepare_context(GOAL)
    ids = {
        row["memory_id"]
        for stage in fresh["stages"].values()
        for row in stage.get("memories", [])
    }
    d.check(
        "来源更新后旧上下文失效，新上下文排除旧流程",
        updated.get("updated") and not stale["ok"] and d.procedure_id not in ids,
        old_snapshot_status=stale,
        new_context=fresh,
    )
    impact = d.api("dependency_impact", memory_ids=[d.source["memory_id"]])
    receipt = d.api(
        "forget_memory_ids",
        memory_ids=[d.source["memory_id"]],
        compact=True,
        reason="withdraw source and hard-derived closure",
        expected_versions={
            d.source["memory_id"]: d.service.meta.get_memory(d.source["memory_id"])[
                "version_id"
            ],
            **impact.get("expected_versions", {}),
        },
    )
    d.check(
        "撤回来源同步清理派生流程与反馈，并保留独立记忆",
        receipt.get("ok")
        and {d.source["memory_id"], d.procedure_id}.issubset(
            receipt.get("forgotten_memory_ids", [])
        )
        and d.service.meta.memory_dependencies(d.procedure_id) == []
        and d.service.meta.procedure_stats(d.procedure_id)["measured"] == 0
        and not d.service.meta.get_memory(d.independent["memory_id"])["deleted"],
        receipt=receipt,
    )


def run_showcase(output, args, runner_factory=OpenHandsMemoryRunner):
    modules = module_manifest(args.installed)
    output = Path(output).resolve()
    output.mkdir(parents=True, exist_ok=False)
    started, results = now(), []
    with TemporaryDirectory(prefix="memguard-real-showcase-") as temporary:
        private = Path(temporary).resolve()
        drivers = []
        try:
            on = Showcase(
                output, private / "preferences", args, modules, runner_factory
            )
            off = Showcase(output, private / "baseline", args, modules, runner_factory)
            fact = Showcase(output, private / "facts", args, modules, runner_factory)
            release = Showcase(
                output, private / "release", args, modules, runner_factory
            )
            drivers.extend((on, off, fact, release))
            result = on.case(
                "A01",
                "学习偏好后查函数",
                SEARCH_TASK,
                paired=True,
                setup=preferences,
                expected_tool="rg",
                memory={"expected_topics": ["code_search_tool", "response_order"]},
                behavior={"output_contains": "load_settings"},
            )
            results.append(result)
            results.append(
                off.case(
                    "A01",
                    "学习偏好后查函数",
                    SEARCH_TASK,
                    paired=True,
                    enabled=False,
                    behavior={"output_contains": "load_settings"},
                )
            )
            results.append(
                on.case(
                    "A02",
                    "纠正为 grep 后的新会话",
                    SEARCH_TASK,
                    setup=correct_tool,
                    expected_tool="grep",
                    memory={
                        "expected_values": ["grep"],
                        "retained_topics": ["response_order"],
                    },
                    behavior={"output_contains": "load_settings"},
                )
            )
            results.append(
                on.case(
                    "A03",
                    "只忘工具，保留回答偏好",
                    SEARCH_TASK,
                    setup=forget_tool,
                    memory={
                        "absent_topics": ["code_search_tool"],
                        "retained_topics": ["response_order"],
                    },
                    behavior={"output_contains": "load_settings"},
                )
            )
            results.append(
                fact.case(
                    "A04",
                    "事实冲突接受新版本",
                    "青山项目发布日是哪一天？",
                    setup=facts,
                    after=fact_answer,
                    task_type="question_answering",
                )
            )
            results.append(
                release.case(
                    "A05",
                    "实际复核后学习客户端流程规范",
                    f"{GOAL}：读取 task-a.json 中的目标文件，核对版本与变更，将 TODO 改为 REVIEWED 并保留其他内容。",
                    setup=release_setup,
                    after=learn_release,
                    task_type="documentation",
                )
            )
            results.append(
                release.case(
                    "A06",
                    "新参数复用、来源更新与撤回",
                    f"{GOAL}：本次任务参数在 task-b.json，请处理该任务并检查结果。",
                    setup=reuse_setup,
                    after=revoke_release,
                    task_type="documentation",
                )
            )
            password = fact.remember("记住我的密码 11515。", "management")
            temporary_fact = fact.remember(
                "临时项目代号是蓝河，该信息只保留到本次会话结束。", "temporary"
            )
            hits = fact.service.search(
                "蓝河", project_root=str(fact.project), session_id="other", audit=False
            )
            management = {
                "origin": "client_product_api",
                "model_calls": 0,
                "password_refused": not password.get("stored"),
                "password_result": password,
                "session_isolated": temporary_fact.get("stored")
                and temporary_fact.get("memory_id")
                not in {row["memory_id"] for row in hits},
                "session_result": temporary_fact,
            }
            fact.save()
            summary = {
                "schema": "memguardos-real-agent-showcase-v1",
                "started_at": started,
                "generated_at": now(),
                "mode": "real_openhands_agent"
                if runner_factory is OpenHandsMemoryRunner
                else "in_process_test_factory",
                "external_llm_used": runner_factory is OpenHandsMemoryRunner,
                "agent_sessions": len(results),
                "max_agent_sessions": 7,
                "llm_request_limit_note": "Seven runner.run sessions; SDK iterations/repair may use multiple requests.",
                "modules": modules,
                "results": [
                    {
                        "case_id": row["case_id"],
                        "memory_mode": row["memory_mode"],
                        "result_path": row["result_path"],
                        "status": row["evaluation"]["status"],
                    }
                    for row in results
                ],
                "management_checks": management,
                "passed": all(row["evaluation"]["status"] == "pass" for row in results)
                and management["password_refused"]
                and management["session_isolated"],
            }
            write_json(
                output / "summary.json", redact(summary, fact.service, fact.secret)
            )
            return summary
        finally:
            for driver in drivers:
                driver.service.close()


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--installed", action="store_true")
    parser.add_argument("--max-iterations", type=int, default=10)
    parser.add_argument("--model", default=os.environ.get("OPENHANDS_MODEL", ""))
    parser.add_argument(
        "--base-url",
        "--baseurl",
        dest="base_url",
        default=os.environ.get("OPENHANDS_BASE_URL", ""),
    )
    parser.add_argument(
        "--api-key-env",
        default=os.environ.get("OPENHANDS_API_KEY_ENV", "OPENHANDS_API_KEY"),
    )
    args = parser.parse_args(argv)
    if not 1 <= args.max_iterations <= 50:
        parser.error("--max-iterations must be between 1 and 50")
    if args.output_dir.exists():
        parser.error("--output-dir must be a fresh, nonexistent directory")
    if not args.model.strip():
        parser.error("--model or OPENHANDS_MODEL is required")
    result = run_showcase(args.output_dir, args)
    print("Evidence: " + str(args.output_dir.resolve()))
    return 0 if result["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
