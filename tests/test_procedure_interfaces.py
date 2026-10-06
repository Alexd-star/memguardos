from copy import deepcopy
import json
import subprocess

import pytest

from memguard.api.server import handle_request
from memguard.cli import memguard
from memguard.models import MemorySource, make_memory
from memguard.service import MemGuardService


def _spec():
    return {
        "goal": "检查发布产物", "steps": ["人工检查 $name 的发布内容"],
        "failure_guards": ["来源变化时停止复用"], "parameters": {"name": "artifact.txt"},
        "postconditions": [{"type": "file_contains", "path": "$name", "value": "ready"}],
        "environment": {},
    }


class ProcedureService:
    def __init__(self, status="pass"):
        self.status = status
        self.calls = []
        self.closed = False

    def result(self):
        return {
            "ok": self.status == "pass", "stored": self.status == "pass",
            "memory_id": "procedure-1", "spec": _spec(),
            "rendered_steps": ["人工检查 artifact.txt 的发布内容"],
            "source_memory_ids": ["source-1", "source-2"],
            "verification": {"overall": self.status, "checks": [{
                "type": "file_contains", "path": "artifact.txt", "status": self.status,
                "reason": "matched" if self.status == "pass" else "unavailable",
            }]},
            "session_id": "session-1", "use_id": "use-1",
            "feedback": {"recorded": True, "status": self.status},
        }

    def learn_procedure(self, spec, **kwargs):
        self.calls.append(("learn_procedure", spec, kwargs))
        return self.result()

    def recall_procedures(self, task, **kwargs):
        self.calls.append(("recall_procedures", task, kwargs))
        return [self.result()]

    def verify_procedure(self, memory_id, **kwargs):
        self.calls.append(("verify_procedure", memory_id, kwargs))
        return self.result()

    def dependency_impact(self, memory_ids):
        self.calls.append(("dependency_impact", memory_ids))
        return {
            "root_memory_ids": memory_ids,
            "derived_memories": [{"memory_id": "procedure-1", "summary": "检查发布产物", "governance_state": "active"}],
        }

    def close(self):
        self.closed = True


def _request(tmp_path, command):
    return {
        "command": command, "workspace": str(tmp_path), "session_id": "session-1",
        "spec": _spec(), "source_memory_ids": ["source-1", "source-2"],
        "arguments": {"name": "artifact.txt"}, "memory_id": "procedure-1",
        "memory_ids": ["source-1"], "task": "检查发布产物", "limit": 3, "use_id": "use-1",
    }


@pytest.mark.parametrize("command", ["learn_procedure", "recall_procedures", "verify_procedure", "dependency_impact"])
def test_api_dispatches_strict_procedure_requests(tmp_path, command):
    service = ProcedureService()
    request = _request(tmp_path, command)

    response = handle_request(service, request)

    assert response["ok"] is True
    assert service.calls[0][0] == command
    if command != "dependency_impact":
        kwargs = service.calls[0][2]
        assert kwargs["workspace"] == str(tmp_path)
        assert kwargs["session_id"] == "session-1"
        assert kwargs["arguments"] == {"name": "artifact.txt"}
    if command == "learn_procedure":
        assert service.calls[0][2]["source_memory_ids"] == ["source-1", "source-2"]
    if command == "verify_procedure":
        assert service.calls[0][2]["use_id"] == "use-1"


@pytest.mark.parametrize("command", ["learn_procedure", "recall_procedures", "verify_procedure"])
@pytest.mark.parametrize("arguments", [None, [], {"name": 1}, {"bad-name": "x"}, {"n": "x" * 4097}, {"n": "\x00"}, {str(index): "x" for index in range(33)}])
def test_api_rejects_invalid_argument_dicts_before_service(tmp_path, command, arguments):
    service = ProcedureService()
    response = handle_request(service, {**_request(tmp_path, command), "arguments": arguments})
    assert response["ok"] is False
    assert response["error"] == "invalid_request"
    assert service.calls == []


@pytest.mark.parametrize("command,field", [("learn_procedure", "source_memory_ids"), ("dependency_impact", "memory_ids")])
@pytest.mark.parametrize("values", [None, "source-1", [], [1], [None], [False], [""], [" "], ["x" * 129], ["x\x00"]])
def test_api_rejects_non_string_memory_ids(tmp_path, command, field, values):
    service = ProcedureService()
    response = handle_request(service, {**_request(tmp_path, command), field: values})
    assert response["ok"] is False
    assert service.calls == []


@pytest.mark.parametrize("command,field,count", [("learn_procedure", "source_memory_ids", 33), ("dependency_impact", "memory_ids", 101)])
def test_api_bounds_memory_id_lists(tmp_path, command, field, count):
    service = ProcedureService()
    response = handle_request(service, {**_request(tmp_path, command), field: ["source"] * count})
    assert response["ok"] is False
    assert service.calls == []


