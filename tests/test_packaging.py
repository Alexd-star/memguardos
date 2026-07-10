from pathlib import Path
from zipfile import ZIP_DEFLATED, ZipFile
import json

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
from scripts.generate_sbom import generate_sbom
from scripts.build_deb_packages import build_package_plan, validate_no_overlap


def test_competition_ready_hygiene_ignores_local_output_workspaces():
    assert "outputs" in IGNORED_HYGIENE_PARTS


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
    assert "/usr/share/doc/memguardos-doc/README.md" in plan["memguardos-doc"]["files"]
    assert validate_no_overlap(plan) == []
    assert (ROOT / "scripts/verify_deb_lifecycle.sh").exists()


def test_debian_packages_apply_openkylin_native_install_lifecycle():
    builder = (ROOT / "scripts/build_deb_packages.py").read_text(encoding="utf-8")
    menu = (ROOT / "packaging/desktop/memguardos-center.desktop").read_text(encoding="utf-8")

    assert "systemctl enable --now memguardd.service" in builder
    assert "systemctl --global enable memguard-kylin-warmup.service" in builder
    assert "用户数据保留" in builder
    assert "update-desktop-database" in builder
    assert "xdg-desktop-menu forceupdate" in builder
    assert "gtk-update-icon-cache" in builder
    assert "peony -q" in builder
    assert "pkill -x ukui-control-center" in builder
    assert "rm -rf /var/lib/memguardos" not in builder
    assert "rm -rf /opt/memguardos/data" not in builder
    assert "Name=MemGuardOS 记忆安全" in menu
    assert "Exec=ukui-control-center" in menu
    assert "Categories=Settings;Security;" in menu


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
            "docs/evidence/openkylin_native_latest/openkylin_status.txt",
            "README.md",
            "docs/测试报告.md",
            "docs/competition_ready.json",
            "docs/竞赛就绪自检报告.md",
            "docs/超标准交付评分矩阵.md",
            "docs/真实Agent记忆价值评测报告.md",
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


def test_competition_package_keeps_formal_and_process_materials_separate():
    formal_paths = [
        "memguard/service.py",
        "scripts/install_openkylin.sh",
        "scripts/build_deb.sh",
        "scripts/collect_openkylin_evidence.sh",
        "scripts/collect_openkylin_native_evidence.sh",
        "scripts/collect_agent_evidence.sh",
        "scripts/warmup_kylin_sdk.sh",
        "scripts/memguard-kylin-warmup.service",
        "scripts/memguardos-native-helper.sh",
        "native/bpf_lsm/memguard_lsm.bpf.c",
        "packaging/polkit/org.memguardos.policy",
        "docs/项目介绍总览.md",
        "docs/openKylin原生化适配说明.md",
        "docs/真实Agent接入使用说明.md",
        "docs/真实Agent闭环测试报告.md",
        "docs/交付文档/MemGuardOS中文操作手册.md",
        "docs/交付文档/MemGuardOS中文操作手册.docx",
        "docs/交付文档/MemGuardOS中文操作手册.pdf",
        "docs/赛题符合性说明.md",
        "docs/evidence/latest/pytest.txt",
        "docs/evidence/openkylin_native_latest/openkylin_status.txt",
        "docs/evidence/agent_latest/pytest_openkylin.txt",
        "docs/evidence/agent_latest/benchmark/metrics.json",
        "pyproject.toml",
    ]
    process_paths = [
        "scripts/package_submission.py",
        "scripts/inspect_submission_package.py",
        "scripts/release_preflight.py",
        "scripts/smoke_submission_archive.py",
        "scripts/check_competition_ready.py",
        "scripts/check_docs_links.py",
        "docs/赛题要求逐句符合性核查.md",
        "docs/竞赛就绪自检报告.md",
        "docs/research/智能体记忆论文深度调研与MemGuardOS改进建议.md",
        "CHANGELOG.md",
        ".github/workflows/ci.yml",
        "Dockerfile",
        "Makefile",
        "tests/test_packaging.py",
    ]
    for rel in formal_paths:
        assert classify_file(Path(rel)) in COMPETITION_ROLES, rel
    for rel in process_paths:
        assert classify_file(Path(rel)) not in COMPETITION_ROLES, rel
    assert should_skip(ROOT / "docs/research/sources/arxiv_2512.13564.pdf") is True


