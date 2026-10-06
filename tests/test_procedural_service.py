"""Mixin contracts and temporary-store MemGuardService integration.

The record subclass supplies the dependency field until the main integration
adds it to MemoryRecord. Real integration tests use only temporary datastores.
"""

from copy import deepcopy
from dataclasses import dataclass, field
import json
import platform
import subprocess

import pytest

from memguard.models import MemoryRecord, MemorySource, VersionInfo, make_memory
from memguard import procedure_service
from memguard.procedure_service import ProcedureServiceMixin
from memguard.procedural import MAX_FILE_BYTES, ProcedureValidationError
from memguard.sensitivity.detector import SensitiveDetector


@dataclass
class DependencyRecord(MemoryRecord):
    dependencies: list[dict[str, str]] = field(default_factory=list)


def _make_record(**kwargs):
    base = make_memory(**kwargs)
    return DependencyRecord(**base.__dict__)


def _row(record):
    return {
        "memory_id": record.memory_id, "version_id": record.version.version_id,
        "type": record.type, "scope": record.scope, "content": record.content,
        "project_root": record.project_root, "trust_level": record.trust_level,
        "governance_state": record.governance_state, "deleted": record.deleted,
        "retention_session_id": record.retention_session_id,
        "valid_to": record.temporal.valid_to, "superseded_by": record.temporal.superseded_by,
        "dependencies": deepcopy(record.dependencies),
    }


def _spec(**updates):
    result = {"goal": "Prepare artifact", "steps": ["Write artifact manually"],
              "failure_guards": ["Stop if writing fails"], "parameters": {}, "environment": {},
              "postconditions": [{"type": "file_contains", "path": "artifact.txt", "value": "ready"}]}
    result.update(updates)
    return result


class FakeMeta:
    def __init__(self):
        self.rows = {}
        self.feedback = {}
        self.feedback_calls = []
        self.reject_feedback = False

    def get_memory(self, memory_id):
        return deepcopy(self.rows.get(memory_id))

    def procedure_stats(self, memory_id):
        statuses = [status for key, status in self.feedback.items() if key[0] == memory_id]
        measured = statuses.count("pass") + statuses.count("fail")
        return {"pass": statuses.count("pass"), "fail": statuses.count("fail"),
                "unknown": statuses.count("unknown"), "measured": measured,
                "success_rate": statuses.count("pass") / measured if measured else None}

    def record_procedure_feedback(self, memory_id, version_id, session_id, use_id, status):
        self.feedback_calls.append((memory_id, version_id, session_id, use_id, status))
        row = self.rows[memory_id]
        if self.reject_feedback or row["version_id"] != version_id or row["governance_state"] != "active":
            return False
        for dependency in row["dependencies"]:
            source = self.rows.get(dependency["memory_id"])
            if not source or source["version_id"] != dependency["version_id"] or source["deleted"] or source["governance_state"] != "active":
                return False
        key = (memory_id, version_id, session_id, use_id)
        if key in self.feedback:
            if self.feedback[key] != status:
                raise ValueError("use identifier already has another verification result")
            return {"recorded": False, "duplicate": True, "status": self.feedback[key]}
        self.feedback[key] = status
        return {"recorded": True, "duplicate": False, "status": status}


class Host(ProcedureServiceMixin):
    def __init__(self, workspace):
        self.namespace = "contract-user"
        self.detector = SensitiveDetector()
        self.meta = FakeMeta()
        self.persisted = []
        self.search_calls = []
        self.candidates = None
        self.reject_persist = False
        self.project = str(workspace.resolve())
        source = _make_record(content="The artifact should contain ready", memory_type="knowledge",
                              source=MemorySource(kind="manual"), scope=self.namespace, project_root=self.project)
        self.source_id = source.memory_id
        self.meta.rows[self.source_id] = _row(source)

    def _persist_new_memory(self, record):
        if self.reject_persist:
            raise ValueError("transactional source CAS refused")
        assert record.to_dict()["dependencies"] == record.dependencies
        assert self._procedure_sources_current(record.dependencies, record.project_root, "")
        self.persisted.append(record)
        self.meta.rows[record.memory_id] = _row(record)
        return record

    def search(self, task, **kwargs):
        self.search_calls.append((task, kwargs))
        return deepcopy(list(self.meta.rows.values()) if self.candidates is None else self.candidates)


@pytest.fixture
def host(tmp_path, monkeypatch):
    monkeypatch.setattr(procedure_service, "make_memory", _make_record)
    (tmp_path / "artifact.txt").write_text("ready", encoding="utf-8")
    return Host(tmp_path)


def _learn(host, tmp_path, **kwargs):
    return host.learn_procedure(_spec(), tmp_path, [host.source_id], **kwargs)