@pytest.mark.parametrize("payload", [None, [], {"goal": "g", "steps": ["s"], "postconditions": [], "verified": True}])
def test_api_rejects_invalid_spec(tmp_path, payload):
    service = ProcedureService()
    response = handle_request(service, {**_request(tmp_path, "learn_procedure"), "spec": payload})
    assert response["ok"] is False
    assert service.calls == []


@pytest.mark.parametrize("command", ["learn_procedure", "recall_procedures", "verify_procedure"])
@pytest.mark.parametrize("workspace", [None, "", "relative", "http://example.invalid", 1])
def test_api_requires_explicit_local_workspace(tmp_path, command, workspace):
    service = ProcedureService()
    response = handle_request(service, {**_request(tmp_path, command), "workspace": workspace})
    assert response["ok"] is False
    assert service.calls == []


@pytest.mark.parametrize("limit", [0, 51, True, "5", 1.5])
def test_api_recall_limit_is_strict_integer(tmp_path, limit):
    service = ProcedureService()
    response = handle_request(service, {**_request(tmp_path, "recall_procedures"), "limit": limit})
    assert response["ok"] is False
    assert service.calls == []


def test_api_verify_requires_reproducible_session(tmp_path):
    service = ProcedureService()
    request = _request(tmp_path, "verify_procedure")
    del request["session_id"]
    response = handle_request(service, request)
    assert response["ok"] is False
    assert "session_id" in response["detail"]
    assert service.calls == []


@pytest.mark.parametrize("status", ["fail", "unknown"])
def test_api_unknown_or_failure_cannot_be_success(tmp_path, status):
    service = ProcedureService(status)
    # Even a transport-success flag must not turn measured unknown into pass.
    result = service.result()
    result["ok"] = True
    service.verify_procedure = lambda *args, **kwargs: result
    response = handle_request(service, _request(tmp_path, "verify_procedure"))
    assert response["ok"] is False
    assert response["result"]["verification"]["overall"] == status


def test_api_contradictory_success_flag_cannot_override_unknown_check(tmp_path):
    service = ProcedureService()
    result = service.result()
    result["verification"]["checks"][0]["status"] = "unknown"
    service.verify_procedure = lambda *args, **kwargs: result
    assert handle_request(service, _request(tmp_path, "verify_procedure"))["ok"] is False


def test_api_blocked_result_is_failure(tmp_path):
    service = ProcedureService()
    service.verify_procedure = lambda *args, **kwargs: {"blocked": True, "overall": "pass", "reason": "source_changed"}
    assert handle_request(service, _request(tmp_path, "verify_procedure"))["ok"] is False


def test_api_missing_workspace_is_not_defaulted(tmp_path):
    service = ProcedureService()
    request = _request(tmp_path, "recall_procedures")
    del request["workspace"]
    response = handle_request(service, request)
    assert response["ok"] is False
    assert service.calls == []


@pytest.mark.parametrize("command", ["learn-procedure", "procedures", "verify-procedure", "dependencies"])
@pytest.mark.parametrize("json_position", ["global", "command", "readable"])
def test_cli_procedure_dispatch_and_readable_output(tmp_path, monkeypatch, capsys, command, json_position):
    service = ProcedureService()
    monkeypatch.setattr(memguard, "_open_service", lambda args: service)
    spec_file = tmp_path / "spec.json"
    spec_file.write_text(json.dumps(_spec(), ensure_ascii=False), encoding="utf-8")
    specific = {
        "learn-procedure": [str(spec_file), "--source", "source-1", "--source", "source-2", "--argument", "name=artifact.txt"],
        "procedures": ["检查发布产物", "--limit", "3", "--argument", "name=artifact.txt"],
        "verify-procedure": ["procedure-1", "--use-id", "use-1", "--argument", "name=artifact.txt"],
        "dependencies": ["source-1"],
    }
    argv = ["--workspace", str(tmp_path), "--session-id", "session-1"]
    if json_position == "global":
        argv += ["--json"]
    argv += [command, *specific[command]]
    if json_position == "command":
        argv += ["--json"]

    assert memguard.main(argv) == 0
    output = capsys.readouterr().out
    assert service.closed
    if json_position != "readable":
        assert json.loads(output)["ok"] is True
    elif command == "dependencies":
        assert "派生影响预览" in output and "procedure-1" in output and "不删除" in output
    else:
        for text in ["状态", "步骤", "风险约束", "来源记忆", "实际检查", "检查来源", "反馈", "artifact.txt", "matched"]:
            assert text in output


