from pathlib import Path
from zipfile import ZIP_DEFLATED, ZipFile
import hashlib
import json
import os
import shutil
import stat
import subprocess
import sys

from scripts.inspect_submission_package import REQUIRED_PATHS, inspect_package
from scripts.package_submission import COMPETITION_ROLES, ROOT, classify_file, should_skip
from scripts.refresh_evidence_manifest import refresh_evidence_manifest
from scripts.smoke_submission_archive import command_name
from scripts.check_competition_ready import (
    check_deb_install_evidence,
    check_eval,
    IGNORED_HYGIENE_PARTS,
    check_memory_value_evidence,
    check_native_evidence_revision,
    check_openkylin_metrics,
    check_performance_evidence,
    check_real_agent_extension_evidence,
    check_real_agent_holdout_evidence,
    check_security_scan_artifacts,
    check_supply_chain_artifacts,
)
import scripts.check_competition_ready as readiness
import scripts.build_deb_packages as deb_packages
import scripts.package_submission as package_submission
from scripts.generate_sbom import generate_sbom
from scripts.build_deb_packages import (
    DebianPackageBuilder,
    build_package_plan,
    validate_no_overlap,
)


def test_competition_ready_hygiene_ignores_local_output_workspaces():
    assert "outputs" in IGNORED_HYGIENE_PARTS
    metadata = (ROOT / "pyproject.toml").read_text(encoding="utf-8")
    assert 'requires-python = ">=3.10"' in metadata


def test_submission_packager_excludes_runtime_and_nested_worktree_outputs():
    excluded = {
        ".playwright-cli",
        ".venv-agent",
        ".worktrees",
        "artifacts",
        "build",
        "node_modules",
        "output",
        "outputs",
    }
    assert excluded <= package_submission.EXCLUDED_DIRS
    for directory in excluded:
        candidate = package_submission.ROOT / directory / "runtime.bin"
        assert package_submission.should_skip(candidate) is True


def test_submission_packager_cli_can_build_only_the_competition_archive():
    args = package_submission._parser().parse_args(
        ["--kind", "competition", "--timestamp", "20260727_000000"]
    )
    assert args.kind == "competition"
    assert args.timestamp == "20260727_000000"


def test_formal_doc_consistency_rejects_previously_current_test_counts(monkeypatch, tmp_path):
    (tmp_path / "README.md").write_text(
        "历史口径包括 341 passed、344 passed、527 passed 和 636 passed。\n",
        encoding="utf-8",
    )
    monkeypatch.setattr(readiness, "ROOT", tmp_path)

    checks = readiness.check_formal_doc_consistency()
    result = next(item for item in checks if item.name == "正式文档测试数字口径")

    assert result.ok is False
    assert "README.md:341 passed" in result.detail
    assert "README.md:344 passed" in result.detail
    assert "README.md:527 passed" in result.detail
    assert "README.md:636 passed" in result.detail


def test_formal_doc_consistency_does_not_hardcode_current_test_counts(monkeypatch, tmp_path):
    monkeypatch.setattr(readiness, "ROOT", tmp_path)

    checks = readiness.check_formal_doc_consistency()
    result = next(item for item in checks if item.name == "正式文档测试数字口径")

    assert result.ok is True
    assert result.detail == "正式材料未引用已知过期测试数字"


def test_split_debian_package_plan_has_enterprise_ownership_boundaries():
    plan = build_package_plan(version="0.1.0-2", architecture="amd64", multiarch="x86_64-linux-gnu")

    assert set(plan) == {
        "memguardos",
        "memguardos-core",
        "memguardos-ukui",
        "memguardos-agent-bridge",
        "memguardos-doc",
    }
    assert plan["memguardos"]["architecture"] == "all"
    assert plan["memguardos-doc"]["architecture"] == "all"
    assert "memguardos-core (= 0.1.0-2)" in plan["memguardos-ukui"]["depends"]
    assert "memguardos-core (= 0.1.0-2)" in plan["memguardos-agent-bridge"]["depends"]
    assert plan["memguardos"]["files"] == []
    assert "/usr/bin/memctl" in plan["memguardos-core"]["files"]
    assert "/usr/bin/memguardos-tray" in plan["memguardos-ukui"]["files"]
    assert "/usr/share/applications/memguardos-center.desktop" in plan["memguardos-ukui"]["files"]
    assert "/lib/systemd/system/memguard-agent@.service" in plan["memguardos-agent-bridge"]["files"]
    assert "/usr/lib/memguardos/memguardos-agent-launcher.sh" in plan["memguardos-agent-bridge"]["files"]
    assert "/usr/bin/memguard-agent-showcase" in plan["memguardos-agent-bridge"]["files"]
    assert "/usr/bin/memguard-agent-setup" in plan["memguardos-agent-bridge"]["files"]
    assert "/usr/share/doc/memguardos-doc/README.md" in plan["memguardos-doc"]["files"]
    assert validate_no_overlap(plan) == []
    assert (ROOT / "scripts/verify_deb_lifecycle.sh").exists()


