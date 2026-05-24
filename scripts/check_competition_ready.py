from __future__ import annotations

import argparse
import hashlib
import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional


ROOT = Path(__file__).resolve().parents[1]

IGNORED_HYGIENE_PARTS = {
    ".git",
    ".mypy_cache",
    ".pytest_cache",
    ".ruff_cache",
    ".venv",
    "__pycache__",
    "dist",
    "local_notes",
}

REQUIRED_DOCS = [
    "README.md",
    "docs/项目介绍总览.md",
    "docs/概要设计说明书.md",
    "docs/详细设计说明书.md",
    "docs/部署手册.md",
    "docs/用户手册.md",
    "docs/测试报告.md",
    "docs/赛题符合性说明.md",
    "docs/正式提交材料说明.md",
    "docs/评委证据包索引.md",
    "docs/安全设计说明书.md",
    "docs/操作系统机制说明.md",
    "docs/Peony与UKUI桌面集成实现说明.md",
    "docs/麒麟SDK集成证据.md",
    "docs/答辩提纲.md",
    "docs/演示视频脚本.md",
]

REQUIRED_SOURCE_PATHS = [
    "memguard/service.py",
    "memguard/store/mmap_store.py",
    "memguard/store/sqlite_meta.py",
    "memguard/retrieval/hybrid.py",
    "memguard/extraction/semantic.py",
    "memguard/sensitivity/detector.py",
    "memguard/forgetting/planner.py",
    "memguard/api/server.py",
    "memguard/web/dashboard.py",
    "scripts/install_openkylin.sh",
    "scripts/collect_openkylin_evidence.sh",
    "scripts/build_deb.sh",
    "scripts/generate_bpf_lsm_policy.py",
    "scripts/package_submission.py",
    "scripts/web_dashboard_smoke.py",
    "scripts/verify_ukui_plugins.sh",
    "native/bpf_lsm/memguard_lsm.bpf.c",
    "native/ukui/peony-memguard/memguard_peony_plugin.cpp",
    "native/ukui/ukcc-memguard/memguard_ukcc_plugin.cpp",
]

REQUIRED_EVIDENCE = [
    "pytest.txt",
    "eval_1k.txt",
    "eval_10k.txt",
    "sdk_status.txt",
    "sdk_embedding_probe.txt",
    "embedding_cpp_probe.txt",
    "vector_probe.txt",
    "kylin_retrieval.txt",
    "namespace_isolation.txt",
    "systemd_cgroup.txt",
    "systemd_namespace.txt",
    "auditd_ingest.txt",
    "audit_concurrency.txt",
    "verify_audit.txt",
    "bpf_lsm_enforcer.txt",
    "deb_package.txt",
    "ukui_plugin_build.txt",
    "ukui_plugin_install.txt",
    "ukui_plugin_visible_final.txt",
    "ukui_peony_visible_manual_confirmation.txt",
    "ukui_control_center_visible_manual_confirmation.txt",
    "natural_patterns.txt",
]

EVIDENCE_MANIFEST_EXCLUDED = {"manifest.json", "SHA256SUMS.txt", "evidence_manifest.txt"}


@dataclass
class Check:
    category: str
    name: str
    ok: bool
    detail: str
    severity: str = "P0"

    def to_dict(self) -> Dict[str, object]:
        return {
            "category": self.category,
            "name": self.name,
            "ok": self.ok,
            "detail": self.detail,
            "severity": self.severity,
        }


def main() -> int:
    parser = argparse.ArgumentParser(description="Check MemGuardOS competition-readiness gates")
    parser.add_argument("--eval", default="docs/eval_result.json", help="Evaluation JSON path")
    parser.add_argument("--evidence-dir", default="docs/evidence/latest", help="openKylin evidence directory")
    parser.add_argument("--output", default="", help="Optional JSON report path")
    parser.add_argument("--markdown", default="", help="Optional Markdown report path")
    parser.add_argument("--allow-missing-openkylin", action="store_true", help="Downgrade missing openKylin evidence files to P1 warnings")
    args = parser.parse_args()

    eval_path = _resolve(args.eval)
    evidence_dir = _resolve(args.evidence_dir)
    checks = []
    checks.extend(check_docs())
    checks.extend(check_sources())
    checks.extend(check_eval(eval_path))
    checks.extend(check_openkylin_evidence(evidence_dir, allow_missing=args.allow_missing_openkylin))
    checks.extend(check_repository_hygiene())

    failed = [item for item in checks if not item.ok and item.severity == "P0"]
    warnings = [item for item in checks if not item.ok and item.severity != "P0"]
    report = {
        "ok": not failed,
        "summary": {
            "total": len(checks),
            "passed": len([item for item in checks if item.ok]),
            "failed": len(failed),
            "warnings": len(warnings),
        },
        "checks": [item.to_dict() for item in checks],
    }
    text = json.dumps(report, ensure_ascii=False, indent=2)
    print(text)
    if args.output:
        _resolve(args.output).write_text(text + "\n", encoding="utf-8")
    if args.markdown:
        _resolve(args.markdown).write_text(to_markdown(report), encoding="utf-8")
    return 0 if not failed else 2


