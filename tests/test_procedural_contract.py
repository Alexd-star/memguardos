from copy import deepcopy
import os
from pathlib import Path
import stat
import subprocess
from types import SimpleNamespace

import pytest

from memguard import procedural
from memguard.procedural import (
    MAX_FILE_BYTES,
    ProcedureSpec,
    ProcedureValidationError,
    check_environment,
    verify_postconditions,
)


def _payload(**updates):
    result = {
        "goal": "Prepare an artifact",
        "steps": ["Write the artifact manually"],
        "failure_guards": ["Stop if the destination is ambiguous"],
        "parameters": {},
        "postconditions": [{"type": "path_exists", "path": "artifact.txt"}],
        "environment": {"os": "test-host"},
    }
    result.update(updates)
    return result


def test_template_validation_preserves_python_310_compatibility(monkeypatch):
    monkeypatch.delattr(procedural.Template, "is_valid", raising=False)
    monkeypatch.delattr(procedural.Template, "get_identifiers", raising=False)
    spec = ProcedureSpec.from_dict(_payload(parameters={"file": "artifact.txt"},
        steps=["Read ${file}; literal dollar $$"],
        postconditions=[{"type": "path_exists", "path": "$file"}]))
    assert spec.render_steps() == ["Read artifact.txt; literal dollar $"]
    for text in ("Read $missing", "Read ${file", "Read $!"):
        with pytest.raises(ProcedureValidationError):
            ProcedureSpec.from_dict(_payload(steps=[text], parameters={"file": "artifact.txt"}))


def _content(kind="file_contains", value="ready", path="artifact.txt"):
    return {"type": kind, "path": path, "value": value}


@pytest.mark.skipif(os.name == "nt", reason="POSIX system directory policy")
def test_var_tmp_child_workspaces_do_not_unlock_system_state_or_credentials():
    assert not procedural._sensitive(Path("/var/tmp/project/report.txt"))
    for path in ("/var", "/var/tmp", "/var/lib/project", "/var/tmp/project/.ssh/key", "/var/tmp/project/.aws/config"):
        assert procedural._sensitive(Path(path))


def _link(link, target, *, directory=False):
    try:
        link.symlink_to(target, target_is_directory=directory)
    except (OSError, NotImplementedError) as exc:
        pytest.skip(f"local symlink creation unavailable: {exc}")


def test_roundtrip_is_detached_and_constructor_is_validated():
    payload = _payload()
    spec = ProcedureSpec.from_dict(payload)
    assert spec.to_dict() == payload
    assert ProcedureSpec.from_dict(spec.to_dict()) == spec
    payload["steps"].append("Changed outside")
    payload["postconditions"][0]["path"] = "outside.txt"
    detached = spec.to_dict()
    detached["steps"].clear()
    detached["postconditions"][0]["path"] = "different.txt"
    detached["environment"]["os"] = "different"
    assert spec.to_dict() == _payload()
    with pytest.raises(ProcedureValidationError):
        ProcedureSpec(goal="g", steps=[], postconditions=[])


def test_optional_fields_default_to_empty_containers():
    spec = ProcedureSpec.from_dict({
        "goal": "g", "steps": ["s"],
        "postconditions": [{"type": "path_exists", "path": "x"}],
    })
    assert spec.failure_guards == []
    assert spec.parameters == {}
    assert spec.environment == {}


@pytest.mark.parametrize("payload", [None, False, 1, "text", [], (), object()])
def test_non_dictionary_input_is_a_clean_validation_error(payload):
    with pytest.raises(ProcedureValidationError):
        ProcedureSpec.from_dict(payload)


@pytest.mark.parametrize("field", ["goal", "steps", "postconditions"])
def test_required_fields_cannot_be_omitted(field):
    payload = _payload()
    del payload[field]
    with pytest.raises(ProcedureValidationError):
        ProcedureSpec.from_dict(payload)


