#!/usr/bin/env python3
"""Export recorded Agent evidence, without executing actions or loading references."""

from __future__ import annotations

import argparse
from dataclasses import dataclass
import hashlib
from html import escape
import json
import math
import os
from pathlib import Path
import re
import stat
import sys
import tempfile

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from memguard.sensitivity.detector import SensitiveDetector  # noqa: E402


MAX_INPUT_BYTES = 16 * 1024 * 1024
MAX_DEPTH = 64
PREVIEW_CHARS = 1600
MAX_FILES = 1024
ADAPTER_ORIGINS = (
    "repair",
    "internal_feedback",
    "structured_intent_recovery",
    "procedural_memory_fallback",
    "natural_high_risk_request_guard",
)
STAGES = {
    "planning": "规划",
    "tool_selection": "工具选择",
    "response_generation": "回复生成",
}
CSS = """
:root{color-scheme:light;font-family:"Microsoft YaHei","PingFang SC",sans-serif;
color:#39434b;background:#fff;line-height:1.6;font-size:14px}
*{box-sizing:border-box;letter-spacing:0}body{margin:0}main{max-width:1560px;margin:auto;padding:24px}
h1{font-size:24px;margin:0 0 6px}h2{font-size:19px;margin:0}h3{font-size:15px;margin:0 0 8px}
h4{font-size:14px;margin:0}p{margin:6px 0}a{color:#086c80}header,section,article,div{min-width:0}
.muted,.meta{color:#69747d;font-size:12px}.case{border-top:2px solid #dbe2e5;margin-top:24px;padding-top:16px}
.case-head,.event-head{display:flex;align-items:baseline;gap:10px;flex-wrap:wrap}
.arms{display:grid;grid-template-columns:repeat(2,minmax(0,1fr));gap:24px;margin-top:12px}
.arms.single{grid-template-columns:minmax(0,1fr)}.arm{min-width:0}
.arm-head{border-bottom:1px solid #dbe2e5;padding-bottom:10px;margin-bottom:12px}
.band{border-top:1px solid #e5eaed;margin-top:14px;padding-top:12px}
.event{border-left:3px solid #cbd3d8;padding:8px 10px;margin:10px 0;background:#fafbfc}
.tool{border-color:#078296;background:#f2fafb}.success{color:#147540}.failure{color:#b32e3a}
.adapter{border-left:3px solid #b32e3a;background:#fff5f5;padding:8px 10px;margin:10px 0}
.tag{font-size:12px;font-weight:600}.status{font-weight:700}.empty{color:#69747d;padding:6px 0}
pre,.text{white-space:pre-wrap;overflow-wrap:anywhere;word-break:break-word;margin:6px 0}
pre{font-family:Consolas,"Cascadia Mono",monospace;font-size:12px;line-height:1.55}
details{margin:8px 0}summary{cursor:pointer;color:#53606b}dl{margin:6px 0}
dt{color:#69747d;font-size:12px}dd{margin:0 0 6px;overflow-wrap:anywhere}
.source{border-top:1px solid #e5eaed;margin-top:14px;padding-top:8px}
nav{display:flex;flex-wrap:wrap;gap:12px;margin-top:12px}
@media(max-width:900px){main{padding:16px}.arms{grid-template-columns:minmax(0,1fr);gap:18px}}
@media print{main{padding:0}.case{break-before:auto}.event{break-inside:avoid}nav{display:none}}
"""


class ReportError(ValueError):
    pass


@dataclass(frozen=True)
class Source:
    name: str
    sha256: str
    case_id: str
    mode: str
    data: dict


def _credential_key(key: str) -> bool:
    normalized = re.sub(r"[^a-z0-9]", "", key.lower())
    return (
        any(
            part in normalized
            for part in (
                "apikey",
                "accesskey",
                "secretkey",
                "privatekey",
                "apitoken",
                "authtoken",
            )
        )
        or normalized
        in {
            "password",
            "passwd",
            "pwd",
            "passcode",
            "pin",
            "secret",
            "secrets",
            "token",
            "authorization",
            "credentials",
            "credential",
            "cookie",
            "cookies",
            "bearer",
            "clientsecret",
        }
        or normalized.endswith(("password", "passwd", "secret", "token", "credentials"))
    )


