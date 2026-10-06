from copy import deepcopy
import hashlib
from html import escape
from html.parser import HTMLParser
import json
from pathlib import Path
import subprocess
import sys

import pytest

from scripts import render_agent_trace as report


def _result(case_id="R01", mode="on", **fields):
    data = {
        "case_id": case_id,
        "title": "offline fixture, not real evidence",
        "memory_mode": mode,
        "task": "inspect local logs",
        "model": "recorded-model-name",
        "run": {"session_id": case_id.lower() + "-live"},
        "events": [],
    }
    data.update(fields)
    return data


def _write(path, data):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
    return path


def _export(tmp_path, data):
    source = _write(tmp_path / "result.json", data)
    output = tmp_path / "report.html"
    report.export_report(source, output)
    return output.read_text(encoding="utf-8")


class Tags(HTMLParser):
    def __init__(self, html):
        super().__init__()
        self.tags = []
        self.feed(html)

    def handle_starttag(self, tag, attrs):
        self.tags.append((tag, dict(attrs)))


def test_xss_is_escaped_in_attributes_text_messages_commands_and_outputs(tmp_path):
    attack = (
        '<img src="https://invalid.test/x" onerror="alert(1)"><script>alert(2)</script>'
    )
    data = _result(
        attack,
        title=attack,
        task=attack,
        model=attack,
        events=[
            {"event_type": "user_message", "content": attack},
            {"event_type": "tool_call", "tool_args": {"command": attack}},
            {"event_type": "tool_result", "tool_result": attack, "exit_code": 0},
        ],
    )
    html = _export(tmp_path, data)
    assert escape(attack) in html
    tags = Tags(html).tags
    assert not {"script", "img", "iframe", "link", "base", "object"} & {
        tag for tag, _ in tags
    }
    assert all(not any(key.startswith("on") for key in attrs) for _, attrs in tags)
    case = next(attrs for _, attrs in tags if "data-case-id" in attrs)
    assert case["data-case-id"] == attack
    assert "default-src &#x27;none&#x27;" in html or "default-src 'none'" in html


def test_directory_pairs_on_before_off_and_retains_failed_arm(tmp_path):
    _write(
        tmp_path / "cases/R01/memory_off/result.json",
        _result(
            mode="off",
            task="OFF_TASK",
            evaluation={
                "status": "fail",
                "agent_behavior": {"passed": False, "task_completed": False},
            },
            run_exception={"type": "RuntimeError", "message": "OFF_FAILURE"},
        ),
    )
    _write(
        tmp_path / "cases/R01/memory_on/result.json",
        _result(
            task="ON_TASK",
            run={
                "ok": True,
                "task_completion": {"passed": True},
            },
        ),
    )
    output = tmp_path / "paired.html"
    report.export_report(tmp_path, output)
    html = output.read_text(encoding="utf-8")
    assert html.index('data-memory-mode="on"') < html.index('data-memory-mode="off"')
    assert html.index("ON_TASK") < html.index("OFF_TASK")
    assert '<div class="arms">' in html
    assert "OFF_FAILURE" in html and "失败／未完成" in html
    assert "成功（原始完成判定）" in html
    assert sum("data-case-id" in attrs for _, attrs in Tags(html).tags) == 1


@pytest.mark.parametrize("flag", [True, None, "missing"])
def test_legacy_off_is_only_a_declared_control_not_proven_disabled(tmp_path, flag):
    run = {"ok": True}
    if flag != "missing":
        run["memory_enabled"] = flag
    html = _export(tmp_path, _result(mode="off", run=run))
    assert "对照侧 · off（声明模式）" in html
    assert "记忆关闭 · off" not in html
    assert "不具备完全关闭证明" in html
    assert "实际 run.memory_enabled：" in html
    assert "成功（原始完成判定）" in html
    if flag is True:
        assert "仍启用" in html and "run.memory_enabled：true" in html
    else:
        assert "未记录实际开关" in html


def test_explicit_false_is_required_to_label_off_as_disabled(tmp_path):
    html = _export(tmp_path, _result(mode="off", run={"memory_enabled": False}))
    assert "记忆关闭 · off（run.memory_enabled=false）" in html
    assert "不具备完全关闭证明" not in html
    assert "实际 run.memory_enabled：false" in html