def test_debian_packages_apply_openkylin_native_install_lifecycle():
    builder = (ROOT / "scripts/build_deb_packages.py").read_text(encoding="utf-8")
    menu = (ROOT / "packaging/desktop/memguardos-center.desktop").read_text(encoding="utf-8")

    assert "systemctl enable --now memguardd.service" in builder
    assert "systemctl --global disable memguard-kylin-warmup.service" in builder
    assert "systemctl --global enable memguard-kylin-warmup.service" in builder
    assert 'stage / "opt/memguardos/README.md"' in builder
    assert "用户数据保留" in builder
    assert "update-desktop-database" in builder
    assert "xdg-desktop-menu forceupdate" in builder
    assert "gtk-update-icon-cache" in builder
    assert "peony -q" in builder
    assert "pkill -f '^/usr/bin/ukui-control-center" in builder
    installer = (ROOT / "scripts/install_ukui_plugins.sh").read_text(encoding="utf-8")
    assert "pkill -f '^/usr/bin/ukui-control-center" in installer
    assert "hashlib.md5(path.read_bytes(), usedforsecurity=False)" in builder
    assert "rm -rf /var/lib/memguardos" not in builder
    assert "rm -rf /opt/memguardos/data" not in builder
    assert "Name=MemGuardOS 记忆安全" in menu
    assert "Exec=ukui-control-center" in menu
    assert "Categories=Settings;Security;" in menu


def test_debian_package_set_has_real_apt_install_gate():
    installer = (ROOT / "scripts/install_deb_package_set.sh").read_text(encoding="utf-8")
    collector = (ROOT / "scripts/collect_openkylin_native_evidence.sh").read_text(encoding="utf-8")
    assert "apt-get install -y --reinstall" in installer
    assert "package_count=${#PACKAGE_PATHS[@]}" in installer
    assert "systemctl enable --now memguardd.service" in installer
    assert "apt_install=ok" in installer
    assert "install_deb_package_set.sh --no-build" in collector


def test_debian_manifest_paths_survive_project_directory_move(tmp_path, monkeypatch):
    root = tmp_path / "original-project"
    output = root / "dist"
    output.mkdir(parents=True)
    builder = DebianPackageBuilder(root, output, root / "build", "1.0.1-4", "amd64", "x86_64-linux-gnu")

    def build(command, **kwargs):
        assert command[:3] == ["dpkg-deb", "--build", "--root-owner-group"]
        Path(command[-1]).write_bytes(b"isolated package payload")

    monkeypatch.setattr(deb_packages.subprocess, "run", build)
    builder._build_one("memguardos-core", root / "build")
    row = builder.outputs[0]
    assert row["path"] == "dist/memguardos-core_1.0.1-4_amd64.deb"
    moved = tmp_path / "another-machine-project"
    shutil.copytree(root, moved)
    assert hashlib.sha256((moved / row["path"]).read_bytes()).hexdigest() == row["sha256"]
    deb_packages._write_sha256(moved / "SHA256SUMS", [moved / row["path"]])
    assert row["sha256"] in (moved / "SHA256SUMS").read_text()


def test_agent_showcase_prefers_installed_product_and_guides_first_run_setup():
    launcher = (ROOT / "scripts/memguard-agent-showcase.sh").read_text(encoding="utf-8")
    assert 'for candidate in /opt/memguardos "${HOME}/memguardos"' in launcher
    assert "memguard-agent-setup" in launcher
    assert "agent-venv/bin/python" in launcher
    assert 'WORKSPACE="$(cd "${WORKSPACE}" && pwd -P)"' in launcher
    assert 'cd "${SOURCE_ROOT}"' in launcher
    assert "export PYTHONNOUSERSITE=1" in launcher
    assert "OPENHANDS_SUPPRESS_BANNER=1" in launcher
    assert "LITELLM_LOCAL_MODEL_COST_MAP=True" in launcher


def test_installed_python_wrappers_are_isolated_from_callers_working_directory(tmp_path):
    builder = DebianPackageBuilder(
        root=ROOT,
        output_dir=tmp_path / "dist",
        build_root=tmp_path / "build",
        version="0.1.0-2",
        architecture="amd64",
        multiarch="x86_64-linux-gnu",
    )
    wrapper = tmp_path / "memguard"
    builder._write_wrapper(wrapper, "memguard.cli.memguard")
    content = wrapper.read_text(encoding="utf-8")

    assert "export PYTHONPATH=/opt/memguardos" in content
    assert "export PYTHONNOUSERSITE=1" in content
    assert "unset PYTHONHOME" in content
    assert "cd /opt/memguardos" in content
    assert "python3 -m memguard.cli.memguard" in content


def test_agent_setup_supports_installed_product_and_user_scoped_runtime():
    setup = (ROOT / "scripts/install_agent_extras_openkylin.sh").read_text(encoding="utf-8")

    assert "PROJECT_ROOT=/opt/memguardos" in setup
    assert "XDG_DATA_HOME" in setup
    assert "python3.12 python3" in setup
    assert "memguard-agent-showcase [工作目录]" in setup


def test_agent_bridge_package_migrates_only_the_known_legacy_systemd_copy(tmp_path):
    builder = DebianPackageBuilder(
        root=ROOT,
        output_dir=tmp_path / "dist",
        build_root=tmp_path / "build",
        version="0.1.0-2",
        architecture="amd64",
        multiarch="x86_64-linux-gnu",
    )
    stage = tmp_path / "stage"
    (stage / "DEBIAN").mkdir(parents=True)

    builder._write_metadata("memguardos-agent-bridge", stage)

    postinst = (stage / "DEBIAN/postinst").read_text(encoding="utf-8")
    assert "/etc/systemd/system/memguard-agent@.service" in postinst
    assert "/opt/memguardos/.venv-agent/bin/python" in postinst
    assert "memguard-agent@.service.memguardos-legacy" in postinst
    assert "cp -p" in postinst
    assert "rm -f" in postinst
    assert "systemctl daemon-reload" in postinst


