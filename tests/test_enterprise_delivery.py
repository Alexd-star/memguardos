from __future__ import annotations

from pathlib import Path
import zipfile

import pytest

from memguard.service import MemGuardService
from scripts.backup_memguard import backup_memguard
from scripts.check_slo import evaluate_slo
from scripts.restore_memguard import restore_memguard
from scripts.sign_release import create_release_attestation


def test_backup_restore_validates_manifest_and_refuses_tampered_archive(tmp_path):
    source = tmp_path / "source"
    source.mkdir()
    service = MemGuardService(store_path=source / "memory.segment", db_path=source / "memory.sqlite3")
    try:
        service.ingest_text("以后回答 openKylin 问题时先给可复制命令。", source_kind="manual")
    finally:
        service.close()

    archive = tmp_path / "memguardos-backup.zip"
    manifest = backup_memguard(source / "memory.segment", source / "memory.sqlite3", archive, namespace="default")

    assert archive.exists()
    assert manifest["ok"] is True
    assert manifest["file_count"] >= 2
    assert manifest["sha256"]

    restored = restore_memguard(archive, tmp_path / "restored")
    restored_service = MemGuardService(
        store_path=Path(restored["files"]["memory.segment"]),
        db_path=Path(restored["files"]["memory.sqlite3"]),
    )
    try:
        hits = restored_service.search("openKylin 命令", limit=3)
    finally:
        restored_service.close()
    assert hits

    tampered = tmp_path / "tampered.zip"
    with zipfile.ZipFile(archive, "r") as source_zip, zipfile.ZipFile(tampered, "w") as target_zip:
        for name in source_zip.namelist():
            payload = b"tampered" if name == "memory.segment" else source_zip.read(name)
            target_zip.writestr(name, payload)

    with pytest.raises(ValueError, match="sha256"):
        restore_memguard(tampered, tmp_path / "tampered-restore")


def test_slo_gate_reports_pass_and_actionable_failures():
    ok_report = {
        "results": {
            "memguardos_enabled": {
                "success_rate": 1.0,
                "latency": {"p95_ms": 87.5, "p99_ms": 140.0},
                "resources": {"peak_rss_mb": 42.0, "mean_cpu_percent": 7.5},
            }
        },
        "protocol": {"same_workload": True},
    }
    passing = evaluate_slo(ok_report)

    assert passing["ok"] is True
    assert passing["failed"] == []

    bad_report = {
        "results": {
            "memguardos_enabled": {
                "success_rate": 0.95,
                "latency": {"p95_ms": 1500.0, "p99_ms": 2200.0},
                "resources": {"peak_rss_mb": 1024.0, "mean_cpu_percent": 88.0},
            }
        },
        "protocol": {"same_workload": False},
    }
    failing = evaluate_slo(bad_report)

    assert failing["ok"] is False
    assert {"metric": "same_workload", "actual": False, "threshold": True} in failing["failed"]
    assert any(item["metric"] == "latency.p95_ms" for item in failing["failed"])
    assert any(item["metric"] == "success_rate" for item in failing["failed"])


def test_slo_gate_accepts_resource_control_report_schema():
    resource_report = {
        "protocol": {"same_workload": True},
        "enabled": {
            "task_success_rate": 1.0,
            "p95_latency_ms": 12.5,
            "p99_latency_ms": 20.0,
            "peak_rss_kb": 131072,
            "mean_cpu_percent": 9.0,
        },
    }

    result = evaluate_slo(resource_report)

    assert result["ok"] is True
    p95 = next(item for item in result["checks"] if item["metric"] == "latency.p95_ms")
    rss = next(item for item in result["checks"] if item["metric"] == "resources.peak_rss_mb")
    assert p95["actual"] == 12.5
    assert rss["actual"] == 128.0


def test_sign_release_never_fakes_cryptographic_signature(tmp_path):
    artifact = tmp_path / "submission.zip"
    artifact.write_text("release payload", encoding="utf-8")

    attestation = create_release_attestation(artifact, tmp_path / "attestation")

    assert attestation["artifact"] == str(artifact)
    assert len(attestation["sha256"]) == 64
    assert attestation["signature_status"] in {"signed", "not_configured"}
    if attestation["signature_status"] == "not_configured":
        assert attestation["signature_file"] is None


def test_general_forget_reports_managed_boundary_and_external_residue(tmp_path):
    service = MemGuardService(store_path=tmp_path / "memory.segment", db_path=tmp_path / "memory.sqlite3")
    try:
        service.ingest_text("临时代号 BlueRiver 只用于测试，后续应删除。", source_kind="manual")
        plan = service.forget("忘掉 BlueRiver", confirm=False)
        result = service.forget("忘掉 BlueRiver", confirm=True, compact=True)
    finally:
        service.close()

    assert plan["external_residue"]["scope"] == "dry_run_boundary_notice"
    assert result["external_residue"]["managed_store_boundary"] is True
    assert "MemGuardOS" in result["external_residue"]["explanation"]
    assert "external_agent_logs" in result["external_residue"]["not_covered"]