@pytest.mark.parametrize("field,value", [
    ("goal", None), ("goal", 1), ("goal", " "), ("goal", "x" * 1025),
    ("steps", "not a list"), ("steps", ("s",)), ("steps", []),
    ("steps", ["s"] * 21), ("steps", [False]), ("steps", ["s" * 4097]),
    ("failure_guards", {}), ("failure_guards", ["g"] * 21),
    ("failure_guards", [None]), ("parameters", []), ("parameters", {"x": 1}),
    ("parameters", {f"p{i}": "v" for i in range(33)}),
    ("parameters", {"x": "v" * 4097}), ("environment", []),
    ("environment", {"os": 1}), ("environment", {1: "x"}),
    ("environment", {"k" * 129: "x"}),
    ("environment", {f"k{i}": "v" for i in range(33)}),
    ("postconditions", None), ("postconditions", []),
    ("postconditions", [None]), ("postconditions", [False]),
    ("postconditions", [{"type": "path_exists", "path": "x"}] * 17),
    ("postconditions", [{"type": [], "path": "x"}]),
    ("postconditions", [{"type": "shell", "path": "x"}]),
    ("postconditions", [{"type": "path_exists"}]),
    ("postconditions", [{"type": "file_contains", "path": "x"}]),
    ("postconditions", [{"type": "path_exists", "path": "x", "value": "v"}]),
    ("postconditions", [_content(value="")]),
    ("postconditions", [_content(value=False)]),
    ("postconditions", [_content(value="v" * 4097)]),
    ("postconditions", [{"type": "path_exists", "path": "x" * 1025}]),
])
def test_strict_field_shapes_and_bounds(field, value):
    with pytest.raises(ProcedureValidationError):
        ProcedureSpec.from_dict(_payload(**{field: value}))


@pytest.mark.parametrize("name", ["1name", "a-b", "a.b", "", "na\u00efve", "\u540d", "x" * 65, 1])
def test_parameter_names_are_ascii_identifiers(name):
    with pytest.raises(ProcedureValidationError):
        ProcedureSpec.from_dict(_payload(parameters={name: "v"}))


@pytest.mark.parametrize("extra", ["verified", "overall", "shell", "command"])
def test_unknown_top_level_fields_cannot_assert_success(extra):
    with pytest.raises(ProcedureValidationError):
        ProcedureSpec.from_dict(_payload(**{extra: True}))


def test_unknown_postcondition_fields_cannot_assert_success():
    payload = _payload()
    payload["postconditions"][0]["verified"] = True
    with pytest.raises(ProcedureValidationError):
        ProcedureSpec.from_dict(payload)


@pytest.mark.parametrize("updates", [
    {"goal": "$unknown"}, {"steps": ["${unknown}"]},
    {"failure_guards": ["$unknown"]},
    {"postconditions": [{"type": "path_exists", "path": "$unknown"}]},
    {"postconditions": [_content(value="$unknown")]},
    {"steps": ["${broken"]}, {"steps": ["$9"]}, {"steps": ["trailing$"]},
])
def test_unknown_and_malformed_placeholders_are_rejected(updates):
    with pytest.raises(ProcedureValidationError):
        ProcedureSpec.from_dict(_payload(**updates))


def test_valid_maximum_counts_and_string_bounds():
    spec = ProcedureSpec.from_dict(_payload(
        goal="g" * 1024, steps=["s" * 4096] * 20,
        failure_guards=["g"] * 20,
        parameters={f"p{i}": "" for i in range(32)},
        environment={f"e{i}": "v" for i in range(32)},
        postconditions=[_content(value="v" * 4096)] * 16,
    ))
    assert len(spec.to_dict()["postconditions"]) == 16


