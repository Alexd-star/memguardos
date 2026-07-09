import json
from pathlib import Path

from memguard.cli.memctl import main as memctl_main
from memguard.desktop.scan_manifest import apply_scan_manifest, create_scan_manifest
from memguard.os_guard import openkylin_native
from memguard.os_guard.privileged_actions import ALLOWED_ACTIONS, run_privileged_action
from memguard.os_guard.service_control import service_resource_status
from memguard.service import MemGuardService
from scripts.check_competition_ready import check_native_openkylin_evidence


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
    assert isinstance(payload["recommendations"], list)


def test_openkylin_status_fast_uses_light_sdk_status(tmp_path, monkeypatch, capsys):
    monkeypatch.setenv("MEMGUARD_HOME", str(tmp_path / "home"))
    monkeypatch.delenv("MEMGUARD_NAMESPACE", raising=False)

    def fail_heavy_probe(*args, **kwargs):
        raise AssertionError("fast openKylin status must not load the full Kylin SDK path")

    monkeypatch.setattr(openkylin_native, "kylin_sdk_status", fail_heavy_probe)

    assert memctl_main(["openkylin-status", "--fast"]) == 0
    payload = json.loads(capsys.readouterr().out)

    assert payload["schema"] == "memguardos-openkylin-native-status-v1"
    assert payload["sdk"]["embedding"]["status_mode"] == "fast"
    assert payload["sdk"]["embedding"]["embedding_probe"] is None
    assert payload["sdk"]["embedding"]["inference_ready"] is None
    assert payload["sdk"]["embedding"]["capability_state"] in {
        "installed_unwarmed",
        "recently_ready",
        "not_installed",
        "metadata_incomplete",
    }
    assert payload["sdk"]["embedding"]["user_message"]
    assert payload["sdk"]["embedding"]["next_action"]
    assert "vector_store" in payload["sdk"]
    assert payload["sdk"]["vector_store"]["capability_state"] in {
        "socket_ready",
        "service_active",
        "metadata_available",
        "not_installed",
    }
    assert payload["sdk"]["vector_store"]["user_message"]
    assert payload["sdk"]["vector_store"]["next_action"]


def test_openkylin_status_default_uses_light_product_status(tmp_path, monkeypatch, capsys):
    monkeypatch.setenv("MEMGUARD_HOME", str(tmp_path / "home"))
    monkeypatch.delenv("MEMGUARD_NAMESPACE", raising=False)

    def fail_heavy_probe(*args, **kwargs):
        raise AssertionError("default openKylin status must stay lightweight; use --probe-sdk for full SDK probing")

    monkeypatch.setattr(openkylin_native, "kylin_sdk_status", fail_heavy_probe)

    assert memctl_main(["openkylin-status"]) == 0
    payload = json.loads(capsys.readouterr().out)

    assert payload["schema"] == "memguardos-openkylin-native-status-v1"
    assert payload["sdk"]["embedding"]["status_mode"] == "fast"
    assert payload["sdk"]["embedding"]["embedding_probe"] is None
    assert payload["sdk"]["embedding"]["inference_ready"] is None


def test_fast_openkylin_status_does_not_mark_installed_embedding_as_fault(monkeypatch, tmp_path):
    monkeypatch.setenv("MEMGUARD_HOME", str(tmp_path / "home"))
    monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path / "state"))

    monkeypatch.setattr(
        openkylin_native,
        "_service_status",
        lambda units: {
            "systemd": {"available": True, "active": True},
            "memguardd.service": {"available": True, "active": True, "state": "active"},
            "kylin-ai-vector-engine.service": {"available": True, "active": True, "state": "active"},
            "kytensor.service": {"available": True, "active": True, "state": "active"},
        },
    )
    monkeypatch.setattr(
        openkylin_native,
        "_fast_sdk_status",
        lambda services: {
            "embedding": {
                "status_mode": "fast",
                "available": True,
                "inference_ready": None,
                "embedding_probe": None,
                "model_info": {"name": "ensemble-embd_gte-base_uint8-text", "dim": 768, "ondevice": True},
                "fallback": "hash_embedding",
                "diagnosis": {},
            },
            "vector_store": {"available": True, "user_service": {"active": True}},
        },
    )
    monkeypatch.setattr(
        openkylin_native,
        "_desktop_status",
        lambda: {
            "peony": {"available": True, "plugin_installed": True},
            "ukui_control_center": {"available": True, "plugin_installed": True},
            "notify_send": {"available": True},
        },
    )
    monkeypatch.setattr(
        openkylin_native,
        "_security_status",
        lambda: {
            "auditd": {"available": True, "rules_tool_available": True},
            "polkit": {"available": True, "policy_installed": True},
            "bpf_lsm": {"source_available": True, "loader_available": True},
        },
    )
    monkeypatch.setattr(
        openkylin_native,
        "_resource_status",
        lambda namespace: {
            "cgroup_v2": True,
            "service_managed": True,
            "control_group": "/system.slice/memguardd.service",
            "memory": {},
            "cpu": {},
            "namespace": namespace,
        },
    )

    payload = openkylin_native.openkylin_native_status(namespace="default", fast=True)
    components = {item["name"]: item for item in payload["components"]}

    assert components["kylin_embedding"]["level"] == "green"
    assert components["kylin_embedding"]["ready"] is True
    assert components["kylin_embedding"]["user_message"] == "已安装，当前未预热"
    assert components["kylin_embedding"]["next_action"] == "首次使用可能稍慢，可点击“预热 Kylin SDK”"
    assert payload["summary"]["overall"] == "green"
    assert payload["recommendations"] == []
    assert not any("未进入真实推理就绪状态" in item for item in payload["recommendations"])