def test_competition_package_requires_memory_value_code_data_and_evidence():
    required = {
        "memguard/eval/memory_value.py",
        "memguard/eval/real_agent_behavior.py",
        "scripts/evaluate_memory_value.py",
        "scripts/run_openhands_behavior_suite.py",
        "scripts/collect_memory_value_evidence.sh",
        "scripts/collect_openkylin_native_evidence.sh",
        "scripts/warmup_kylin_sdk.sh",
        "scripts/memguard-kylin-warmup.service",
        "scripts/memguardos-native-helper.sh",
        "memguard/os_guard/openkylin_native.py",
        "memguard/desktop/peony_scan.py",
        "packaging/polkit/org.memguardos.policy",
        "docs/openKylin原生化适配说明.md",
        "data/benchmarks/agent_memory_value.jsonl",
        "data/benchmarks/real_agent_behavior.jsonl",
        "data/benchmarks/real_agent_behavior_holdout.jsonl",
        "docs/真实Agent记忆价值评测报告.md",
        "docs/evidence/memory_value_latest/deterministic/metrics.json",
        "docs/evidence/memory_value_latest/real_agent/metrics.json",
        "docs/evidence/memory_value_latest/manifest.json",
        "docs/evidence/memory_value_latest/SHA256SUMS.txt",
        "docs/evidence/openkylin_native_latest/openkylin_status.txt",
        "docs/evidence/openkylin_native_latest/openkylin_status_probe_sdk.txt",
        "docs/evidence/openkylin_native_latest/kylin_warmup.txt",
        "docs/evidence/openkylin_native_latest/source_revision.json",
        "docs/evidence/openkylin_native_latest/peony_batch_preview.json",
        "docs/evidence/openkylin_native_latest/peony_batch_apply.json",
        "docs/evidence/openkylin_native_latest/peony_sensitive_preview.json",
        "docs/evidence/openkylin_native_latest/tray_assets.txt",
        "docs/evidence/openkylin_native_latest/debian_packages.txt",
        "docs/evidence/openkylin_native_latest/deb_lifecycle.txt",
        "docs/evidence/openkylin_native_latest/pytest_openkylin.txt",
        "docs/evidence/openkylin_native_latest/manifest.json",
        "docs/evidence/openkylin_native_latest/SHA256SUMS.txt",
        "docs/evidence/real_agent_holdout_latest/metrics.json",
        "docs/evidence/real_agent_holdout_latest/manifest.json",
        "docs/evidence/real_agent_holdout_latest/SHA256SUMS.txt",
        "docs/security_scan.json",
        "docs/安全扫描报告.md",
    }

    assert required.issubset(REQUIRED_PATHS)


def test_generate_sbom_records_project_dependencies_and_core_artifacts(tmp_path):
    sbom_path = tmp_path / "sbom.json"
    markdown_path = tmp_path / "sbom.md"

    sbom = generate_sbom(sbom_path=sbom_path, markdown_path=markdown_path)

    assert sbom_path.exists()
    assert markdown_path.exists()
    assert sbom["name"] == "memguardos"
    assert sbom["version"] == "0.1.0"
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


def test_submission_package_inspector_rejects_process_materials(tmp_path):
    package = tmp_path / "submission.zip"
    manifest = tmp_path / "submission_manifest.json"
    names = sorted(REQUIRED_PATHS | {"scripts/release_preflight.py", "scripts/smoke_submission_archive.py"})
    with ZipFile(package, "w", ZIP_DEFLATED) as zf:
        for name in names:
            zf.writestr(name, sample_submission_body(name))
    manifest.write_text(
        '{"files": [%s]}'
        % ",".join('{"path": "%s", "role": "%s"}' % (name, classify_file(Path(name))) for name in names),
        encoding="utf-8",
    )
    report = inspect_package(package, manifest)
    assert report["ok"] is False
    assert "scripts/release_preflight.py" in report["forbidden_hits"]
    assert "scripts/smoke_submission_archive.py" in report["forbidden_hits"]