def test_on_false_disagreement_is_visible_without_rescoring(tmp_path):
    data = _result(
        run={"ok": True, "memory_enabled": False}, evaluation={"status": "pass"}
    )
    html = _export(tmp_path, data)
    assert "声明 on 与实际 memory_enabled=false 不一致" in html
    assert "成功（原始完成判定）" in html
    assert "不重新算分" in html
    assert data["evaluation"]["status"] == "pass"


def test_exact_case_filter_keeps_failures_and_rejects_absent_id(tmp_path):
    for case_id in ("R1", "R10"):
        _write(
            tmp_path / ("cases/%s/result.json" % case_id),
            _result(case_id, run={"ok": False}),
        )
    sources = report.load_sources(tmp_path, "R1")
    assert [source.case_id for source in sources] == ["R1"]
    assert "失败／未完成" in report.render_report(sources)
    with pytest.raises(report.ReportError, match="精确匹配"):
        report.load_sources(tmp_path, "R")


def test_filter_does_not_silently_hide_invalid_other_records(tmp_path):
    _write(tmp_path / "cases/R01/result.json", _result())
    _write(tmp_path / "cases/R02/result.json", {"case_id": "R02", "events": "invalid"})
    with pytest.raises(report.ReportError, match="events"):
        report.load_sources(tmp_path, "R01")


def test_event_order_and_history_live_labels_do_not_follow_timestamp_sort(tmp_path):
    data = _result(
        task=None,
        setup_results=[{"stored": True}],
        events=[
            {
                "event_type": "assistant_message",
                "session_id": "r01-live",
                "sequence": 20,
                "content": "FIRST_LIVE",
                "timestamp": "2030",
            },
            {
                "event_type": "user_message",
                "session_id": "R01-history",
                "sequence": 1,
                "content": "SECOND_HISTORY",
                "timestamp": "2020",
            },
            {
                "event_type": "user_message",
                "session_id": "r01-live",
                "sequence": 3,
                "content": "THIRD_USER",
                "timestamp": "2021",
            },
        ],
    )
    html = _export(tmp_path, data)
    timeline = html[html.index("对话与工具记录") : html.index("判定（原始字段")]
    assert (
        timeline.index("FIRST_LIVE")
        < timeline.index("SECOND_HISTORY")
        < timeline.index("THIRD_USER")
    )
    articles = [attrs for tag, attrs in Tags(html).tags if tag == "article"]
    assert [attrs["data-event-index"] for attrs in articles] == ["1", "2", "3"]
    assert [attrs["data-phase"] for attrs in articles] == ["live", "history", "live"]
    assert "任务（首条 live 用户消息）" in html
    assert "历史初始化结果（不计作 live 动作）" in html


@pytest.mark.parametrize("origin", report.ADAPTER_ORIGINS)
def test_adapter_origins_are_obviously_non_model_actions(tmp_path, origin):
    html = _export(
        tmp_path,
        _result(
            events=[
                {
                    "event_type": "tool_call",
                    "tool_args": {
                        "command": "recorded command",
                        "execution_origin": origin,
                    },
                }
            ]
        ),
    )
    assert "适配器纠正／接管（不是模型动作）" in html
    assert origin in html
    article = next(attrs for tag, attrs in Tags(html).tags if tag == "article")
    assert "adapter" in article["class"]


def test_adapter_run_fields_are_visible_even_if_compact_trace_drops_origin(tmp_path):
    html = _export(
        tmp_path,
        _result(
            run={
                "adherence": {
                    "repair_attempted": True,
                    "repair_attempt_count": 1,
                    "passed": True,
                },
                "structured_intent_recovery": {"attempted": True, "executed": True},
                "procedural_memory_fallback": {"attempted": True, "executed": False},
                "execution_trace": [
                    {
                        "event_type": "tool_call",
                        "tool_name": "terminal",
                        "command": "cmd",
                        "status": "success",
                    }
                ],
            }
        ),
    )
    assert "适配器纠正／接管记录（不是模型动作）" in html
    assert "不计作模型自主遵循" in html
    assert "仅有 execution_trace 精简摘要" in html
    assert "完成状态未确认" in html
    assert "执行来源：未标记" in html


def test_trace_only_does_not_invent_tool_output_completion_or_statistics(tmp_path):
    html = _export(
        tmp_path,
        _result(
            run={
                "execution_trace": [
                    {
                        "event_type": "tool_call",
                        "tool_name": "terminal",
                        "command": "echo evidence",
                        "status": "success",
                    },
                    {
                        "event_type": "tool_result",
                        "tool_name": "terminal",
                        "exit_code": 0,
                        "outcome": "success",
                    },
                    {"event_type": "assistant_message", "message": "recorded response"},
                ]
            }
        ),
    )
    assert "工具输出未记录" in html
    assert "统计未记录" in html
    assert "完成状态未确认" in html
    assert "成功（原始完成判定）" not in html
    assert "成功率" not in html and "tool_call_count" not in html
    assert "recorded response" in html