def check_docs() -> List[Check]:
    checks: List[Check] = []
    for rel in REQUIRED_DOCS:
        path = ROOT / rel
        if not path.exists():
            checks.append(Check("中文文档", rel, False, "文件不存在"))
            continue
        text = path.read_text(encoding="utf-8", errors="replace")
        cjk_count = sum(1 for char in text if "\u4e00" <= char <= "\u9fff")
        checks.append(Check("中文文档", rel, cjk_count >= 80, "中文字符数 %s" % cjk_count))
    return checks


def check_sources() -> List[Check]:
    checks = []
    for rel in REQUIRED_SOURCE_PATHS:
        path = ROOT / rel
        checks.append(Check("源码与脚本", rel, path.exists(), "存在" if path.exists() else "文件不存在"))
    return checks


def check_eval(eval_path: Path) -> List[Check]:
    if not eval_path.exists():
        return [Check("量化评测", str(eval_path.relative_to(ROOT)), False, "评测 JSON 不存在，请先运行 memguard.eval.run_eval")]
    result = json.loads(eval_path.read_text(encoding="utf-8"))
    checks = [
        _metric_check(result, "量化评测", "偏好提取准确率 TP/(TP+FP)", ["preference_and_knowledge_extraction", "preference_precision"], 0.90),
        _metric_check(result, "量化评测", "知识提取准确率 TP/(TP+FP)", ["preference_and_knowledge_extraction", "knowledge_precision"], 0.90),
        _metric_check(result, "量化评测", "知识检索 Recall@5", ["retrieval", "mean_recall_at_5"], 0.90),
        _metric_check(result, "量化评测", "跨语言 Recall@5", ["retrieval", "cross_language_recall_at_5"], 0.80),
        _metric_check(result, "量化评测", "敏感阻断 F1", ["sensitive_detection", "block_f1"], 0.95),
        _metric_check(result, "量化评测", "敏感标签 F1", ["sensitive_detection", "label_f1"], 0.90),
        _metric_check(result, "量化评测", "精准遗忘候选精确率", ["forgetting", "candidate_precision"], 0.90),
        _metric_check(result, "量化评测", "精准遗忘候选召回率", ["forgetting", "candidate_recall"], 0.90),
        _metric_check(result, "量化评测", "冲突处理正确率", ["conflict_resolution", "correctness"], 0.90),
        _metric_check(result, "量化评测", "负载 P95 延迟小于 500ms", ["latency_load", "p95_latency_ms"], 500.0, upper_bound=True),
        _bool_check(result, "量化评测", "记忆分层策略报告", ["memory_tiers", "policy_report_ok"]),
        _bool_check(result, "量化评测", "并发检索", ["concurrent_search", "ok"]),
        _bool_check(result, "量化评测", "Agent 批量检索", ["agent_batch_search", "ok"]),
        _bool_check(result, "量化评测", "RAG 脱敏保持", ["rag_context_budget", "redaction_enforced"]),
    ]
    dataset = result.get("dataset", {})
    total_cases = sum(int(dataset.get(key, 0)) for key in dataset)
    checks.append(Check("量化评测", "benchmark 用例规模", total_cases >= 100, "总用例/种子数 %s" % total_cases, severity="P1"))
    return checks


def check_openkylin_evidence(evidence_dir: Path, allow_missing: bool = False) -> List[Check]:
    severity = "P1" if allow_missing else "P0"
    checks = [Check("openKylin证据", "证据目录", evidence_dir.exists(), _display_path(evidence_dir), severity=severity)]
    if not evidence_dir.exists():
        return checks
    for rel in REQUIRED_EVIDENCE:
        path = evidence_dir / rel
        ok = path.exists() and path.stat().st_size > 0
        checks.append(Check("openKylin证据", rel, ok, "存在且非空" if ok else "缺失或为空", severity=severity))
    manifest = evidence_dir / "manifest.json"
    sha = evidence_dir / "SHA256SUMS.txt"
    checks.append(Check("openKylin证据", "manifest.json", manifest.exists(), "证据清单存在" if manifest.exists() else "缺少证据清单", severity="P1"))
    checks.append(Check("openKylin证据", "SHA256SUMS.txt", sha.exists(), "哈希清单存在" if sha.exists() else "缺少哈希清单", severity="P1"))
    checks.extend(check_evidence_manifest_freshness(evidence_dir, severity=severity))
    return checks