def test_learn_persists_spec_not_rendered_arguments_and_binds_source_versions(host, tmp_path):
    spec = _spec(steps=["Inspect $name"], parameters={"name": None})
    before = deepcopy(spec)
    result = host.learn_procedure(spec, tmp_path, [host.source_id], arguments={"name": "provided-at-call-time"})
    assert result["status"] == "learned"
    record = result["record"]
    assert record.type == "procedure" and record.subtype == "workflow"
    assert record.scope == host.namespace and record.project_root == host.project
    assert record.source.kind == "verified_procedure"
    assert record.trust_level == "verified" and record.governance_state == "active"
    assert json.loads(record.content) == spec == before
    assert "provided-at-call-time" not in record.to_json()
    assert record.dependencies == [{"memory_id": host.source_id,
                                    "version_id": host.meta.rows[host.source_id]["version_id"],
                                    "relation": "derived_from"}]
    assert result["verification"]["rendered_steps"] == ["Inspect provided-at-call-time"]
    assert result["rendered"]["steps"] == ["Inspect provided-at-call-time"]
    assert len(host.persisted) == 1 and not host.meta.feedback_calls


def test_learn_allows_global_source_but_keeps_procedure_project_bound(host, tmp_path):
    host.meta.rows[host.source_id]["project_root"] = ""
    assert _learn(host, tmp_path)["record"].project_root == host.project


@pytest.mark.parametrize("updates", [
    {"deleted": True}, {"scope": "other-user"}, {"project_root": "other-project"},
    {"trust_level": "untrusted"}, {"trust_level": "normal"}, {"trust_level": ""},
    {"governance_state": "quarantined"}, {"governance_state": "source_invalidated"},
    {"governance_state": "sourceinvalidated"}, {"valid_to": "expired"},
    {"superseded_by": "replacement"}, {"version_id": ""}, {"retention_session_id": "other-session"},
])
def test_learn_rejects_ineligible_source(host, tmp_path, updates):
    host.meta.rows[host.source_id].update(updates)
    result = _learn(host, tmp_path)
    assert result["status"] == "blocked" and result["reason"] == "source_unavailable"
    assert not result["stored"] and not host.persisted


@pytest.mark.parametrize("ids", [None, [], (), "source", [None], ["x"] * 33])
def test_learning_requires_bounded_explicit_source_ids(host, tmp_path, ids):
    with pytest.raises(ProcedureValidationError):
        host.learn_procedure(_spec(), tmp_path, ids)
    assert not host.persisted


def test_every_declared_source_must_be_eligible(host, tmp_path):
    result = host.learn_procedure(_spec(), tmp_path, [host.source_id, "missing-source"])
    assert result["status"] == "blocked" and not host.persisted
    with pytest.raises(ProcedureValidationError):
        host.learn_procedure(_spec(), tmp_path, [host.source_id, host.source_id])


@pytest.mark.parametrize("field,value", [
    ("goal", "password=secret123"), ("steps", ["password=secret123"]),
    ("failure_guards", ["password=secret123"]),
    ("parameters", {"password": "secret123"}),
    ("environment", {"credential": "-----BEGIN PRIVATE KEY-----"}),
    ("postconditions", [{"type": "file_contains", "path": "artifact.txt", "value": "password=secret123"}]),
])
def test_entire_spec_is_sensitive_checked_before_file_verification(host, tmp_path, monkeypatch, field, value):
    def forbidden(*args, **kwargs):
        raise AssertionError("sensitive inputs must not trigger file verification")

    monkeypatch.setattr(procedure_service, "verify_postconditions", forbidden)
    result = host.learn_procedure(_spec(**{field: value}), tmp_path, [host.source_id])
    assert result["reason"] == "sensitive_high" and not result["stored"]
    assert "secret123" not in json.dumps(result)
    assert not host.persisted


def test_sensitive_argument_and_rendered_combination_are_blocked(host, tmp_path):
    spec = _spec(parameters={"password": None})
    assert host.learn_procedure(spec, tmp_path, [host.source_id], arguments={"password": "secret123"})["reason"] == "sensitive_high"
    spec = _spec(steps=["password=$value"], parameters={"value": None})
    result = host.learn_procedure(spec, tmp_path, [host.source_id], arguments={"value": "secret123"})
    assert result["reason"] == "sensitive_high" and not host.persisted


def test_learning_never_executes_steps(host, tmp_path, monkeypatch):
    def forbidden(*args, **kwargs):
        raise AssertionError("shell execution forbidden")

    monkeypatch.setattr(subprocess, "run", forbidden)
    monkeypatch.setattr(subprocess, "Popen", forbidden)
    result = host.learn_procedure(_spec(steps=["arbitrary-command --never-execute"]), tmp_path, [host.source_id])
    assert result["status"] == "learned"


