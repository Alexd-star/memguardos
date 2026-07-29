import hashlib
import json
from pathlib import Path

from memguard.cli.memguard import (
    _compare_live,
    _forget,
    _friendly_agent_error,
    _memory_mode,
    _remember,
    _user_setting,
)
from memguard.integrations.events import AgentInteractionEvent
from memguard.integrations.openhands_runner import (
    OpenHandsMemoryRunner,
    _execution_trace,
    _runtime_preference_effect_proofs,
    evaluate_task_completion,
    task_requires_terminal_evidence,
)
from memguard.service import MemGuardService
from scripts.run_openhands_memguard_demo import build_parser as build_agent_demo_parser
from scripts.verify_video_demo import _source_dirty, _source_revision


class _NoMemoryService:
    namespace = "demo-test"

    def build_agent_context(self, **kwargs):
        raise AssertionError("memory-off run must not build memory context")

    def ingest_interaction(self, event):
        raise AssertionError("memory-off run must not persist interaction events")


class _ForgetService:
    def forget(self, request, confirm=False, compact=False):
        assert request == "忘掉代码搜索工具偏好"
        assert confirm is True
        assert compact is True
        return {
            "forgotten": ["mem-tool"],
            "verification_report": {
                "verification_steps": [
                    {"name": "检索结果复核", "status": "passed"},
                    {"name": "审计链复核", "status": "passed"},
                ],
                "user_summary": "目标记忆已从受管检索链路移除。",
            },
        }


def test_one_shot_agent_demo_accepts_documented_iteration_limit(tmp_path):
    args = build_agent_demo_parser().parse_args(
        [
            "--session-id",
            "video-session",
            "--workspace",
            str(tmp_path),
            "--max-iterations",
            "6",
            "--dry-init",
        ]
    )

    assert args.workspace == str(tmp_path)
    assert args.max_iterations == 6


def test_sensitive_cli_feedback_is_user_readable(tmp_path, capsys):
    service = MemGuardService(
        store_path=tmp_path / "memory.segment",
        db_path=tmp_path / "memory.sqlite3",
        namespace="sensitive-cli",
    )
    try:
        assert _remember(
            service,
            "请记住测试凭据 OPENAI_API_KEY=sk-test-only-1234567890",
        ) == 2
    finally:
        service.close()

    output = capsys.readouterr().out
    assert "已保护敏感内容" in output
    assert "检测类型" in output
    assert "sensitive_block" not in output


def test_video_preflight_accepts_verified_revision_file(tmp_path, monkeypatch):
    revision = "a" * 40
    (tmp_path / ".source_revision").write_text(revision + "\n", encoding="utf-8")
    monkeypatch.delenv("MEMGUARD_SOURCE_REVISION", raising=False)

    assert _source_revision(tmp_path) == (revision, "revision_file")


def test_video_preflight_requires_hash_manifest_for_gitless_source(tmp_path):
    revision = "a" * 40
    source = tmp_path / "memguard" / "service.py"
    source.parent.mkdir()
    source.write_text("value = 1\n", encoding="utf-8")
    digest = hashlib.sha256(source.read_bytes()).hexdigest()
    (tmp_path / ".source_manifest.json").write_text(
        json.dumps(
            {
                "schema": "memguardos-source-manifest-v1",
                "source_revision": revision,
                "files": [{"path": "memguard/service.py", "sha256": digest}],
            }
        ),
        encoding="utf-8",
    )

    assert _source_dirty(tmp_path, "revision_file", revision) is False
    source.write_text("value = 2\n", encoding="utf-8")
    assert _source_dirty(tmp_path, "revision_file", revision) is True


def _event(event_type, *, sequence=1, tool_args=None, exit_code=None):
    return AgentInteractionEvent(
        agent_name="openhands",
        agent_version="test",
        session_id="session-demo",
        turn_id="turn-1",
        sequence=sequence,
        event_type=event_type,
        role="assistant" if event_type == "tool_call" else "tool",
        tool_name="terminal",
        tool_args=tool_args or {},
        exit_code=exit_code,
    )


def test_memory_mode_is_explicit_and_reversible(capsys):
    state = {"memory_enabled": True}

    assert _memory_mode("/memory off", state) == 0
    assert state["memory_enabled"] is False
    assert "不读取或写入长期记忆" in capsys.readouterr().out

    assert _memory_mode("/memory on", state) == 0
    assert state["memory_enabled"] is True
    assert "已开启" in capsys.readouterr().out


def test_compare_live_runs_memory_off_before_memory_on(monkeypatch, capsys):
    modes = []

    def fake_run(_service, task, state):
        assert task == "检查项目"
        modes.append(state["memory_enabled"])
        return 0

    monkeypatch.setattr("memguard.cli.memguard._run_agent_task", fake_run)
    assert _compare_live(object(), "检查项目", workspace=".", model="test-model") == 0
    assert modes == [False, True]
    output = capsys.readouterr().out
    assert "对照组：关闭记忆" in output
    assert "实验组：开启记忆" in output