def test_core_package_removes_only_known_unowned_legacy_runtime_scripts(tmp_path):
    builder = DebianPackageBuilder(
        root=ROOT,
        output_dir=tmp_path / "dist",
        build_root=tmp_path / "build",
        version="0.1.0-2",
        architecture="amd64",
        multiarch="x86_64-linux-gnu",
    )
    stage = tmp_path / "stage"
    (stage / "DEBIAN").mkdir(parents=True)

    builder._write_metadata("memguardos-core", stage)

    postinst = (stage / "DEBIAN/postinst").read_text(encoding="utf-8")
    assert "/opt/memguardos/scripts/collect_openkylin_native_evidence.sh" in postinst
    assert 'dpkg-query -S "$legacy_path"' in postinst
    assert 'rm -f -- "$legacy_path"' in postinst
    assert "rm -rf" not in postinst
    assert "install -d -o memguard -g memguard -m 0700 /var/lib/memguardos" in postinst
    assert "/etc/systemd/system/memguardd.service" in postinst
    assert "/etc/systemd/system/memguardd@.service" in postinst
    assert "memguardd.service.memguardos-legacy" in postinst
    assert "memguardd@.service.memguardos-legacy" in postinst
    assert 'grep -Fqx "$legacy_exec" "$legacy_unit"' in postinst
    assert '! grep -Eq "^User=" "$legacy_unit"' in postinst
    assert "rm -rf /var/lib/memguardos" not in postinst
    assert "~/.memguardos" not in postinst
    assert "/root/.memguardos" not in postinst
    assert "rm -rf /home" not in postinst


def test_debian_staging_permissions_are_normalized_to_policy(monkeypatch, tmp_path):
    stage = tmp_path / "memguardos-core"
    payload_dir = stage / "usr/share/memguardos"
    payload_dir.mkdir(parents=True)
    regular_file = payload_dir / "settings.conf"
    executable_file = stage / "usr/bin/memctl"
    executable_file.parent.mkdir(parents=True)
    regular_file.write_text("setting=true\n", encoding="utf-8")
    executable_file.write_text("#!/bin/sh\n", encoding="utf-8")

    original_stat = Path.stat
    simulated_modes = {
        stage: stat.S_IFDIR | 0o775,
        stage / "usr": stat.S_IFDIR | 0o775,
        stage / "usr/share": stat.S_IFDIR | 0o775,
        payload_dir: stat.S_IFDIR | 0o775,
        stage / "usr/bin": stat.S_IFDIR | 0o775,
        regular_file: stat.S_IFREG | 0o664,
        executable_file: stat.S_IFREG | 0o775,
    }

    def fake_stat(path, *args, **kwargs):
        result = original_stat(path, *args, **kwargs)
        mode = simulated_modes.get(path)
        if mode is None:
            return result
        fields = list(result)
        fields[0] = mode
        return os.stat_result(fields)

    applied_modes = {}

    def record_chmod(path, mode):
        applied_modes[path.relative_to(stage).as_posix() or "."] = mode

    monkeypatch.setattr(Path, "stat", fake_stat)
    monkeypatch.setattr(Path, "chmod", record_chmod)

    deb_packages._normalize_stage_permissions(stage)

    assert applied_modes["."] == 0o755
    assert applied_modes["usr"] == 0o755
    assert applied_modes["usr/share/memguardos/settings.conf"] == 0o644
    assert applied_modes["usr/bin/memctl"] == 0o755


def test_debian_builder_normalizes_permissions_after_metadata_before_build():
    source = (ROOT / "scripts/build_deb_packages.py").read_text(encoding="utf-8")

    metadata_offset = source.index("self._write_metadata(package, stage)")
    normalize_offset = source.index("_normalize_stage_permissions(stage)")
    build_offset = source.index("self._build_one(package, stage)")

    assert metadata_offset < normalize_offset < build_offset


def test_native_evidence_revision_rejects_unknown_and_mismatch(tmp_path):
    expected = "a" * 40
    revision = tmp_path / "source_revision.json"
    revision.write_text(json.dumps({"source_revision": expected, "dirty": False}), encoding="utf-8")
    assert check_native_evidence_revision(tmp_path, expected).ok is True

    revision.write_text(json.dumps({"source_revision": "synchronized_tree_without_git_metadata"}), encoding="utf-8")
    assert check_native_evidence_revision(tmp_path, expected).ok is False

    revision.write_text(json.dumps({"source_revision": "b" * 40, "dirty": False}), encoding="utf-8")
    result = check_native_evidence_revision(tmp_path, expected)
    assert result.ok is False
    assert result.severity == "P0"
    assert "不一致" in result.detail

    ci_result = check_native_evidence_revision(tmp_path, expected, severity="P1")
    assert ci_result.ok is False
    assert ci_result.severity == "P1"
    assert "不一致" in ci_result.detail