def test_fast_openkylin_status_reports_recent_kylin_warmup_without_probe(monkeypatch, tmp_path):
    state_home = tmp_path / "state"
    warmup_dir = state_home / "memguardos"
    warmup_dir.mkdir(parents=True)
    (warmup_dir / "kylin-sdk-warmup.json").write_text(
        json.dumps(
            {
                "schema": "memguardos-kylin-sdk-warmup-v1",
                "ok": True,
                "inference_ready": True,
                "dim": 768,
                "model": "ensemble-embd_gte-base_uint8-text",
                "completed_at": "2026-07-06T10:30:00+08:00",
            }
        ),
        encoding="utf-8",
    )
    monkeypatch.setenv("XDG_STATE_HOME", str(state_home))
    monkeypatch.setattr(
        openkylin_native,
        "_fast_sdk_status",
        lambda services: {
            "embedding": {
                "status_mode": "fast",
                "available": True,
                "inference_ready": None,
                "embedding_probe": None,
                "model_info": {"name": "ensemble-embd_gte-base_uint8-text", "dim": 768, "ondevice": True},
                "fallback": "hash_embedding",
                "diagnosis": {},
            },
            "vector_store": {"available": False},
        },
    )

    payload = openkylin_native.openkylin_native_status(namespace="default", fast=True)
    embedding = payload["sdk"]["embedding"]
    components = {item["name"]: item for item in payload["components"]}

    assert embedding["product_state"]["installed"] is True
    assert embedding["product_state"]["last_probe_success"] is True
    assert embedding["product_state"]["warmup_state"] == "ready"
    assert embedding["product_state"]["last_success_dim"] == 768
    assert embedding["capability_state"] == "recently_ready"
    assert components["kylin_embedding"]["level"] == "green"
    assert components["kylin_embedding"]["user_message"] == "已安装，最近一次端侧向量预热成功"


def test_openkylin_sdk_recommendation_uses_user_facing_copy(monkeypatch, tmp_path):
    monkeypatch.setenv("MEMGUARD_HOME", str(tmp_path / "home"))
    monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path / "state"))

    monkeypatch.setattr(
        openkylin_native,
        "_service_status",
        lambda units: {
            "systemd": {"available": True, "active": True},
            "memguardd.service": {"available": True, "active": True, "state": "active"},
            "kylin-ai-vector-engine.service": {"available": True, "active": True, "state": "active"},
            "kytensor.service": {"available": True, "active": True, "state": "active"},
        },
    )
    monkeypatch.setattr(
        openkylin_native,
        "kylin_sdk_status",
        lambda probe_embedding=False, probe_timeout=45.0: {
            "embedding": {
                "available": True,
                "inference_ready": False,
                "embedding_probe": {"ok": False, "error": "timeout"},
                "model_info": {"name": "ensemble-embd_gte-base_uint8-text", "dim": 768},
                "fallback": "hash_embedding",
                "diagnosis": {},
            },
            "vector_store": {"available": True, "user_service": {"active": True}},
        },
    )
    monkeypatch.setattr(
        openkylin_native,
        "_desktop_status",
        lambda: {
            "peony": {"available": True, "plugin_installed": True},
            "ukui_control_center": {"available": True, "plugin_installed": True},
            "notify_send": {"available": True},
        },
    )
    monkeypatch.setattr(
        openkylin_native,
        "_security_status",
        lambda: {
            "auditd": {"available": True, "rules_tool_available": True},
            "polkit": {"policy_installed": True},
            "bpf_lsm": {},
        },
    )
    monkeypatch.setattr(
        openkylin_native,
        "_resource_status",
        lambda namespace: {
            "cgroup_v2": True,
            "service_managed": True,
            "control_group": "/system.slice/memguardd.service",
            "memory": {},
            "cpu": {},
            "namespace": namespace,
        },
    )

    payload = openkylin_native.openkylin_native_status(namespace="default", probe_sdk=True)
    recommendations = "\n".join(payload["recommendations"])

    assert "真实推理" not in recommendations
    assert "warmup_kylin_sdk.sh" not in recommendations
    assert "预热 Kylin SDK" in recommendations
    assert "默认检索不受影响" in recommendations