class Redactor:
    def __init__(self, documents: list[dict]):
        self.detector = SensitiveDetector(
            custom_patterns={
                "export_private_key": {
                    "regex": r"-----BEGIN [A-Z ]*PRIVATE KEY-----[\s\S]*?(?:-----END [A-Z ]*PRIVATE KEY-----|$)",
                    "level": "high",
                },
                "export_bearer": {
                    "regex": r"(?i)\bBearer[ \t]+[A-Za-z0-9._~+/=-]+",
                    "level": "high",
                },
                "export_credential_assignment": {
                    "regex": (
                        r"(?i)\b[\w-]*(?:api[_-]?key|access[_-]?key|token|secret|password|passwd|pwd)"
                        r"""["']?\s*[:=：]\s*(?:"[^"\r\n]*"|'[^'\r\n]*'|[^\s,;}]+)"""
                    ),
                    "level": "high",
                },
            }
        )
        self.secrets: set[str] = set()
        for document in documents:
            self._collect(document)
        # A single substitution prevents a credential from being reintroduced
        # or partially exposed by overlapping replacements elsewhere in the record.
        self.secret_pattern = (
            re.compile(
                "|".join(
                    re.escape(item)
                    for item in sorted(self.secrets, key=len, reverse=True)
                )
            )
            if self.secrets
            else None
        )

    def _collect(self, value, secret: bool = False) -> None:
        if isinstance(value, dict):
            for key, item in value.items():
                self._collect(item, secret or _credential_key(key))
        elif isinstance(value, list):
            for item in value:
                self._collect(item, secret)
        elif (
            secret
            and isinstance(value, (str, int, float))
            and not isinstance(value, bool)
        ):
            if str(value):
                self.secrets.add(str(value))
        elif isinstance(value, str):
            for finding in self.detector.detect(value):
                if finding.label in {"api_key", "jwt", "aws_access_key_id"}:
                    self.secrets.add(finding.value)
                elif finding.label in {"password_secret", "weak_password_hint_cn"}:
                    for name in ("password_secret", "password_remember"):
                        match = self.detector.patterns[name].match(value, finding.start)
                        if match:
                            self.secrets.add(match.group("value").strip("\"'“”‘’"))
                elif finding.label in {
                    "token_assignment",
                    "export_credential_assignment",
                    "aws_secret_access_key",
                }:
                    parts = re.split(r"[:=：]", finding.value, maxsplit=1)
                    if len(parts) == 2 and parts[1].strip(" \t\"'"):
                        self.secrets.add(parts[1].strip(" \t\"'"))
                elif finding.label == "export_bearer":
                    self.secrets.add(finding.value.split(None, 1)[1])
                elif finding.label == "export_private_key":
                    self.secrets.update(
                        line.strip()
                        for line in finding.value.splitlines()
                        if line.strip() and not line.startswith("-----")
                    )

    def text(self, value: str) -> str:
        value = self.detector.redact(value)
        if self.secret_pattern:
            value = self.secret_pattern.sub("[REDACTED:credential]", value)
        return value

    def clean(self, value):
        if isinstance(value, dict):
            return {
                self.text(key): self.clean(item)
                for key, item in value.items()
                if not _credential_key(key)
            }
        if isinstance(value, list):
            return [self.clean(item) for item in value]
        return self.text(value) if isinstance(value, str) else value