@pytest.mark.parametrize("data,overall", [("not ready anymore?", "pass"), ("absent", "fail"), (b"\xff", "unknown")])
def test_learning_requires_all_actual_postconditions_pass(host, tmp_path, data, overall):
    target = tmp_path / "artifact.txt"
    target.write_bytes(data if isinstance(data, bytes) else data.encode("utf-8"))
    result = _learn(host, tmp_path)
    assert result["overall"] == overall
    assert result["stored"] is (overall == "pass")


def test_learning_rejects_unknown_environment_but_empty_conditions_pass(host, tmp_path):
    result = host.learn_procedure(_spec(environment={"unmeasured_dependency": "installed"}), tmp_path, [host.source_id])
    assert result["reason"] == "environment_not_applicable" and result["overall"] == "unknown"
    assert not host.persisted
    assert _learn(host, tmp_path)["environment"]["overall"] == "pass"


def test_measured_environment_matches_only_known_exact_values(host, tmp_path):
    facts = {"platform.system": platform.system(), "python_version": platform.python_version(),
             "python": ".".join(platform.python_version_tuple()[:2])}
    result = host.learn_procedure(_spec(environment=facts), tmp_path, [host.source_id])
    assert result["status"] == "learned"
    result = host.learn_procedure(_spec(environment={"python_version": "not-this-interpreter"}), tmp_path, [host.source_id])
    assert result["status"] == "blocked" and result["overall"] == "fail"


def test_missing_arguments_are_not_invented_during_learning(host, tmp_path):
    with pytest.raises(ProcedureValidationError):
        host.learn_procedure(_spec(parameters={"required": None}), tmp_path, [host.source_id])
    assert not host.persisted


def test_source_change_while_verifying_prevents_learning(host, tmp_path, monkeypatch):
    real_verify = procedure_service.verify_postconditions

    def change_source(*args, **kwargs):
        result = real_verify(*args, **kwargs)
        host.meta.rows[host.source_id]["version_id"] = "new-source-version"
        return result

    monkeypatch.setattr(procedure_service, "verify_postconditions", change_source)
    assert _learn(host, tmp_path)["reason"] == "source_changed"
    assert not host.persisted


def test_transactional_persistence_cas_rejection_is_not_reported_as_success(host, tmp_path):
    host.reject_persist = True
    assert _learn(host, tmp_path)["reason"] == "source_snapshot_changed"
    assert not host.persisted


def test_dependency_serialization_support_is_required(host, tmp_path, monkeypatch):
    def without_dependency_support(**kwargs):
        record = _make_record(**kwargs)
        record.__dataclass_fields__ = {key: item for key, item in record.__dataclass_fields__.items() if key != "dependencies"}
        return record

    monkeypatch.setattr(procedure_service, "make_memory", without_dependency_support)
    with pytest.raises(RuntimeError, match="serialize dependencies"):
        _learn(host, tmp_path)
    assert not host.persisted


def test_recall_uses_scoped_non_audited_search_and_returns_suggestions_and_stats(host, tmp_path):
    result = _learn(host, tmp_path)
    recalled = host.recall_procedures("prepare artifact", tmp_path, session_id="session-a")
    assert len(recalled) == 1
    assert recalled[0]["memory_id"] == result["memory_id"]
    assert recalled[0]["steps"] == _spec()["steps"]
    assert recalled[0]["failure_guards"] == _spec()["failure_guards"]
    assert recalled[0]["postconditions"] == _spec()["postconditions"]
    assert recalled[0]["dependencies"] == result["dependencies"]
    assert recalled[0]["feedback_stats"] == {"pass": 0, "fail": 0, "unknown": 0, "measured": 0, "success_rate": None}
    assert recalled[0]["status"] == "ready"
    assert host.search_calls[-1][1]["project_root"] == host.project
    assert host.search_calls[-1][1]["session_id"] == "session-a"
    assert host.search_calls[-1][1]["audit"] is False
    assert not host.meta.feedback_calls


def test_recall_is_not_a_claim_that_postconditions_still_pass(host, tmp_path):
    _learn(host, tmp_path)
    (tmp_path / "artifact.txt").unlink()
    assert host.recall_procedures("artifact", tmp_path)[0]["status"] == "ready"


def test_recall_missing_arguments_is_explicit_and_has_no_fabricated_steps(host, tmp_path):
    spec = _spec(steps=["Inspect $name"], parameters={"name": None})
    host.learn_procedure(spec, tmp_path, [host.source_id], arguments={"name": "initial"})
    recalled = host.recall_procedures("artifact", tmp_path)[0]
    assert recalled["status"] == "requires_arguments"
    assert recalled["required_arguments"] == ["name"]
    assert recalled["steps"] == recalled["failure_guards"] == recalled["postconditions"] == []
    assert "initial" not in json.dumps(recalled)
    recalled = host.recall_procedures("artifact", tmp_path, arguments={"name": "later"})[0]
    assert recalled["status"] == "ready" and recalled["steps"] == ["Inspect later"]