def test_substitution_is_single_pass_and_never_executes(tmp_path, monkeypatch):
    def forbidden(*args, **kwargs):
        raise AssertionError("procedure suggestions must never execute")

    monkeypatch.setattr(subprocess, "run", forbidden)
    monkeypatch.setattr(subprocess, "Popen", forbidden)
    monkeypatch.setattr(os, "system", forbidden)
    injected = "$(touch executed); __import__('os').system('bad')"
    spec = ProcedureSpec.from_dict(_payload(
        goal="Prepare $name", steps=["echo ${name}; $$HOME; $required"],
        failure_guards=["Keep $name literal"],
        parameters={"name": injected, "required": None},
    ))
    (tmp_path / "artifact.txt").write_text("ready", encoding="utf-8")
    result = verify_postconditions(spec, tmp_path, {"required": "literal $not_declared"})
    assert result["overall"] == "pass"
    assert result["rendered_steps"] == [f"echo {injected}; $HOME; literal $not_declared"]
    assert spec.render({"required": "x"})["failure_guards"] == [f"Keep {injected} literal"]
    assert not (tmp_path / "executed").exists()


@pytest.mark.parametrize("arguments", [None, [], {"unknown": "v"}, {"required": None}, {"required": 1}])
def test_invalid_or_missing_arguments_are_rejected(arguments):
    spec = ProcedureSpec.from_dict(_payload(parameters={"required": None}))
    with pytest.raises(ProcedureValidationError):
        spec.render(arguments)


def test_empty_defaults_and_explicit_overrides_are_supported():
    spec = ProcedureSpec.from_dict(_payload(steps=["value=$x"], parameters={"x": ""}))
    assert spec.render_steps() == ["value="]
    assert spec.render_steps({"x": "override"}) == ["value=override"]


def test_render_expansion_remains_bounded():
    spec = ProcedureSpec.from_dict(_payload(steps=["$x$x"], parameters={"x": "v" * 4096}))
    with pytest.raises(ProcedureValidationError):
        spec.render_steps()


def test_nested_mutation_is_revalidated_before_verification(tmp_path):
    spec = ProcedureSpec.from_dict(_payload())
    spec.postconditions.clear()
    with pytest.raises(ProcedureValidationError):
        verify_postconditions(spec, tmp_path)


def test_all_checks_use_actual_local_evidence_and_rendered_values(tmp_path):
    (tmp_path / "artifact.txt").write_text("ready: accepted\n", encoding="utf-8")
    spec = ProcedureSpec.from_dict(_payload(
        steps=["Inspect $filename"], parameters={"filename": "artifact.txt", "needle": "accepted"},
        postconditions=[{"type": "path_exists", "path": "$filename"},
                        _content(path="${filename}", value="$needle"),
                        _content("file_not_contains", "rejected", "$filename")],
    ))
    result = verify_postconditions(spec, tmp_path)
    assert result["overall"] == "pass"
    assert [check["status"] for check in result["checks"]] == ["pass"] * 3
    assert result["rendered_steps"] == ["Inspect artifact.txt"]
    assert all(check["path"] == "artifact.txt" for check in result["checks"])
    (tmp_path / "artifact.txt").write_text("rejected", encoding="utf-8")
    assert verify_postconditions(spec, tmp_path)["overall"] == "fail"


def test_plain_dictionary_spec_is_accepted(tmp_path):
    (tmp_path / "artifact.txt").touch()
    assert verify_postconditions(_payload(), tmp_path)["overall"] == "pass"


@pytest.mark.parametrize("kind", ["path_exists", "file_contains", "file_not_contains"])
def test_missing_file_is_not_verified(kind, tmp_path):
    condition = {"type": "path_exists", "path": "artifact.txt"} if kind == "path_exists" else _content(kind)
    result = verify_postconditions(_payload(postconditions=[condition]), tmp_path)
    assert result["overall"] == "fail"
    assert result["checks"][0]["reason"] == "missing_path"