@pytest.mark.parametrize("status", ["fail", "unknown"])
@pytest.mark.parametrize("json_mode", [False, True])
def test_cli_verify_failed_checks_return_nonzero(tmp_path, monkeypatch, capsys, status, json_mode):
    service = ProcedureService(status)
    monkeypatch.setattr(memguard, "_open_service", lambda args: service)
    argv = ["--workspace", str(tmp_path), "verify-procedure", "procedure-1", "--session-id", "session-1"]
    if json_mode:
        argv += ["--json"]
    assert memguard.main(argv) == 2
    output = capsys.readouterr().out
    if json_mode:
        assert json.loads(output)["ok"] is False
    else:
        assert ("未知（未证明通过）" if status == "unknown" else "失败") in output


def test_cli_verify_missing_session_is_explicit_error(tmp_path, monkeypatch, capsys):
    service = ProcedureService()
    monkeypatch.setattr(memguard, "_open_service", lambda args: service)
    monkeypatch.delenv("MEMGUARD_SESSION_ID", raising=False)
    assert memguard.main(["--workspace", str(tmp_path), "verify-procedure", "procedure-1"]) == 2
    assert "需要 --session-id" in capsys.readouterr().out
    assert service.calls == []


@pytest.mark.parametrize("arguments", [["missing-equals"], ["name=a", "name=b"], ["bad-name=x"]])
def test_cli_rejects_bad_arguments(tmp_path, monkeypatch, capsys, arguments):
    service = ProcedureService()
    monkeypatch.setattr(memguard, "_open_service", lambda args: service)
    argv = ["--workspace", str(tmp_path), "procedures", "task", "--json"]
    for argument in arguments:
        argv += ["--argument", argument]
    assert memguard.main(argv) == 2
    assert json.loads(capsys.readouterr().out)["ok"] is False
    assert service.calls == []


@pytest.mark.parametrize("payload", ["not json", "[]", "x" * (1024 * 1024 + 1), "[" * 1200 + "0" + "]" * 1200], ids=["invalid-json", "non-object", "over-limit", "too-deep"])
def test_cli_rejects_bad_spec_files(tmp_path, monkeypatch, capsys, payload):
    service = ProcedureService()
    monkeypatch.setattr(memguard, "_open_service", lambda args: service)
    spec_file = tmp_path / "bad.json"
    spec_file.write_text(payload, encoding="utf-8")
    assert memguard.main(["--workspace", str(tmp_path), "learn-procedure", str(spec_file), "--source", "source-1", "--json"]) == 2
    assert json.loads(capsys.readouterr().out)["ok"] is False
    assert service.calls == []


def test_interface_validation_does_not_execute_suggestions_or_transmit(tmp_path, monkeypatch):
    def forbidden(*args, **kwargs):
        raise AssertionError("no command execution")
    monkeypatch.setattr(subprocess, "run", forbidden)
    monkeypatch.setattr(subprocess, "Popen", forbidden)
    service = ProcedureService()
    request = _request(tmp_path, "learn_procedure")
    request["spec"] = deepcopy(request["spec"])
    request["spec"]["steps"] = ["curl https://example.invalid; rm -rf /"]
    assert handle_request(service, request)["ok"] is True
    assert service.calls[0][1]["steps"] == request["spec"]["steps"]
    assert handle_request(service, {**request, "token": "bad"}, auth_token="required")["error"] == "unauthorized"
    assert len(service.calls) == 1


def test_real_api_procedure_lifecycle_and_measured_unknown(tmp_path):
    (tmp_path / "artifact.txt").write_text("ready", encoding="utf-8")
    service = MemGuardService(tmp_path / "memory.segment", tmp_path / "meta.sqlite3")
    try:
        source = service._persist_new_memory(make_memory(
            "发布产物的人工检查依据", "knowledge", MemorySource("manual"),
            scope=service.namespace, project_root=str(tmp_path),
        ))
        learned = handle_request(service, {
            "command": "learn_procedure", "workspace": str(tmp_path), "spec": _spec(),
            "source_memory_ids": [source.memory_id], "session_id": "real-interface-session",
        })
        assert learned["ok"] is True
        procedure_id = learned["result"]["memory_id"]
        assert learned["result"]["verification"]["overall"] == "pass"
        recalled = handle_request(service, {
            "command": "recall_procedures", "task": "检查发布产物", "workspace": str(tmp_path),
        })
        assert recalled["ok"] is True
        assert any(item["memory_id"] == procedure_id for item in recalled["result"])
        verified = handle_request(service, {
            "command": "verify_procedure", "memory_id": procedure_id, "workspace": str(tmp_path),
            "session_id": "real-interface-session", "use_id": "first-use",
        })
        assert verified["ok"] is True
        assert verified["result"]["overall"] == "pass"
        assert verified["result"]["recorded"] is True
        (tmp_path / "artifact.txt").write_bytes(b"\xff\xfe")
        unknown = handle_request(service, {
            "command": "verify_procedure", "memory_id": procedure_id, "workspace": str(tmp_path),
            "session_id": "real-interface-session", "use_id": "non-text-use",
        })
        assert unknown["ok"] is False
        assert unknown["result"]["overall"] == "unknown"
        assert unknown["result"]["checks"][0]["reason"] == "not_utf8_text"
        impact = handle_request(service, {"command": "dependency_impact", "memory_ids": [source.memory_id]})
        assert impact["ok"] is True
        assert {item["memory_id"] for item in impact["result"]["derived_memories"]} == {procedure_id}
        assert not service.meta.get_memory(source.memory_id)["deleted"]
        assert not service.meta.get_memory(procedure_id)["deleted"]
    finally:
        service.close()