def test_native_evidence_revision_allows_generated_readiness_reports(monkeypatch, tmp_path):
    expected = "b" * 40
    revision = tmp_path / "source_revision.json"
    revision.write_text(json.dumps({"source_revision": "a" * 40, "dirty": False}), encoding="utf-8")
    monkeypatch.setattr(
        readiness,
        "_revision_delta_paths",
        lambda _source, _target: [
            "docs/evidence/openkylin/openkylin_status.txt",
            "README.md",
            "docs/testing.md",
            "docs/competition.md",
        ],
    )

    result = readiness.check_native_evidence_revision(tmp_path, expected)

    assert result.ok is True
    assert "追加/更新原生证据" in result.detail


def test_native_evidence_revision_rejects_runtime_changes_after_collection(monkeypatch, tmp_path):
    expected = "b" * 40
    revision = tmp_path / "source_revision.json"
    revision.write_text(json.dumps({"source_revision": "a" * 40, "dirty": False}), encoding="utf-8")
    monkeypatch.setattr(readiness, "_revision_delta_paths", lambda _source, _target: ["memguard/service.py"])

    result = readiness.check_native_evidence_revision(tmp_path, expected)

    assert result.ok is False
    assert "不一致" in result.detail


def test_native_evidence_revision_reads_unquoted_non_ascii_delta_paths():
    source = (ROOT / "scripts/check_competition_ready.py").read_text(encoding="utf-8")

    assert "core.quotePath=false" in source
    assert 'encoding="utf-8"' in source
    assert 'errors="replace"' in source


def test_openkylin_evidence_uses_package_manifest_instead_of_legacy_deb_name():
    collector = (ROOT / "scripts/collect_openkylin_evidence.sh").read_text(encoding="utf-8")
    verifier = (ROOT / "scripts/verify_deb_package_set.sh").read_text(encoding="utf-8")
    source = collector + verifier

    assert 'run_capture "deb_package" bash scripts/verify_deb_package_set.sh' in collector
    assert "memguardos_debian_packages.json" in source
    assert "memguardos_${DEB_VERSION}_$(dpkg --print-architecture).deb" not in source
    assert "package_count=" in source
    assert "offline_extract_doctor=ok" in source
    assert "plugin_ldd_missing=0" in source


def test_deb_package_set_verifier_is_in_release_inventory():
    required = "scripts/verify_deb_package_set.sh"
    inventory = json.loads((ROOT / "packaging/release_manifest.json").read_text(encoding="utf-8"))
    assert required in inventory["required_paths"]


def test_openkylin_evidence_separates_sdk_path_from_latency_budget_fallback():
    source = (ROOT / "scripts/collect_openkylin_evidence.sh").read_text(encoding="utf-8")

    assert "MEMGUARD_KYLIN_RERANK_BUDGET_MS=5000" in source
    assert "hits[0].get('vector_backend') == 'kylin_sdk'" in source
    assert 'run_shell_capture "kylin_retrieval_budget_fallback"' in source
    assert "exceeded latency budget" in source


def test_competition_package_keeps_formal_and_process_materials_separate():
    formal_paths = [
        "memguard/service.py",
        "scripts/install_openkylin.sh",
        "scripts/collect_openkylin_native_evidence.sh",
        "scripts/package_submission.py",
        "native/bpf_lsm/memguard_lsm.bpf.c",
        "packaging/polkit/org.memguardos.policy",
        "docs/architecture.md",
        "docs/agent-integration.md",
        "docs/evidence/openkylin/openkylin_status.txt",
        "docs/evidence/agent/base-benchmark-metrics.json",
        "pyproject.toml",
    ]
    process_paths = [
        "CHANGELOG.md",
        ".github/workflows/ci.yml",
        "CONTRIBUTING.md",
        "CODE_OF_CONDUCT.md",
    ]
    for rel in formal_paths:
        assert classify_file(Path(rel)) in COMPETITION_ROLES, rel
    for rel in process_paths:
        assert classify_file(Path(rel)) not in COMPETITION_ROLES, rel
    assert should_skip(ROOT / "docs/research/sources/arxiv_2512.13564.pdf") is True


def test_competition_package_requires_memory_value_code_data_and_evidence():
    required = {
        "scripts/run_openhands_behavior_suite.py",
        "scripts/collect_openkylin_native_evidence.sh",
        "packaging/polkit/org.memguardos.policy",
        "data/benchmarks/agent_memory_value.jsonl",
        "data/benchmarks/real_agent_behavior.jsonl",
        "data/benchmarks/real_agent_behavior_holdout.jsonl",
        "docs/agent-integration.md",
        "docs/evidence/agent/memory-value-deterministic.json",
        "docs/evidence/agent/memory-value-real-agent.json",
        "docs/evidence/agent/holdout.json",
        "docs/evidence/openkylin/source_revision.json",
        "docs/security_scan.json",
    }

    assert required.issubset(REQUIRED_PATHS)