def test_recall_unknown_arguments_is_explicit(host, tmp_path):
    _learn(host, tmp_path)
    recalled = host.recall_procedures("artifact", tmp_path, arguments={"unknown": "value"})[0]
    assert recalled["status"] == "invalid_arguments" and recalled["steps"] == []


@pytest.mark.parametrize("updates", [
    {"deleted": True}, {"type": "knowledge"}, {"scope": "foreign"},
    {"project_root": "foreign-project"}, {"trust_level": "untrusted"},
    {"governance_state": "source_invalidated"}, {"valid_to": "expired"},
    {"superseded_by": "replacement"}, {"retention_session_id": "other-session"},
])
def test_recall_and_verify_recheck_current_procedure_metadata(host, tmp_path, updates):
    learned = _learn(host, tmp_path)
    host.candidates = [deepcopy(host.meta.rows[learned["memory_id"]])]
    host.meta.rows[learned["memory_id"]].update(updates)
    assert host.recall_procedures("artifact", tmp_path) == []
    assert host.verify_procedure(learned["memory_id"], tmp_path)["status"] == "blocked"
    assert not host.meta.feedback_calls


def test_stale_search_versions_and_duplicate_search_hits_are_filtered(host, tmp_path):
    learned = _learn(host, tmp_path)
    row = deepcopy(host.meta.rows[learned["memory_id"]])
    host.candidates = [dict(row, version_id="stale")]
    assert host.recall_procedures("artifact", tmp_path) == []
    host.candidates = [row, row]
    assert len(host.recall_procedures("artifact", tmp_path)) == 1


@pytest.mark.parametrize("updates", [{"version_id": "changed"}, {"deleted": True}, {"trust_level": "untrusted"},
                                     {"scope": "foreign"}, {"project_root": "foreign-project"},
                                     {"governance_state": "quarantined"}])
def test_stale_or_invalid_source_blocks_recall_and_verification(host, tmp_path, updates):
    learned = _learn(host, tmp_path)
    host.meta.rows[host.source_id].update(updates)
    assert host.recall_procedures("artifact", tmp_path) == []
    assert host.verify_procedure(learned["memory_id"], tmp_path)["status"] == "blocked"
    assert not host.meta.feedback_calls


@pytest.mark.parametrize("dependencies", [[], None, "not-json", [{"memory_id": "x"}],
                                          [{"memory_id": "x", "version_id": "v", "relation": "other"}]])
def test_malformed_dependencies_fail_closed(host, tmp_path, dependencies):
    learned = _learn(host, tmp_path)
    host.meta.rows[learned["memory_id"]]["dependencies"] = dependencies
    assert host.recall_procedures("artifact", tmp_path) == []
    assert host.verify_procedure(learned["memory_id"], tmp_path)["status"] == "blocked"


def test_dependency_json_projection_and_global_procedures_are_supported(host, tmp_path):
    learned = _learn(host, tmp_path)
    row = host.meta.rows[learned["memory_id"]]
    row["dependencies_json"] = json.dumps(row.pop("dependencies"))
    row["project_root"] = ""
    assert host.recall_procedures("artifact", tmp_path)[0]["dependencies"] == learned["dependencies"]


def test_unknown_environment_is_not_recalled_or_verified(host, tmp_path):
    learned = _learn(host, tmp_path)
    host.meta.rows[learned["memory_id"]]["content"] = json.dumps(_spec(environment={"unmeasured": "value"}))
    assert host.recall_procedures("artifact", tmp_path) == []
    result = host.verify_procedure(learned["memory_id"], tmp_path)
    assert result["overall"] == "unknown" and not host.meta.feedback_calls


def test_high_sensitivity_recall_arguments_never_reach_search(host, tmp_path):
    assert host.recall_procedures("artifact", tmp_path, arguments={"password": "secret123"}) == []
    assert not host.search_calls


@pytest.mark.parametrize("data,status", [(b"ready", "pass"), (b"missing", "fail"), (b"\xff", "unknown")])
def test_verification_records_only_minimal_fields_and_unknown_is_not_success(host, tmp_path, data, status):
    learned = _learn(host, tmp_path)
    (tmp_path / "artifact.txt").write_bytes(data)
    result = host.verify_procedure(learned["memory_id"], tmp_path, session_id="session-a", use_id="use-a")
    assert result["status"] == result["overall"] == status
    assert result["verified"] is (status == "pass")
    assert result["recorded"] is True
    assert host.meta.feedback_calls == [(learned["memory_id"], learned["version_id"], "session-a", "use-a", status)]
    assert "artifact.txt" not in json.dumps(host.meta.feedback_calls)
    assert host.project not in json.dumps(host.meta.feedback_calls)
    stats = host.meta.procedure_stats(learned["memory_id"])
    assert stats["pass"] == int(status == "pass")
    assert stats["unknown"] == int(status == "unknown")
    assert stats["measured"] == int(status != "unknown")
    if status == "unknown":
        assert stats["success_rate"] is None


