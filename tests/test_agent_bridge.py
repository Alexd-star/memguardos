import json
from pathlib import Path

from memguard.integrations.bridge import AgentJobQueue


class FakeRunner:
    def __init__(self, **kwargs):
        self.kwargs = kwargs

    def run(self, task):
        return {
            "ok": True,
            "task_seen": task,
            "session_id": self.kwargs["session_id"],
            "workspace": str(self.kwargs["workspace"]),
        }


def test_agent_bridge_processes_one_job_in_isolated_workspace(tmp_path):
    queue = AgentJobQueue(tmp_path, runner_factory=FakeRunner)
    queue.submit(
        {
            "job_id": "job-001",
            "session_id": "session-001",
            "task": "查找精准遗忘实现",
            "task_type": "code_assistance",
        }
    )

    result = queue.process_next(service=object())

    assert result["ok"] is True
    assert result["task_seen"] == "查找精准遗忘实现"
    assert Path(result["workspace"]) == (tmp_path / "workspaces" / "session-001").resolve()
    assert (tmp_path / "outbox" / "job-001.result.json").is_file()
    archive = tmp_path / "archive" / "job-001.json"
    assert archive.is_file()
    assert "查找精准遗忘实现" not in archive.read_text(encoding="utf-8")
    assert list((tmp_path / "inbox").glob("*.json")) == []


def test_agent_bridge_rejects_path_traversal_and_does_not_echo_secrets(tmp_path):
    queue = AgentJobQueue(tmp_path, runner_factory=FakeRunner)
    queue.submit(
        {
            "job_id": "job-002",
            "session_id": "../../escape",
            "task": "token=top-secret-value",
        }
    )

    result = queue.process_next(service=object())

    assert result["ok"] is False
    assert result["error"] == "invalid_job"
    serialized = json.dumps(result, ensure_ascii=False)
    assert "top-secret-value" not in serialized
    assert "top-secret-value" not in (
        tmp_path / "archive" / "job-002.json"
    ).read_text(encoding="utf-8")


def test_agent_systemd_unit_is_unprivileged_and_resource_limited():
    unit = (Path(__file__).parents[1] / "scripts" / "memguard-agent@.service").read_text(
        encoding="utf-8"
    )

    for directive in (
        "User=%i",
        "NoNewPrivileges=true",
        "PrivateTmp=true",
        "ProtectSystem=strict",
        "PrivateDevices=true",
        "MemoryMax=",
        "CPUQuota=",
        "EnvironmentFile=-/etc/memguardos/agent-%i.env",
    ):
        assert directive in unit