def test_fast_openkylin_status_treats_vector_socket_as_ready(monkeypatch, tmp_path):
    monkeypatch.setenv("MEMGUARD_HOME", str(tmp_path / "home"))

    monkeypatch.setattr(
        openkylin_native,
        "_service_status",
        lambda units: {
            "systemd": {"available": True, "active": True},
            "memguardd.service": {"available": True, "active": True, "state": "active"},
            "kylin-ai-vector-engine.service": {"available": False, "active": False, "state": "inactive"},
            "kytensor.service": {"available": True, "active": True, "state": "active"},
        },
    )
    monkeypatch.setattr(
        openkylin_native,
        "_fast_sdk_status",
        lambda services: {
            "embedding": {
                "status_mode": "fast",
                "available": True,
                "inference_ready": None,
                "embedding_probe": None,
                "model_info": {"name": "ensemble-embd_gte-base_uint8-text", "dim": 768, "ondevice": True},
                "fallback": "hash_embedding",
                "diagnosis": {},
            },
            "vector_store": {
                "available": True,
                "socket_exists": True,
                "user_service": {"available": False, "active": False, "state": "inactive"},
            },
        },
    )
    monkeypatch.setattr(
        openkylin_native,
        "_desktop_status",
        lambda: {
            "peony": {"available": True, "plugin_installed": True},
            "ukui_control_center": {"available": True, "plugin_installed": True},
            "notify_send": {"available": True},
        },
    )
    monkeypatch.setattr(
        openkylin_native,
        "_security_status",
        lambda: {
            "auditd": {"available": True, "rules_tool_available": True},
            "polkit": {"available": True, "policy_installed": True},
            "bpf_lsm": {"source_available": True, "loader_available": True},
        },
    )
    monkeypatch.setattr(
        openkylin_native,
        "_resource_status",
        lambda namespace: {
            "cgroup_v2": True,
            "service_managed": True,
            "control_group": "/system.slice/memguardd.service",
            "memory": {},
            "cpu": {},
            "namespace": namespace,
        },
    )

    payload = openkylin_native.openkylin_native_status(namespace="default", fast=True)
    components = {item["name"]: item for item in payload["components"]}

    assert components["kylin_vector"]["level"] == "green"
    assert components["kylin_vector"]["ready"] is True
    assert components["kylin_vector"]["capability_state"] == "socket_ready"
    assert components["kylin_vector"]["user_message"] == "向量服务接口可连接"
    assert payload["summary"]["overall"] == "green"
    assert payload["recommendations"] == []
    assert not any("Kylin vector SDK 已安装但用户服务未激活" in item for item in payload["recommendations"])


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


def test_scan_manifest_preserves_order_and_blocks_sensitive_items(tmp_path, monkeypatch):
    monkeypatch.setenv("MEMGUARD_HOME", str(tmp_path / "home"))
    safe = tmp_path / "safe.md"
    secret = tmp_path / ".env"
    safe.write_text("以后回答 openKylin 问题时先给命令。\n", encoding="utf-8")
    secret.write_text("OPENAI_API_KEY=sk-test-example1234567890\n", encoding="utf-8")
    service = MemGuardService()
    try:
        result = create_scan_manifest([safe, secret], service, store_dir=tmp_path / "pending")
    finally:
        service.close()

    assert result["schema"] == "memguardos-desktop-scan-manifest-v1"
    assert [item["path"] for item in result["items"]] == [str(safe.resolve()), str(secret.resolve())]
    assert result["items"][0]["selectable"] is True
    assert result["items"][1]["selectable"] is False
    assert result["items"][1]["recommended_action"] == "block_sensitive"
    assert Path(result["manifest_path"]).exists()