def _no_duplicates(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ReportError("JSON 包含重复字段")
        result[key] = value
    return result


def _reject_constant(_value):
    raise ReportError("JSON 包含非有限数值")


def _validate_tree(value, depth: int = 0) -> None:
    if depth > MAX_DEPTH:
        raise ReportError("JSON 嵌套超过 64 层")
    if isinstance(value, dict):
        for key, item in value.items():
            _validate_tree(key, depth + 1)
            _validate_tree(item, depth + 1)
    elif isinstance(value, list):
        for item in value:
            _validate_tree(item, depth + 1)
    elif isinstance(value, str):
        if any(0xD800 <= ord(char) <= 0xDFFF for char in value):
            raise ReportError("JSON 包含非法 Unicode 字符")
    elif isinstance(value, float) and not math.isfinite(value):
        raise ReportError("JSON 包含非有限数值")


def _validate_record(data) -> None:
    if not isinstance(data, dict) or not set(data) & {
        "case_id",
        "task",
        "run",
        "events",
        "evaluation",
        "run_exception",
        "setup_results",
    }:
        raise ReportError("输入必须是单 case 的 JSON 结果对象")
    for key in ("case_id", "title", "model", "memory_mode"):
        if data.get(key) is not None and not isinstance(data[key], str):
            raise ReportError("字段 %s 必须是字符串" % key)
    for key in ("run", "evaluation", "run_exception"):
        if data.get(key) is not None and not isinstance(data[key], dict):
            raise ReportError("字段 %s 必须是对象" % key)
    if data.get("task") is not None and not isinstance(data["task"], (str, dict)):
        raise ReportError("字段 task 必须是字符串或对象")
    run = data.get("run") or {}
    if run.get("session_id") is not None and not isinstance(run["session_id"], str):
        raise ReportError("run.session_id 必须是字符串")
    if run.get("task") is not None and not isinstance(run["task"], (str, dict)):
        raise ReportError("run.task 必须是字符串或对象")
    evaluation = data.get("evaluation") or {}
    if evaluation.get("status") is not None and not isinstance(
        evaluation["status"], str
    ):
        raise ReportError("evaluation.status 必须是字符串")
    for label, items in (
        ("events", data.get("events")),
        ("execution_trace", run.get("execution_trace")),
    ):
        if items is not None and (
            not isinstance(items, list)
            or any(not isinstance(item, dict) for item in items)
        ):
            raise ReportError("字段 %s 必须是对象数组" % label)
        for event in items or []:
            if event.get("tool_args") is not None and not isinstance(
                event["tool_args"], dict
            ):
                raise ReportError("事件 tool_args 必须是对象")
            for key in (
                "event_type",
                "role",
                "execution_origin",
                "tool_name",
                "session_id",
                "phase",
            ):
                if event.get(key) is not None and not isinstance(event[key], str):
                    raise ReportError("事件字段 %s 必须是字符串" % key)
            origin = (event.get("tool_args") or {}).get("execution_origin")
            if origin is not None and not isinstance(origin, str):
                raise ReportError("tool_args.execution_origin 必须是字符串")
    if data.get("setup_results") is not None and not isinstance(
        data["setup_results"], list
    ):
        raise ReportError("字段 setup_results 必须是数组")
    for parent, key in (
        (run, "context"),
        (run, "adherence"),
        (run, "task_completion"),
        (data.get("evaluation") or {}, "agent_behavior"),
        (data.get("evaluation") or {}, "memory_system"),
    ):
        if parent.get(key) is not None and not isinstance(parent[key], dict):
            raise ReportError("字段 %s 必须是对象" % key)
    for parent, keys in (
        (run, ("ok", "memory_enabled")),
        (run.get("adherence") or {}, ("passed", "repair_attempted")),
        (run.get("task_completion") or {}, ("passed",)),
        (
            (data.get("evaluation") or {}).get("agent_behavior") or {},
            ("passed", "task_completed"),
        ),
        ((data.get("evaluation") or {}).get("memory_system") or {}, ("passed",)),
    ):
        for key in keys:
            if parent.get(key) is not None and not isinstance(parent[key], bool):
                raise ReportError("判定字段 %s 必须是布尔值" % key)


def _files(input_path: Path) -> list[Path]:
    if input_path.is_symlink():
        raise ReportError("不接受符号链接输入")
    if input_path.is_file():
        return [input_path]
    if not input_path.is_dir():
        raise ReportError("输入文件或目录不存在")
    root = input_path.resolve()
    files = []

    def within(path: Path) -> None:
        if path.is_symlink() or not path.resolve().is_relative_to(root):
            raise ReportError("不读取符号链接目录或路径外文件")

    def collect(directory: Path) -> None:
        within(directory)
        candidates = [directory / "result.json"]
        for mode in ("memory_on", "memory_off"):
            arm = directory / mode
            if arm.exists() or arm.is_symlink():
                within(arm)
                if not arm.is_dir():
                    raise ReportError("memory_on/memory_off 必须是目录")
                candidates.append(arm / "result.json")
        for candidate in candidates:
            if candidate.exists() or candidate.is_symlink():
                within(candidate)
                files.append(candidate)
                if len(files) > MAX_FILES:
                    raise ReportError("输入文件数超过 1024")

    collect(input_path)
    cases = input_path / "cases"
    if cases.exists() or cases.is_symlink():
        within(cases)
        if not cases.is_dir():
            raise ReportError("cases 必须是目录")
        for case_directory in sorted(cases.iterdir()):
            if case_directory.is_symlink() or case_directory.is_dir():
                collect(case_directory)
    if not files:
        raise ReportError("目录内没有支持的 result.json 记录")
    if len(files) > MAX_FILES:
        raise ReportError("输入文件数超过 1024")
    return files


def load_sources(input_path: Path, case_id: str | None = None) -> list[Source]:
    input_path = Path(input_path).absolute()
    files = _files(input_path)
    root = (input_path if input_path.is_dir() else input_path.parent).resolve()
    remaining = MAX_INPUT_BYTES
    sources = []
    for path in files:
        if (
            path.suffix.lower() != ".json"
            or path.is_symlink()
            or not path.resolve().is_relative_to(root)
        ):
            raise ReportError("只接受输入范围内的普通 JSON 文件，不读取路径外文件")
        if not stat.S_ISREG(path.stat().st_mode):
            raise ReportError("输入必须是普通 JSON 文件")
        if path.stat().st_size > remaining:
            raise ReportError("输入总大小超过 16 MiB")
        with path.open("rb") as handle:
            raw = handle.read(remaining + 1)
        remaining -= len(raw)
        if remaining < 0:
            raise ReportError("输入总大小超过 16 MiB")
        try:
            data = json.loads(
                raw.decode("utf-8-sig"),
                object_pairs_hook=_no_duplicates,
                parse_constant=_reject_constant,
            )
            _validate_tree(data)
        except ReportError:
            raise
        except (ValueError, RecursionError) as exc:
            raise ReportError("输入不是有效的 UTF-8 JSON，或嵌套过深") from exc
        _validate_record(data)
        inferred_mode = {"memory_on": "on", "memory_off": "off"}.get(
            path.parent.name, "unknown"
        )
        mode = data.get("memory_mode") or inferred_mode
        mode = {"memory_on": "on", "memory_off": "off"}.get(mode, mode)
        if mode not in {"on", "off", "unknown"} or (
            inferred_mode != "unknown" and mode != inferred_mode
        ):
            raise ReportError("memory_mode 非法或与目录中的 on/off 标记冲突")
        inferred_id = (
            path.parent.parent.name if inferred_mode != "unknown" else path.parent.name
        )
        identifier = data.get("case_id") or inferred_id
        sources.append(
            Source(path.name, hashlib.sha256(raw).hexdigest(), identifier, mode, data)
        )
    # Validate every discovered record before filtering, including failed runs.
    if case_id is not None:
        sources = [source for source in sources if source.case_id == case_id]
        if not sources:
            raise ReportError("没有与 --case-id 精确匹配的 case")
    if len({(source.case_id, source.mode) for source in sources}) != len(sources):
        raise ReportError("同一 case 的同一记忆模式存在重复结果，不能静默覆盖")
    return sources


def _text(value) -> str:
    if isinstance(value, str):
        return value
    return json.dumps(value, ensure_ascii=False, indent=2)


def _block(value, *, preview: bool = True) -> str:
    text = _text(value)
    if not preview or len(text) <= PREVIEW_CHARS:
        return "<pre>%s</pre>" % escape(text)
    omitted = len(text) - PREVIEW_CHARS
    return (
        '<pre>%s</pre><p class="muted">已省略 %s 个字符（脱敏后的记录文本）</p>'
        "<details><summary>完整记录文本（已脱敏）</summary><pre>%s</pre></details>"
    ) % (escape(text[:PREVIEW_CHARS]), omitted, escape(text))


def _details(label: str, value) -> str:
    return "<details><summary>%s</summary>%s</details>" % (escape(label), _block(value))


def _phase(event: dict, run: dict, case_id: str) -> tuple[str, str]:
    phase = event.get("phase")
    if phase in {"setup", "history", "initialization"}:
        return "history", "历史初始化"
    if phase in {"live", "runtime"}:
        return "live", "live 实际运行"
    session = event.get("session_id")
    live_session = run.get("session_id")
    if session and (session == live_session or session == case_id.lower() + "-live"):
        return "live", "live 实际运行"
    if session and (live_session or session == case_id + "-history"):
        return "history", "历史初始化／其他会话"
    return "unknown", "来源阶段未标记"


def _status(data: dict) -> tuple[str, str]:
    run = data.get("run") or {}
    evaluation = data.get("evaluation") or {}
    behavior = evaluation.get("agent_behavior") or {}
    memory = evaluation.get("memory_system") or {}
    completion = run.get("task_completion") or {}
    negatives = [
        run.get("ok"),
        behavior.get("passed"),
        behavior.get("task_completed"),
        memory.get("passed"),
        completion.get("passed"),
    ]
    if (
        data.get("run_exception")
        or any(value is False for value in negatives)
        or evaluation.get("status")
        in {
            "fail",
            "failed",
            "error",
            "memory_pass_agent_fail",
            "memory_fail_agent_pass",
            "incomplete",
        }
    ):
        return "失败／未完成", "failure"
    if (
        run.get("ok") is True
        or behavior.get("task_completed") is True
        or completion.get("passed") is True
    ):
        return "成功（原始完成判定）", "success"
    return "未完成／完成状态未确认", "failure"


def _event(
    event: dict, index: int, run: dict, case_id: str, compact: bool = False
) -> str:
    event_type = str(event.get("event_type") or "未记录类型")
    role = str(
        event.get("role")
        or {
            "user_message": "user",
            "assistant_message": "assistant",
            "system_message": "system",
            "tool_call": "assistant",
            "tool_result": "tool",
        }.get(event_type, "unknown")
    )
    role_label = {
        "user": "用户",
        "assistant": "Agent",
        "tool": "工具",
        "system": "系统／适配器",
    }.get(role, role)
    args = event.get("display_tool_args", event.get("tool_args")) or {}
    origin = str(event.get("execution_origin") or args.get("execution_origin") or "")
    intervention = event.get("capture_origin") == "adapter_generated" or any(
        marker in origin.lower() or marker in event_type.lower()
        for marker in ADAPTER_ORIGINS
    )
    phase, phase_label = (
        ("live", "live 精简执行摘要") if compact else _phase(event, run, case_id)
    )
    classes = (
        "event tool"
        if event_type in {"tool_call", "tool_result", "policy_decision"}
        else "event"
    )
    if intervention:
        classes += " adapter"
    parts = [
        '<article class="%s" data-event-index="%s" data-phase="%s">'
        % (classes, index, phase)
    ]
    parts.append(
        '<div class="event-head"><h4>%s · %s</h4><span class="tag">%s</span></div>'
        % (
            escape(role_label),
            escape(event_type),
            escape(phase_label),
        )
    )
    if intervention:
        parts.append(
            '<p class="failure tag">适配器纠正／接管（不是模型动作）：%s</p>'
            % escape(origin or event_type)
        )
    elif role == "system":
        parts.append('<p class="muted">系统／适配器消息，不归为模型动作</p>')
    else:
        parts.append('<p class="muted">执行来源：%s</p>' % escape(origin or "未标记"))
    metadata = {
        key: event[key]
        for key in (
            "sequence",
            "timestamp",
            "session_id",
            "tool_name",
            "exit_code",
            "status",
            "outcome",
        )
        if key in event
    }
    if metadata:
        parts.append(_details("事件元数据（原始字段）", metadata))
    if event.get("capture_redacted"):
        parts.append('<p class="muted">本事件含敏感内容，展示记录已脱敏或隐藏。</p>')
    if event_type == "tool_call":
        if event.get("tool_name") == "finish":
            answer = next((args[key] for key in ("message", "summary", "output", "response") if args.get(key)), None)
            if answer is not None:
                parts.append('<p class="tag">Agent 最终答复（完成工具原文）</p>' + _block(answer))
        command = args.get("command", event.get("command"))
        parts.append('<p class="tag">工具命令</p>')
        parts.append(
            _block(command)
            if command is not None
            else '<p class="empty">工具命令未记录</p>'
        )
        if args:
            parts.append(_details("工具参数（已脱敏）", args))
    if event_type == "tool_result":
        output = event.get("display_tool_result", event.get("tool_result", event.get("content")))
        parts.append('<p class="tag">工具输出</p>')
        if isinstance(event.get("exit_code"), int) and not isinstance(event.get("exit_code"), bool):
            code = event["exit_code"]
            parts.append('<p class="%s tag">工具退出码：%s%s</p>' % (
                "success" if code == 0 else "failure", code,
                "（执行成功，不等同于任务完成）" if code == 0 else "（执行未成功）",
            ))
        if output is None:
            parts.append('<p class="empty">工具输出未记录</p>')
        elif output == "":
            parts.append('<p class="empty">工具输出为空（原记录）</p>')
        else:
            parts.append(_block(output))
        for key in ("stdout", "stderr"):
            if key in event:
                parts.append('<p class="tag">%s</p>%s' % (key, _block(event[key])))
    elif "content" in event or "message" in event:
        parts.append(_block(event.get("display_content", event.get("content", event.get("message")))))
    elif event_type != "tool_call":
        parts.append('<p class="empty">消息正文未记录</p>')
    parts.append("</article>")
    return "".join(parts)


def _context(context: dict) -> str:
    parts = [
        '<section class="band"><h3>记忆选择／阶段</h3>',
        '<p class="muted">上下文注入不等同于实际遵循；遵循与完成结果以原始判定为准。</p>',
    ]
    fields = (
        "selected_memory_ids",
        "selected_topics",
        "selected_values",
        "selected_subtypes",
        "stage_budgets",
        "stages",
    )
    stages = dict.fromkeys(
        stage
        for key in fields
        if isinstance(context.get(key), dict)
        for stage in context[key]
    )
    for stage in stages:
        selection = {
            key: context[key][stage]
            for key in fields
            if isinstance(context.get(key), dict) and stage in context[key]
        }
        parts.append(
            "<h4>%s · %s</h4>%s"
            % (escape(STAGES.get(stage, stage)), escape(stage), _block(selection))
        )
    if context:
        parts.append(_details("完整上下文记录（仅本 JSON 内字段，已脱敏）", context))
    else:
        parts.append('<p class="empty">记忆选择／阶段未记录</p>')
    parts.append("</section>")
    return "".join(parts)


def _arm(source: Source, redactor: Redactor) -> str:
    data = redactor.clean(source.data)
    run = data.get("run") or {}
    identifier = redactor.text(source.case_id)
    events = data.get("events") or []
    status, status_class = _status(data)
    memory_enabled = run.get("memory_enabled")
    mode_label = {
        "on": "记忆侧 · on（声明模式）",
        "off": "对照侧 · off（声明模式）",
        "unknown": "记忆模式未记录",
    }[source.mode]
    if source.mode == "off" and memory_enabled is False:
        mode_label = "记忆关闭 · off（run.memory_enabled=false）"
    elif source.mode == "on" and memory_enabled is True:
        mode_label = "记忆开启 · on（run.memory_enabled=true）"
    parts = [
        '<section class="arm" data-memory-mode="%s"><div class="arm-head"><h3>%s</h3>'
        % (source.mode, mode_label),
        '<p class="status %s">%s</p>' % (status_class, status),
        '<p class="meta">模型：%s</p>' % escape(str(data.get("model") or "未记录")),
    ]
    actual_flag = (
        "true"
        if memory_enabled is True
        else "false"
        if memory_enabled is False
        else "未记录／null"
    )
    parts.append('<p class="meta">实际 run.memory_enabled：%s</p>' % actual_flag)
    if source.mode == "off" and memory_enabled is not False:
        message = (
            "仍启用；不具备完全关闭证明。"
            if memory_enabled is True
            else "未记录实际开关；不具备完全关闭证明。"
        )
        parts.append('<p class="failure">%s</p>' % message)
    elif source.mode == "on" and memory_enabled is False:
        parts.append(
            '<p class="failure">声明 on 与实际 memory_enabled=false 不一致；保留原始判定，不重新算分。</p>'
        )
    parts.append("</div>")
    task = data.get("task", run.get("task"))
    task_label = "任务（原始字段）"
    if task is None:
        task = next(
            (
                event.get("content")
                for event in events
                if event.get("event_type") == "user_message"
                and _phase(event, run, identifier)[0] == "live"
            ),
            None,
        )
        task_label = "任务（首条 live 用户消息）"
    parts.append(
        "<h3>%s</h3>%s"
        % (
            task_label,
            _block(task) if task is not None else '<p class="empty">任务正文未记录</p>',
        )
    )
    parts.append(_context(run.get("context") or {}))
    adapter = {
        key: run[key]
        for key in (
            "repair",
            "internal_feedback",
            "structured_intent_recovery",
            "procedural_memory_fallback",
            "natural_high_risk_block",
        )
        if key in run
    }
    if (run.get("adherence") or {}).get(
        "repair_attempted"
    ) or "repair_attempt_count" in (run.get("adherence") or {}):
        adapter["adherence_repair"] = run["adherence"]
    if adapter:
        parts.append(
            '<section class="adapter"><h3>适配器纠正／接管记录（不是模型动作）</h3>'
            '<p class="muted">是否尝试或实际执行，以各字段为准，不计作模型自主遵循。</p>%s</section>'
            % _block(adapter)
        )
    if "setup_results" in data or "setup_event_count" in data:
        setup = {
            key: data[key]
            for key in ("setup_results", "setup_event_count")
            if key in data
        }
        parts.append(_details("历史初始化结果（不计作 live 动作）", setup))
    parts.append('<section class="band"><h3>对话与工具记录 · JSON 原顺序</h3>')
    if events:
        parts.extend(
            _event(event, index, run, identifier)
            for index, event in enumerate(events, 1)
        )
        if run.get("execution_trace"):
            parts.append(
                _details(
                    "执行摘要（execution_trace；不重复计数）", run["execution_trace"]
                )
            )
    elif run.get("execution_trace"):
        parts.append(
            '<p class="muted">仅有 execution_trace 精简摘要；上游可能已截断命令和消息，未补写工具输出。</p>'
        )
        parts.extend(
            _event(event, index, run, identifier, compact=True)
            for index, event in enumerate(run["execution_trace"], 1)
        )
    else:
        parts.append('<p class="empty">没有事件或执行摘要记录</p>')
    parts.append(
        '</section><section class="band"><h3>判定（原始字段，未重新评测）</h3>'
    )
    verdict = {key: data[key] for key in ("evaluation", "run_exception") if key in data}
    verdict["run"] = {
        key: run[key]
        for key in (
            "ok",
            "task_completion",
            "adherence",
            "event_summary",
            "preference_effect_proofs",
            "sensitive_block_notices",
        )
        if key in run
    }
    parts.append(
        _block(verdict)
        if verdict.get("run") or "evaluation" in verdict or "run_exception" in verdict
        else '<p class="empty">判定与统计未记录；不依据工具退出码或记忆注入推算任务成功</p>'
    )
    parts.append(
        '</section><footer class="source"><p class="meta">原始文件：%s</p>'
        % escape(redactor.text(source.name))
    )
    parts.append('<p class="meta text">来源 SHA-256：%s</p>' % escape(source.sha256))
    parts.append(_details("完整输入记录（已脱敏，凭据字段已移除）", data))
    parts.append("</footer></section>")
    return "".join(parts)


def render_report(sources: list[Source]) -> str:
    if not sources:
        raise ReportError("没有可导出的记录")
    redactor = Redactor([source.data for source in sources])
    groups: dict[str, list[Source]] = {}
    for source in sources:
        groups.setdefault(source.case_id, []).append(source)
    parts = [
        '<!doctype html><html lang="zh-CN"><head><meta charset="utf-8">',
        '<meta name="viewport" content="width=device-width, initial-scale=1">',
        "<meta http-equiv=\"Content-Security-Policy\" content=\"default-src 'none'; style-src 'unsafe-inline'; base-uri 'none'; form-action 'none'\">",
        "<title>Agent 运行记录</title><style>%s</style></head><body><main>" % CSS,
        '<header><h1>Agent 运行记录</h1><p class="muted">离线原始结果 · 失败保留 · 文本已脱敏</p><nav>',
    ]
    for index, case_id in enumerate(groups, 1):
        parts.append(
            '<a href="#case-%s">%s</a>' % (index, escape(redactor.text(case_id)))
        )
    parts.append("</nav></header>")
    for index, (case_id, arms) in enumerate(groups.items(), 1):
        arms.sort(key=lambda source: {"on": 0, "off": 1, "unknown": 2}[source.mode])
        title = redactor.text(str(arms[0].data.get("title") or "任务名称未记录"))
        parts.append(
            '<section class="case" id="case-%s" data-case-id="%s"><div class="case-head"><h2>%s</h2><span class="meta">%s</span></div>'
            % (
                index,
                escape(redactor.text(case_id), quote=True),
                escape(title),
                escape(redactor.text(case_id)),
            )
        )
        modes = {source.mode for source in arms}
        paired = {"on", "off"}.issubset(modes)
        if not paired and modes & {"on", "off"}:
            missing = "off" if "on" in modes else "on"
            parts.append(
                '<p class="muted">缺少 memory_%s 结果；不建立空对照栏，不补写判定。</p>'
                % missing
            )
        parts.append('<div class="arms%s">' % ("" if paired else " single"))
        parts.extend(_arm(source, redactor) for source in arms)
        parts.append("</div></section>")
    parts.append("</main></body></html>")
    return "\n".join(parts)


def export_report(
    input_path: Path,
    output: Path,
    *,
    overwrite: bool = False,
    case_id: str | None = None,
) -> Path:
    output = Path(output)
    if output.suffix.lower() != ".html":
        raise ReportError("输出必须是 .html 文件")
    if output.is_symlink():
        raise ReportError("不接受符号链接输出")
    if output.exists() and not overwrite:
        raise ReportError("输出已存在；显式使用 --overwrite 才可覆盖")
    report = render_report(load_sources(input_path, case_id))
    if not overwrite:
        with output.open("x", encoding="utf-8", newline="\n") as handle:
            handle.write(report)
    else:
        temporary = None
        try:
            with tempfile.NamedTemporaryFile(
                mode="w",
                encoding="utf-8",
                newline="\n",
                dir=output.parent,
                delete=False,
            ) as handle:
                temporary = Path(handle.name)
                handle.write(report)
            os.replace(temporary, output)
        finally:
            if temporary is not None:
                temporary.unlink(missing_ok=True)
    return output


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="离线导出真实 Agent JSON 运行记录；不执行工具、不读取外部引用"
    )
    parser.add_argument(
        "--input", type=Path, required=True, help="单 case JSON 文件或包含 cases 的目录"
    )
    parser.add_argument("--output", type=Path, required=True, help="静态 .html 报告")
    parser.add_argument("--case-id", help="按 case_id 精确筛选")
    parser.add_argument("--overwrite", action="store_true", help="显式允许覆盖已有输出")
    args = parser.parse_args(argv)
    try:
        export_report(
            args.input, args.output, overwrite=args.overwrite, case_id=args.case_id
        )
    except ReportError as exc:
        print("导出失败：%s" % exc, file=sys.stderr)
        return 2
    except OSError:
        print(
            "导出失败：文件读取或写入失败；请检查路径、权限和已有输出", file=sys.stderr
        )
        return 2
    print("离线 HTML 报告已生成")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