def check_evidence_manifest_freshness(evidence_dir: Path, severity: str) -> List[Check]:
    manifest_path = evidence_dir / "manifest.json"
    sha_path = evidence_dir / "SHA256SUMS.txt"
    expected_paths = _expected_evidence_paths(evidence_dir)
    checks: List[Check] = []

    if manifest_path.exists():
        try:
            manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
            entries = {str(item.get("path")): item for item in manifest.get("files", [])}
            missing = sorted(set(expected_paths) - set(entries))
            extra = sorted(set(entries) - set(expected_paths))
            stale_hashes = [
                rel
                for rel, item in entries.items()
                if rel in expected_paths and item.get("sha256") != _sha256_file(evidence_dir / rel)
            ]
            ok = not missing and not extra and not stale_hashes and int(manifest.get("file_count", -1)) == len(expected_paths)
            detail = "覆盖 %s 个证据文件" % len(expected_paths)
            if not ok:
                detail = "缺失 %s，额外 %s，哈希过期 %s，manifest file_count=%s，实际=%s" % (
                    len(missing),
                    len(extra),
                    len(stale_hashes),
                    manifest.get("file_count"),
                    len(expected_paths),
                )
            checks.append(Check("openKylin证据", "manifest 覆盖全部证据文件", ok, detail, severity=severity))
        except Exception as exc:  # pragma: no cover - defensive report path
            checks.append(Check("openKylin证据", "manifest 覆盖全部证据文件", False, "manifest 解析失败：%s" % exc, severity=severity))

    if sha_path.exists():
        sha_lines = {}
        for raw_line in sha_path.read_text(encoding="utf-8", errors="replace").splitlines():
            if "  " not in raw_line:
                continue
            digest, rel = raw_line.split("  ", 1)
            sha_lines[rel] = digest
        missing = sorted(set(expected_paths) - set(sha_lines))
        stale = sorted(rel for rel in expected_paths if rel in sha_lines and sha_lines[rel] != _sha256_file(evidence_dir / rel))
        ok = not missing and not stale
        detail = "覆盖 %s 个证据文件" % len(expected_paths) if ok else "缺失 %s，哈希过期 %s" % (len(missing), len(stale))
        checks.append(Check("openKylin证据", "SHA256SUMS 覆盖全部证据文件", ok, detail, severity=severity))

    return checks


def check_repository_hygiene() -> List[Check]:
    forbidden = []
    for path in ROOT.rglob("*"):
        if not path.is_file():
            continue
        rel = path.relative_to(ROOT).as_posix()
        if any(part in IGNORED_HYGIENE_PARTS for part in path.relative_to(ROOT).parts):
            continue
        if any(part.endswith(".egg-info") for part in path.relative_to(ROOT).parts):
            continue
        if path.name.endswith((".pyc", ".pyo", ".sqlite3", ".segment")):
            forbidden.append(rel)
    checks = [
        Check("工程卫生", "运行时文件未混入提交主体", not forbidden, "发现 %s 个运行时文件" % len(forbidden)),
        Check("工程卫生", "许可证", (ROOT / "LICENSE").exists(), "LICENSE 存在" if (ROOT / "LICENSE").exists() else "LICENSE 缺失"),
        Check("工程卫生", "提交包脚本", (ROOT / "scripts/package_submission.py").exists(), "可生成竞赛提交包"),
    ]
    return checks


def to_markdown(report: Dict[str, object]) -> str:
    lines = [
        "# MemGuardOS 竞赛就绪自检报告",
        "",
        "| 项目 | 数值 |",
        "|---|---:|",
        "| 总检查项 | %s |" % report["summary"]["total"],
        "| 通过 | %s |" % report["summary"]["passed"],
        "| 失败 | %s |" % report["summary"]["failed"],
        "| 警告 | %s |" % report["summary"]["warnings"],
        "",
        "## 检查明细",
        "",
        "| 类别 | 检查项 | 状态 | 严重级别 | 说明 |",
        "|---|---|---|---|---|",
    ]
    for item in report["checks"]:
        lines.append(
            "| %s | %s | %s | %s | %s |"
            % (
                item["category"],
                item["name"],
                "通过" if item["ok"] else "未通过",
                item["severity"],
                str(item["detail"]).replace("|", "/"),
            )
        )
    lines.append("")
    return "\n".join(lines)


def _metric_check(result: Dict[str, Any], category: str, name: str, keys: Iterable[str], threshold: float, upper_bound: bool = False) -> Check:
    value = _nested_get(result, list(keys))
    if value is None:
        return Check(category, name, False, "指标缺失")
    numeric = float(value)
    ok = numeric <= threshold if upper_bound else numeric >= threshold
    op = "<=" if upper_bound else ">="
    return Check(category, name, ok, "当前 %.4f，要求 %s %.4f" % (numeric, op, threshold))


def _bool_check(result: Dict[str, Any], category: str, name: str, keys: Iterable[str]) -> Check:
    value = _nested_get(result, list(keys))
    return Check(category, name, bool(value), "当前 %s" % value)


def _nested_get(data: Dict[str, Any], keys: List[str]) -> Optional[Any]:
    current: Any = data
    for key in keys:
        if not isinstance(current, dict) or key not in current:
            return None
        current = current[key]
    return current


def _resolve(path: str) -> Path:
    raw = Path(path)
    return raw if raw.is_absolute() else ROOT / raw


def _display_path(path: Path) -> str:
    try:
        return path.relative_to(ROOT).as_posix()
    except ValueError:
        return str(path)


def _expected_evidence_paths(evidence_dir: Path) -> List[str]:
    return sorted(
        path.relative_to(evidence_dir).as_posix()
        for path in evidence_dir.rglob("*")
        if path.is_file() and path.name not in EVIDENCE_MANIFEST_EXCLUDED
    )


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


if __name__ == "__main__":
    raise SystemExit(main())