def test_apply_scan_manifest_rejects_tamper_expiry_and_replay(tmp_path, monkeypatch):
    monkeypatch.setenv("MEMGUARD_HOME", str(tmp_path / "home"))
    safe = tmp_path / "safe.md"
    safe.write_text("systemd MemoryMax 可以限制服务内存。\n", encoding="utf-8")
    service = MemGuardService()
    try:
        created = create_scan_manifest(
            [safe],
            service,
            store_dir=tmp_path / "pending",
            now=1000.0,
            ttl_seconds=60,
        )
        manifest_path = Path(created["manifest_path"])
        document = json.loads(manifest_path.read_text(encoding="utf-8"))
        document["items"][0]["path"] = str(tmp_path / "other.md")
        manifest_path.write_text(json.dumps(document), encoding="utf-8")
        tampered = apply_scan_manifest(manifest_path, [str(safe.resolve())], service, now=1010.0)
        assert tampered["ok"] is False
        assert tampered["error_code"] == "manifest_signature_invalid"

        created = create_scan_manifest([safe], service, store_dir=tmp_path / "pending", now=2000.0, ttl_seconds=10)
        expired = apply_scan_manifest(created["manifest_path"], [str(safe.resolve())], service, now=2011.0)
        assert expired["ok"] is False
        assert expired["error_code"] == "manifest_expired"

        created = create_scan_manifest([safe], service, store_dir=tmp_path / "pending", now=3000.0)
        first = apply_scan_manifest(created["manifest_path"], [str(safe.resolve())], service, now=3001.0)
        second = apply_scan_manifest(created["manifest_path"], [str(safe.resolve())], service, now=3002.0)
        assert first["ok"] is True
        assert first["summary"]["stored"] == 1
        assert second["ok"] is False
        assert second["error_code"] == "manifest_already_used"
    finally:
        service.close()


def test_scan_manifest_enforces_batch_limits(tmp_path, monkeypatch):
    monkeypatch.setenv("MEMGUARD_HOME", str(tmp_path / "home"))
    files = []
    for index in range(3):
        path = tmp_path / f"note-{index}.txt"
        path.write_text("x" * 10, encoding="utf-8")
        files.append(path)
    service = MemGuardService()
    try:
        too_many = create_scan_manifest(files, service, store_dir=tmp_path / "pending", max_files=2)
        too_large = create_scan_manifest(files[:2], service, store_dir=tmp_path / "pending", max_total_bytes=15)
    finally:
        service.close()

    assert too_many["ok"] is False
    assert too_many["error_code"] == "too_many_files"
    assert too_large["ok"] is False
    assert too_large["error_code"] == "total_size_limit_exceeded"


def test_desktop_scan_files_and_apply_cli_require_explicit_selection(tmp_path, monkeypatch, capsys):
    monkeypatch.setenv("MEMGUARD_HOME", str(tmp_path / "home"))
    safe = tmp_path / "safe.md"
    safe.write_text("openKylin 使用 systemd 管理后台服务。\n", encoding="utf-8")

    assert memctl_main(["desktop", "scan-files", "--file", str(safe)]) == 0
    preview = json.loads(capsys.readouterr().out)
    assert preview["ok"] is True
    assert preview["items"][0]["selectable"] is True
    assert preview["items"][0]["ingest"]["attempted"] is False

    assert memctl_main(
        [
            "desktop",
            "apply-scan",
            "--manifest",
            preview["manifest_path"],
            "--select",
            str(safe),
        ]
    ) == 0
    applied = json.loads(capsys.readouterr().out)
    assert applied["ok"] is True
    assert applied["summary"]["stored"] == 1


def test_desktop_forget_import_cli_targets_selected_memory_ids(tmp_path, monkeypatch, capsys):
    monkeypatch.setenv("MEMGUARD_HOME", str(tmp_path / "home"))
    safe = tmp_path / "safe.md"
    safe.write_text("以后 openKylin 服务排查优先查看 systemctl status memguardd。\n", encoding="utf-8")

    assert memctl_main(["desktop", "scan-files", "--file", str(safe)]) == 0
    preview = json.loads(capsys.readouterr().out)
    assert memctl_main(
        [
            "desktop",
            "apply-scan",
            "--manifest",
            preview["manifest_path"],
            "--select",
            str(safe),
        ]
    ) == 0
    applied = json.loads(capsys.readouterr().out)
    memory_id = applied["results"][0]["ingest"]["memory_id"]

    assert memctl_main(["desktop", "forget-import", "--memory-id", memory_id, "--compact"]) == 0
    forgotten = json.loads(capsys.readouterr().out)

    assert forgotten["ok"] is True
    assert forgotten["summary"]["forgotten"] == 1
    assert forgotten["forgotten_memory_ids"] == [memory_id]
    assert forgotten["verification"][0]["irreversible_ok"] is True
    assert memctl_main(["verify-forget", memory_id, "--probe", "systemctl status memguardd"]) == 0
    probe = json.loads(capsys.readouterr().out)
    assert probe["irreversible_ok"] is True


