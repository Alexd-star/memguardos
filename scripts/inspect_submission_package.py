from __future__ import annotations

from pathlib import Path
from zipfile import ZipFile
import argparse
import json
import re
import sys

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scripts.package_submission import COMPETITION_ROLES, DIST, FORMAL_DOCS, classify_file  # noqa: E402


REQUIRED_PATHS = {
    "README.md",
    "LICENSE",
    "pyproject.toml",
    "memguard/service.py",
    "memguard/api/server.py",
    "memguard/web/dashboard.py",
    "memguard/integrations/openhands_adapter.py",
    "memguard/integrations/openhands_runner.py",
    "memguard/integrations/bridge.py",
    "memguard/store/mmap_store.py",
    "memguard/store/sqlite_meta.py",
    "memguard/retrieval/hybrid.py",
    "memguard/retrieval/kylin_sdk.py",
    "scripts/install_openkylin.sh",
    "scripts/build_deb.sh",
    "scripts/collect_openkylin_evidence.sh",
    "scripts/collect_agent_evidence.sh",
    "scripts/web_dashboard_smoke.py",
    "scripts/memguard-agent@.service",
    "scripts/memguard_agent_bridge.py",
    "scripts/run_openhands_memguard_demo.py",
    "scripts/evaluate_agent_interactions.py",
    "data/benchmarks/agent_interactions.jsonl",
    "scripts/peony_file_scan_smoke.sh",
    "native/bpf_lsm/memguard_lsm.bpf.c",
    "native/ukui/peony-memguard/memguard_peony_plugin.cpp",
    "native/ukui/ukcc-memguard/memguard_ukcc_plugin.cpp",
    "docs/evidence/latest/pytest.txt",
    "docs/evidence/latest/web_dashboard.txt",
    "docs/evidence/latest/peony_file_scan_smoke.txt",
    "docs/evidence/latest/sdk_status.txt",
    "docs/evidence/latest/sdk_embedding_probe.txt",
    "docs/evidence/latest/deb_apt_install_maintenance.txt",
    "docs/evidence/latest/deb_apt_install_verify.txt",
    "docs/evidence/latest/deb_apt_install_sdk_after_fix.txt",
    "docs/evidence/latest/eval_result_openkylin_10k.json",
    "docs/evidence/latest/build_context_explainability.txt",
    "docs/evidence/latest/hierarchical_graph_search.txt",
    "docs/evidence/latest/verify_audit.txt",
    "docs/evidence/latest/manifest.json",
    "docs/evidence/latest/SHA256SUMS.txt",
    "docs/evidence/agent_latest/pytest_openkylin.txt",
    "docs/evidence/agent_latest/benchmark/metrics.json",
    "docs/evidence/agent_latest/session_a_events.json",
    "docs/evidence/agent_latest/real-agent-session-b5_context.json",
    "docs/evidence/agent_latest/interaction_status.json",
    "docs/evidence/agent_latest/agent_bridge_security.txt",
    "docs/evidence/agent_latest/audit_verify.json",
    "docs/evidence/agent_latest/manifest.json",
    "docs/evidence/agent_latest/SHA256SUMS.txt",
} | FORMAL_DOCS

FORBIDDEN_EXACT = {
    "scripts/package_submission.py",
    "scripts/release_preflight.py",
    "scripts/smoke_submission_archive.py",
    "scripts/check_competition_ready.py",
    "scripts/check_docs_links.py",
    "docs/赛题要求逐句符合性核查.md",
    "docs/竞赛就绪自检报告.md",
    "tests/test_packaging.py",
    "CHANGELOG.md",
    "CONTRIBUTING.md",
    "CODE_OF_CONDUCT.md",
    "SECURITY.md",
    "Dockerfile",
    "Makefile",
}

FORBIDDEN_PREFIXES = {
    ".git/",
    ".github/",
    ".devcontainer/",
    ".pytest_cache/",
    ".ruff_cache/",
    ".venv/",
    "__pycache__/",
    "dist/",
    "local_notes/",
}

FORBIDDEN_SUFFIXES = {
    ".pyc",
    ".pyo",
    ".sqlite3",
    ".segment",
    ".log",
    ".tmp",
}

LOCAL_PATH_MARKERS = {
    "D:\\",
    "C:\\Users\\",
    "/home/luo",
    "Luo_docs",
    "26312",
    "192.168.73.128",
}


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    package = Path(args.package) if args.package else latest_competition_package()
    manifest = Path(args.manifest) if args.manifest else inferred_manifest(package)
    report = inspect_package(package, manifest if manifest.exists() else None)
    if args.output:
        Path(args.output).write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0 if report["ok"] else 2


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Inspect a MemGuardOS competition submission package")
    parser.add_argument("--package", default="", help="Submission zip path. Defaults to the latest dist/memguardos_competition_submission_*.zip")
    parser.add_argument("--manifest", default="", help="Optional package manifest JSON path")
    parser.add_argument("--output", default="", help="Optional JSON report path")
    return parser


