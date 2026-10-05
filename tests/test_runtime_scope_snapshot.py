import copy
import io
import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from memguard.integrations import openhands_memory_hook as hook
from memguard.integrations.openhands_runner import OpenHandsMemoryRunner
from memguard.models import MemorySource, make_memory
from memguard.store.sqlite_meta import SQLiteMetaStore


@pytest.fixture
def runtime(tmp_path):
    meta = SQLiteMetaStore(tmp_path / "memory.sqlite3")
    scope = {
        "namespace": "default",
        "project_root": str((tmp_path / "project").resolve()),
        "session_id": "session-a",
        "agent_name": "openhands",
        "task_type": "code_assistance",
    }
    try:
        yield meta, scope
    finally:
        meta.close()


def add_memory(meta, *, preference=False, **kwargs):
    record = make_memory(
        "Use rg for code search." if preference else "The deployment uses systemd.",
        "preference" if preference else "knowledge",
        MemorySource(kind="manual"),
        subtype="tool_preference" if preference else "fact",
        preference_topic="code_search_tool" if preference else "",
        preference_value="rg" if preference else "",
        **kwargs,
    )
    meta.upsert_memory(record)
    return record


def payload_for(meta, scope, records=()):
    return {
        **scope,
        "trace_id": "ctx-test",
        "stages": {"tool_selection": {
            **{key: value for key, value in scope.items() if key != "namespace"},
            "context": "selected memory" if records else "",
            "memories": [{
                "memory_id": record.memory_id,
                "version_id": record.version.version_id,
                "preference_topic": record.preference_topic,
                "preference_value": record.preference_value,
            } for record in records],
        }},
        "memory_snapshot": {
            "memory_enabled": True,
            "versions": {record.memory_id: record.version.version_id for record in records},
            "db_path": str(meta.path.resolve()),
            "namespace": scope["namespace"],
            "use_scope": dict(scope),
            "active_preference_signature": hook.runtime_preference_signature(meta.path, scope),
        },
    }


@pytest.mark.parametrize("kind", ["global", "project", "retention", "session", "agent", "task_type"])
def test_matching_scope_is_valid(runtime, kind):
    meta, scope = runtime
    kwargs = {
        "global": {},
        "project": {"project_root": scope["project_root"]},
        "retention": {"retention_session_id": scope["session_id"], "tier": "session"},
        "session": {"preference_scope": "session", "preference_session_id": scope["session_id"]},
        "agent": {"preference_scope": "agent", "agent_name": scope["agent_name"]},
        "task_type": {"preference_scope": "task_type", "task_type": scope["task_type"]},
    }[kind]
    record = add_memory(meta, preference=kind in {"session", "agent", "task_type"}, **kwargs)
    assert hook.context_snapshot_status(payload_for(meta, scope, [record]))["ok"]


@pytest.mark.parametrize("kind", ["project", "retention", "session", "agent", "task_type", "namespace"])
def test_foreign_selected_memory_is_rejected_even_with_current_version(runtime, kind):
    meta, scope = runtime
    kwargs = {
        "project": {"project_root": str(Path(scope["project_root"]).parent / "foreign")},
        "retention": {"retention_session_id": "session-b", "tier": "session"},
        "session": {"preference_scope": "session", "preference_session_id": "session-b"},
        "agent": {"preference_scope": "agent", "agent_name": "other-agent"},
        "task_type": {"preference_scope": "task_type", "task_type": "other-task"},
        "namespace": {"scope": "foreign"},
    }[kind]
    record = add_memory(meta, preference=kind in {"session", "agent", "task_type"}, **kwargs)
    status = hook.context_snapshot_status(payload_for(meta, scope, [record]))
    assert status == {"ok": False, "reason": "memory_changed", "stale_memory_ids": [record.memory_id]}


@pytest.mark.parametrize("field", ["namespace", "session_id", "project_root", "agent_name", "task_type"])
def test_payload_scope_must_match_snapshot(runtime, field):
    meta, scope = runtime
    payload = payload_for(meta, scope)
    payload[field] = "foreign"
    assert hook.context_snapshot_status(payload)["reason"] == "scope_mismatch"