def test_generate_sbom_records_project_dependencies_and_core_artifacts(tmp_path):
    sbom_path = tmp_path / "sbom.json"
    markdown_path = tmp_path / "sbom.md"

    sbom = generate_sbom(sbom_path=sbom_path, markdown_path=markdown_path)

    assert sbom_path.exists()
    assert markdown_path.exists()
    assert sbom["name"] == "memguardos"
    assert sbom["version"] == "1.0.1"
    assert sbom["schema"] == "memguardos-sbom-v1"
    assert sbom["dependencies"]["runtime"] == []
    assert "pytest>=8" in sbom["dependencies"]["optional"]["dev"]
    assert "openhands-sdk==1.28.1" in sbom["dependencies"]["optional"]["agent-openhands"]
    assert "bandit>=1.7" in sbom["dependencies"]["optional"]["security"]
    assert "pip-audit>=2.7" in sbom["dependencies"]["optional"]["security"]
    artifact_paths = {item["path"] for item in sbom["artifacts"]}
    assert "memguard/service.py" in artifact_paths
    assert "scripts/run_openhands_behavior_suite.py" in artifact_paths
    assert "native/bpf_lsm/memguard_lsm.bpf.c" in artifact_paths
    markdown = markdown_path.read_text(encoding="utf-8")
    assert "MemGuardOS 软件物料清单" in markdown
    assert "运行时依赖" in markdown
    assert "OpenHands 适配依赖" in markdown


def test_competition_ready_requires_supply_chain_artifacts(tmp_path):
    sbom_path = tmp_path / "sbom.json"
    markdown_path = tmp_path / "sbom.md"
    generate_sbom(sbom_path=sbom_path, markdown_path=markdown_path)

    checks = check_supply_chain_artifacts(sbom_path, markdown_path)
    by_name = {item.name: item for item in checks}

    assert by_name["SBOM JSON"].ok is True
    assert by_name["SBOM 中文说明"].ok is True
    assert by_name["SBOM 关键源码覆盖"].ok is True
    assert by_name["SBOM 依赖分组"].ok is True


def test_competition_ready_requires_clean_security_scan_artifacts(tmp_path):
    report_path = tmp_path / "security_scan.json"
    markdown_path = tmp_path / "安全扫描报告.md"
    report_path.write_text(
        json.dumps(
            {
                "schema": "memguardos-security-scan-v1",
                "ok": True,
                "summary": {
                    "bandit_high_count": 0,
                    "bandit_medium_count": 0,
                    "pip_audit_vulnerability_count": 0,
                    "missing_tools": [],
                },
                "tools": {
                    "bandit": {"status": "clean"},
                    "pip_audit": {"status": "clean"},
                },
            }
        ),
        encoding="utf-8",
    )
    markdown_path.write_text("# MemGuardOS 安全扫描报告\n", encoding="utf-8")

    checks = check_security_scan_artifacts(report_path, markdown_path)
    by_name = {item.name: item for item in checks}

    assert by_name["安全扫描 JSON"].ok is True
    assert by_name["安全扫描中文报告"].ok is True
    assert by_name["安全扫描门禁通过"].ok is True

    report = json.loads(report_path.read_text(encoding="utf-8"))
    report["summary"]["bandit_medium_count"] = 1
    report_path.write_text(json.dumps(report), encoding="utf-8")
    checks = check_security_scan_artifacts(report_path, markdown_path)
    by_name = {item.name: item for item in checks}
    assert by_name["安全扫描门禁通过"].ok is False


def test_memory_value_collector_supports_synchronized_tree_without_git_head():
    script = (ROOT / "scripts" / "collect_memory_value_evidence.sh").read_text(
        encoding="utf-8"
    )

    assert "git rev-parse --verify HEAD" in script
    assert "MEMGUARD_SOURCE_REVISION" in script
    assert "synchronized_tree_without_git_metadata" in script
    assert "\\`deterministic/\\`" in script
    assert "\\`real_agent/\\`" in script


def test_refresh_evidence_manifest_rebuilds_checksums_and_manifest(tmp_path):
    evidence = tmp_path / "evidence"
    (evidence / "nested").mkdir(parents=True)
    (evidence / "README.md").write_text("证据说明\n", encoding="utf-8")
    (evidence / "nested" / "result.json").write_text(
        '{"ok": true}\n',
        encoding="utf-8",
    )

    report = refresh_evidence_manifest(evidence)

    assert report["file_count"] == 2
    checksum_lines = (evidence / "SHA256SUMS.txt").read_text(
        encoding="utf-8"
    ).splitlines()
    assert len(checksum_lines) == 2
    assert checksum_lines[0].endswith("  README.md")
    assert checksum_lines[1].endswith("  nested/result.json")
    manifest = json.loads(
        (evidence / "manifest.json").read_text(encoding="utf-8")
    )
    assert manifest["file_count"] == 2
    assert [item["path"] for item in manifest["files"]] == [
        "README.md",
        "nested/result.json",
    ]


def test_submission_archive_smoke_command_names_are_stable():
    assert command_name(["python", "-m", "compileall", "-q", "memguard"]) == "compileall"
    assert command_name(["python", "scripts/web_dashboard_smoke.py"]) == "web_dashboard_smoke.py"
    assert command_name([]) == "unknown"


def test_python_distribution_smoke_is_required_for_release():
    source = (ROOT / "scripts/smoke_python_distribution.py").read_text(encoding="utf-8")
    workflow = (ROOT / ".github/workflows/ci.yml").read_text(encoding="utf-8")
    assert "memguard.resources" in source
    assert "openhands_memory_hook" in source
    assert "smoke_python_distribution.py" in workflow