def latest_competition_package() -> Path:
    candidates = [
        path
        for path in DIST.glob("memguardos_competition_submission_*.zip")
        if not path.name.endswith("_manifest.zip")
    ]
    if not candidates:
        raise SystemExit("no competition submission package found in dist/")
    return max(candidates, key=lambda item: item.stat().st_mtime)


def inferred_manifest(package: Path) -> Path:
    return package.with_name(package.stem + "_manifest.json")


def inspect_package(package: Path, manifest: Path | None = None) -> dict[str, object]:
    with ZipFile(package) as zf:
        names = sorted(name for name in zf.namelist() if not name.endswith("/"))

    name_set = set(names)
    missing_required = sorted(REQUIRED_PATHS - name_set)
    forbidden_hits = sorted(name for name in names if is_forbidden(name))
    bad_roles = sorted(
        ({"path": name, "role": classify_file(Path(name))}
        for name in names
        if classify_file(Path(name)) not in COMPETITION_ROLES),
        key=lambda item: item["path"],
    )
    local_path_hits = scan_formal_docs_for_local_paths(package, names)
    evidence_content_failures = check_evidence_content(package, name_set)
    manifest_report = inspect_manifest(manifest, name_set) if manifest else {"available": False}
    ok = (
        not missing_required
        and not forbidden_hits
        and not bad_roles
        and not local_path_hits
        and not evidence_content_failures
        and bool(manifest_report.get("ok", True))
    )
    return {
        "ok": ok,
        "package": str(package),
        "file_count": len(names),
        "missing_required": missing_required,
        "forbidden_hits": forbidden_hits,
        "bad_roles": bad_roles,
        "local_path_hits": local_path_hits,
        "evidence_content_failures": evidence_content_failures,
        "manifest": manifest_report,
    }