def test_stage_scope_must_match_snapshot(runtime):
    meta, scope = runtime
    payload = payload_for(meta, scope)
    payload["stages"]["tool_selection"]["session_id"] = "session-b"
    assert hook.context_snapshot_status(payload)["reason"] == "scope_mismatch"


@pytest.mark.parametrize("column,value", [
    ("project_root", "foreign"), ("retention_session_id", "session-b"),
    ("deleted", 1), ("governance_state", "quarantined"),
    ("trust_level", "untrusted"), ("valid_to", "2026-01-01"),
    ("version_id", "new-version"),
])
def test_selected_scope_and_state_changes_are_checked_without_revision(runtime, column, value):
    meta, scope = runtime
    record = add_memory(meta)
    payload = payload_for(meta, scope, [record])
    # Fixed test-only column names; no real store or timestamp manipulation.
    meta.conn.execute(f"UPDATE memory SET {column} = ? WHERE memory_id = ?", (value, record.memory_id))
    meta.conn.commit()
    status = hook.context_snapshot_status(payload)
    assert not status["ok"]
    assert status["stale_memory_ids"] == [record.memory_id]


@pytest.mark.parametrize("has_old_preference", [False, True])
def test_new_higher_priority_preference_invalidates_even_empty_context(runtime, has_old_preference):
    meta, scope = runtime
    old = add_memory(meta, preference=True) if has_old_preference else None
    payload = payload_for(meta, scope, [old] if old else [])
    add_memory(meta, preference=True, preference_scope="session", preference_session_id=scope["session_id"])
    if old:
        assert meta.get_memory(old.memory_id)["version_id"] == old.version.version_id
    assert hook.context_snapshot_status(payload) == {
        "ok": False, "reason": "preferences_changed", "stale_memory_ids": [],
    }


@pytest.mark.parametrize("column,value", [
    ("deleted", 1), ("governance_state", "quarantined"),
    ("trust_level", "untrusted"), ("valid_to", "2026-01-01"),
    ("preference_explicit", 0), ("preference_value", "grep"),
    ("preference_scope", "agent"),
])
def test_unselected_applicable_candidate_changes_invalidate_signature(runtime, column, value):
    meta, scope = runtime
    record = add_memory(meta, preference=True, agent_name="openhands")
    payload = payload_for(meta, scope)
    meta.conn.execute(f"UPDATE memory SET {column} = ? WHERE memory_id = ?", (value, record.memory_id))
    meta.conn.commit()
    assert meta.get_memory(record.memory_id)["version_id"] == record.version.version_id
    assert hook.context_snapshot_status(payload)["reason"] == "preferences_changed"


def test_new_priority_evidence_invalidates_signature(runtime):
    from memguard.integrations.events import AgentInteractionEvent

    meta, scope = runtime
    record = add_memory(meta, preference=True)
    payload = payload_for(meta, scope)
    event = AgentInteractionEvent(
        event_id="event-priority", agent_name="openhands", agent_version="test", session_id="session-a",
        turn_id="turn-a", sequence=1, event_type="user_message", role="user", content="Use rg.",
    )
    meta.insert_agent_event(event)
    meta.link_memory_event(record.memory_id, event.event_id, "confirmed_by")
    assert hook.context_snapshot_status(payload)["reason"] == "preferences_changed"


def test_preference_signature_uses_one_bulk_evidence_query(runtime):
    meta, scope = runtime
    for _ in range(12):
        add_memory(meta, preference=True)
    statements = []
    meta.conn.set_trace_callback(statements.append)
    try:
        signature = hook._active_preference_signature(meta.conn, scope)
    finally:
        meta.conn.set_trace_callback(None)
    queries = [statement for statement in statements if statement.lstrip().upper().startswith("SELECT")]
    assert len(signature) == 64
    assert len(queries) == 2
    assert sum("memory_event_links" in query for query in queries) == 1