def test_submission_package_inspector_accepts_required_formal_materials(tmp_path):
    package = tmp_path / "submission.zip"
    manifest = tmp_path / "submission_manifest.json"
    names = sorted(REQUIRED_PATHS)
    with ZipFile(package, "w", ZIP_DEFLATED) as zf:
        for name in names:
            zf.writestr(name, sample_submission_body(name))
    manifest.write_text(
        '{"files": [%s]}'
        % ",".join('{"path": "%s", "role": "%s"}' % (name, classify_file(Path(name))) for name in names),
        encoding="utf-8",
    )
    report = inspect_package(package, manifest)
    assert report["ok"] is True
    assert report["missing_required"] == []
    assert report["forbidden_hits"] == []
    assert report["local_path_hits"] == []


def test_submission_archive_smoke_command_names_are_stable():
    assert command_name(["python", "-m", "compileall", "-q", "memguard"]) == "compileall"
    assert command_name(["python", "scripts/web_dashboard_smoke.py"]) == "web_dashboard_smoke.py"
    assert command_name([]) == "unknown"


def test_submission_package_inspector_rejects_local_paths_in_formal_docs(tmp_path):
    package = tmp_path / "submission.zip"
    manifest = tmp_path / "submission_manifest.json"
    names = sorted(REQUIRED_PATHS)
    with ZipFile(package, "w", ZIP_DEFLATED) as zf:
        for name in names:
            body = "cd D:\\Luo_docs\\竞赛\\研创赛\\memguardos" if name == "README.md" else "x"
            zf.writestr(name, body)
    manifest.write_text(
        '{"files": [%s]}'
        % ",".join('{"path": "%s", "role": "%s"}' % (name, classify_file(Path(name))) for name in names),
        encoding="utf-8",
    )
    report = inspect_package(package, manifest)
    assert report["ok"] is False
    assert report["local_path_hits"] == [{"path": "README.md", "line": 1, "markers": ["D:\\", "Luo_docs"]}]


def test_submission_package_inspector_rejects_weak_sdk_evidence(tmp_path):
    package = tmp_path / "submission.zip"
    manifest = tmp_path / "submission_manifest.json"
    names = sorted(REQUIRED_PATHS)
    with ZipFile(package, "w", ZIP_DEFLATED) as zf:
        for name in names:
            body = sample_submission_body(name)
            if name == "docs/evidence/latest/sdk_embedding_probe.txt":
                body = '{"embedding":{"inference_ready":null,"embedding_probe":null}}\n'
            zf.writestr(name, body)
    manifest.write_text(
        '{"files": [%s]}'
        % ",".join('{"path": "%s", "role": "%s"}' % (name, classify_file(Path(name))) for name in names),
        encoding="utf-8",
    )
    report = inspect_package(package, manifest)
    assert report["ok"] is False
    assert any(item["path"] == "docs/evidence/latest/sdk_embedding_probe.txt" for item in report["evidence_content_failures"])


def test_submission_package_inspector_rejects_weak_agent_evidence(tmp_path):
    package = tmp_path / "submission.zip"
    manifest = tmp_path / "submission_manifest.json"
    names = sorted(REQUIRED_PATHS)
    with ZipFile(package, "w", ZIP_DEFLATED) as zf:
        for name in names:
            body = sample_submission_body(name)
            if name == "docs/evidence/agent_latest/benchmark/metrics.json":
                body = '{"case_count":200,"metrics":{"extraction":{"f1":0.5}},"failures":["bad"]}\n'
            zf.writestr(name, body)
    manifest.write_text(
        '{"files": [%s]}'
        % ",".join('{"path": "%s", "role": "%s"}' % (name, classify_file(Path(name))) for name in names),
        encoding="utf-8",
    )
    report = inspect_package(package, manifest)
    assert report["ok"] is False
    assert any(item["path"] == "docs/evidence/agent_latest/benchmark/metrics.json" for item in report["evidence_content_failures"])