def test_directories_only_satisfy_existence(tmp_path):
    (tmp_path / "artifact.txt").mkdir()
    assert verify_postconditions(_payload(), tmp_path)["overall"] == "pass"
    assert verify_postconditions(_payload(postconditions=[_content()]), tmp_path)["overall"] == "unknown"


def test_oversized_file_is_unknown_and_not_opened(tmp_path, monkeypatch):
    (tmp_path / "artifact.txt").write_bytes(b"x" * (MAX_FILE_BYTES + 1))
    result = verify_postconditions(_payload(postconditions=[_content()]), tmp_path)
    assert result["overall"] == "unknown"
    assert result["checks"][0]["reason"] == "file_too_large"


def test_exact_read_limit_is_supported(tmp_path):
    (tmp_path / "artifact.txt").write_bytes(b"x" * (MAX_FILE_BYTES - 5) + b"ready")
    assert verify_postconditions(_payload(postconditions=[_content()]), tmp_path)["overall"] == "pass"


def test_invalid_utf8_is_unknown_not_an_absence_proof(tmp_path):
    (tmp_path / "artifact.txt").write_bytes(b"\xff\xfe")
    result = verify_postconditions(_payload(postconditions=[_content("file_not_contains")]), tmp_path)
    assert result["overall"] == "unknown"
    assert result["checks"][0]["reason"] == "not_utf8_text"


def test_permission_failure_is_unknown(tmp_path, monkeypatch):
    (tmp_path / "artifact.txt").write_text("ready", encoding="utf-8")

    def denied(*args, **kwargs):
        raise PermissionError("denied")

    monkeypatch.setattr(procedural, "_open_local", denied)
    assert verify_postconditions(_payload(postconditions=[_content()]), tmp_path)["overall"] == "unknown"


def test_concurrent_content_change_is_unknown(tmp_path, monkeypatch):
    (tmp_path / "artifact.txt").write_text("ready", encoding="utf-8")
    original = os.fstat
    calls = 0

    def changing(fd):
        nonlocal calls
        info = original(fd)
        calls += 1
        if calls == 2:
            return SimpleNamespace(st_mode=info.st_mode, st_size=info.st_size,
                                   st_mtime_ns=info.st_mtime_ns + 1, st_ctime_ns=info.st_ctime_ns)
        return info

    monkeypatch.setattr(os, "fstat", changing)
    result = verify_postconditions(_payload(postconditions=[_content()]), tmp_path)
    assert result["overall"] == "unknown"
    assert result["checks"][0]["reason"] == "file_changed_during_read"


def test_aggregate_fail_dominates_unknown_and_unknown_dominates_pass(tmp_path):
    (tmp_path / "artifact.txt").write_text("ready", encoding="utf-8")
    (tmp_path / "binary.txt").write_bytes(b"\xff")
    conditions = [_content(), _content(path="binary.txt")]
    result = verify_postconditions(_payload(postconditions=conditions), tmp_path)
    assert [check["status"] for check in result["checks"]] == ["pass", "unknown"]
    assert result["overall"] == "unknown"
    conditions.append(_content(path="missing.txt"))
    assert verify_postconditions(_payload(postconditions=conditions), tmp_path)["overall"] == "fail"


@pytest.mark.parametrize("path", [
    "../x", "a/../x", "a\\..\\x", "/etc/passwd", "C:/Windows/win.ini",
    "C:relative", "\\rooted", "\\\\server\\share\\x", "//server/share/x",
    "\\\\?\\C:\\Windows\\x", "x:stream", ".", "a//x", "a/./x", "x/",
    "x\x00", "x\n", "NUL", "con.txt", "COM1", "LPT1.txt", "x.", "x ",
    ".ssh/id_rsa", "dir/.AWS/credentials", ".gnupg/key", "dir/*", "dir/?",
])
def test_unsafe_schema_paths_are_rejected_without_reading(path):
    with pytest.raises(ProcedureValidationError):
        ProcedureSpec.from_dict(_payload(postconditions=[{"type": "path_exists", "path": path}]))