def check_evidence_content(package: Path, name_set: set[str]) -> list[dict[str, object]]:
    failures: list[dict[str, object]] = []
    with ZipFile(package) as zf:
        def text(name: str) -> str:
            return zf.read(name).decode("utf-8", "replace") if name in name_set else ""

        pytest_text = text("docs/evidence/latest/pytest.txt")
        if not re.search(r"\b\d+\s+passed\b", pytest_text) or " failed" in pytest_text:
            failures.append({"path": "docs/evidence/latest/pytest.txt", "reason": "missing clean pytest passed summary"})

        agent_pytest_text = text("docs/evidence/agent_latest/pytest_openkylin.txt")
        if not re.search(r"\b\d+\s+passed\b", agent_pytest_text) or " failed" in agent_pytest_text:
            failures.append({"path": "docs/evidence/agent_latest/pytest_openkylin.txt", "reason": "missing clean openKylin pytest passed summary"})

        agent_metrics = _json_from_text(text("docs/evidence/agent_latest/benchmark/metrics.json"))
        if not agent_metrics:
            failures.append({"path": "docs/evidence/agent_latest/benchmark/metrics.json", "reason": "cannot parse Agent benchmark JSON"})
        else:
            agent_rules = [
                ("case_count", ["case_count"], 200.0, "lower"),
                ("extraction.precision", ["metrics", "extraction", "precision"], 0.95, "lower"),
                ("extraction.recall", ["metrics", "extraction", "recall"], 0.95, "lower"),
                ("extraction.f1", ["metrics", "extraction", "f1"], 0.95, "lower"),
                ("role_pollution_rate", ["metrics", "role_pollution_rate"], 0.0, "upper"),
                ("sensitive_leak_rate", ["metrics", "sensitive_leak_rate"], 0.0, "upper"),
                ("forget_success_rate", ["metrics", "forget_success_rate"], 0.95, "lower"),
                ("cross_session_adherence", ["metrics", "cross_session_adherence"], 0.95, "lower"),
                ("latency_ms.p95", ["latency_ms", "p95"], 500.0, "upper"),
            ]
            for label, keys, threshold, mode in agent_rules:
                value = _nested_get(agent_metrics, keys)
                ok = isinstance(value, (int, float)) and (value <= threshold if mode == "upper" else value >= threshold)
                if not ok:
                    failures.append(
                        {
                            "path": "docs/evidence/agent_latest/benchmark/metrics.json",
                            "reason": "%s violates threshold" % label,
                            "actual": value,
                        }
                    )
            if agent_metrics.get("failures"):
                failures.append(
                    {
                        "path": "docs/evidence/agent_latest/benchmark/metrics.json",
                        "reason": "Agent benchmark contains failures",
                        "actual": agent_metrics.get("failures"),
                    }
                )

        sdk_probe = _json_from_capture(text("docs/evidence/latest/sdk_embedding_probe.txt"))
        if not sdk_probe:
            failures.append({"path": "docs/evidence/latest/sdk_embedding_probe.txt", "reason": "cannot parse SDK probe JSON"})
        else:
            inference_ready = _nested_get(sdk_probe, ["embedding", "inference_ready"])
            probe_dim = _nested_get(sdk_probe, ["embedding", "embedding_probe", "dim"])
            fallback = _nested_get(sdk_probe, ["embedding", "fallback"])
            diagnosis = _nested_get(sdk_probe, ["embedding", "diagnosis"])
            if inference_ready is True and probe_dim != 768:
                failures.append({"path": "docs/evidence/latest/sdk_embedding_probe.txt", "reason": "embedding dim is not 768", "actual": probe_dim})
            if inference_ready is not True and not fallback and not diagnosis:
                failures.append({"path": "docs/evidence/latest/sdk_embedding_probe.txt", "reason": "embedding is not ready and no fallback/diagnosis is documented"})

        eval_10k = _json_from_text(text("docs/evidence/latest/eval_result_openkylin_10k.json"))
        if not eval_10k:
            failures.append({"path": "docs/evidence/latest/eval_result_openkylin_10k.json", "reason": "cannot parse 10k eval JSON"})
        else:
            metric_rules = [
                ("latency_load.p95_latency_ms", ["latency_load", "p95_latency_ms"], 500.0, "upper"),
                ("latency_load.p99_latency_ms", ["latency_load", "p99_latency_ms"], 500.0, "upper"),
                ("resource_usage.process_rss_kb", ["resource_usage", "process_rss_kb"], 256 * 1024, "upper"),
                ("procedural_memory.recall_at_5", ["procedural_memory", "recall_at_5"], 0.90, "lower"),
                ("agent_batch_search.p95_latency_ms", ["agent_batch_search", "p95_latency_ms"], 100.0, "upper"),
            ]
            for label, keys, threshold, mode in metric_rules:
                value = _nested_get(eval_10k, keys)
                ok = isinstance(value, (int, float)) and (value <= threshold if mode == "upper" else value >= threshold)
                if not ok:
                    failures.append({"path": "docs/evidence/latest/eval_result_openkylin_10k.json", "reason": "%s violates threshold" % label, "actual": value})
            for label, keys in [
                ("procedural_memory.hierarchical_graph_layers_ok", ["procedural_memory", "hierarchical_graph_layers_ok"]),
                ("procedural_memory.context_explainability_ok", ["procedural_memory", "context_explainability_ok"]),
            ]:
                if _nested_get(eval_10k, keys) is not True:
                    failures.append({"path": "docs/evidence/latest/eval_result_openkylin_10k.json", "reason": "%s is not true" % label})

        context_text = text("docs/evidence/latest/build_context_explainability.txt")
        if "explainability" not in context_text or "selection_trace" not in context_text:
            failures.append({"path": "docs/evidence/latest/build_context_explainability.txt", "reason": "missing explainability or selection_trace"})

        graph_text = text("docs/evidence/latest/hierarchical_graph_search.txt")
        if not all(token in graph_text for token in ('"summary"', '"entity_relation"', '"chunk"', '"retrieval_path"')):
            failures.append({"path": "docs/evidence/latest/hierarchical_graph_search.txt", "reason": "missing hierarchical graph layers"})

        peony_text = text("docs/evidence/latest/peony_file_scan_smoke.txt")
        peony_smoke = _json_from_capture(peony_text)
        if not peony_smoke:
            failures.append({"path": "docs/evidence/latest/peony_file_scan_smoke.txt", "reason": "cannot parse Peony file scan smoke JSON"})
        else:
            for key in ("ok", "file_ingest_stored", "audit_ok"):
                if peony_smoke.get(key) is not True:
                    failures.append({"path": "docs/evidence/latest/peony_file_scan_smoke.txt", "reason": "%s is not true" % key, "actual": peony_smoke.get(key)})
            if int(peony_smoke.get("search_hits", 0)) < 1:
                failures.append({"path": "docs/evidence/latest/peony_file_scan_smoke.txt", "reason": "search_hits < 1", "actual": peony_smoke.get("search_hits")})

        deb_install = text("docs/evidence/latest/deb_apt_install_maintenance.txt")
        if "正在设置 memguardos" not in deb_install and "memguardos (0.1.0)" not in deb_install:
            failures.append({"path": "docs/evidence/latest/deb_apt_install_maintenance.txt", "reason": "missing apt install setup evidence"})

        deb_verify = text("docs/evidence/latest/deb_apt_install_verify.txt")
        for token, reason in [
            ("Status: install ok installed", "dpkg status is not install ok installed"),
            ("Active: active (running)", "memguardd.service is not active in evidence"),
        ]:
            if token not in deb_verify:
                failures.append({"path": "docs/evidence/latest/deb_apt_install_verify.txt", "reason": reason})
        if "plugin_ldd_missing=0" not in deb_verify:
            failures.append({"path": "docs/evidence/latest/deb_apt_install_verify.txt", "reason": "desktop plugin ldd check is not clean"})
        if '"ok": true' not in deb_verify or ("memctl verify-audit" not in deb_verify and "--- verify audit ---" not in deb_verify):
            failures.append({"path": "docs/evidence/latest/deb_apt_install_verify.txt", "reason": "audit chain verification did not pass after deb install"})

        deb_sdk = text("docs/evidence/latest/deb_apt_install_sdk_after_fix.txt")
        deb_sdk_json = _json_from_capture(deb_sdk)
        inference_ready = _nested_get(deb_sdk_json or {}, ["embedding", "inference_ready"]) is True
        embedding_dim = _nested_get(deb_sdk_json or {}, ["embedding", "embedding_probe", "dim"])
        legacy_embedding_ok = ("inference_ready=true" in deb_sdk or '"inference_ready": true' in deb_sdk) and "embedding.length=768" in deb_sdk
        if not ((inference_ready and embedding_dim == 768) or legacy_embedding_ok):
            failures.append({"path": "docs/evidence/latest/deb_apt_install_sdk_after_fix.txt", "reason": "missing post-install Kylin embedding inference evidence"})
    return failures