def test_memory_off_runner_builds_empty_context_and_does_not_persist(tmp_path):
    workspace = tmp_path / "workspace"
    state_dir = tmp_path / "state"
    workspace.mkdir()
    runner = OpenHandsMemoryRunner(
        service=_NoMemoryService(),
        session_id="session-demo",
        workspace=workspace,
        state_dir=state_dir,
        memory_enabled=False,
    )

    bundle = runner.prepare_context("查找函数定义")
    assert all(not stage["memories"] for stage in bundle["stages"].values())
    runner._ingest_event(
        _event("tool_call", tool_args={"command": "rg -n build_agent_context memguard"})
    )
    assert runner._event_types["tool_call"] == 1
    assert runner._event_results == []


def test_execution_trace_hides_credentials_and_keeps_command_evidence():
    trace = _execution_trace(
        [
            {
                "event_type": "tool_call",
                "tool_name": "terminal",
                "tool_args": {"command": "curl -H 'Authorization: Bearer sk-example-secret-123456' /health"},
            },
            {"event_type": "tool_result", "tool_name": "terminal", "exit_code": 0},
            {
                "event_type": "tool_call",
                "tool_name": "finish",
                "tool_args": {"message": "memguard/service.py:2727"},
            },
            {
                "event_type": "assistant_message",
                "content": "结果位于 memguard/service.py，临时令牌 sk-example-secret-123456 已隐藏。",
            },
        ]
    )

    assert trace[0]["command"].startswith("curl")
    assert "sk-example" not in trace[0]["command"]
    assert "[已隐藏凭据]" in trace[0]["command"]
    assert trace[1]["outcome"] == "success"
    assert trace[2]["message"] == "memguard/service.py:2727"
    assert "sk-example" not in trace[3]["message"]
    assert "[已隐藏凭据]" in trace[3]["message"]


def test_execution_trace_separates_blocked_attempts_from_successful_commands():
    trace = _execution_trace(
        [
            {
                "event_type": "policy_decision",
                "tool_name": "terminal",
                "tool_args": {"decision": "deny", "reason": "必须使用 grep"},
                "exit_code": 2,
            },
            {
                "event_type": "tool_call",
                "tool_name": "terminal",
                "tool_args": {"command": "rg -n target ."},
            },
            {
                "event_type": "policy_decision",
                "tool_name": "terminal",
                "tool_args": {"decision": "allow"},
                "exit_code": 0,
            },
            {
                "event_type": "tool_call",
                "tool_name": "terminal",
                "tool_args": {"command": "grep -R -n target ."},
            },
            {"event_type": "tool_result", "tool_name": "terminal", "exit_code": 0},
        ]
    )

    calls = [item for item in trace if item["event_type"] == "tool_call"]
    assert calls[0]["status"] == "blocked"
    assert calls[0]["policy_reason"] == "必须使用 grep"
    assert calls[1]["status"] == "success"
    assert calls[1]["exit_code"] == 0


def test_task_completion_requires_real_command_and_visible_answer_for_code_task():
    task = "请在当前项目中查找 build_agent_context 的定义位置"
    assert task_requires_terminal_evidence(task) is True

    status_only = [{"event_type": "task_complete", "content": ""}]
    assert evaluate_task_completion(task, status_only)["passed"] is False

    command_only = [
        {
            "event_type": "tool_call",
            "tool_name": "terminal",
            "tool_args": {"command": "rg -n build_agent_context ."},
        },
        {"event_type": "tool_result", "tool_name": "terminal", "exit_code": 0},
    ]
    assert evaluate_task_completion(task, command_only)["passed"] is False

    completed = [
        *command_only,
        {"event_type": "assistant_message", "content": "命令：rg -n build_agent_context ."},
    ]
    assert evaluate_task_completion(task, completed)["passed"] is True


def test_task_completion_accepts_explicit_safe_policy_block():
    result = evaluate_task_completion(
        "删除项目目录",
        [],
        natural_high_risk_block={"blocked": True},
    )

    assert result["passed"] is True
    assert result["mode"] == "safe_policy_block"


def test_code_assistance_task_type_requires_tool_evidence_without_keywords():
    result = evaluate_task_completion(
        "把它处理好",
        [{"event_type": "assistant_message", "content": "已经处理。"}],
        task_type="code_assistance",
    )

    assert result["passed"] is False
    assert result["requires_terminal"] is True