def test_evidence_sanitizer_runs_before_native_manifest_hashing():
    collector = (ROOT / "scripts/collect_openkylin_native_evidence.sh").read_text(encoding="utf-8")
    sanitize_at = collector.index("scripts/sanitize_evidence_paths.py")
    manifest_at = collector.index("scripts/refresh_evidence_manifest.py")
    assert sanitize_at < manifest_at


def test_evidence_sanitizer_redacts_api_tokens(tmp_path):
    evidence = tmp_path / "evidence"
    evidence.mkdir()
    sample = evidence / "preview.json"
    sample.write_text(
        '{"preview":"OPENAI_API_KEY=sk-test-example1234567890"}\n',
        encoding="utf-8",
    )

    result = subprocess.run(
        [sys.executable, str(ROOT / "scripts" / "sanitize_evidence_paths.py"), str(evidence)],
        cwd=ROOT,
        text=True,
        capture_output=True,
        check=False,
    )

    assert result.returncode == 0, result.stderr
    assert "sk-test-example1234567890" not in sample.read_text(encoding="utf-8")
    assert "<API_TOKEN_REDACTED>" in sample.read_text(encoding="utf-8")


def test_submission_package_inspector_accepts_release_inventory(tmp_path):
    package, manifest = write_submission_fixture(tmp_path)
    report = inspect_package(package, manifest)
    assert report["ok"] is True
    assert report["manifest"]["archive_sha256_ok"] is True


def test_submission_package_inspector_rejects_local_paths(tmp_path):
    package, manifest = write_submission_fixture(
        tmp_path,
        overrides={"README.md": "cd D:\\PrivateWorkspace\\memguardos"},
    )
    report = inspect_package(package, manifest)
    assert report["ok"] is False
    assert report["local_path_hits"]


def test_submission_package_inspector_rejects_forbidden_research_files(tmp_path):
    package, manifest = write_submission_fixture(
        tmp_path,
        extra={"docs/research/raw-paper.pdf": "not a release artifact"},
    )
    report = inspect_package(package, manifest)
    assert "docs/research/raw-paper.pdf" in report["forbidden_hits"]


def test_submission_package_inspector_rejects_weak_agent_evidence(tmp_path):
    path = "docs/evidence/agent/base-benchmark-metrics.json"
    package, manifest = write_submission_fixture(
        tmp_path,
        overrides={path: '{"case_count":200,"failures":["bad"]}\n'},
    )
    report = inspect_package(package, manifest)
    assert any(item["path"] == path for item in report["evidence_content_failures"])


def test_submission_package_inspector_rejects_incomplete_real_agent_evidence(tmp_path):
    path = "docs/evidence/agent/memory-value-real-agent.json"
    package, manifest = write_submission_fixture(
        tmp_path,
        overrides={path: '{"case_count":12,"completed_case_count":11}\n'},
    )
    report = inspect_package(package, manifest)
    assert any(item["path"] == path for item in report["evidence_content_failures"])


def write_submission_fixture(tmp_path, overrides=None, extra=None):
    overrides = overrides or {}
    bodies = {name: overrides.get(name, sample_submission_body(name)) for name in REQUIRED_PATHS}
    bodies.update(extra or {})
    package = tmp_path / "submission.zip"
    manifest = tmp_path / "submission_manifest.json"
    with ZipFile(package, "w", ZIP_DEFLATED) as archive:
        for name, body in sorted(bodies.items()):
            archive.writestr(name, body)
    entries = [
        {
            "path": name,
            "role": classify_file(Path(name)),
            "sha256": hashlib.sha256(body.encode("utf-8")).hexdigest(),
        }
        for name, body in sorted(bodies.items())
    ]
    manifest.write_text(
        json.dumps(
            {
                "archive_sha256": hashlib.sha256(package.read_bytes()).hexdigest(),
                "file_count": len(entries),
                "files": entries,
            }
        ),
        encoding="utf-8",
    )
    return package, manifest


def sample_submission_body(name: str) -> str:
    samples = {
        "docs/evidence/openkylin/pytest_openkylin.txt": "694 passed, 1 skipped in 20.0s\n",
        "docs/evidence/openkylin/source_revision.json": '{"source_revision":"aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa","dirty":false}\n',
        "docs/evidence/performance_latest/eval_result_openkylin_10k_optimized.json": '{"retrieval":{"mean_recall_at_5":1.0},"latency_load":{"p95_latency_ms":10.1,"p99_latency_ms":75.3}}\n',
        "docs/evidence/performance_latest/retrieval-v2-openkylin.json": '{"gate":{"pass":true}}\n',
        "docs/evidence/agent/base-benchmark-metrics.json": '{"case_count":200,"metrics":{"sensitive_leak_rate":0.0},"latency_ms":{"p95":113.966},"failures":[]}\n',
        "docs/evidence/agent/memory-value-deterministic.json": '{"case_count":16,"system_validation":{"passed":16,"failed":0},"failures":[]}\n',
        "docs/evidence/agent/memory-value-real-agent.json": '{"case_count":12,"completed_case_count":12,"unclassified_failure_count":0}\n',
        "docs/evidence/agent/extension-r13-r20.json": '{"case_count":8,"completed_case_count":8,"unclassified_failure_count":0}\n',
        "docs/evidence/agent/holdout.json": '{"case_count":8,"completed_case_count":8,"unclassified_failure_count":0}\n',
        "docs/security_scan.json": '{"ok":true}\n',
    }
    return samples.get(name, "正式发布内容\n")