def test_injected_context_and_adherence_alone_are_not_task_success(tmp_path):
    html = _export(
        tmp_path,
        _result(
            run={
                "context": {
                    "selected_memory_ids": {"planning": ["MEM_A"]},
                    "selected_values": {"tool_selection": ["rg"]},
                },
                "adherence": {"passed": True},
            }
        ),
    )
    assert "MEM_A" in html and "规划" in html and "工具选择" in html
    assert "上下文注入不等同于实际遵循" in html
    assert "成功（原始完成判定）" not in html


def test_incomplete_and_failed_fields_override_positive_flags(tmp_path):
    html = _export(
        tmp_path,
        _result(
            run={"ok": True, "task_completion": {"passed": False}},
            evaluation={
                "status": "pass",
                "agent_behavior": {"passed": True, "task_completed": False},
            },
        ),
    )
    assert "失败／未完成" in html
    assert "成功（原始完成判定）" not in html


def test_missing_fields_are_explicit_and_no_service_is_imported(tmp_path):
    html = _export(tmp_path, {"case_id": "LEGACY"})
    for message in (
        "任务正文未记录",
        "模型：未记录",
        "记忆选择／阶段未记录",
        "没有事件或执行摘要记录",
        "统计未记录",
    ):
        assert message in html
    cold = subprocess.run(
        [
            sys.executable,
            "-c",
            "from scripts import render_agent_trace; import sys; "
            "assert 'memguard.service' not in sys.modules; assert 'openhands' not in sys.modules",
        ],
        cwd=report.ROOT,
        capture_output=True,
        text=True,
        timeout=20,
    )
    assert cold.returncode == 0, cold.stderr
    assert not list(tmp_path.glob("*.sqlite*"))


def test_credentials_are_removed_from_raw_details_and_all_echoes(tmp_path):
    secret = "short-hidden-key"
    token = "sk-proj-" + "S" * 32
    password = "hidden-password-value"
    data = _result(
        task="plain echo " + secret,
        api_key=secret,
        events=[
            {
                "event_type": "tool_call",
                "tool_args": {
                    "command": "echo " + secret,
                    "OPENAI_API_KEY": token,
                    "headers": {"Authorization": "Bearer header-hidden-value"},
                },
            },
            {
                "event_type": "tool_result",
                "tool_result": "echo " + token + " password=" + password,
            },
        ],
        run={
            "credentials": {"password": "tiny"},
            "context": {"selected_values": {"planning": [secret]}},
        },
    )
    html = _export(tmp_path, data)
    for value in (
        secret,
        token,
        password,
        "header-hidden-value",
        "tiny",
        "OPENAI_API_KEY",
        '"credentials"',
        '"api_key"',
    ):
        assert value not in html
    assert "REDACTED" in html


def test_password_token_bearer_and_private_key_text_are_redacted(tmp_path):
    key_body = "PRIVATE_BODY_DO_NOT_EXPORT"
    data = _result(
        task="API_KEY=abc Authorization: Bearer naked-hidden-value",
        events=[
            {"event_type": "user_message", "content": "密码是1234"},
            {
                "event_type": "tool_result",
                "tool_result": "-----BEGIN RSA PRIVATE KEY-----\n"
                + key_body
                + "\n-----END RSA PRIVATE KEY-----",
            },
        ],
    )
    html = _export(tmp_path, data)
    assert all(
        value not in html
        for value in (
            "abc",
            "naked-hidden-value",
            "1234",
            key_body,
            "BEGIN RSA PRIVATE KEY",
        )
    )


def test_free_text_secret_values_cannot_leak_in_unlabelled_echoes(tmp_path):
    html = _export(
        tmp_path,
        _result(
            task="password=one-specific-secret TOKEN=short-value",
            events=[
                {
                    "event_type": "tool_result",
                    "tool_result": "echo one-specific-secret and short-value",
                },
            ],
        ),
    )
    assert "one-specific-secret" not in html and "short-value" not in html