@pytest.mark.parametrize("path", ["../outside.txt", "/etc/passwd", "C:\\Windows\\x", ".ssh/key", "NUL", "x:stream"])
def test_argument_path_escape_fails_without_opening_files(path, tmp_path, monkeypatch):
    spec = ProcedureSpec.from_dict(_payload(
        parameters={"target": None}, postconditions=[_content(path="$target")],
    ))

    def forbidden(*args, **kwargs):
        raise AssertionError("unsafe path must never be opened")

    monkeypatch.setattr(procedural, "_open_local", forbidden)
    result = verify_postconditions(spec, tmp_path, {"target": path})
    assert result["overall"] == "fail"
    assert result["checks"][0]["reason"] == "unsafe_path"


@pytest.mark.parametrize("workspace", [None, "", ".", "relative", 1, b"path", "bad\x00path", "\\\\server\\share"])
def test_workspace_is_explicit_absolute_and_local(workspace):
    with pytest.raises(ProcedureValidationError):
        verify_postconditions(_payload(), workspace)


def test_workspace_cannot_be_missing_file_root_or_sensitive(tmp_path, monkeypatch):
    (tmp_path / "file").touch()
    sensitive = Path(tmp_path.anchor) / ("Windows" if os.name == "nt" else "etc")

    def forbidden(path):
        raise AssertionError("sensitive workspace must be rejected before inspection")

    with pytest.raises(ProcedureValidationError):
        verify_postconditions(_payload(), tmp_path / "missing")
    with pytest.raises(ProcedureValidationError):
        verify_postconditions(_payload(), tmp_path / "file")
    monkeypatch.setattr(procedural, "_no_links", forbidden)
    for root in (Path(tmp_path.anchor), sensitive):
        with pytest.raises(ProcedureValidationError):
            verify_postconditions(_payload(), root)


def test_outside_symlink_cannot_supply_content_proof(tmp_path, monkeypatch):
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    outside = tmp_path / "outside.txt"
    outside.write_text("ready", encoding="utf-8")
    _link(workspace / "artifact.txt", outside)

    def forbidden(*args, **kwargs):
        raise AssertionError("symlink evidence must never be opened")

    monkeypatch.setattr(procedural, "_open_local", forbidden)
    result = verify_postconditions(_payload(postconditions=[_content()]), workspace)
    assert result["overall"] == "fail"
    assert result["checks"][0]["reason"] == "unsafe_path"


def test_parent_directory_symlinks_and_linked_workspaces_are_rejected(tmp_path):
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / "file.txt").write_text("ready", encoding="utf-8")
    _link(workspace / "linked", outside, directory=True)
    result = verify_postconditions(_payload(postconditions=[_content(path="linked/file.txt")]), workspace)
    assert result["overall"] == "fail"
    with pytest.raises(ProcedureValidationError):
        verify_postconditions(_payload(), workspace / "linked")


def test_internal_symlinks_are_conservatively_rejected(tmp_path):
    (tmp_path / "real.txt").write_text("ready", encoding="utf-8")
    _link(tmp_path / "artifact.txt", tmp_path / "real.txt")
    assert verify_postconditions(_payload(), tmp_path)["overall"] == "fail"


def test_windows_reparse_points_are_classified_as_links():
    assert procedural._is_link(SimpleNamespace(st_mode=stat.S_IFDIR, st_file_attributes=0x400))
    assert not procedural._is_link(SimpleNamespace(st_mode=stat.S_IFREG, st_file_attributes=0))


@pytest.mark.skipif(os.name == "nt", reason="POSIX FIFO contract")
def test_fifo_is_unknown_and_never_blocks(tmp_path):
    os.mkfifo(tmp_path / "artifact.txt")
    assert verify_postconditions(_payload(postconditions=[_content()]), tmp_path)["overall"] == "unknown"
    assert verify_postconditions(_payload(), tmp_path)["overall"] == "unknown"