def test_competition_ready_checks_procedural_memory_metrics(tmp_path):
    eval_json = tmp_path / "eval.json"
    eval_json.write_text(
        """
{
  "preference_and_knowledge_extraction": {"preference_precision": 1.0, "knowledge_precision": 1.0},
  "retrieval": {"mean_recall_at_5": 1.0, "cross_language_recall_at_5": 1.0},
  "sensitive_detection": {"block_f1": 1.0, "label_f1": 1.0},
  "forgetting": {"candidate_precision": 1.0, "candidate_recall": 1.0},
  "conflict_resolution": {"correctness": 1.0},
  "latency_load": {"p95_latency_ms": 10.0},
  "memory_tiers": {"policy_report_ok": true},
  "concurrent_search": {"ok": true},
  "agent_batch_search": {"ok": true},
  "rag_context_budget": {"redaction_enforced": true},
  "procedural_memory": {
    "recall_at_5": 1.0,
    "procedural_subtype_hit_rate": 1.0,
    "hierarchical_graph_layers_ok": true,
    "context_explainability_ok": true
  },
  "dataset": {
    "seed_memories": 59,
    "retrieval_cases": 54,
    "sensitive_cases": 31,
    "forgetting_cases": 29,
    "conflict_cases": 10,
    "graph_cases": 19,
    "quality_cases": 6,
    "procedural_cases": 9
  }
}
""".strip(),
        encoding="utf-8",
    )

    checks = check_eval(eval_json)
    by_name = {item.name: item for item in checks}
    assert by_name["程序性记忆 Recall@5"].ok is True
    assert by_name["程序性记忆子类型命中率"].ok is True
    assert by_name["层次图谱检索三层检查"].ok is True
    assert by_name["RAG 上下文解释性检查"].ok is True
    assert by_name["benchmark 用例规模"].detail == "总用例/种子数 217"


def test_competition_ready_checks_openkylin_metric_evidence(tmp_path):
    evidence = tmp_path / "evidence"
    evidence.mkdir()
    (evidence / "eval_result_openkylin_10k.json").write_text(
        """
{
  "latency_load": {"p95_latency_ms": 10.844, "p99_latency_ms": 106.095},
  "resource_usage": {"process_rss_kb": 149284},
  "procedural_memory": {
    "recall_at_5": 1.0,
    "hierarchical_graph_layers_ok": true,
    "context_explainability_ok": true
  },
  "agent_batch_search": {"p95_latency_ms": 15.427}
}
""".strip(),
        encoding="utf-8",
    )
    (evidence / "sdk_embedding_probe.txt").write_text(
        '$ python -m memguard.cli.memctl sdk-status --probe-embedding\n\n{"embedding":{"inference_ready":true,"embedding_probe":{"dim":768}}}\n',
        encoding="utf-8",
    )
    (evidence / "build_context_explainability.txt").write_text('{"explainability":{"selection_trace":[]}}\n', encoding="utf-8")
    (evidence / "hierarchical_graph_search.txt").write_text(
        '{"layers":{"summary":[],"entity_relation":[],"chunk":[]},"explainability":{"retrieval_path":["summary","entity_relation","chunk"]}}\n',
        encoding="utf-8",
    )
    (evidence / "peony_file_scan_smoke.txt").write_text(
        '{"ok":true,"file_ingest_stored":true,"search_hits":2,"audit_ok":true}\n',
        encoding="utf-8",
    )

    checks = check_openkylin_metrics(evidence, severity="P0")
    assert checks
    assert all(item.ok for item in checks)


def test_competition_ready_checks_optimized_performance_and_ablation(tmp_path):
    evidence = tmp_path / "performance"
    evidence.mkdir()
    (evidence / "eval_result_openkylin_10k_optimized.json").write_text(
        json.dumps(
            {
                "latency_load": {"p95_latency_ms": 10.105, "p99_latency_ms": 75.255},
                "resource_usage": {"process_rss_kb": 130444},
                "agent_batch_search": {"p95_latency_ms": 11.699},
            }
        ),
        encoding="utf-8",
    )
    (evidence / "performance_suite_openkylin.json").write_text(
        json.dumps(
            {
                "concurrent_summary": {
                    "all_ok": True,
                    "worst_p95_latency_ms": 54.381,
                    "worst_p99_latency_ms": 69.783,
                },
                "ablation": {
                    "variants": {
                        "text_only": {"status": "completed", "recall_at_5": 0.8426},
                        "full_no_cache": {"status": "completed", "recall_at_5": 1.0},
                        "full_kylin_sdk": {
                            "status": "completed",
                            "recall_at_5": 1.0,
                            "p95_latency_ms": 279.641,
                            "p99_latency_ms": 1492.348,
                        },
                    }
                },
            }
        ),
        encoding="utf-8",
    )

    checks = check_performance_evidence(evidence, severity="P0")
    by_name = {item.name: item for item in checks}

    assert all(item.ok for item in checks)
    assert by_name["优化后 10k 负载 P95 < 500ms"].detail.startswith("当前 10.1050")
    assert by_name["多轮并发最差 P95 < 500ms"].detail.startswith("当前 54.3810")
    assert by_name["消融实验证明混合检索提升召回"].ok is True
    assert "P99=1492.348" in by_name["Kylin SDK 消融边界已记录"].detail


