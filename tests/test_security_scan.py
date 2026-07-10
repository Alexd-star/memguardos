from __future__ import annotations

from pathlib import Path
import json
import subprocess

from scripts.run_security_scan import run_security_scan


class FakeRunner:
    def __init__(self, responses):
        self.responses = list(responses)
        self.commands = []

    def __call__(self, command, **kwargs):
        self.commands.append(command)
        response = self.responses.pop(0)
        return subprocess.CompletedProcess(
            command,
            response["returncode"],
            response.get("stdout", ""),
            response.get("stderr", ""),
        )


def test_security_scan_accepts_clean_bandit_and_pip_audit_results(tmp_path):
    output_json = tmp_path / "security_scan.json"
    output_markdown = tmp_path / "安全扫描报告.md"
    runner = FakeRunner(
        [
            {
                "returncode": 0,
                "stdout": json.dumps(
                    {
                        "results": [],
                        "metrics": {"_totals": {"SEVERITY.HIGH": 0}},
                    }
                ),
            },
            {
                "returncode": 0,
                "stdout": json.dumps({"dependencies": []}),
            },
        ]
    )

    report = run_security_scan(
        output_json=output_json,
        output_markdown=output_markdown,
        runner=runner,
        python="python",
        dependency_scope="environment",
    )

    assert report["ok"] is True
    assert report["tools"]["bandit"]["status"] == "clean"
    assert report["tools"]["pip_audit"]["status"] == "clean"
    assert report["summary"]["bandit_high_count"] == 0
    assert report["summary"]["pip_audit_vulnerability_count"] == 0
    assert output_json.exists()
    markdown = output_markdown.read_text(encoding="utf-8")
    assert "MemGuardOS 安全扫描报告" in markdown
    assert "Bandit" in markdown
    assert "pip-audit" in markdown


def test_security_scan_fails_on_high_severity_bandit_issue(tmp_path):
    runner = FakeRunner(
        [
            {
                "returncode": 1,
                "stdout": json.dumps(
                    {
                        "results": [
                            {
                                "filename": "memguard/service.py",
                                "line_number": 12,
                                "issue_severity": "HIGH",
                                "issue_confidence": "HIGH",
                                "test_id": "B602",
                                "issue_text": "subprocess shell=True",
                            }
                        ],
                        "metrics": {"_totals": {"SEVERITY.HIGH": 1}},
                    }
                ),
            },
            {
                "returncode": 0,
                "stdout": json.dumps({"dependencies": []}),
            },
        ]
    )

    report = run_security_scan(
        output_json=tmp_path / "security_scan.json",
        output_markdown=tmp_path / "scan.md",
        runner=runner,
        python="python",
        dependency_scope="environment",
    )

    assert report["ok"] is False
    assert report["tools"]["bandit"]["status"] == "issues"
    assert report["summary"]["bandit_high_count"] == 1


def test_security_scan_fails_on_unreviewed_medium_severity_bandit_issue(tmp_path):
    runner = FakeRunner(
        [
            {
                "returncode": 1,
                "stdout": json.dumps(
                    {
                        "results": [
                            {
                                "filename": "memguard/llm/adapter.py",
                                "line_number": 42,
                                "issue_severity": "MEDIUM",
                                "issue_confidence": "HIGH",
                                "test_id": "B310",
                                "issue_text": "unvalidated URL opener",
                            }
                        ]
                    }
                ),
            },
            {
                "returncode": 0,
                "stdout": json.dumps({"dependencies": []}),
            },
        ]
    )

    report = run_security_scan(
        output_json=tmp_path / "security_scan.json",
        output_markdown=tmp_path / "scan.md",
        runner=runner,
        python="python",
        dependency_scope="environment",
    )

    assert report["ok"] is False
    assert report["summary"]["bandit_medium_count"] == 1
    assert report["policy"]["bandit_medium_severity_allowed"] == 0


def test_security_scan_records_missing_tools_without_pretending_success(tmp_path):
    runner = FakeRunner(
        [
            {
                "returncode": 1,
                "stderr": "No module named bandit",
            },
            {
                "returncode": 1,
                "stderr": "No module named pip_audit",
            },
        ]
    )

    strict_report = run_security_scan(
        output_json=tmp_path / "strict.json",
        output_markdown=tmp_path / "strict.md",
        runner=runner,
        python="python",
        dependency_scope="environment",
    )

    assert strict_report["ok"] is False
    assert strict_report["tools"]["bandit"]["status"] == "missing"
    assert strict_report["tools"]["pip_audit"]["status"] == "missing"

    skipped_runner = FakeRunner(
        [
            {"returncode": 1, "stderr": "No module named bandit"},
            {"returncode": 1, "stderr": "No module named pip_audit"},
        ]
    )
    skipped_report = run_security_scan(
        output_json=tmp_path / "skipped.json",
        output_markdown=tmp_path / "skipped.md",
        runner=skipped_runner,
        python="python",
        allow_missing_tools=True,
        dependency_scope="environment",
    )

    assert skipped_report["ok"] is True
    assert skipped_report["summary"]["missing_tools"] == ["bandit", "pip_audit"]
    assert "未安装" in Path(tmp_path / "skipped.md").read_text(encoding="utf-8")


def test_security_scan_runtime_dependency_scope_is_clean_when_project_has_no_runtime_deps(tmp_path):
    runner = FakeRunner(
        [
            {
                "returncode": 0,
                "stdout": json.dumps({"results": []}),
            }
        ]
    )

    report = run_security_scan(
        output_json=tmp_path / "runtime.json",
        output_markdown=tmp_path / "runtime.md",
        runner=runner,
        python="python",
        dependency_scope="runtime",
    )

    assert report["ok"] is True
    assert report["tools"]["pip_audit"]["status"] == "clean"
    assert report["tools"]["pip_audit"]["dependency_scope"] == "runtime"
    assert report["tools"]["pip_audit"]["dependency_count"] == 0
    assert len(runner.commands) == 1


def test_security_scan_reports_portable_commands_without_local_python_paths(tmp_path):
    local_python = r"D:\private-workspace\.venv\Scripts\python.exe"
    output_json = tmp_path / "security_scan.json"
    output_markdown = tmp_path / "安全扫描报告.md"
    runner = FakeRunner(
        [
            {
                "returncode": 0,
                "stdout": json.dumps({"results": []}),
            },
            {
                "returncode": 0,
                "stdout": json.dumps({"dependencies": []}),
            },
        ]
    )

    report = run_security_scan(
        output_json=output_json,
        output_markdown=output_markdown,
        runner=runner,
        python=local_python,
        dependency_scope="environment",
    )

    assert runner.commands[0][0] == local_python
    assert report["tools"]["bandit"]["command"][0] == "python"
    assert report["tools"]["pip_audit"]["command"][0] == "python"
    assert local_python not in output_json.read_text(encoding="utf-8")
    assert local_python not in output_markdown.read_text(encoding="utf-8")
