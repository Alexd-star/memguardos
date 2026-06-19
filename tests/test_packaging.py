from pathlib import Path
from zipfile import ZIP_DEFLATED, ZipFile
import json

from scripts.inspect_submission_package import REQUIRED_PATHS, inspect_package
from scripts.package_submission import COMPETITION_ROLES, ROOT, classify_file, should_skip
from scripts.smoke_submission_archive import command_name
from scripts.check_competition_ready import (
    check_deb_install_evidence,
    check_eval,
    check_openkylin_metrics,
    check_performance_evidence,
)


def test_competition_package_keeps_formal_and_process_materials_separate():
    formal_paths = [
        "memguard/service.py",
        "scripts/install_openkylin.sh",
        "scripts/build_deb.sh",
        "scripts/collect_openkylin_evidence.sh",
        "scripts/collect_agent_evidence.sh",
        "native/bpf_lsm/memguard_lsm.bpf.c",
        "docs/项目介绍总览.md",
        "docs/真实Agent接入使用说明.md",
        "docs/真实Agent闭环测试报告.md",
        "docs/交付文档/MemGuardOS中文操作手册.md",
        "docs/交付文档/MemGuardOS中文操作手册.docx",
        "docs/交付文档/MemGuardOS中文操作手册.pdf",
        "docs/赛题符合性说明.md",
        "docs/evidence/latest/pytest.txt",
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