def test_competition_ready_accepts_current_deb_install_evidence_format(tmp_path):
    evidence = tmp_path / "evidence"
    evidence.mkdir()
    (evidence / "deb_apt_install_maintenance.txt").write_text("正在设置 memguardos (0.1.0) ...\n", encoding="utf-8")
    (evidence / "deb_apt_install_verify.txt").write_text(
        """
Status: install ok installed
Active: active (running)
--- verify audit ---
{"ok": true, "checked": 96}
--- plugin dependency check ---
plugin_ldd_missing=0
""".strip()
        + "\n",
        encoding="utf-8",
    )
    (evidence / "deb_apt_install_sdk_after_fix.txt").write_text(
        """
$ memctl sdk-status --probe-embedding
{"embedding":{"inference_ready":true,"embedding_probe":{"ok":true,"dim":768}}}
""".strip()
        + "\n",
        encoding="utf-8",
    )

    checks = check_deb_install_evidence(evidence, severity="P0")
    assert checks
    assert all(item.ok for item in checks)


def test_competition_ready_requires_complete_memory_value_evidence(tmp_path):
    evidence = tmp_path / "memory_value"
    (evidence / "deterministic").mkdir(parents=True)
    (evidence / "real_agent").mkdir(parents=True)
    (evidence / "deterministic" / "metrics.json").write_text(
        json.dumps(
            {
                "case_count": 16,
                "system_validation": {"passed": 16, "failed": 0},
                "failures": [],
            }
        ),
        encoding="utf-8",
    )
    (evidence / "real_agent" / "metrics.json").write_text(
        json.dumps(
            {
                "case_count": 12,
                "completed_case_count": 12,
                "unclassified_failure_count": 0,
                "memory_system_pass_rate": 0.916667,
                "agent_behavior_pass_rate": 0.75,
            }
        ),
        encoding="utf-8",
    )
    (evidence / "manifest.json").write_text(
        '{"file_count": 2, "files": []}',
        encoding="utf-8",
    )
    (evidence / "SHA256SUMS.txt").write_text("evidence hashes\n", encoding="utf-8")

    checks = check_memory_value_evidence(evidence, severity="P0")
    by_name = {item.name: item for item in checks}

    assert by_name["16 组确定性配对实验"].ok is True
    assert by_name["至少 12 组真实 OpenHands 案例"].ok is True
    assert by_name["真实案例失败均已归因"].ok is True
    assert by_name["真实记忆系统层通过率"].ok is True


def test_competition_ready_rejects_incomplete_memory_value_evidence(tmp_path):
    evidence = tmp_path / "memory_value"
    (evidence / "real_agent").mkdir(parents=True)
    (evidence / "real_agent" / "metrics.json").write_text(
        json.dumps(
            {
                "case_count": 10,
                "completed_case_count": 9,
                "unclassified_failure_count": 1,
                "memory_system_pass_rate": 0.5,
            }
        ),
        encoding="utf-8",
    )

    checks = check_memory_value_evidence(evidence, severity="P0")

    assert any(not item.ok for item in checks)


def test_competition_ready_accepts_real_agent_extension_evidence(tmp_path):
    evidence = tmp_path / "real_agent_extension"
    evidence.mkdir()
    (evidence / "metrics.json").write_text(
        json.dumps(
            {
                "case_count": 8,
                "completed_case_count": 8,
                "memory_system_pass_rate": 1.0,
                "agent_behavior_pass_rate": 1.0,
                "unclassified_failure_count": 0,
                "failed_case_ids": [],
            }
        ),
        encoding="utf-8",
    )
    (evidence / "manifest.json").write_text(
        '{"file_count": 1, "files": []}',
        encoding="utf-8",
    )
    (evidence / "SHA256SUMS.txt").write_text("evidence hashes\n", encoding="utf-8")

    checks = check_real_agent_extension_evidence(evidence, severity="P0")
    by_name = {item.name: item for item in checks}

    assert by_name["R13-R20 新增 8 组真实 OpenHands 案例"].ok is True
    assert by_name["manifest.json"].ok is True
    assert by_name["SHA256SUMS.txt"].ok is True


def test_competition_ready_accepts_real_agent_holdout_evidence(tmp_path):
    evidence = tmp_path / "real_agent_holdout"
    evidence.mkdir()
    (evidence / "metrics.json").write_text(
        json.dumps(
            {
                "case_count": 8,
                "completed_case_count": 8,
                "status_distribution": {"pass": 8},
                "memory_system_pass_rate": 1.0,
                "agent_behavior_pass_rate": 1.0,
                "unclassified_failure_count": 0,
                "failed_case_ids": [],
            }
        ),
        encoding="utf-8",
    )
    (evidence / "manifest.json").write_text(
        '{"file_count": 1, "files": []}',
        encoding="utf-8",
    )
    (evidence / "SHA256SUMS.txt").write_text("evidence hashes\n", encoding="utf-8")

    checks = check_real_agent_holdout_evidence(evidence, severity="P0")
    by_name = {item.name: item for item in checks}

    assert by_name["8 组留出真实 OpenHands 案例"].ok is True
    assert by_name["manifest.json"].ok is True
    assert by_name["SHA256SUMS.txt"].ok is True