def test_collect_openkylin_evidence_cli_exports_manifested_package(tmp_path, monkeypatch, capsys):
    monkeypatch.setenv("MEMGUARD_HOME", str(tmp_path / "home"))
    output_dir = tmp_path / "native-evidence"

    assert memctl_main(["collect-openkylin-evidence", "--output-dir", str(output_dir)]) == 0
    payload = json.loads(capsys.readouterr().out)

    assert payload["ok"] is True
    assert Path(payload["output_dir"]) == output_dir.resolve()
    expected = {
        "source_revision.json",
        "environment.json",
        "openkylin_status.json",
        "diagnostics.json",
        "audit_chain.json",
        "systemd_cgroup.json",
        "desktop_assets.json",
        "manifest.json",
        "SHA256SUMS.txt",
    }
    assert expected.issubset({path.name for path in output_dir.iterdir()})
    manifest = json.loads((output_dir / "manifest.json").read_text(encoding="utf-8"))
    assert manifest["schema"] == "memguardos-openkylin-evidence-manifest-v1"
    assert "SHA256SUMS.txt" not in {item["path"] for item in manifest["files"]}
    assert "manifest.json" not in {item["path"] for item in manifest["files"]}
    assert (output_dir / "SHA256SUMS.txt").read_text(encoding="utf-8").strip()


def test_peony_plugin_uses_preview_confirmation_contract():
    source = (Path(__file__).resolve().parents[1] / "native/ukui/peony-memguard/memguard_peony_plugin.cpp").read_text(encoding="utf-8")

    assert "scan-files" in source
    assert "apply-scan" in source
    assert "QTableWidget" in source
    assert "QDialogButtonBox" in source
    assert "导入所选文件" in source
    assert "--ingest" not in source
    assert "selectionUris.first" not in source
    assert "/tmp/memguardos_peony_plugin.log" not in source
    assert "QTextStream out(&log)" not in source
    assert "JSON 结果" not in source
    assert "结果解析失败" not in source
    assert "没有收到有效的检查结果" in source
    assert "检查结果暂时无法读取" in source
    assert "已保存 %1 个安全文件" in source
    assert "可保存内容" in source
    assert "可导入" in source
    assert "已保护" in source
    assert "只记录" in source
    assert "查看结果" in source
    assert "撤销本次导入" in source
    assert "forget-import" in source
    assert "--memory-id" in source
    assert "遗忘本次导入内容" in source
    assert "openLocalConsole(QStringLiteral(\"#audit\"))" in source
    assert "[this, progress, process]" in source


