from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
import re
import sys
from zipfile import ZipFile

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scripts.package_submission import COMPETITION_ROLES, DIST, classify_file  # noqa: E402
from scripts.release_inventory import inventory_set, load_release_inventory  # noqa: E402


REQUIRED_PATHS = inventory_set("required_paths")
FORMAL_DOCS = inventory_set("formal_docs")
LOCAL_PATH_MARKERS = set(load_release_inventory()["local_path_markers"])

FORBIDDEN_PREFIXES = {
    ".git/",
    ".pytest_cache/",
    ".ruff_cache/",
    ".venv/",
    ".venv-agent/",
    ".worktrees/",
    "build/",
    "dist/",
    "docs/research/",
    "docs/superpowers/",
    "external_references/",
    "local_notes/",
    "output/",
    "outputs/",
    "项目全流程导师资料包/",
}
FORBIDDEN_SUFFIXES = {".pyc", ".pyo", ".sqlite3", ".segment", ".log", ".tmp"}


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    package = Path(args.package) if args.package else latest_competition_package()
    manifest = Path(args.manifest) if args.manifest else inferred_manifest(package)
    report = inspect_package(package, manifest if manifest.exists() else None)
    payload = json.dumps(report, ensure_ascii=False, indent=2)
    print(payload)
    if args.output:
        Path(args.output).write_text(payload + "\n", encoding="utf-8")
    return 0 if report["ok"] else 2


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="审计 MemGuardOS 可复现提交包")
    parser.add_argument("--package", default="", help="提交 ZIP；默认选择 dist 中最新竞赛包")
    parser.add_argument("--manifest", default="", help="包清单 JSON；默认按 ZIP 文件名推导")
    parser.add_argument("--output", default="", help="可选审计报告 JSON")
    return parser


def latest_competition_package() -> Path:
    candidates = list(DIST.glob("memguardos_competition_submission_*.zip"))
    if not candidates:
        raise SystemExit("dist 中没有竞赛提交包")
    return max(candidates, key=lambda item: item.stat().st_mtime)


def inferred_manifest(package: Path) -> Path:
    return package.with_name(package.stem + "_manifest.json")


def inspect_package(package: Path, manifest: Path | None = None) -> dict[str, object]:
    if not package.exists():
        return {"ok": False, "package": str(package), "error": "package_not_found"}

    with ZipFile(package) as archive:
        if "src/pyproject.toml" in archive.namelist():
            from scripts.final_submission import inspect_final_archive
            return inspect_final_archive(package, manifest)
        names = sorted(name for name in archive.namelist() if not name.endswith("/"))
        name_set = set(names)
        local_path_hits = scan_formal_docs_for_local_paths(archive, name_set)
        evidence_failures = check_evidence_content(archive, name_set)
        content_hashes = {
            name: hashlib.sha256(archive.read(name)).hexdigest() for name in names
        }

    missing_required = sorted(REQUIRED_PATHS - name_set)
    forbidden_hits = sorted(name for name in names if is_forbidden(name))
    bad_roles = sorted(
        {"path": name, "role": classify_file(Path(name))}
        for name in names
        if classify_file(Path(name)) not in COMPETITION_ROLES
    )
    manifest_report = (
        inspect_manifest(manifest, package, name_set, content_hashes)
        if manifest is not None
        else {"available": False, "ok": False, "reason": "manifest_missing"}
    )
    ok = not any(
        (missing_required, forbidden_hits, bad_roles, local_path_hits, evidence_failures)
    ) and bool(manifest_report.get("ok"))
    return {
        "ok": ok,
        "package": str(package),
        "file_count": len(names),
        "missing_required": missing_required,
        "forbidden_hits": forbidden_hits,
        "bad_roles": bad_roles,
        "local_path_hits": local_path_hits,
        "evidence_content_failures": evidence_failures,
        "manifest": manifest_report,
    }


def is_forbidden(name: str) -> bool:
    parts = name.split("/")
    if any(name.startswith(prefix) for prefix in FORBIDDEN_PREFIXES):
        return True
    if "__pycache__" in parts or any(part.endswith(".egg-info") for part in parts):
        return True
    return any(name.endswith(suffix) for suffix in FORBIDDEN_SUFFIXES)


def scan_formal_docs_for_local_paths(
    archive: ZipFile, names: set[str]
) -> list[dict[str, object]]:
    hits: list[dict[str, object]] = []
    for name in sorted(FORMAL_DOCS & names):
        try:
            text = archive.read(name).decode("utf-8")
        except UnicodeDecodeError:
            continue
        for line_no, line in enumerate(text.splitlines(), start=1):
            markers = sorted(marker for marker in LOCAL_PATH_MARKERS if marker in line)
            if markers:
                hits.append({"path": name, "line": line_no, "markers": markers})
    return hits


