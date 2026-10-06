import json
from types import SimpleNamespace

import pytest

from memguard.integrations.events import AgentInteractionEvent
from memguard.integrations.openhands_runner import OpenHandsMemoryRunner
from scripts import verify_real_agent_showcase as showcase


class OfflineRunner:
    def __init__(self, service, **kwargs):
        self.service, self.kwargs = service, kwargs
        self.runner = OpenHandsMemoryRunner(service, **kwargs)
        self.events = []

    def recorded_events(self):
        return self.events

    def run(self, task):
        bundle = self.runner.prepare_context(task)
        scope = self.kwargs
        project = scope["workspace"]
        selected = [
            row for stage in bundle["stages"].values() for row in stage["memories"]
        ]
        preferred = next(
            (
                row["preference_value"]
                for row in selected
                if row.get("preference_topic") == "code_search_tool"
            ),
            "grep",
        )
        command = f"{preferred} -n load_settings settings.py"
        output = "settings.py:3:def load_settings(path):"
        if "青山" in task:
            command, output = "", "青山项目发布日是周五。"
        elif showcase.GOAL in task:
            file = "release-a.md" if "task-a.json" in task else "release-b.md"
            target = project / file
            target.write_text(
                target.read_text(encoding="utf-8").replace("TODO", "REVIEWED"),
                encoding="utf-8",
            )
            command, output = f"python review.py {file}", "REVIEWED"
        fields = {
            "agent_name": "openhands",
            "agent_version": "test",
            "session_id": scope["session_id"],
            "turn_id": "turn-1",
        }
        self.events = [
            AgentInteractionEvent(
                **fields,
                sequence=1,
                event_type="user_message",
                role="user",
                content=task,
            ).to_dict()
        ]
        if command:
            self.events.extend(
                [
                    AgentInteractionEvent(
                        **fields,
                        sequence=2,
                        event_type="tool_call",
                        role="assistant",
                        tool_name="terminal",
                        tool_args={"command": command},
                    ).to_dict(),
                    AgentInteractionEvent(
                        **fields,
                        sequence=3,
                        event_type="tool_result",
                        role="tool",
                        tool_name="terminal",
                        tool_result=output,
                        exit_code=0,
                    ).to_dict(),
                ]
            )
        answer = f"```bash\n{command}\n```\n\n{output}" if command else output
        self.events.append(
            AgentInteractionEvent(
                **fields,
                sequence=4,
                event_type="assistant_message",
                role="assistant",
                content=answer,
            ).to_dict()
            | {"display_content": answer}
        )
        return {
            "ok": True,
            "memory_enabled": scope["memory_enabled"],
            "session_id": scope["session_id"],
            "events": self.events,
        }


def arguments():
    return SimpleNamespace(
        installed=False,
        model="offline-fixture",
        base_url="",
        api_key_env="NONEXISTENT_SHOWCASE_KEY",
    )


def test_full_driver_uses_product_contracts_and_separates_fake_factory(tmp_path):
    output = tmp_path / "evidence"
    result = showcase.run_showcase(output, arguments(), runner_factory=OfflineRunner)
    assert result["external_llm_used"] is False
    assert result["mode"] == "in_process_test_factory"
    assert result["agent_sessions"] == 7
    assert result["passed"], [
        (
            row["result_path"],
            json.loads((output / row["result_path"]).read_text(encoding="utf-8"))[
                "product_checks"
            ],
        )
        for row in result["results"]
        if row["status"] != "pass"
    ]
    off = json.loads(
        (output / "cases/A01/memory_off/result.json").read_text(encoding="utf-8")
    )
    assert off["run"]["memory_enabled"] is False
    assert off["events"]
    assert not any(stage["memories"] for stage in off["context"]["stages"].values())
    assert (
        "REVIEWED"
        in json.loads((output / "cases/A06/result.json").read_text(encoding="utf-8"))[
            "workspace_after"
        ]["release-b.md"]["text"]
    )
    with pytest.raises(FileExistsError):
        showcase.run_showcase(output, arguments(), runner_factory=OfflineRunner)


def test_driver_preserves_failed_runs_without_creating_success_files(tmp_path):
    class FailingRunner(OfflineRunner):
        def run(self, task):
            self.events = [
                {"event_type": "user_message", "role": "user", "content": "partial"}
            ]
            raise RuntimeError("fixture transport failure")

    output = tmp_path / "evidence"
    result = showcase.run_showcase(output, arguments(), runner_factory=FailingRunner)
    assert not result["passed"]
    assert len(result["results"]) == 7
    first = json.loads(
        (output / result["results"][0]["result_path"]).read_text(encoding="utf-8")
    )
    assert first["errors"] and first["events"][0]["content"] == "partial"
    release = json.loads((output / "cases/A05/result.json").read_text(encoding="utf-8"))
    assert "TODO" in release["workspace_after"]["release-a.md"]["text"]


def test_evidence_redacts_exact_provider_credential(tmp_path):
    service = showcase.MemGuardService(tmp_path / "m.segment", tmp_path / "m.sqlite3")
    try:
        secret = "opaque-provider-credential"
        assert secret not in str(
            showcase.redact({"message": "request rejected " + secret}, service, secret)
        )
    finally:
        service.close()