def test_ukui_control_center_uses_structured_native_product_ui():
    root = Path(__file__).resolve().parents[1]
    source = (root / "native/ukui/ukcc-memguard/memguard_ukcc_plugin.cpp").read_text(encoding="utf-8")

    assert "QTabWidget" in source
    assert "QTreeWidget" in source
    assert "QJsonDocument" in source
    assert "维护结果" in source
    assert "查看维护结果" in source
    assert "详细结果" not in source
    assert "高级信息" not in source
    assert "高级运行信息" not in source
    assert "原始输出" not in source
    assert "技术详情" not in source
    assert "命令与诊断技术详情" not in source
    assert "操作结果" in source
    assert "当前任务" not in source[source.index("QWidget *MemGuardUkccPlugin::pluginUi") :]
    assert "处理进度" in source
    assert "结果位置" in source
    assert "operationGuide_" in source
    assert "updateOperationGuide" in source
    assert "日常操作" in source
    assert "完整检查" not in source
    assert "导出快速报告" in source
    assert "日志、内核保护和系统授权放在【管理员】页" in source
    assert "操作历史" in source
    assert "资源快照" in source
    assert "更多状态" in source
    assert "查看组件、资源和操作历史" in source
    assert "overviewDetailsToggle_" in source
    assert "overviewDetailsPanel_->setVisible(false)" in source
    assert "QProgressBar" in source
    assert "QScrollArea" in source
    assert "setTextVisible(false)" in source
    assert "readinessValueLabel_" in source
    assert "日常使用" in source
    assert "管理员" in source
    assert "打开文件管理器" in source
    assert "查看遗忘入口" in source
    assert "服务健康" in source
    assert "普通用户常用入口" in source
    assert "任务入口" not in source
    assert "普通用户" in source
    assert "文件安全" in source
    assert "已保护敏感内容" in source
    assert "遗忘指定记忆" in source
    assert "处理建议" in source
    assert "recommendations" in source
    assert "addRecommendationRow" in source
    assert "handleRecommendationAction" in source
    assert "setItemWidget" in source
    assert "立即检查 SDK" not in source
    assert "检查本机智能检索" in source
    assert "端侧 SDK 建议确认" not in source
    assert "首次检查可能较慢" not in source
    assert "首次 Kylin SDK 检查可能较慢" not in source
    assert "正在执行操作，请稍候" not in source
    assert "校验操作记录" in source
    assert "去服务控制" in source
    assert "setCurrentIndex(serviceTabIndex_)" in source
    assert "operationStartMessage" in source
    assert "operationCompleteMessage" in source
    assert "showDetailsOutput" in source
    assert "cancelOperationButton_" in source
    assert "停止当前操作" in source
    assert "startedAtMs" not in source
    assert "耗时" not in source
    assert "QDateTime" not in source
    assert "HH:mm:ss" not in source
    assert 'QStringLiteral("时间")' not in source
    assert "服务日志已显示在【系统维护】" in source
    assert "summaryDescription_->setText(message)" in source
    assert 'runMemctl({QStringLiteral("diagnostics")}, QStringLiteral("export"))' in source
    assert "保存 → 理解 → 使用 → 遗忘" in source
    assert "服务运行" in source
    assert "桌面集成" in source
    assert "setStatusBadge" in source
    assert "makeOperationButton" in source
    assert "setIconSize(QSize(22, 22))" in source
    assert "QFrame" in source
    assert "QPalette::WindowText" in source
    assert "服务控制" in source
    assert "本机智能检索" in source
    assert "安全审计" in source
    assert "维护工具" in source
    assert "报告与控制台" in source
    assert "系统维护说明" in source
    assert "adminGrid->addWidget(serviceBox, 0, 0)" in source
    assert "adminGrid->addWidget(sdkBox, 0, 1)" in source
    assert "maintenanceGrid->addWidget(reportBox, 0, 0)" in source
    assert "maintenanceGrid->addWidget(maintenanceBox, 0, 1)" in source
    assert "openkylin-status" in source
    assert "--fast" in source
    assert "管理员授权" in source
    assert "appendHistory" in source
    assert "renderResourceSnapshot" in source
    assert "formatMaintenanceResult" in source
    assert "formatResourceValue" in source
    assert "details_->setPlainText(formatMaintenanceResult" in source
    assert "CPU 使用" in source
    assert "CPU 配额" in source
    assert "CPU 统计" not in source
    assert "QJsonDocument(cpu.value(QStringLiteral(\"cpu_stat\")).toObject()).toJson" not in source
    assert "capability_state" in source
    assert "user_message" in source
    assert "next_action" in source
    assert "installed_unwarmed" in source
    assert "recently_ready" in source
    assert "inference_ready" in source
    assert "installed_unwarmed || recently_ready || inferenceReady" in source
    assert "这不是故障" in source
    assert "未提前准备" in source
    assert "未执行推理探针" not in source
    assert "预热本机智能检索" in source
    assert "启动服务" in source
    assert "停止服务" in source
    assert "重启服务" in source
    assert "查看服务日志" in source
    assert "系统维护" in source
    assert "导出系统报告" in source
    assert "导出本机证据" in source
    assert "验收证据" not in source[source.index("QWidget *MemGuardUkccPlugin::pluginUi") :]
    assert "collect-openkylin-evidence" in source
    assert "报告保存已取消，系统状态未改变" in source
    assert "检查内核保护状态" in source
    assert "native-action" in source
    assert "setStyleSheet" not in source
    assert "QPalette" in source


def test_ukui_control_center_has_spacious_native_layout_tokens():
    root = Path(__file__).resolve().parents[1]
    source = (root / "native/ukui/ukcc-memguard/memguard_ukcc_plugin.cpp").read_text(encoding="utf-8")

    assert "applyPageSpacing" in source
    assert "applyPanelSpacing" in source
    assert "setContentsMargins(18, 16, 18, 18)" in source
    assert "setSpacing(14)" in source
    assert "setHorizontalSpacing(12)" in source
    assert "setVerticalSpacing(12)" in source
    assert "setColumnStretch(0, 1)" in source
    assert "setColumnStretch(1, 1)" in source
    assert "QSizePolicy::Expanding" in source
    assert "makeSectionHeader" in source
    assert "makeDisclosureButton" in source
    assert "日常使用概览" in source
    assert "常用操作" in source
    assert "管理员控制台" in source
    assert "维护与报告" in source
    assert "setFrameShape(QFrame::NoFrame)" in source
    assert "setMinimumHeight(44)" in source
    assert "setMaximumHeight(52)" in source
    assert "setMaximumWidth(220)" in source
    assert "当前任务：" not in source[source.index("QWidget *MemGuardUkccPlugin::pluginUi") :]
    assert "处理进度：" in source


def test_ukui_control_center_does_not_block_user_actions_on_background_refresh():
    root = Path(__file__).resolve().parents[1]
    source = (root / "native/ukui/ukcc-memguard/memguard_ukcc_plugin.cpp").read_text(encoding="utf-8")
    header = (root / "native/ukui/ukcc-memguard/memguard_ukcc_plugin.h").read_text(encoding="utf-8")

    assert 'process->setProperty("purpose", purpose)' in source
    assert 'activePurpose == QStringLiteral("refresh")' in source
    assert "已让位给你刚选择的操作" in source
    assert 'setBusy(true, operationStartMessage(arguments, purpose), purpose != QStringLiteral("refresh"))' in source
    assert "button->setEnabled(!(busy && lockActions))" in source
    assert "void setBusy(bool busy, const QString &message, bool lockActions = true)" in header
    assert "最近 50 条服务日志" in source
    assert 'QStringLiteral("-n"), QStringLiteral("50")' in source