def test_feedback_idempotency_rejects_conflicting_status_without_double_counting(host, tmp_path):
    learned = _learn(host, tmp_path)
    first = host.verify_procedure(learned["memory_id"], tmp_path, use_id="same-use")
    second = host.verify_procedure(learned["memory_id"], tmp_path, use_id="same-use")
    assert first["recorded"] and not second["recorded"] and second["duplicate"]
    (tmp_path / "artifact.txt").write_text("absent", encoding="utf-8")
    third = host.verify_procedure(learned["memory_id"], tmp_path, use_id="same-use")
    assert third["status"] == "blocked" and third["reason"] == "feedback_snapshot_changed"
    assert host.meta.procedure_stats(learned["memory_id"])["measured"] == 1


def test_default_use_ids_are_distinct(host, tmp_path):
    learned = _learn(host, tmp_path)
    first = host.verify_procedure(learned["memory_id"], tmp_path)
    second = host.verify_procedure(learned["memory_id"], tmp_path)
    assert first["use_id"] != second["use_id"]
    assert host.meta.procedure_stats(learned["memory_id"])["measured"] == 2


def test_boolean_feedback_contract_is_supported(host, tmp_path, monkeypatch):
    learned = _learn(host, tmp_path)
    monkeypatch.setattr(host.meta, "record_procedure_feedback", lambda *args: True)
    assert host.verify_procedure(learned["memory_id"], tmp_path)["recorded"] is True


def test_cas_feedback_refusal_does_not_claim_a_verified_use(host, tmp_path):
    learned = _learn(host, tmp_path)
    host.meta.reject_feedback = True
    result = host.verify_procedure(learned["memory_id"], tmp_path)
    assert result["reason"] == "feedback_snapshot_changed"
    assert not result["verified"] and not result["recorded"]
    assert not host.meta.feedback


def test_transactional_feedback_exception_is_a_blocked_result(host, tmp_path, monkeypatch):
    learned = _learn(host, tmp_path)

    def refused(*args):
        raise ValueError("CAS conflict")

    monkeypatch.setattr(host.meta, "record_procedure_feedback", refused)
    assert host.verify_procedure(learned["memory_id"], tmp_path)["reason"] == "feedback_snapshot_changed"


@pytest.mark.parametrize("target", ["procedure", "source"])
def test_version_changes_during_verification_prevent_feedback(host, tmp_path, monkeypatch, target):
    learned = _learn(host, tmp_path)
    real_verify = procedure_service.verify_postconditions

    def changed(*args, **kwargs):
        result = real_verify(*args, **kwargs)
        memory_id = learned["memory_id"] if target == "procedure" else host.source_id
        host.meta.rows[memory_id]["version_id"] = "changed-version"
        return result

    monkeypatch.setattr(procedure_service, "verify_postconditions", changed)
    assert host.verify_procedure(learned["memory_id"], tmp_path)["reason"] == "procedure_or_source_changed"
    assert not host.meta.feedback_calls


def test_sensitive_verification_arguments_do_not_write_feedback(host, tmp_path):
    learned = _learn(host, tmp_path)
    result = host.verify_procedure(learned["memory_id"], tmp_path, arguments={"password": "secret123"})
    assert result["reason"] == "sensitive_high" and not host.meta.feedback_calls


def test_parameter_path_escape_is_fail_not_a_verified_success(host, tmp_path):
    spec = _spec(parameters={"path": None}, postconditions=[{"type": "path_exists", "path": "$path"}])
    learned = host.learn_procedure(spec, tmp_path, [host.source_id], arguments={"path": "artifact.txt"})
    result = host.verify_procedure(learned["memory_id"], tmp_path, arguments={"path": "../outside.txt"})
    assert result["overall"] == "fail" and not result["verified"]
    assert host.meta.feedback_calls[-1][-1] == "fail"
    assert "../outside.txt" not in json.dumps(host.meta.feedback_calls)
    recalled = host.recall_procedures("artifact", tmp_path, arguments={"path": "../outside.txt"})[0]
    assert recalled["status"] == "invalid_arguments" and recalled["steps"] == []


def test_oversized_evidence_is_unknown_in_feedback(host, tmp_path):
    learned = _learn(host, tmp_path)
    (tmp_path / "artifact.txt").write_bytes(b"x" * (MAX_FILE_BYTES + 1))
    result = host.verify_procedure(learned["memory_id"], tmp_path)
    assert result["overall"] == "unknown" and not result["verified"]
    assert host.meta.feedback_calls[-1][-1] == "unknown"