def test_real_cli_readable_learn_recall_verify_and_dependency_preview(tmp_path, capsys):
    (tmp_path / "artifact.txt").write_text("ready", encoding="utf-8")
    store = tmp_path / "memory.segment"
    db = tmp_path / "meta.sqlite3"
    service = MemGuardService(store, db)
    try:
        source = service._persist_new_memory(make_memory(
            "发布产物的人工检查依据", "knowledge", MemorySource("manual"),
            scope=service.namespace, project_root=str(tmp_path),
        ))
    finally:
        service.close()
    spec_file = tmp_path / "spec.json"
    spec_file.write_text(json.dumps(_spec(), ensure_ascii=False), encoding="utf-8")
    common = ["--workspace", str(tmp_path), "--store", str(store), "--db", str(db), "--session-id", "cli-session"]
    assert memguard.main([*common, "learn-procedure", str(spec_file), "--source", source.memory_id]) == 0
    output = capsys.readouterr().out
    for text in ["已学习并验证", "人工检查", "来源变化时停止复用", "matched", source.memory_id]:
        assert text in output
    service = MemGuardService(store, db)
    try:
        procedure_id = next(item["memory_id"] for item in service.list_memories() if item["type"] == "procedure")
    finally:
        service.close()
    assert memguard.main([*common, "procedures", "检查发布产物"]) == 0
    output = capsys.readouterr().out
    assert "可供复用（尚未执行或复核本次任务）" in output
    assert "来源变化时停止复用" in output
    assert "未验证" in output
    assert memguard.main([*common, "verify-procedure", procedure_id, "--use-id", "real-cli-use"]) == 0
    output = capsys.readouterr().out
    for text in ["实际检查：通过", "matched", "反馈：已记录", "real-cli-use", "cli-session"]:
        assert text in output
    assert memguard.main([*common, "dependencies", source.memory_id]) == 0
    output = capsys.readouterr().out
    assert procedure_id in output
    assert "本次仅预览" in output


def test_cli_stale_derivation_selection_reports_confirmation_boundary(tmp_path, capsys):
    service = MemGuardService(tmp_path / "memory.segment", tmp_path / "meta.sqlite3")
    try:
        source = service._persist_new_memory(make_memory(
            "Invoice inventory source.", "knowledge", MemorySource("manual"), scope=service.namespace,
        ))
        child = service._persist_new_memory(make_memory(
            "Derived packing procedure.", "procedure", MemorySource("verified_procedure"),
            scope=service.namespace, dependencies=[{
                "memory_id": source.memory_id, "version_id": source.version.version_id, "relation": "derived_from",
            }],
        ))
        result = service.forget_memory_ids(
            [source.memory_id], "user selection", expected_versions={source.memory_id: source.version.version_id},
        )
        assert result["reason"] == "stale_preview"
        assert memguard._print_confirmed_forget(result) == 2
        output = capsys.readouterr().out
        assert "来源的派生记忆必须一并确认；请重看影响范围" in output
        assert child.memory_id in output
        assert "没有找到需要遗忘" not in output
        assert not service.meta.get_memory(source.memory_id)["deleted"]
        assert not service.meta.get_memory(child.memory_id)["deleted"]
    finally:
        service.close()


def test_cli_stale_version_reports_reselection_without_claiming_missing_target(capsys):
    assert memguard._print_confirmed_forget({
        "ok": False, "reason": "stale_preview", "stale_memory_ids": ["changed-memory"], "forgotten_memory_ids": [],
    }) == 2
    output = capsys.readouterr().out
    assert "记忆版本已变化" in output
    assert "changed-memory" in output
    assert "没有找到需要遗忘" not in output