def test_utf8_bom_hash_uses_original_bytes(tmp_path):
    source = tmp_path / "result.json"
    raw = b"\xef\xbb\xbf" + json.dumps(_result()).encode("utf-8")
    source.write_bytes(raw)
    sources = report.load_sources(source)
    assert sources[0].sha256 == hashlib.sha256(raw).hexdigest()
    assert sources[0].case_id == "R01"


def test_long_output_counts_omitted_characters_and_retains_redacted_full_text(tmp_path):
    text = "工具原文" * 900 + "FINAL_ORIGINAL_TEXT"
    html = _export(
        tmp_path, _result(events=[{"event_type": "tool_result", "tool_result": text}])
    )
    assert "已省略 %s 个字符" % (len(text) - report.PREVIEW_CHARS) in html
    assert "完整记录文本（已脱敏）" in html
    assert escape(text) in html
    assert "FINAL_ORIGINAL_TEXT" in html


def test_source_hash_basename_and_read_only_references(tmp_path, monkeypatch):
    outside = tmp_path / "outside-secret.txt"
    outside.write_text("DO_NOT_READ_EXTERNAL_CONTENT", encoding="utf-8")
    source = _write(
        tmp_path / "input/result&trace.json",
        _result(
            run={"context_file": str(outside)},
            events=[
                {
                    "event_type": "tool_call",
                    "tool_args": {
                        "command": "echo unsafe > " + str(tmp_path / "should-not-exist")
                    },
                },
            ],
            reference="https://invalid.test/do-not-fetch",
        ),
    )
    original = source.read_bytes()
    real_open = Path.open

    def restricted_open(path, *args, **kwargs):
        assert path != outside
        return real_open(path, *args, **kwargs)

    monkeypatch.setattr(Path, "open", restricted_open)
    output = tmp_path / "out.html"
    report.export_report(source, output)
    html = output.read_text(encoding="utf-8")
    assert hashlib.sha256(original).hexdigest() in html
    assert "result&amp;trace.json" in html
    assert "DO_NOT_READ_EXTERNAL_CONTENT" not in html
    assert not (tmp_path / "should-not-exist").exists()
    assert source.read_bytes() == original
    assert all(
        not attrs.get("href", "").startswith(("http", "file"))
        for _, attrs in Tags(html).tags
    )


def test_output_is_exclusive_and_explicit_overwrite_is_supported(tmp_path):
    source = _write(tmp_path / "result.json", _result())
    output = tmp_path / "out.html"
    output.write_text("valuable output", encoding="utf-8")
    with pytest.raises(report.ReportError, match="overwrite"):
        report.export_report(source, output)
    assert output.read_text(encoding="utf-8") == "valuable output"
    report.export_report(source, output, overwrite=True)
    assert "Agent 运行记录" in output.read_text(encoding="utf-8")
    assert {path.name for path in tmp_path.iterdir()} == {"result.json", "out.html"}


@pytest.mark.parametrize(
    "data",
    [
        [],
        {},
        {"events": "wrong"},
        {"events": [1]},
        {"run": []},
        {"run": {"execution_trace": {}}},
        {"run": {"execution_trace": [False]}},
        {"events": [{"tool_args": []}]},
        {"events": [{"phase": []}]},
        {"case_id": 123},
        {"evaluation": "wrong"},
        {"task": []},
        {"run": {"context": []}},
        {"run": {"ok": "true"}},
        {"run": {"memory_enabled": "false"}},
        {"evaluation": {"agent_behavior": {"task_completed": "false"}}},
        {"evaluation": {"status": []}},
        {"run": {"session_id": []}},
        {"memory_mode": "paired", "case_id": "R01"},
    ],
)
def test_invalid_structures_fail_without_creating_output(tmp_path, data):
    source = _write(tmp_path / "result.json", data)
    output = tmp_path / "out.html"
    with pytest.raises(report.ReportError):
        report.export_report(source, output)
    assert not output.exists()


@pytest.mark.parametrize(
    "text",
    [
        '{"case_id":"A","case_id":"B"}',
        '{"task":NaN}',
        '{"task":Infinity}',
        '{"task":1e999}',
        '{"task":"\\ud800"}',
        '{"run":',
        "[" * 1000 + "0" + "]" * 1000,
        '{"task":' + "9" * 5000 + "}",
    ],
)
def test_invalid_json_and_depth_fail_explicitly(tmp_path, text):
    source = tmp_path / "result.json"
    source.write_text(text, encoding="utf-8")
    with pytest.raises(report.ReportError):
        report.load_sources(source)