def test_all_entrypoints_require_explicit_workspace(host):
    with pytest.raises(ProcedureValidationError):
        host.learn_procedure(_spec(), None, [host.source_id])
    with pytest.raises(ProcedureValidationError):
        host.recall_procedures("artifact", None)
    with pytest.raises(ProcedureValidationError):
        host.verify_procedure("procedure-id", None)


@pytest.mark.parametrize("limit", [0, -1, 51, True, "5"])
def test_recall_limit_is_bounded(host, tmp_path, limit):
    with pytest.raises(ProcedureValidationError):
        host.recall_procedures("artifact", tmp_path, limit=limit)


def test_recall_limit_and_namespace_filter_are_enforced(host, tmp_path):
    for _ in range(3):
        _learn(host, tmp_path)
    assert len(host.recall_procedures("artifact", tmp_path, limit=2)) == 2


@pytest.mark.parametrize("arguments", [[], {"name": None}, {"name": "x" * 4097}, {1: "value"}])
def test_service_arguments_remain_bounded_strings(host, tmp_path, arguments):
    with pytest.raises(ProcedureValidationError):
        host.recall_procedures("artifact", tmp_path, arguments=arguments)


def test_invalid_saved_spec_never_reaches_feedback(host, tmp_path):
    learned = _learn(host, tmp_path)
    host.meta.rows[learned["memory_id"]]["content"] = json.dumps(_spec(postconditions=[]))
    assert host.recall_procedures("artifact", tmp_path) == []
    assert host.verify_procedure(learned["memory_id"], tmp_path)["status"] == "blocked"
    assert not host.meta.feedback_calls


def test_context_line_displays_full_template_even_when_arguments_are_required(host, tmp_path):
    spec = _spec(goal="Prepare $name", steps=["Inspect $name", "Second step", "Final step"],
                 failure_guards=["Never overwrite", "Keep all constraints"],
                 parameters={"name": None, "mode": "safe"},
                 postconditions=[{"type": "path_exists", "path": "artifact.txt"},
                                 {"type": "file_contains", "path": "artifact.txt", "value": "ready"},
                                 {"type": "file_not_contains", "path": "artifact.txt", "value": "corrupt"}])
    learned = host.learn_procedure(spec, tmp_path, [host.source_id], arguments={"name": "initial"})
    line, reason = host.procedure_context_line(learned["memory_id"], tmp_path)
    assert line and reason == ""
    for key in ("goal", "steps", "failure_guards", "postconditions"):
        assert f"{key}={json.dumps(spec[key], ensure_ascii=False)}" in line
    parameters, _ = json.JSONDecoder().raw_decode(line.split("parameters=", 1)[1])
    assert parameters == spec["parameters"]
    assert 'arguments must be provided for=["name"]' in line
    assert json.dumps(learned["dependencies"], ensure_ascii=False) in line
    assert "initial" not in line and "suggestions only" in line
    assert not host.meta.feedback_calls and not host.search_calls


def test_context_line_is_never_truncated(host, tmp_path):
    spec = _spec(steps=["x" * 4096] * 20, failure_guards=["g" * 4096] * 20)
    learned = host.learn_procedure(spec, tmp_path, [host.source_id])
    line, reason = host.procedure_context_line(learned["memory_id"], tmp_path)
    assert reason == "" and len(line) > 160000
    assert f"steps={json.dumps(spec['steps'])}" in line
    assert f"failure_guards={json.dumps(spec['failure_guards'])}" in line
    assert line.endswith(json.dumps(learned["dependencies"]))


@pytest.mark.parametrize("updates", [
    {"deleted": True}, {"scope": "foreign"}, {"project_root": "foreign-project"},
    {"trust_level": "untrusted"}, {"governance_state": "source_invalidated"},
    {"committed": False}, {"write_state": "staged"},
])
def test_context_line_omits_ineligible_or_uncommitted_records(host, tmp_path, updates):
    learned = _learn(host, tmp_path)
    host.meta.rows[learned["memory_id"]].update(updates)
    assert host.procedure_context_line(learned["memory_id"], tmp_path) == ("", "procedure_or_source_unavailable")


def test_context_line_requires_environment_and_current_source(host, tmp_path):
    learned = _learn(host, tmp_path)
    host.meta.rows[learned["memory_id"]]["content"] = json.dumps(_spec(environment={"unknown_condition": "value"}))
    assert host.procedure_context_line(learned["memory_id"], tmp_path) == ("", "environment_not_applicable")
    host.meta.rows[host.source_id]["version_id"] = "changed-source"
    assert host.procedure_context_line(learned["memory_id"], tmp_path) == ("", "procedure_or_source_unavailable")


def test_context_line_returns_reason_for_invalid_workspace_and_missing_memory(host, tmp_path):
    assert host.procedure_context_line("missing", tmp_path) == ("", "procedure_or_source_unavailable")
    assert host.procedure_context_line("missing", None) == ("", "invalid_workspace_or_memory_id")