def test_submission_package_inspector_rejects_incomplete_memory_value_evidence(tmp_path):
    package = tmp_path / "submission.zip"
    manifest = tmp_path / "submission_manifest.json"
    names = sorted(REQUIRED_PATHS)
    with ZipFile(package, "w", ZIP_DEFLATED) as zf:
        for name in names:
            body = sample_submission_body(name)
            if name == "docs/evidence/memory_value_latest/real_agent/metrics.json":
                body = '{"case_count":10,"completed_case_count":9,"unclassified_failure_count":1,"memory_system_pass_rate":0.5}\n'
            zf.writestr(name, body)
    manifest.write_text(
        '{"files": [%s]}'
        % ",".join('{"path": "%s", "role": "%s"}' % (name, classify_file(Path(name))) for name in names),
        encoding="utf-8",
    )

    report = inspect_package(package, manifest)

    assert report["ok"] is False
    assert any(
        item["path"] == "docs/evidence/memory_value_latest/real_agent/metrics.json"
        for item in report["evidence_content_failures"]
    )


def sample_submission_body(name: str) -> str:
    if name == "docs/evidence/latest/pytest.txt":
        return "60 passed in 22.65s\n"
    if name == "docs/evidence/agent_latest/pytest_openkylin.txt":
        return "133 passed in 18.96s\n"
    if name == "docs/evidence/agent_latest/benchmark/metrics.json":
        return """
{
  "case_count": 200,
  "metrics": {
    "extraction": {"precision": 1.0, "recall": 1.0, "f1": 1.0},
    "role_pollution_rate": 0.0,
    "sensitive_leak_rate": 0.0,
    "forget_success_rate": 1.0,
    "cross_session_adherence": 1.0
  },
  "latency_ms": {"p95": 115.97},
  "failures": []
}
""".strip()
    if name == "docs/evidence/memory_value_latest/deterministic/metrics.json":
        return '{"case_count":16,"system_validation":{"passed":16,"failed":0},"failures":[]}\n'
    if name == "docs/evidence/memory_value_latest/real_agent/metrics.json":
        return '{"case_count":12,"completed_case_count":12,"unclassified_failure_count":0,"memory_system_pass_rate":0.916667,"agent_behavior_pass_rate":0.75}\n'
    if name == "docs/evidence/real_agent_extension_r13_r20_latest/metrics.json":
        return '{"case_count":8,"completed_case_count":8,"unclassified_failure_count":0,"memory_system_pass_rate":1.0,"agent_behavior_pass_rate":1.0}\n'
    if name == "docs/evidence/real_agent_holdout_latest/metrics.json":
        return '{"case_count":8,"completed_case_count":8,"unclassified_failure_count":0,"memory_system_pass_rate":1.0,"agent_behavior_pass_rate":1.0,"failed_case_ids":[]}\n'
    if name == "docs/security_scan.json":
        return '{"schema":"memguardos-security-scan-v1","ok":true,"summary":{"bandit_high_count":0,"pip_audit_vulnerability_count":0,"missing_tools":[]},"tools":{"bandit":{"status":"warnings"},"pip_audit":{"status":"clean"}}}\n'
    if name == "docs/安全扫描报告.md":
        return "# MemGuardOS 安全扫描报告\n"
    if name == "docs/evidence/latest/sdk_embedding_probe.txt":
        return '$ memctl sdk-status --probe-embedding\n\n{"embedding":{"inference_ready":true,"embedding_probe":{"dim":768}}}\n'
    if name == "docs/evidence/latest/eval_result_openkylin_10k.json":
        return """
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
""".strip()
    if name == "docs/evidence/latest/build_context_explainability.txt":
        return '{"explainability":{"selection_trace":[]}}\n'
    if name == "docs/evidence/latest/hierarchical_graph_search.txt":
        return '{"layers":{"summary":[],"entity_relation":[],"chunk":[]},"explainability":{"retrieval_path":["summary","entity_relation","chunk"]}}\n'
    if name == "docs/evidence/latest/peony_file_scan_smoke.txt":
        return '{"ok":true,"file_ingest_stored":true,"search_hits":2,"audit_ok":true}\n'
    if name == "docs/evidence/latest/deb_apt_install_maintenance.txt":
        return "正在设置 memguardos (0.1.0) ...\n"
    if name == "docs/evidence/latest/deb_apt_install_verify.txt":
        return "Status: install ok installed\nActive: active (running)\nplugin_ldd_missing=0\n+ memctl verify-audit\n{\"ok\": true}\n"
    if name == "docs/evidence/latest/deb_apt_install_sdk_after_fix.txt":
        return "{\"embedding\":{\"inference_ready\": true}}\nembedding.length=768\n"
    return "x"


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