def test_input_total_limit_covers_all_files_before_filtering(tmp_path, monkeypatch):
    first = _write(tmp_path / "cases/R01/result.json", _result("R01"))
    second = _write(tmp_path / "cases/R02/result.json", _result("R02"))
    monkeypatch.setattr(
        report, "MAX_INPUT_BYTES", first.stat().st_size + second.stat().st_size - 1
    )
    with pytest.raises(report.ReportError, match="16 MiB"):
        report.load_sources(tmp_path, "R01")


def test_oversize_file_and_non_json_extension_are_rejected(tmp_path, monkeypatch):
    source = _write(tmp_path / "result.json", _result())
    monkeypatch.setattr(report, "MAX_INPUT_BYTES", source.stat().st_size - 1)
    with pytest.raises(report.ReportError, match="16 MiB"):
        report.load_sources(source)
    other = _write(tmp_path / "result.txt", _result())
    with pytest.raises(report.ReportError, match="JSON 文件"):
        report.load_sources(other)
    assert report.MAX_DEPTH == 64


def test_missing_side_duplicate_arm_and_mode_conflict_are_not_hidden(tmp_path):
    _write(tmp_path / "cases/R01/memory_on/result.json", _result())
    html = report.render_report(report.load_sources(tmp_path))
    assert "缺少 memory_off 结果" in html
    _write(tmp_path / "cases/R02/result.json", _result())
    with pytest.raises(report.ReportError, match="重复结果"):
        report.load_sources(tmp_path)


@pytest.mark.parametrize("mode,missing", [("on", "off"), ("off", "on")])
def test_single_arm_is_full_width_without_empty_comparison_column(
    tmp_path, mode, missing
):
    html = _export(tmp_path, _result(mode=mode))
    assert '<div class="arms single">' in html
    assert "缺少 memory_%s 结果" % missing in html
    arms = [attrs for _, attrs in Tags(html).tags if "data-memory-mode" in attrs]
    assert [attrs["data-memory-mode"] for attrs in arms] == [mode]
    _write(tmp_path / "cases/R01/memory_on/result.json", _result(mode="off"))
    with pytest.raises(report.ReportError, match="冲突"):
        report.load_sources(tmp_path)


def test_symlink_outside_input_and_symlink_output_are_rejected(tmp_path):
    source = _write(tmp_path / "outside/result.json", _result())
    linked = tmp_path / "suite/cases/R01/memory_on/result.json"
    linked.parent.mkdir(parents=True)
    try:
        linked.symlink_to(source)
    except OSError:
        pytest.skip("host does not permit creating symlinks")
    with pytest.raises(report.ReportError, match="路径外"):
        report.load_sources(tmp_path / "suite")
    output = tmp_path / "out.html"
    output.symlink_to(source)
    with pytest.raises(report.ReportError, match="符号链接输出"):
        report.export_report(source, output, overwrite=True)
    assert json.loads(source.read_text(encoding="utf-8"))["case_id"] == "R01"


def test_rendering_does_not_mutate_input_records(tmp_path):
    data = _result(
        api_key="hidden-key",
        events=[{"event_type": "tool_call", "tool_args": {"command": "recorded"}}],
    )
    source = _write(tmp_path / "result.json", data)
    sources = report.load_sources(source)
    before = deepcopy(sources[0].data)
    report.render_report(sources)
    assert sources[0].data == before


def test_cli_subprocess_works_without_optional_agent_dependencies(tmp_path):
    source = _write(
        tmp_path / "result.json",
        _result(run_exception={"message": "recorded exception"}),
    )
    output = tmp_path / "out.html"
    command = [
        sys.executable,
        str(report.ROOT / "scripts/render_agent_trace.py"),
        "--input",
        str(source),
        "--output",
        str(output),
    ]
    completed = subprocess.run(command, capture_output=True, text=True, timeout=20)
    assert completed.returncode == 0, completed.stderr
    assert "recorded exception" in output.read_text(encoding="utf-8")
    repeated = subprocess.run(command, capture_output=True, text=True, timeout=20)
    assert repeated.returncode == 2
    assert output.exists()


def test_cli_reports_errors_without_traceback_or_false_success(tmp_path, capsys):
    source = _write(tmp_path / "result.json", {"events": "invalid"})
    output = tmp_path / "out.html"
    assert report.main(["--input", str(source), "--output", str(output)]) == 2
    captured = capsys.readouterr()
    assert "导出失败" in captured.err
    assert "Traceback" not in captured.err and captured.out == ""
    assert not output.exists()