def test_ukui_control_center_formats_service_logs_for_product_ui():
    root = Path(__file__).resolve().parents[1]
    source = (root / "native/ukui/ukcc-memguard/memguard_ukcc_plugin.cpp").read_text(encoding="utf-8")

    assert "formatServiceLogResult" in source
    assert "looksLikeServiceLog" in source
    assert "最近服务日志" in source
    assert "QRegularExpression(QStringLiteral(\"^\\\\w{3}\\\\s+\\\\d{1,2}\\\\s+\\\\d{2}:\\\\d{2}:\\\\d{2}" in source
    assert "return text.isEmpty() ? summary : text;" not in source


def test_privileged_actions_are_fixed_and_report_structured_result(monkeypatch):
    calls = []
    monkeypatch.setattr("memguard.os_guard.privileged_actions.shutil.which", lambda name: "/usr/bin/pkexec" if name == "pkexec" else None)

    class Completed:
        returncode = 0
        stdout = "service started\n"
        stderr = ""

    def fake_run(command, **kwargs):
        calls.append((command, kwargs))
        return Completed()

    monkeypatch.setattr("memguard.os_guard.privileged_actions.subprocess.run", fake_run)

    result = run_privileged_action("start-service")

    assert set(ALLOWED_ACTIONS) >= {"start-service", "stop-service", "restart-service", "install-auditd-rules", "run-bpf-lsm-test"}
    assert result["ok"] is True
    assert result["action"] == "start-service"
    assert calls[0][0] == ["/usr/bin/pkexec", "/usr/lib/memguardos/memguardos-native-helper.sh", "start-service"]


def test_native_evidence_collector_installs_and_records_polkit_assets():
    root = Path(__file__).resolve().parents[1]
    collector = (root / "scripts/collect_openkylin_native_evidence.sh").read_text(encoding="utf-8")
    readiness = (root / "scripts/check_competition_ready.py").read_text(encoding="utf-8")

    assert "install -m 0644 packaging/polkit/org.memguardos.policy" in collector
    assert "install -m 0755 scripts/memguardos-native-helper.sh" in collector
    assert "policy_installed=true" in collector
    assert "helper_installed=true" in collector
    assert "policy_install_blocked=readonly_usr" in collector
    assert "policy_staged=true" in collector
    assert "helper_staged=true" in collector
    assert "unknown_action_rejected=true" in collector
    assert "Polkit policy 交付状态" in readiness
    assert "Polkit helper 交付状态" in readiness


def test_native_evidence_readiness_parses_polkit_result_lines_not_command_echoes(tmp_path):
    evidence = tmp_path / "openkylin_native_latest"
    evidence.mkdir()
    for name in (
        "source_revision.json",
        "environment.txt",
        "openkylin_status.txt",
        "openkylin_status_probe_sdk.txt",
        "peony_batch_preview.json",
        "peony_batch_apply.json",
        "peony_sensitive_preview.json",
        "systemd_cgroup.txt",
        "tray_assets.txt",
        "desktop_build.txt",
        "debian_packages.txt",
        "deb_lifecycle.txt",
        "pytest_openkylin.txt",
        "manifest.json",
        "SHA256SUMS.txt",
    ):
        (evidence / name).write_text("placeholder\n", encoding="utf-8")
    (evidence / "source_revision.json").write_text(
        json.dumps(
            {
                "schema": "memguardos-source-revision-v1",
                "source_revision": "abc123",
                "source_mode": "git_archive",
                "dirty": False,
            }
        ),
        encoding="utf-8",
    )
    (evidence / "polkit_assets.txt").write_text(
        "\n".join(
            [
                'test -s "${POLICY_TARGET}" && echo policy_installed=true || echo policy_installed=false',
                'test -x "${HELPER_TARGET}" && echo helper_installed=true || echo helper_installed=false',
                "policy_install_blocked=readonly_usr",
                "policy_staged=true",
                "helper_staged=true",
                "unknown_action_rejected=true",
                "policy_installed=false",
                "helper_installed=false",
            ]
        )
        + "\n",
        encoding="utf-8",
    )

    checks = check_native_openkylin_evidence(evidence, expected_revision="abc123", severity="P0")
    by_name = {check.name: check for check in checks}

    assert by_name["Polkit policy 交付状态"].ok is True
    assert by_name["Polkit policy 交付状态"].detail == "readonly /usr with staged policy"
    assert by_name["Polkit helper 交付状态"].ok is True
    assert by_name["Polkit helper 交付状态"].detail == "readonly /usr with staged helper"