def test_foreign_preferences_and_non_preference_inserts_do_not_invalidate(runtime):
    meta, scope = runtime
    record = add_memory(meta, preference=True)
    payload = payload_for(meta, scope, [record])
    for kwargs in [
        {"scope": "foreign"},
        {"project_root": str(Path(scope["project_root"]).parent / "foreign")},
        {"retention_session_id": "session-b"},
        {"preference_scope": "session", "preference_session_id": "session-b"},
        {"preference_scope": "agent", "agent_name": "other"},
        {"preference_scope": "task_type", "task_type": "other"},
    ]:
        add_memory(meta, preference=True, **kwargs)
    add_memory(meta)
    assert hook.context_snapshot_status(payload)["ok"]


def test_ineligible_candidate_becoming_active_invalidates(runtime):
    meta, scope = runtime
    record = add_memory(meta, preference=True, governance_state="quarantined")
    payload = payload_for(meta, scope)
    meta.conn.execute("UPDATE memory SET governance_state = 'active' WHERE memory_id = ?", (record.memory_id,))
    meta.conn.commit()
    assert hook.context_snapshot_status(payload)["reason"] == "preferences_changed"


@pytest.mark.parametrize("defect", ["no_scope", "no_signature", "bad_signature", "wrong_namespace", "missing_store"])
def test_incomplete_snapshot_fails_closed_even_without_selected_memory(runtime, defect):
    meta, scope = runtime
    payload = payload_for(meta, scope)
    snapshot = payload["memory_snapshot"]
    if defect == "no_scope":
        snapshot.pop("use_scope")
    elif defect == "no_signature":
        snapshot.pop("active_preference_signature")
    elif defect == "bad_signature":
        snapshot["active_preference_signature"] = "invalid"
    elif defect == "wrong_namespace":
        snapshot["namespace"] = "foreign"
    else:
        missing = meta.path.parent / "missing.sqlite3"
        snapshot["db_path"] = str(missing)
    assert not hook.context_snapshot_status(payload)["ok"]
    if defect == "missing_store":
        assert not missing.exists()


@pytest.mark.parametrize("payload", [
    None,
    {"stages": {"planning": {"memories": {}}}},
    {"stages": {"planning": {"memories": [{}]}}},
    {"stages": {"planning": {}}, "memory_snapshot": []},
    {"stages": {"planning": {"memories": [{"memory_id": "m", "version_id": "v"}]}}},
])
def test_malformed_memory_payloads_fail_closed(payload):
    assert not hook.context_snapshot_status(payload)["ok"]


def make_runner(tmp_path, meta, records=(), **kwargs):
    def build_context(**request):
        return {
            **{key: request[key] for key in ("agent_name", "session_id", "project_root", "task_type", "stage")},
            "context": "temporary test memory" if records else "",
            "memories": [{"memory_id": record.memory_id, "version_id": record.version.version_id} for record in records],
        }

    return OpenHandsMemoryRunner(
        SimpleNamespace(namespace="default", meta=meta, build_agent_context=build_context),
        "session-a", tmp_path / "project", tmp_path / "state",
        conversation_factory=lambda **_kwargs: None, **kwargs,
    )


def test_runner_persists_verifiable_scope_and_returns_same_metadata(runtime, tmp_path):
    meta, scope = runtime
    record = add_memory(meta, project_root=scope["project_root"])
    runner = make_runner(tmp_path, meta, [record])
    bundle = runner.prepare_context("Inspect systemd deployment")
    persisted = json.loads(Path(bundle["context_file"]).read_text(encoding="utf-8"))
    assert bundle["memory_snapshot"]["use_scope"] == scope
    assert persisted["memory_snapshot"] == bundle["memory_snapshot"]
    assert hook.context_snapshot_status(bundle)["ok"]
    assert hook.context_snapshot_status(persisted)["ok"]
    runner.session_id = "session-b"
    with pytest.raises(RuntimeError, match="scope"):
        runner._require_fresh_context(bundle)


def test_runner_rejects_preference_change_during_context_build(runtime, tmp_path):
    meta, _scope = runtime
    runner = make_runner(tmp_path, meta)
    original = runner.service.build_agent_context

    def changed_context(**request):
        if request["stage"] == "tool_selection":
            add_memory(meta, preference=True)
        return original(**request)

    runner.service.build_agent_context = changed_context
    with pytest.raises(RuntimeError):
        runner.prepare_context("Inspect systemd deployment")
    assert not list((tmp_path / "state").glob("*.json"))