def _open_real_service(tmp_path, monkeypatch):
    import socket

    from memguard.os_guard.journald import JournaldLogger
    from memguard.service import MemGuardService

    def no_external_calls(*args, **kwargs):
        raise AssertionError("integration must remain local and must not execute steps")

    monkeypatch.setenv("MEMGUARD_EMBEDDING_BACKEND", "hash")
    monkeypatch.setenv("MEMGUARD_EXTRACTOR_COMMAND", "")
    monkeypatch.setenv("MEMGUARD_DENSE_CANDIDATES", "0")
    monkeypatch.setattr(socket, "create_connection", no_external_calls)
    monkeypatch.setattr(subprocess, "run", no_external_calls)
    monkeypatch.setattr(subprocess, "Popen", no_external_calls)
    monkeypatch.setattr(JournaldLogger, "log", lambda *args, **kwargs: None)
    return MemGuardService(tmp_path / "memory.segment", tmp_path / "meta.sqlite3",
                          namespace="procedure-contract-test")


def _real_source(service, tmp_path):
    return service._persist_new_memory(make_memory(
        "Audited release acceptance criteria.", "knowledge", MemorySource("manual"),
        scope=service.namespace, project_root=str(tmp_path.resolve()),
    ))


def test_real_service_roundtrip_parameter_transfer_and_feedback_idempotency(tmp_path, monkeypatch):
    training = tmp_path / "training-artifact.txt"
    deployed = tmp_path / "deployed-artifact.txt"
    training.write_text("ready", encoding="utf-8")
    deployed.write_text("ready", encoding="utf-8")
    spec = _spec(goal="ArtifactTransferContract", steps=["Inspect $name"],
                 parameters={"name": None},
                 postconditions=[{"type": "file_contains", "path": "$name", "value": "ready"}])
    service = _open_real_service(tmp_path, monkeypatch)
    try:
        source = _real_source(service, tmp_path)
        learned = service.learn_procedure(spec, tmp_path, [source.memory_id],
                                         arguments={"name": training.name})
        assert learned["stored"] and learned["verification"]["overall"] == "pass"
        memory_id = learned["memory_id"]
        version_id = learned["version_id"]
    finally:
        service.close()

    service = _open_real_service(tmp_path, monkeypatch)
    try:
        stored = service.store.get_latest(memory_id)
        assert json.loads(stored.content) == spec
        assert stored.version.version_id == version_id
        assert stored.dependencies == [{"memory_id": source.memory_id,
                                        "version_id": source.version.version_id, "relation": "derived_from"}]
        assert training.name not in stored.to_json()
        required = next(item for item in service.recall_procedures(spec["goal"], tmp_path)
                        if item["memory_id"] == memory_id)
        assert required["status"] == "requires_arguments" and required["required_arguments"] == ["name"]
        recalled = next(item for item in service.recall_procedures(spec["goal"], tmp_path,
                                                                  arguments={"name": deployed.name})
                        if item["memory_id"] == memory_id)
        assert recalled["steps"] == [f"Inspect {deployed.name}"]
        assert recalled["postconditions"][0]["path"] == deployed.name
        kwargs = {"session_id": "real-test-session", "use_id": "real-use",
                  "arguments": {"name": deployed.name}}
        first = service.verify_procedure(memory_id, tmp_path, **kwargs)
        duplicate = service.verify_procedure(memory_id, tmp_path, **kwargs)
        assert first["overall"] == duplicate["overall"] == "pass"
        assert first["goal"] == spec["goal"] and first["steps"] == [f"Inspect {deployed.name}"]
        assert first["failure_guards"] == spec["failure_guards"]
        assert first["postconditions"][0]["path"] == deployed.name
        assert first["dependencies"] == stored.dependencies and first["environment"]["overall"] == "pass"
        assert first["recorded"] and not first["duplicate"]
        assert not duplicate["recorded"] and duplicate["duplicate"]
        assert service.meta.procedure_stats(memory_id) == {
            "pass": 1, "fail": 0, "unknown": 0, "measured": 1, "success_rate": 1.0,
        }
        deployed.write_text("not accepted", encoding="utf-8")
        conflict = service.verify_procedure(memory_id, tmp_path, **kwargs)
        assert conflict["reason"] == "feedback_snapshot_changed" and not conflict["verified"]
        deployed.write_bytes(b"\xff")
        unknown = service.verify_procedure(memory_id, tmp_path, **{**kwargs, "use_id": "real-unknown-use"})
        assert unknown["overall"] == "unknown" and unknown["recorded"] and not unknown["verified"]
        assert service.meta.procedure_stats(memory_id) == {
            "pass": 1, "fail": 0, "unknown": 1, "measured": 1, "success_rate": 1.0,
        }
        feedback_rows = [dict(row) for row in service.meta.conn.execute("SELECT * FROM procedure_feedback")]
        assert len(feedback_rows) == 2
        assert set(feedback_rows[0]) == {"memory_id", "version_id", "session_id", "use_id", "status", "created_at"}
        feedback_json = json.dumps(feedback_rows)
        assert training.name not in feedback_json and deployed.name not in feedback_json
        assert str(tmp_path) not in feedback_json and "Inspect" not in feedback_json
    finally:
        service.close()