def test_environment_check_is_pure_partial_and_not_host_attestation(monkeypatch):
    def forbidden(*args, **kwargs):
        raise AssertionError("pure environment comparison must not probe host")

    monkeypatch.setattr(subprocess, "run", forbidden)
    expected = {"os": "linux", "python": "3.11", "literal": "$fingerprint"}
    saved = deepcopy(expected)
    absent = check_environment(expected)
    assert absent["overall"] == "unknown"
    assert all(check["status"] == "unknown" for check in absent["checks"])
    partial = check_environment(expected, {"os": "linux"})
    assert [check["status"] for check in partial["checks"]] == ["pass", "unknown", "unknown"]
    assert partial["overall"] == "unknown"
    assert check_environment(expected, dict(expected, unrelated="unverified"))["overall"] == "pass"
    assert check_environment(expected, {"os": "windows"})["overall"] == "fail"
    assert check_environment({})["overall"] == "unknown"
    assert expected == saved
    assert ProcedureSpec.from_dict(_payload(environment=expected)).environment == expected


@pytest.mark.parametrize("expected,actual", [(None, {}), ({}, []), ({"os": 1}, {}), ({}, {"os": False})])
def test_invalid_environment_dictionaries_are_rejected(expected, actual):
    with pytest.raises(ProcedureValidationError):
        check_environment(expected, actual)


def test_postcondition_pass_does_not_claim_environment_pass(tmp_path):
    (tmp_path / "artifact.txt").touch()
    spec = ProcedureSpec.from_dict(_payload(environment={"os": "unmeasured-host"}))
    assert verify_postconditions(spec, tmp_path)["overall"] == "pass"
    assert check_environment(spec.environment)["overall"] == "unknown"


def test_path_exists_inspects_opened_file_and_directory_not_target_stat(tmp_path, monkeypatch):
    file = tmp_path / "artifact.txt"
    directory = tmp_path / "directory"
    file.touch()
    directory.mkdir()
    original_stat = Path.stat

    def no_following_stat(path, *args, **kwargs):
        if path in {file, directory} and kwargs.get("follow_symlinks", True):
            raise AssertionError("path_exists must inspect its pinned object, not target.stat")
        return original_stat(path, *args, **kwargs)

    monkeypatch.setattr(Path, "stat", no_following_stat)
    result = verify_postconditions(_payload(postconditions=[
        {"type": "path_exists", "path": "artifact.txt"},
        {"type": "path_exists", "path": "directory"},
    ]), tmp_path)
    assert result["overall"] == "pass"


@pytest.mark.skipif(os.name != "nt", reason="Windows native handle validation")
@pytest.mark.parametrize("directory", [False, True])
def test_path_exists_rejects_opened_handle_outside_workspace(tmp_path, monkeypatch, directory):
    target = tmp_path / "artifact.txt"
    target.mkdir() if directory else target.touch()
    monkeypatch.setattr(procedural, "_windows_handle_path", lambda handle: tmp_path.parent / "outside")
    result = verify_postconditions(_payload(), tmp_path)
    assert result["overall"] == "fail" and result["checks"][0]["reason"] == "unsafe_path"


@pytest.mark.skipif(os.name == "nt", reason="POSIX no-follow descriptor race")
def test_path_exists_rejects_link_swap_after_preflight(tmp_path, monkeypatch):
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    target = workspace / "artifact.txt"
    target.touch()
    outside = tmp_path / "outside.txt"
    outside.touch()
    original_open = procedural._open_local

    def swapped(root, parts):
        target.unlink()
        target.symlink_to(outside)
        return original_open(root, parts)

    monkeypatch.setattr(procedural, "_open_local", swapped)
    result = verify_postconditions(_payload(), workspace)
    assert result["overall"] == "fail" and result["checks"][0]["reason"] == "unsafe_path"