def test_runner_rejects_missing_store_before_context_build(tmp_path):
    missing = tmp_path / "missing.sqlite3"
    runner = make_runner(tmp_path, SimpleNamespace(path=missing))
    with pytest.raises(RuntimeError, match="verified"):
        runner.prepare_context("Inspect deployment")
    assert not missing.exists()


def test_disabled_memory_and_plain_non_memory_payloads_never_open_store(tmp_path, monkeypatch):
    def unexpected(*_args, **_kwargs):
        pytest.fail("non-memory path accessed SQLite")

    monkeypatch.setattr(hook.sqlite3, "connect", unexpected)
    runner = OpenHandsMemoryRunner(
        object(), "session-a", tmp_path / "project", tmp_path / "state",
        memory_enabled=False, conversation_factory=lambda **_kwargs: None,
    )
    bundle = runner.prepare_context("Inspect deployment")
    assert bundle["memory_snapshot"] == {"versions": {}, "memory_enabled": False}
    assert hook.context_snapshot_status(bundle)["ok"]
    for payload in [{}, {"additional_context": "ordinary context"},
                    {"stages": {"planning": {"memories": []}}, "memory_snapshot": {"versions": {}}}]:
        assert hook.context_snapshot_status(payload)["ok"]
    disabled = copy.deepcopy(bundle)
    disabled["stages"]["planning"]["context"] = "unexpected memory"
    assert not hook.context_snapshot_status(disabled)["ok"]


@pytest.mark.parametrize("guard_tool", [False, True])
def test_existing_prompt_and_pre_tool_checkpoints_allow_fresh_then_deny_stale(runtime, tmp_path, monkeypatch, capsys, guard_tool):
    meta, _scope = runtime
    bundle = make_runner(tmp_path, meta).prepare_context("Inspect deployment")
    argv = ["memory-hook", "--context-file", bundle["context_file"]]
    if guard_tool:
        argv.append("--guard-tool")
    monkeypatch.setattr(hook.sys, "argv", argv)
    monkeypatch.setattr(hook.sys, "stdin", io.StringIO(json.dumps({
        "tool_name": "terminal", "tool_input": {"command": "true"},
    })))
    assert hook.main() == 0
    assert json.loads(capsys.readouterr().out)["decision"] == "allow"
    add_memory(meta, preference=True)
    assert hook.main() == 2
    denied = json.loads(capsys.readouterr().out)
    assert denied["continue"] is False
    assert denied["validation_reason"] == "preferences_changed"


@pytest.mark.parametrize("guard_tool", [False, True])
def test_forgotten_memory_denies_existing_checkpoints_and_fresh_context_recovers(tmp_path, monkeypatch, capsys, guard_tool):
    from memguard.service import MemGuardService

    service = MemGuardService(tmp_path / "memory.segment", tmp_path / "memory.sqlite3")
    try:
        created = service.ingest_text("The deployment uses systemd.", source_kind="manual")
        assert created["stored"]
        record = created["record"]
        runner = make_runner(tmp_path, service.meta, [record])
        bundle = runner.prepare_context("Inspect deployment")
        assert hook.context_snapshot_status(bundle)["ok"]
        service.forget_memory_ids([record.memory_id], reason="runtime test withdrawal", compact=False)
        status = hook.context_snapshot_status(bundle)
        assert not status["ok"]
        assert status["stale_memory_ids"] == [record.memory_id]
        argv = ["memory-hook", "--context-file", bundle["context_file"]]
        if guard_tool:
            argv.append("--guard-tool")
        monkeypatch.setattr(hook.sys, "argv", argv)
        monkeypatch.setattr(hook.sys, "stdin", io.StringIO("{}"))
        assert hook.main() == 2
        denied = json.loads(capsys.readouterr().out)
        assert denied["policy_category"] == "stale_memory_context"
        assert denied["affected_memory_ids"] == [record.memory_id]
        with pytest.raises(RuntimeError):
            runner._require_fresh_context(bundle)
        refreshed = make_runner(tmp_path, service.meta).prepare_context("Inspect deployment")
        assert hook.context_snapshot_status(refreshed)["ok"]
        assert refreshed["memory_snapshot"]["versions"] == {}
    finally:
        service.close()