def test_privileged_action_rejects_unknown_action_without_execution(monkeypatch):
    monkeypatch.setattr(
        "memguard.os_guard.privileged_actions.subprocess.run",
        lambda *args, **kwargs: (_ for _ in ()).throw(AssertionError("must not execute")),
    )

    result = run_privileged_action("shell-command")

    assert result["ok"] is False
    assert result["error_code"] == "unsupported_action"


def test_native_helper_has_strict_argument_and_environment_guards():
    root = Path(__file__).resolve().parents[1]
    helper = (root / "scripts/memguardos-native-helper.sh").read_text(encoding="utf-8")
    policy = (root / "packaging/polkit/org.memguardos.policy").read_text(encoding="utf-8")

    assert 'if [ "$#" -ne 1 ]' in helper
    assert "MEMGUARD_PROJECT_ROOT" not in helper
    assert "PATH='/usr/sbin:/usr/bin:/sbin:/bin'" in helper
    for action in ("start-service", "stop-service", "restart-service"):
        assert action in helper
        assert f"org.memguardos.{action}" in policy


def test_ukui_tray_follows_system_theme_and_deduplicates_notifications():
    root = Path(__file__).resolve().parents[1]
    source = (root / "native/ukui/tray/memguard_tray.cpp").read_text(encoding="utf-8")
    desktop = (root / "packaging/desktop/memguardos-tray.desktop").read_text(encoding="utf-8")
    build = (root / "scripts/build_ukui_plugins.sh").read_text(encoding="utf-8")

    assert "QSystemTrayIcon" in source
    assert "openkylin-status" in source
    assert "--fast" in source
    assert "void openControlCenter()" in source
    assert "openControlCenter();" in source
    assert "QIcon::fromTheme" in source
    assert "lastState_" in source
    assert "showMessage" in source
    assert "recommendations" in source
    assert "firstRecommendation" in source
    assert "下一步" in source
    assert "showMessage" in source
    assert "openControlCenter(QStringLiteral(\"sdk\"))" in source
    assert "openControlCenter(QStringLiteral(\"audit\"))" in source
    assert "openDashboard(QStringLiteral(\"#audit\"))" in source
    assert "connect(openDashboardAction, &QAction::triggered, this, [this]() { openDashboard(); });" in source
    assert "查看已保护内容" in source
    assert "setStyleSheet" not in source
    assert "QSystemTrayIcon::isSystemTrayAvailable" in source
    assert "Exec=/usr/bin/memguardos-tray" in desktop
    assert "OnlyShowIn=UKUI;" in desktop
    assert "tray" in build


def test_ukui_control_center_keeps_user_ui_product_facing():
    root = Path(__file__).resolve().parents[1]
    source = (root / "native/ukui/ukcc-memguard/memguard_ukcc_plugin.cpp").read_text(encoding="utf-8")

    assert "普通用户常用入口" in source
    assert "管理员工具" in source
    assert "系统维护" in source
    assert "JSON" not in source[source.index("QWidget *MemGuardUkccPlugin::pluginUi") : source.index("void MemGuardUkccPlugin::renderStatus")]
    assert "栈" not in source
    assert "调试" not in source
    assert "当前没有需要处理的建议" in source
    assert "不影响日常记忆检索" in source


def test_desktop_user_copy_uses_plain_product_language():
    root = Path(__file__).resolve().parents[1]
    ukcc = (root / "native/ukui/ukcc-memguard/memguard_ukcc_plugin.cpp").read_text(encoding="utf-8")
    peony = (root / "native/ukui/peony-memguard/memguard_peony_plugin.cpp").read_text(encoding="utf-8")
    tray = (root / "native/ukui/tray/memguard_tray.cpp").read_text(encoding="utf-8")

    overview = ukcc[ukcc.index("QWidget *MemGuardUkccPlugin::buildOverviewTab") : ukcc.index("QWidget *MemGuardUkccPlugin::buildServiceTab")]
    busy_copy = ukcc[ukcc.index("void MemGuardUkccPlugin::updateOperationGuide") : ukcc.index("void MemGuardUkccPlugin::renderStatus")]

    for user_surface in (overview, busy_copy, peony, tray):
        for awkward_term in (
            "端侧向量",
            "fallback",
            "审计链",
            "memguardd",
            "仅审计",
            "立即遗忘",
            "阻断记录",
            "敏感阻断",
        ):
            assert awkward_term not in user_surface

    assert "本机智能检索" in overview
    assert "操作记录完整性" in overview
    assert "已保护敏感内容" in overview
    assert "遗忘指定记忆" in overview
    assert "本机智能检索" in tray
    assert "查看操作记录" in tray
    assert "查看已保护内容" in tray
    assert "可保存内容" in peony
    assert "只记录选择" in peony
    assert "撤销本次导入" in peony