def test_real_service_source_commit_cas_and_whole_procedure_context_budgets(tmp_path, monkeypatch):
    (tmp_path / "artifact.txt").write_text("ready", encoding="utf-8")
    spec = _spec(goal="WholeConstraintBudgetContract", parameters={"name": None},
                 steps=["Inspect $name", "Retain STEP_END_MARKER"],
                 failure_guards=["Never overwrite; " + "preserve " * 80 + "GUARD_END_MARKER"],
                 postconditions=[{"type": "file_contains", "path": "$name", "value": "ready"},
                                 {"type": "file_not_contains", "path": "$name", "value": "POSTCONDITION_END_MARKER"}])
    service = _open_real_service(tmp_path, monkeypatch)
    try:
        source = _real_source(service, tmp_path)
        learned = service.learn_procedure(spec, tmp_path, [source.memory_id], arguments={"name": "artifact.txt"})
        memory_id = learned["memory_id"]
        line, reason = service.procedure_context_line(memory_id, tmp_path)
        assert line and reason == "" and 'arguments must be provided for=["name"]' in line
        assert all(marker in line for marker in ("STEP_END_MARKER", "GUARD_END_MARKER", "POSTCONDITION_END_MARKER"))

        context = service.build_context(spec["goal"], project_root=str(tmp_path), max_chars=20000, token_budget=20000)
        assert line in context["context"]
        agent_kwargs = {"task": spec["goal"], "stage": "planning", "agent_name": "contract-agent",
                        "session_id": "budget-session", "project_root": str(tmp_path)}
        agent_context = service.build_agent_context(**agent_kwargs, max_chars=20000, token_budget=20000)
        assert line in agent_context["context"]
        for max_chars, token_budget in ((len(line) - 1, 20000), (20000, service.estimate_tokens(line) - 1)):
            small = service.build_context(spec["goal"], project_root=str(tmp_path),
                                          max_chars=max_chars, token_budget=token_budget)
            assert memory_id not in {item["memory_id"] for item in small["memories"]}
            assert any(item["memory_id"] == memory_id and item["reason"] == "procedure_budget_exhausted"
                       for item in small["explainability"]["selection_trace"])
            small_agent = service.build_agent_context(**agent_kwargs, max_chars=max_chars, token_budget=token_budget)
            assert memory_id not in {item["memory_id"] for item in small_agent["memories"]}
            assert any(item.get("memory_id") == memory_id and item["reason"] == "context_budget_exhausted"
                       for item in small_agent["explainability"]["filtered"])
            for output in (small["context"], small_agent["context"]):
                assert "Procedure " + memory_id not in output and "STEP_END_MARKER" not in output
                assert "GUARD_END_MARKER" not in output and "POSTCONDITION_END_MARKER" not in output

        original_stage = service.meta.stage_new_memory_write
        attempted = []

        def stage_then_change_source(record, *args, **kwargs):
            result = original_stage(record, *args, **kwargs)
            if record.type == "procedure":
                attempted.append(record.memory_id)
                # update_memory first recovers pending child writes. Use the
                # real version-write transaction to simulate an independent
                # source commit without that sequential recovery side effect.
                previous = deepcopy(source)
                current = deepcopy(source)
                current.content = "Audited acceptance criteria changed during commit."
                current.version = VersionInfo(parent_version_id=source.version.version_id, op="update")
                previous.invalidate(superseded_by=current.version.version_id)
                current.finalize_hash()
                service._persist_existing_memory(previous, current)
            return result

        monkeypatch.setattr(service.meta, "stage_new_memory_write", stage_then_change_source)
        raced = service.learn_procedure(_spec(goal="CASRaceContract"), tmp_path, [source.memory_id])
        assert raced["status"] == "blocked" and raced["reason"] == "source_snapshot_changed"
        assert len(attempted) == 1 and service.meta.get_memory(attempted[0]) is None
        assert service.store.get_latest(attempted[0]) is None and service.meta.pending_memory_writes() == []
        assert service.meta.get_memory(memory_id)["governance_state"] == "source_invalidated"
        assert service.procedure_context_line(memory_id, tmp_path) == ("", "procedure_or_source_unavailable")
        assert service.verify_procedure(memory_id, tmp_path, session_id="budget-session")["status"] == "blocked"
        assert service.meta.conn.execute("SELECT COUNT(*) FROM procedure_feedback").fetchone()[0] == 0
    finally:
        service.close()