def test_cli_can_explicitly_allow_current_project_workspace(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    runner = OpenHandsMemoryRunner(
        service=_NoMemoryService(),
        session_id="session-demo",
        workspace=Path.cwd(),
        state_dir=tmp_path.parent / "agent-state",
        memory_enabled=False,
        allow_in_place_workspace=True,
    )

    runner._validate_workspace()
    assert "响应超时" in _friendly_agent_error(TimeoutError("request timed out"))


def test_user_setting_prefers_environment_and_reads_local_config(tmp_path, monkeypatch):
    config = tmp_path / ".config" / "memguardos"
    config.mkdir(parents=True)
    (config / "openhands_model").write_text("openai/example-model\n", encoding="utf-8")

    monkeypatch.delenv("MEMGUARD_OPENHANDS_MODEL", raising=False)
    assert _user_setting(
        "MEMGUARD_OPENHANDS_MODEL", "openhands_model", home=tmp_path
    ) == "openai/example-model"

    monkeypatch.setenv("MEMGUARD_OPENHANDS_MODEL", "openai/env-model")
    assert _user_setting(
        "MEMGUARD_OPENHANDS_MODEL", "openhands_model", home=tmp_path
    ) == "openai/env-model"


def test_runtime_proofs_use_only_preselected_memories_and_real_finish_message():
    stages = {
        "planning": {"memories": []},
        "tool_selection": {
            "memories": [
                {
                    "memory_id": "mem-tool",
                    "preference_topic": "code_search_tool",
                    "preference_value": "grep",
                }
            ]
        },
        "response_generation": {
            "memories": [
                {
                    "memory_id": "mem-order",
                    "preference_topic": "response_order",
                    "preference_value": "command_then_explanation",
                }
            ]
        },
    }
    events = [
        {
            "event_type": "tool_call",
            "tool_name": "terminal",
            "tool_args": {"command": "grep -RIn target memguard"},
        },
        {
            "event_type": "tool_call",
            "tool_name": "finish",
            "tool_args": {"message": "命令：grep -RIn target memguard\n说明：已找到。"},
        },
    ]

    proofs = _runtime_preference_effect_proofs(
        stages,
        events,
        task="查找 target",
        agent_name="openhands",
        session_id="session-demo",
    )

    assert [(item["preference"]["topic"], item["compliance"]) for item in proofs] == [
        ("code_search_tool", "passed"),
        ("response_order", "passed"),
    ]


def test_confirmed_forget_uses_readable_verification_summary(capsys):
    assert _forget(
        _ForgetService(),
        "忘掉代码搜索工具偏好",
        confirm=True,
        compact=True,
    ) == 0
    output = capsys.readouterr().out
    assert "已遗忘 1 条目标记忆" in output
    assert "检索结果复核：通过" in output
    assert "外部 Agent 日志" in output
    assert '"forgotten"' not in output


def test_generic_forget_removes_target_preference_aggregate_only(tmp_path):
    service = MemGuardService(
        store_path=tmp_path / "memory.segment",
        db_path=tmp_path / "memory.sqlite3",
        namespace="forget-preference-test",
    )
    try:
        service.ingest_interaction(
            AgentInteractionEvent(
                agent_name="openhands",
                agent_version="test",
                session_id="session-1",
                turn_id="turn-1",
                sequence=1,
                event_type="user_message",
                role="user",
                content="以后搜索代码统一使用 grep，并且回答时先给命令再解释。",
            )
        )
        before = {
            item["topic"]
            for item in service.interaction_status()["preference_aggregates"]
            if item["stable"]
        }
        assert {"code_search_tool", "response_order"} <= before

        result = service.forget("忘掉 grep 的习惯", confirm=True, compact=True)
        after = {
            item["topic"]
            for item in service.interaction_status()["preference_aggregates"]
            if item["stable"]
        }

        assert result["deleted_preference_observation_count"] == 1
        assert "code_search_tool" not in after
        assert "response_order" in after
    finally:
        service.close()


def test_preference_status_hides_legacy_aggregate_linked_to_deleted_memory(tmp_path):
    service = MemGuardService(
        store_path=tmp_path / "memory.segment",
        db_path=tmp_path / "memory.sqlite3",
        namespace="legacy-forget-test",
    )
    try:
        result = service.ingest_interaction(
            AgentInteractionEvent(
                agent_name="openhands",
                agent_version="test",
                session_id="session-1",
                turn_id="turn-1",
                sequence=1,
                event_type="user_message",
                role="user",
                content="以后搜索代码统一使用 grep。",
            )
        )
        memory_id = result["memory_ids"][0]
        service.meta.mark_deleted(memory_id)

        stable_topics = {
            item["topic"]
            for item in service.interaction_status()["preference_aggregates"]
            if item["stable"]
        }
        assert "code_search_tool" not in stable_topics
    finally:
        service.close()