def check_evidence_content(
    archive: ZipFile, names: set[str]
) -> list[dict[str, object]]:
    failures: list[dict[str, object]] = []

    def text(path: str) -> str:
        return archive.read(path).decode("utf-8", "replace") if path in names else ""

    def payload(path: str) -> dict[str, object] | None:
        try:
            value = json.loads(text(path))
        except (json.JSONDecodeError, TypeError):
            return None
        return value if isinstance(value, dict) else None

    pytest_path = "docs/evidence/openkylin/pytest_openkylin.txt"
    pytest_text = text(pytest_path)
    if not re.search(r"\b\d+\s+passed\b", pytest_text) or re.search(r"\b\d+\s+failed\b", pytest_text):
        failures.append({"path": pytest_path, "reason": "缺少干净的 pytest passed 摘要"})

    source_path = "docs/evidence/openkylin/source_revision.json"
    source = payload(source_path)
    if not source or not source.get("source_revision") or source.get("dirty") is not False:
        failures.append({"path": source_path, "reason": "源修订缺失或工作树非干净状态"})

    eval_path = "docs/evidence/performance_latest/eval_result_openkylin_10k_optimized.json"
    evaluation = payload(eval_path)
    if not evaluation:
        failures.append({"path": eval_path, "reason": "无法解析 10k 评测 JSON"})
    else:
        _require_metric(failures, eval_path, evaluation, ["retrieval", "mean_recall_at_5"], 0.9, "min")
        _require_metric(failures, eval_path, evaluation, ["latency_load", "p95_latency_ms"], 500.0, "max")
        _require_metric(failures, eval_path, evaluation, ["latency_load", "p99_latency_ms"], 500.0, "max")

    retrieval_path = "docs/evidence/performance_latest/retrieval-v2-openkylin.json"
    retrieval = payload(retrieval_path)
    if not retrieval or _nested_get(retrieval, ["gate", "pass"]) is not True:
        failures.append({"path": retrieval_path, "reason": "检索 v2 三轮门禁未通过"})

    agent_path = "docs/evidence/agent/base-benchmark-metrics.json"
    agent = payload(agent_path)
    if not agent or agent.get("case_count") != 200 or agent.get("failures"):
        failures.append({"path": agent_path, "reason": "200 条 Agent 专项基准不完整"})
    elif agent:
        _require_metric(failures, agent_path, agent, ["metrics", "sensitive_leak_rate"], 0.0, "max")
        _require_metric(failures, agent_path, agent, ["latency_ms", "p95"], 500.0, "max")

    deterministic_path = "docs/evidence/agent/memory-value-deterministic.json"
    deterministic = payload(deterministic_path)
    if not deterministic or deterministic.get("case_count") != 16 or _nested_get(deterministic, ["system_validation", "failed"]) != 0:
        failures.append({"path": deterministic_path, "reason": "16 组确定性记忆价值门禁不完整"})

    for path, expected in (
        ("docs/evidence/agent/memory-value-real-agent.json", 12),
        ("docs/evidence/agent/extension-r13-r20.json", 8),
        ("docs/evidence/agent/holdout.json", 8),
    ):
        result = payload(path)
        if not result or result.get("case_count") != expected or result.get("completed_case_count") != expected:
            failures.append({"path": path, "reason": f"真实 Agent 结果应完整包含 {expected} 次运行"})

    security_path = "docs/security_scan.json"
    security = payload(security_path)
    if not security or security.get("ok") is not True:
        failures.append({"path": security_path, "reason": "安全扫描门禁未通过"})
    return failures


def inspect_manifest(
    manifest: Path,
    package: Path,
    expected_names: set[str],
    content_hashes: dict[str, str],
) -> dict[str, object]:
    if not manifest.exists():
        return {"available": False, "ok": False, "path": str(manifest)}
    try:
        data = json.loads(manifest.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        return {"available": True, "ok": False, "path": str(manifest), "error": str(exc)}

    entries = {
        str(item.get("path")): item
        for item in data.get("files", [])
        if isinstance(item, dict) and item.get("path")
    }
    paths = set(entries)
    missing = sorted(expected_names - paths)
    extra = sorted(paths - expected_names)
    bad_roles = sorted(
        path for path, item in entries.items() if item.get("role") not in COMPETITION_ROLES
    )
    bad_hashes = sorted(
        path for path in expected_names & paths
        if entries[path].get("sha256") != content_hashes[path]
    )
    archive_hash = hashlib.sha256(package.read_bytes()).hexdigest()
    archive_hash_ok = data.get("archive_sha256") == archive_hash
    file_count_ok = data.get("file_count") == len(expected_names)
    ok = not missing and not extra and not bad_roles and not bad_hashes and archive_hash_ok and file_count_ok
    return {
        "available": True,
        "ok": ok,
        "path": str(manifest),
        "file_count": len(paths),
        "missing_in_manifest": missing,
        "extra_in_manifest": extra,
        "bad_roles": bad_roles,
        "bad_hashes": bad_hashes,
        "archive_sha256_ok": archive_hash_ok,
        "file_count_ok": file_count_ok,
    }


def _require_metric(
    failures: list[dict[str, object]],
    path: str,
    data: dict[str, object],
    keys: list[str],
    threshold: float,
    mode: str,
) -> None:
    value = _nested_get(data, keys)
    ok = isinstance(value, (int, float)) and (value >= threshold if mode == "min" else value <= threshold)
    if not ok:
        failures.append({"path": path, "reason": ".".join(keys) + " 未达到门槛", "actual": value})


def _nested_get(data: dict[str, object], keys: list[str]) -> object:
    current: object = data
    for key in keys:
        if not isinstance(current, dict) or key not in current:
            return None
        current = current[key]
    return current


if __name__ == "__main__":
    raise SystemExit(main())
