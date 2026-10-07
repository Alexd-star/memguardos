from pathlib import Path
import json
import subprocess
import sys

from memguard.eval.resource_control import compare_runs, make_workload


ROOT = (Path(__file__).resolve().parents[1] / "src")


def test_workload_is_deterministic_and_shared_by_both_modes():
    first = make_workload(memory_count=24, query_count=30, seed=20260702)
    second = make_workload(memory_count=24, query_count=30, seed=20260702)

    assert first == second
    assert len(first["memories"]) == 24
    assert len(first["queries"]) == 30
    assert all("expected_id" in query for query in first["queries"])


def test_compare_runs_requires_same_manifest_and_reports_required_metrics():
    baseline = {
        "mode": "memory_disabled",
        "workload_sha256": "same",
        "task_success_rate": 0.8,
        "peak_rss_kb": 100,
        "mean_cpu_percent": 20.0,
        "cpu_time_ms": 10.0,
        "p50_latency_ms": 2.0,
        "p95_latency_ms": 3.0,
        "p99_latency_ms": 4.0,
    }
    enabled = dict(baseline, mode="memguardos_enabled", task_success_rate=1.0, peak_rss_kb=140)

    result = compare_runs(baseline, enabled)

    assert result["protocol_valid"] is True
    assert result["task_success_rate_delta"] == 0.2
    assert result["peak_rss_delta_kb"] == 40
    assert result["same_workload_sha256"] is True


def test_resource_control_cli_runs_paired_isolated_processes(tmp_path):
    output = tmp_path / "resource_control.json"
    report = tmp_path / "resource_control.md"
    completed = subprocess.run(
        [
            sys.executable,
            str(ROOT / "scripts" / "run_resource_control.py"),
            "--output",
            str(output),
            "--markdown",
            str(report),
            "--work-dir",
            str(tmp_path / "work"),
            "--memory-count",
            "30",
            "--query-count",
            "30",
            "--warmup-count",
            "5",
        ],
        cwd=ROOT,
        text=True,
        capture_output=True,
        check=False,
    )

    assert completed.returncode == 0, completed.stderr
    payload = json.loads(output.read_text(encoding="utf-8"))
    assert payload["comparison"]["protocol_valid"] is True
    assert payload["baseline"]["mode"] == "memory_disabled"
    assert payload["enabled"]["mode"] == "memguardos_enabled"
    assert payload["baseline"]["workload_sha256"] == payload["enabled"]["workload_sha256"]
    for run in (payload["baseline"], payload["enabled"]):
        assert run["peak_rss_kb"] > 0
        assert run["mean_cpu_percent"] >= 0
        assert run["query_count"] == 30
        assert run["task_success_rate"] >= 0
        assert run["p95_latency_ms"] >= 0
    markdown = report.read_text(encoding="utf-8")
    assert "同负载独立进程资源对照" in markdown
    assert "峰值 RSS" in markdown
    assert "任务成功率" in markdown