def inspect_manifest(manifest: Path, expected_names: set[str]) -> dict[str, object]:
    if not manifest.exists():
        return {"available": False, "ok": False, "missing": str(manifest)}
    data = json.loads(manifest.read_text(encoding="utf-8"))
    entries = data.get("files", [])
    paths = {str(item.get("path", "")) for item in entries if isinstance(item, dict)}
    roles = {str(item.get("role", "")) for item in entries if isinstance(item, dict)}
    missing_in_manifest = sorted(expected_names - paths)
    extra_in_manifest = sorted(paths - expected_names)
    bad_manifest_roles = sorted(role for role in roles if role not in COMPETITION_ROLES)
    return {
        "available": True,
        "ok": not missing_in_manifest and not extra_in_manifest and not bad_manifest_roles,
        "path": str(manifest),
        "file_count": len(paths),
        "missing_in_manifest": missing_in_manifest,
        "extra_in_manifest": extra_in_manifest,
        "bad_roles": bad_manifest_roles,
    }


def is_forbidden(name: str) -> bool:
    parts = name.split("/")
    if name in FORBIDDEN_EXACT:
        return True
    if any(name.startswith(prefix) for prefix in FORBIDDEN_PREFIXES):
        return True
    if "__pycache__" in parts:
        return True
    return any(name.endswith(suffix) for suffix in FORBIDDEN_SUFFIXES)


def scan_formal_docs_for_local_paths(package: Path, names: list[str]) -> list[dict[str, object]]:
    hits: list[dict[str, object]] = []
    formal_docs = [name for name in names if classify_file(Path(name)) == "正式中文文档"]
    with ZipFile(package) as zf:
        for name in formal_docs:
            try:
                text = zf.read(name).decode("utf-8")
            except UnicodeDecodeError:
                continue
            for line_no, line in enumerate(text.splitlines(), start=1):
                matched = sorted(marker for marker in LOCAL_PATH_MARKERS if marker in line)
                if matched:
                    hits.append({"path": name, "line": line_no, "markers": matched})
    return hits


def _json_from_text(text: str) -> dict[str, object] | None:
    if not text:
        return None
    try:
        payload = json.loads(text)
    except json.JSONDecodeError:
        return None
    return payload if isinstance(payload, dict) else None


def _json_from_capture(text: str) -> dict[str, object] | None:
    start = text.find("{")
    if start < 0:
        return None
    decoder = json.JSONDecoder()
    try:
        payload, _ = decoder.raw_decode(text[start:])
    except json.JSONDecodeError:
        return None
    return payload if isinstance(payload, dict) else None


def _nested_get(data: dict[str, object], keys: list[str]) -> object:
    current: object = data
    for key in keys:
        if not isinstance(current, dict) or key not in current:
            return None
        current = current[key]
    return current


if __name__ == "__main__":
    raise SystemExit(main())
