import json
from pathlib import Path

from memguard.cli.memctl import main as memctl_main
from memguard.os_guard import openkylin_native
from memguard.os_guard.service_control import service_resource_status


def test_openkylin_status_cli_has_native_operational_schema(tmp_path, monkeypatch, capsys):
    monkeypatch.setenv("MEMGUARD_HOME", str(tmp_path / "home"))
    monkeypatch.delenv("MEMGUARD_NAMESPACE", raising=False)

    assert memctl_main(["openkylin-status"]) == 0
    payload = json.loads(capsys.readouterr().out)

    assert payload["schema"] == "memguardos-openkylin-native-status-v1"
    assert payload["summary"]["overall"] in {"green", "yellow", "red"}
    assert payload["summary"]["readiness_score"] >= 0
    assert payload["system"]["platform"]
    assert "systemd" in payload["services"]
    assert "embedding" in payload["sdk"]
    assert "peony" in payload["desktop"]
    assert "auditd" in payload["security"]
    assert "memory" in payload["resources"]
    assert payload["recommendations"]


def test_desktop_scan_file_reports_preview_without_ingesting_sensitive_file(tmp_path, monkeypatch, capsys):
    monkeypatch.setenv("MEMGUARD_HOME", str(tmp_path / "home"))
    secret_file = tmp_path / ".env"
    secret_file.write_text("OPENAI_API_KEY=sk-test-example1234567890\n", encoding="utf-8")

    assert memctl_main(["desktop", "scan-file", "--file", str(secret_file)]) == 0
    payload = json.loads(capsys.readouterr().out)

    assert payload["schema"] == "memguardos-peony-scan-report-v1"
    assert payload["path"] == str(secret_file)
    assert payload["eligible_for_text_import"] is True
    assert payload["sensitivity"]["level"] == "high"
    assert "api_key" in payload["sensitivity"]["labels"]
    assert payload["recommended_action"] == "block_sensitive"
    assert payload["ingest"]["attempted"] is False


def test_desktop_scan_file_ingests_safe_text_and_records_audit(tmp_path, monkeypatch, capsys):
    monkeypatch.setenv("MEMGUARD_HOME", str(tmp_path / "home"))
    sample = tmp_path / "note.md"
    sample.write_text("以后回答 openKylin 问题时先给可复制命令。\n", encoding="utf-8")

    assert memctl_main(["desktop", "scan-file", "--file", str(sample), "--ingest"]) == 0
    payload = json.loads(capsys.readouterr().out)

    assert payload["recommended_action"] == "import_text"
    assert payload["ingest"]["attempted"] is True
    assert payload["ingest"]["stored"] is True
    assert payload["audit_event"]["stored"] is True
    assert payload["verification"]["audit_chain_ok"] is True


def test_desktop_scan_file_reports_directory_as_os_event_only(tmp_path, monkeypatch, capsys):
    monkeypatch.setenv("MEMGUARD_HOME", str(tmp_path / "home"))
    folder = tmp_path / "folder"
    folder.mkdir()

    assert memctl_main(["desktop", "scan-file", "--file", str(folder), "--ingest"]) == 0
    payload = json.loads(capsys.readouterr().out)

    assert payload["file_kind"] == "directory"
    assert payload["eligible_for_text_import"] is False
    assert payload["recommended_action"] == "audit_only"
    assert payload["ingest"]["attempted"] is False
    assert payload["audit_event"]["stored"] is True


def test_openkylin_native_packaging_assets_exist():
    root = Path(__file__).resolve().parents[1]
    assert (root / "scripts" / "warmup_kylin_sdk.sh").exists()
    assert (root / "scripts" / "memguard-kylin-warmup.service").exists()
    assert (root / "scripts" / "memguardos-native-helper.sh").exists()
    assert (root / "packaging" / "polkit" / "org.memguardos.policy").exists()
    build_script = (root / "scripts" / "build_deb.sh").read_text(encoding="utf-8")
    assert "org.memguardos.policy" in build_script
    assert "memguard-kylin-warmup.service" in build_script


def test_service_resource_status_reads_systemd_control_group(monkeypatch, tmp_path):
    cgroup_root = tmp_path / "cgroup"
    unit_group = cgroup_root / "system.slice" / "memguardd.service"
    unit_group.mkdir(parents=True)
    (cgroup_root / "cgroup.controllers").write_text("cpu memory\n", encoding="utf-8")
    (unit_group / "memory.current").write_text("4096\n", encoding="utf-8")
    (unit_group / "memory.max").write_text("268435456\n", encoding="utf-8")
    (unit_group / "cpu.max").write_text("200000 100000\n", encoding="utf-8")
    (unit_group / "cpu.stat").write_text("usage_usec 1250\n", encoding="utf-8")

    monkeypatch.setattr(
        "memguard.os_guard.service_control._systemctl_show",
        lambda unit: {
            "LoadState": "loaded",
            "ActiveState": "active",
            "ControlGroup": "/system.slice/memguardd.service",
            "MemoryMax": "268435456",
            "CPUQuotaPerSecUSec": "2s",
        },
    )

    result = service_resource_status(cgroup_root=cgroup_root)

    assert result["cgroup_v2"] is True
    assert result["service_managed"] is True
    assert result["control_group"] == "/system.slice/memguardd.service"
    assert result["memory_current"] == "4096"
    assert result["memory_max"] == "268435456"
    assert result["cpu_max"] == "200000 100000"
    assert result["cpu_stat"] == {"usage_usec": 1250}


def test_native_resource_status_uses_service_probe(monkeypatch):
    monkeypatch.setattr(
        openkylin_native,
        "service_resource_status",
        lambda: {
            "cgroup_v2": True,
            "service_managed": True,
            "control_group": "/system.slice/memguardd.service",
            "memory_current": "2048",
            "memory_max": "268435456",
            "cpu_max": "200000 100000",
            "cpu_stat": {"usage_usec": 900},
            "cpu_quota_per_sec_usec": "2s",
        },
    )

    result = openkylin_native._resource_status("default")

    assert result["cgroup_v2"] is True
    assert result["service_managed"] is True
    assert result["control_group"] == "/system.slice/memguardd.service"
    assert result["memory"]["cgroup_memory_current"] == "2048"
    assert result["cpu"]["cpu_max"] == "200000 100000"
