from __future__ import annotations

import argparse
import hashlib
import json
import re
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional


ROOT = Path(__file__).resolve().parents[1]

IGNORED_HYGIENE_PARTS = {
    ".git",
    ".mypy_cache",
    ".playwright-cli",
    ".pytest_cache",
    ".ruff_cache",
    ".venv",
    ".worktrees",
    "__pycache__",
    "artifacts",
    "dist",
    "local_notes",
    "output",
    "outputs",
}

POST_NATIVE_EVIDENCE_ALLOWED_PATHS = {
    # These files only report the collected evidence. Runtime, test, packaging,
    # and collector sources remain forbidden after evidence collection.
    "README.md",
    "docs/13_测试说明.md",
    "docs/15_更新日志.md",
    "docs/competition_ready.json",
    "docs/openKylin验证记录.md",
    "docs/交付文档/MemGuardOS中文操作手册.md",
    "docs/平台摘要.md",
    "docs/最终提交说明.md",
    "docs/测试报告.md",
    "docs/真实Agent闭环测试报告.md",
    "docs/竞赛就绪自检报告.md",
    "docs/答辩提纲.md",
    "docs/答辩稿.md",
    "docs/评委证据包索引.md",
    "docs/赛题符合性说明.md",
    "docs/赛题要求逐句符合性核查.md",
    "docs/超标准交付评分矩阵.md",
    "docs/真实Agent记忆价值评测报告.md",
    "docs/项目介绍总览.md",
}

REQUIRED_DOCS = [
    "README.md",
    "PROJECT_INDEX.md",
    "docs/01_项目简介.md",
    "docs/02_用户手册.md",
    "docs/03_操作手册.md",
    "docs/04_开发者文档.md",
    "docs/05_项目架构说明.md",
    "docs/06_代码结构说明.md",
    "docs/07_核心模块说明.md",
    "docs/08_算法说明.md",
    "docs/09_接口文档.md",
    "docs/10_数据说明.md",
    "docs/11_配置说明.md",
    "docs/12_环境依赖与部署.md",
    "docs/13_测试说明.md",
    "docs/14_常见问题FAQ.md",
    "docs/15_更新日志.md",
    "docs/16_历史文件说明.md",
    "docs/17_项目交付说明.md",
    "docs/正式提交材料说明.md",
    "docs/最终提交说明.md",
    "docs/平台摘要.md",
    "docs/项目介绍总览.md",
    "docs/概要设计说明书.md",
    "docs/详细设计说明书.md",
    "docs/安全设计说明书.md",
    "docs/操作系统机制说明.md",
    "docs/Peony与UKUI桌面集成实现说明.md",
    "docs/麒麟SDK集成证据.md",
    "docs/openKylin验证记录.md",
    "docs/部署手册.md",
    "docs/用户手册.md",
    "docs/测试报告.md",
    "docs/真实Agent接入使用说明.md",
    "docs/真实Agent闭环测试报告.md",
    "docs/真实Agent记忆价值评测报告.md",
    "docs/赛题符合性说明.md",
    "docs/可解释抽取答辩说明.md",
    "docs/评委证据包索引.md",
    "docs/答辩提纲.md",
    "docs/答辩稿.md",
    "docs/演示视频脚本.md",
    "docs/超标准交付评分矩阵.md",
    "docs/供应链与SBOM说明.md",
    "docs/安全扫描报告.md",
    "docs/外部标准基准说明.md",
    "docs/安全与遗忘保证边界.md",
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
    "scripts/verify_deb_package_set.sh",
    "scripts/collect_agent_evidence.sh",
    "scripts/collect_memory_value_evidence.sh",
    "scripts/run_openhands_memguard_demo.py",
    "scripts/run_openhands_behavior_suite.py",
    "scripts/evaluate_memory_value.py",
    "scripts/memguard_agent_bridge.py",
    "scripts/memguard-agent@.service",
    "memguard/integrations/openhands_adapter.py",
    "memguard/integrations/openhands_runner.py",
    "memguard/integrations/context_injector.py",
    "memguard/eval/ablation.py",
    "memguard/eval/performance_suite.py",
    "memguard/eval/memory_value.py",
    "memguard/eval/real_agent_behavior.py",
    "scripts/build_deb.sh",
    "scripts/generate_bpf_lsm_policy.py",
    "scripts/package_submission.py",
    "scripts/web_dashboard_smoke.py",
    "scripts/generate_sbom.py",
    "scripts/run_security_scan.py",
    "scripts/run_resource_control.py",
    "scripts/fetch_longmemeval_v2.py",
    "scripts/run_longmemeval_v2.py",
    "scripts/backup_memguard.py",
    "scripts/restore_memguard.py",
    "scripts/check_slo.py",
    "scripts/sign_release.py",
    "scripts/verify_ukui_plugins.sh",
    "memguard/eval/resource_control.py",
    "memguard/integrations/longmemeval_v2.py",
    "memguard/governance/trust.py",
    "config/nginx/memguardos.conf",
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
    "build_context_explainability.txt",
    "hierarchical_graph_search.txt",
    "temporal_status.txt",
    "namespace_isolation.txt",
    "systemd_cgroup.txt",
    "systemd_namespace.txt",
    "auditd_ingest.txt",
    "audit_concurrency.txt",
    "verify_audit.txt",
    "bpf_lsm_enforcer.txt",
    "deb_package.txt",
    "deb_apt_install_maintenance.txt",
    "deb_apt_install_verify.txt",
    "deb_apt_install_sdk_after_fix.txt",
    "ukui_plugin_build.txt",
    "ukui_plugin_install.txt",
    "ukui_plugin_visible_final.txt",
    "ukui_peony_visible_manual_confirmation.txt",
    "ukui_control_center_visible_manual_confirmation.txt",
    "natural_patterns.txt",
    "peony_file_scan_smoke.txt",
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
    parser.add_argument(
        "--native-evidence-dir",
        default="docs/evidence/openkylin_native_latest",
        help="Current-HEAD openKylin desktop product evidence directory",
    )
    parser.add_argument(
        "--performance-evidence-dir",
        default="docs/evidence/performance_latest",
        help="Optimized concurrency and ablation evidence directory",
    )
    parser.add_argument(
        "--memory-value-evidence-dir",
        default="docs/evidence/memory_value_latest",
        help="Agent memory-value paired and real OpenHands evidence directory",
    )
    parser.add_argument(
        "--real-agent-extension-evidence-dir",
        default="docs/evidence/real_agent_extension_r13_r20_latest",
        help="Additional real OpenHands R13-R20 evidence directory",
    )
    parser.add_argument(
        "--real-agent-holdout-evidence-dir",
        default="docs/evidence/real_agent_holdout_latest",
        help="Held-out real OpenHands evidence directory",
    )
    parser.add_argument("--output", default="", help="Optional JSON report path")
    parser.add_argument("--markdown", default="", help="Optional Markdown report path")
    parser.add_argument("--allow-missing-openkylin", action="store_true", help="Downgrade missing openKylin evidence files to P1 warnings")
    args = parser.parse_args()

    eval_path = _resolve(args.eval)
    evidence_dir = _resolve(args.evidence_dir)
    native_evidence_dir = _resolve(args.native_evidence_dir)
    performance_evidence_dir = _resolve(args.performance_evidence_dir)
    memory_value_evidence_dir = _resolve(args.memory_value_evidence_dir)
    real_agent_extension_evidence_dir = _resolve(
        args.real_agent_extension_evidence_dir
    )
    real_agent_holdout_evidence_dir = _resolve(args.real_agent_holdout_evidence_dir)
    checks = []
    checks.extend(check_docs())
    checks.extend(check_sources())
    checks.extend(check_eval(eval_path))
    checks.extend(check_openkylin_evidence(evidence_dir, allow_missing=args.allow_missing_openkylin))
    native_evidence_severity = "P1" if args.allow_missing_openkylin else "P0"
    checks.extend(
        check_native_openkylin_evidence(
            native_evidence_dir,
            expected_revision=_git_head(),
            severity=native_evidence_severity,
        )
    )
    checks.extend(check_performance_evidence(performance_evidence_dir, severity="P0"))
    checks.extend(check_memory_value_evidence(memory_value_evidence_dir, severity="P0"))
    checks.extend(
        check_real_agent_extension_evidence(
            real_agent_extension_evidence_dir,
            severity="P0",
        )
    )
    checks.extend(
        check_real_agent_holdout_evidence(
            real_agent_holdout_evidence_dir,
            severity="P0",
        )
    )
    checks.extend(
        check_supply_chain_artifacts(
            ROOT / "docs/sbom.json",
            ROOT / "docs/供应链与SBOM说明.md",
        )
    )
    checks.extend(
        check_security_scan_artifacts(
            ROOT / "docs/security_scan.json",
            ROOT / "docs/安全扫描报告.md",
        )
    )
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
    checks.extend(check_formal_doc_consistency())
    return checks


def check_formal_doc_consistency() -> List[Check]:
    checks: List[Check] = []
    manual = ROOT / "docs/交付文档/MemGuardOS中文操作手册.md"
    manual_text = _read_text_if_exists(manual)
    placeholder_tokens = ["占位符", "截图占位", "流程图占位", "架构图占位"]
    placeholder_hits = [token for token in placeholder_tokens if token in manual_text]
    checks.append(
        Check(
            "中文文档",
            "交付操作手册无空占位",
            bool(manual_text) and not placeholder_hits,
            "已替换流程图/截图占位" if manual_text and not placeholder_hits else "仍包含：%s" % "、".join(placeholder_hits),
        )
    )
    api_doc = ROOT / "docs/09_接口文档.md"
    api_text = _read_text_if_exists(api_doc)
    checks.append(
        Check(
            "中文文档",
            "API 文档字段与代码一致",
            "`command` 字段" in api_text and "`action` 字段" not in api_text,
            "Unix socket API 使用 command 字段" if api_text else "接口文档缺失",
        )
    )
    dashboard = ROOT / "memguard/web/dashboard.py"
    dashboard_text = _read_text_if_exists(dashboard)
    product_terms = ["评委", "答辩", "评审"]
    product_hits = [token for token in product_terms if token in dashboard_text]
    checks.append(
        Check(
            "中文文档",
            "Web 控制台产品话术",
            bool(dashboard_text) and not product_hits,
            "控制台不含竞赛过程话术" if dashboard_text and not product_hits else "仍包含：%s" % "、".join(product_hits),
        )
    )
    checks.append(
        Check(
            "中文文档",
            "Web 控制台 token 存储策略",
            "sessionStorage" in dashboard_text and "localStorage" not in dashboard_text,
            "token 仅保存在浏览器会话存储" if dashboard_text else "控制台源码缺失",
        )
    )
    required_headers = [
        "X-Frame-Options",
        "Cross-Origin-Opener-Policy",
        "Permissions-Policy",
        "Content-Security-Policy",
    ]
    missing_headers = [header for header in required_headers if header not in dashboard_text]
    checks.append(
        Check(
            "中文文档",
            "Web 控制台安全响应头",
            bool(dashboard_text) and not missing_headers,
            "包含 CSP、frame、opener 和权限策略响应头" if dashboard_text and not missing_headers else "缺少：%s" % "、".join(missing_headers),
        )
    )
    current_docs = [
        "README.md",
        "docs/13_测试说明.md",
        "docs/15_更新日志.md",
        "docs/平台摘要.md",
        "docs/最终提交说明.md",
        "docs/测试报告.md",
        "docs/真实Agent闭环测试报告.md",
        "docs/真实Agent记忆价值评测报告.md",
        "docs/赛题要求逐句符合性核查.md",
        "docs/赛题符合性说明.md",
        "docs/评委证据包索引.md",
        "docs/答辩提纲.md",
        "docs/答辩稿.md",
        "docs/项目介绍总览.md",
        "docs/交付文档/MemGuardOS中文操作手册.md",
    ]
    stale_test_hits = []
    for rel in current_docs:
        text = _read_text_if_exists(ROOT / rel)
        for token in (
            "283 passed",
            "284 passed",
            "289 passed",
            "290 passed",
            "292 passed",
            "293 passed",
            "294 passed",
            "295 passed",
            "296 passed",
            "297 passed",
            "298 passed",
            "299 passed",
            "308 passed",
            "309 passed",
            "338 passed",
            "340 passed",
            "341 passed",
            "343 passed",
            "344 passed",
            "527 passed",
            "636 passed",
        ):
            if token in text:
                stale_test_hits.append(f"{rel}:{token}")
    checks.append(
        Check(
            "中文文档",
            "正式文档测试数字口径",
            not stale_test_hits,
            "正式材料未引用已知过期测试数字" if not stale_test_hits else "过期数字：%s" % "、".join(stale_test_hits),
        )
    )
    product_docs = [
        "README.md",
        "native/ukui/README.md",
        "docs/openKylin验证记录.md",
        "docs/openKylin原生化适配说明.md",
        "docs/Peony与UKUI桌面集成实现说明.md",
        "docs/部署手册.md",
        "docs/操作系统机制说明.md",
        "docs/交付文档/MemGuardOS中文操作手册.md",
    ]
    awkward_hits = []
    for rel in product_docs:
        text = _read_text_if_exists(ROOT / rel)
        for token in ("端侧向量能力", "端侧向量能力检查", "检查端侧向量能力", "预热 Kylin SDK", "【当前任务】"):
            if token in text:
                awkward_hits.append(f"{rel}:{token}")
    checks.append(
        Check(
            "中文文档",
            "openKylin 桌面产品话术",
            not awkward_hits,
            "桌面文档使用本机智能检索、操作进度等成熟产品表达" if not awkward_hits else "仍包含：%s" % "、".join(awkward_hits),
        )
    )
    scorecard = ROOT / "docs/超标准交付评分矩阵.md"
    scorecard_text = _read_text_if_exists(scorecard)
    required_scorecard_terms = [
        "MemGuardOS 超标准交付评分矩阵",
        "49.48x",
        "OWASP Agentic AI",
        "NIST AI RMF",
        "LangMem",
        "OpenAI Agents SDK",
        "不能夸大",
    ]
    missing_scorecard_terms = [
        term for term in required_scorecard_terms if term not in scorecard_text
    ]
    checks.append(
        Check(
            "中文文档",
            "超标准交付评分矩阵",
            bool(scorecard_text) and not missing_scorecard_terms,
            "评分矩阵覆盖指标余量、外部最佳实践和边界声明" if scorecard_text and not missing_scorecard_terms else "缺少：%s" % "、".join(missing_scorecard_terms),
        )
    )
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
        _metric_check(result, "量化评测", "程序性记忆 Recall@5", ["procedural_memory", "recall_at_5"], 0.90),
        _metric_check(result, "量化评测", "程序性记忆子类型命中率", ["procedural_memory", "procedural_subtype_hit_rate"], 0.90),
        _bool_check(result, "量化评测", "层次图谱检索三层检查", ["procedural_memory", "hierarchical_graph_layers_ok"]),
        _bool_check(result, "量化评测", "RAG 上下文解释性检查", ["procedural_memory", "context_explainability_ok"]),
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
    checks.extend(check_openkylin_metrics(evidence_dir, severity=severity))
    checks.extend(check_deb_install_evidence(evidence_dir, severity=severity))
    checks.extend(check_evidence_manifest_freshness(evidence_dir, severity=severity))
    return checks


def check_native_evidence_revision(evidence_dir: Path, expected_revision: str, severity: str = "P0") -> Check:
    path = evidence_dir / "source_revision.json"
    payload = _load_evidence_json(path)
    if not payload:
        return Check("openKylin原生证据", "证据对应当前 Git HEAD", False, "缺少或无法解析 source_revision.json", severity=severity)
    revision = str(payload.get("source_revision", ""))
    dirty = bool(payload.get("dirty", False))
    if not re.fullmatch(r"[0-9a-f]{40}", revision):
        return Check("openKylin原生证据", "证据对应当前 Git HEAD", False, f"无效版本标识：{revision or '<empty>'}", severity=severity)
    if not re.fullmatch(r"[0-9a-f]{40}", expected_revision):
        return Check("openKylin原生证据", "证据对应当前 Git HEAD", False, f"本地 Git HEAD 无效：{expected_revision}", severity=severity)
    if revision != expected_revision:
        delta_paths = _revision_delta_paths(revision, expected_revision)
        if delta_paths is None or not delta_paths or any(not _is_allowed_native_evidence_delta(path) for path in delta_paths):
            return Check("openKylin原生证据", "证据对应当前 Git HEAD", False, f"证据 {revision[:12]} 与当前 {expected_revision[:12]} 不一致", severity=severity)
        return Check(
            "openKylin原生证据",
            "证据对应当前 Git HEAD",
            True,
            "source=%s；当前 HEAD 仅追加/更新原生证据文件 %s 个" % (revision, len(delta_paths)),
            severity=severity,
        )
    if dirty:
        return Check("openKylin原生证据", "证据对应当前 Git HEAD", False, "证据采集树包含未提交修改", severity=severity)
    return Check("openKylin原生证据", "证据对应当前 Git HEAD", True, revision, severity=severity)


def _is_allowed_native_evidence_delta(path: str) -> bool:
    return path.startswith("docs/evidence/openkylin_native_latest/") or path in POST_NATIVE_EVIDENCE_ALLOWED_PATHS


def check_native_openkylin_evidence(evidence_dir: Path, expected_revision: str, severity: str) -> List[Check]:
    required = [
        "source_revision.json",
        "environment.txt",
        "openkylin_status.txt",
        "openkylin_status_probe_sdk.txt",
        "user_cli.txt",
        "peony_batch_preview.json",
        "peony_batch_apply.json",
        "peony_sensitive_preview.json",
        "systemd_cgroup.txt",
        "systemd_security.txt",
        "storage_permissions.txt",
        "service_restart.txt",
        "polkit_assets.txt",
        "tray_assets.txt",
        "desktop_build.txt",
        "peony_dialog_smoke.txt",
        "debian_packages.txt",
        "deb_lifecycle.txt",
        "pytest_openkylin.txt",
        "manifest.json",
        "SHA256SUMS.txt",
    ]
    checks = [Check("openKylin原生证据", "证据目录", evidence_dir.exists(), _display_path(evidence_dir), severity=severity)]
    for name in required:
        path = evidence_dir / name
        checks.append(Check("openKylin原生证据", name, path.exists() and path.stat().st_size > 0, "存在且非空" if path.exists() and path.stat().st_size > 0 else "缺失或为空", severity=severity))
    checks.append(check_native_evidence_revision(evidence_dir, expected_revision, severity=severity))
    security_values = _parse_result_key_values(_read_text_if_exists(evidence_dir / "systemd_security.txt"))
    try:
        exposure = float(security_values.get("overall_exposure", ""))
    except ValueError:
        exposure = -1.0
    security_ok = security_values.get("security_gate") == "passed" and 0.0 <= exposure <= 3.0
    checks.append(
        Check(
            "openKylin原生证据",
            "systemd 安全暴露评分",
            security_ok,
            "overall_exposure=%s" % exposure if security_ok else "缺少评分或评分高于 3.0",
            severity=severity,
        )
    )
    permission_values = _parse_result_key_values(_read_text_if_exists(evidence_dir / "storage_permissions.txt"))
    permissions_ok = permission_values.get("permissions_ok") == "true"
    checks.append(
        Check(
            "openKylin原生证据",
            "受管存储权限策略",
            permissions_ok,
            "0700/0600 与 memguard 所有权已验证" if permissions_ok else "权限或所有权证据未通过",
            severity=severity,
        )
    )
    restart_values = _parse_result_key_values(_read_text_if_exists(evidence_dir / "service_restart.txt"))
    try:
        restart_ms = int(restart_values.get("restart_ms", ""))
    except ValueError:
        restart_ms = -1
    restart_ok = (
        restart_values.get("restart_rc") == "0"
        and restart_values.get("ActiveState") == "active"
        and 0 <= restart_ms <= 5000
    )
    checks.append(
        Check(
            "openKylin原生证据",
            "systemd 服务重启响应时间",
            restart_ok,
            "restart_ms=%s" % restart_ms if restart_ok else "服务重启失败、状态异常或耗时超过 5000 ms",
            severity=severity,
        )
    )
    user_cli_text = _read_text_if_exists(evidence_dir / "user_cli.txt")
    checks.append(
        Check(
            "openKylin原生证据",
            "用户友好 memguard 入口",
            all(token in user_cli_text for token in ("记忆注入", "工具调用", "新增记忆", "遗忘验证")),
            "memguard demo 输出完整闭环" if "记忆注入" in user_cli_text else "缺少 memguard demo 闭环输出",
            severity=severity,
        )
    )
    polkit_text = _read_text_if_exists(evidence_dir / "polkit_assets.txt")
    polkit_values = _parse_result_key_values(polkit_text)
    readonly_polkit_staged = (
        polkit_values.get("policy_install_blocked") == "readonly_usr"
        and polkit_values.get("policy_staged") == "true"
    )
    readonly_helper_staged = (
        polkit_values.get("policy_install_blocked") == "readonly_usr"
        and polkit_values.get("helper_staged") == "true"
    )
    policy_ready = polkit_values.get("policy_installed") == "true" or readonly_polkit_staged
    helper_ready = polkit_values.get("helper_installed") == "true" or readonly_helper_staged
    checks.extend(
        [
            Check("openKylin原生证据", "Polkit policy 交付状态", policy_ready, "system installed" if polkit_values.get("policy_installed") == "true" else ("readonly /usr with staged policy" if readonly_polkit_staged else "缺少 policy 安装或 staging 证据"), severity=severity),
            Check("openKylin原生证据", "Polkit helper 交付状态", helper_ready, "system installed" if polkit_values.get("helper_installed") == "true" else ("readonly /usr with staged helper" if readonly_helper_staged else "缺少 helper 安装或 staging 证据"), severity=severity),
            Check("openKylin原生证据", "Polkit helper 拒绝未知动作", polkit_values.get("unknown_action_rejected") == "true", "unknown_action_rejected=true" if polkit_values.get("unknown_action_rejected") == "true" else "缺少未知动作拒绝证据", severity=severity),
        ]
    )
    deb_lifecycle = _read_text_if_exists(evidence_dir / "deb_lifecycle.txt")
    checks.extend(
        [
            Check("openKylin原生证据", "Debian 五包生命周期", "package_count=5" in deb_lifecycle and "offline_install=ok" in deb_lifecycle, "package_count=5 offline_install=ok" if "package_count=5" in deb_lifecycle and "offline_install=ok" in deb_lifecycle else "缺少五包安装证据", severity=severity),
            Check("openKylin原生证据", "Debian 桌面包卸载重装", "desktop_remove_core_preserved=ok" in deb_lifecycle and "desktop_reinstall=ok" in deb_lifecycle, "桌面包卸载/重装通过" if "desktop_reinstall=ok" in deb_lifecycle else "缺少桌面包卸载/重装证据", severity=severity),
            Check("openKylin原生证据", "Debian 用户数据保护", "user_data_delete_guard=ok" in deb_lifecycle, "user_data_delete_guard=ok" if "user_data_delete_guard=ok" in deb_lifecycle else "缺少用户数据保护证据", severity=severity),
        ]
    )
    peony_preview = _load_evidence_json(evidence_dir / "peony_batch_preview.json")
    peony_apply = _load_evidence_json(evidence_dir / "peony_batch_apply.json")
    peony_sensitive = _load_evidence_json(evidence_dir / "peony_sensitive_preview.json")
    preview_ok = bool(peony_preview and peony_preview.get("ok") and len(peony_preview.get("items", [])) >= 2 and all(bool(item.get("selectable")) for item in peony_preview.get("items", [])))
    stored_count = _nested_get(peony_apply, ["summary", "stored"]) if peony_apply else 0
    try:
        stored_count_numeric = int(stored_count or 0)
    except (TypeError, ValueError):
        stored_count_numeric = 0
    apply_ok = bool(peony_apply and peony_apply.get("ok") and stored_count_numeric >= 2 and _nested_get(peony_apply, ["audit_chain", "ok"]) is True)
    sensitive_items = peony_sensitive.get("items", []) if peony_sensitive else []
    sensitive_ok = bool(sensitive_items and sensitive_items[0].get("recommended_action") == "block_sensitive" and sensitive_items[0].get("selectable") is False)
    peony_dialog_smoke = _read_text_if_exists(evidence_dir / "peony_dialog_smoke.txt")
    dialog_smoke_ok = "peony_file_scan_dialog=visible" in peony_dialog_smoke and re.search(r"preview_rows=[1-9][0-9]*", peony_dialog_smoke) is not None
    checks.extend(
        [
            Check("openKylin原生证据", "Peony 批量预览确认契约", preview_ok, "批量预览可选择安全项" if preview_ok else "批量预览证据不足", severity=severity),
            Check("openKylin原生证据", "Peony 确认导入与审计链", apply_ok, "stored>=2 audit_chain=true" if apply_ok else "确认导入或审计链证据不足", severity=severity),
            Check("openKylin原生证据", "Peony 敏感文件阻断", sensitive_ok, "block_sensitive selectable=false" if sensitive_ok else "敏感阻断证据不足", severity=severity),
            Check("openKylin原生证据", "Peony 结果窗口可见", dialog_smoke_ok, "plugin_loaded=true dialog=visible preview_rows>=1" if dialog_smoke_ok else "缺少真实插件加载或结果窗口可见证据", severity=severity),
        ]
    )
    tray_text = _read_text_if_exists(evidence_dir / "tray_assets.txt")
    checks.append(Check("openKylin原生证据", "UKUI 托盘安装资产", "memguardos-tray" in tray_text and "OnlyShowIn=UKUI" in tray_text, "托盘程序与 UKUI autostart 存在" if "OnlyShowIn=UKUI" in tray_text else "托盘证据不足", severity=severity))
    if evidence_dir.exists():
        checks.extend(check_evidence_manifest_freshness(evidence_dir, severity=severity))
    return checks


def check_openkylin_metrics(evidence_dir: Path, severity: str) -> List[Check]:
    checks: List[Check] = []
    eval_10k = _load_evidence_json(evidence_dir / "eval_result_openkylin_10k.json")
    if eval_10k is None:
        checks.append(Check("openKylin指标", "10k 评测 JSON", False, "eval_result_openkylin_10k.json 不存在或无法解析", severity=severity))
    else:
        checks.extend(
            [
                _metric_check(eval_10k, "openKylin指标", "10k 负载 P95 < 500ms", ["latency_load", "p95_latency_ms"], 500.0, upper_bound=True),
                _metric_check(eval_10k, "openKylin指标", "10k 负载 P99 < 500ms", ["latency_load", "p99_latency_ms"], 500.0, upper_bound=True),
                _metric_check(eval_10k, "openKylin指标", "10k 进程 RSS < 256MB", ["resource_usage", "process_rss_kb"], 256 * 1024, upper_bound=True),
                _metric_check(eval_10k, "openKylin指标", "10k 程序性记忆 Recall@5", ["procedural_memory", "recall_at_5"], 0.90),
                _bool_check(eval_10k, "openKylin指标", "10k 层次图谱检索检查", ["procedural_memory", "hierarchical_graph_layers_ok"]),
                _bool_check(eval_10k, "openKylin指标", "10k 上下文解释性检查", ["procedural_memory", "context_explainability_ok"]),
                _metric_check(eval_10k, "openKylin指标", "10k Agent 批量检索 P95 < 100ms", ["agent_batch_search", "p95_latency_ms"], 100.0, upper_bound=True),
            ]
        )

    sdk_probe = _load_evidence_json_from_capture(evidence_dir / "sdk_embedding_probe.txt")
    if sdk_probe is None:
        checks.append(Check("openKylin指标", "Kylin embedding 真实推理", False, "sdk_embedding_probe.txt 不存在或无法解析 JSON", severity=severity))
    else:
        inference_ready = _nested_get(sdk_probe, ["embedding", "inference_ready"])
        probe_dim = _nested_get(sdk_probe, ["embedding", "embedding_probe", "dim"])
        fallback = _nested_get(sdk_probe, ["embedding", "fallback"])
        diagnosis = _nested_get(sdk_probe, ["embedding", "diagnosis"])
        if inference_ready:
            checks.append(Check("openKylin指标", "Kylin embedding 真实推理", True, "inference_ready=True", severity=severity))
            checks.append(Check("openKylin指标", "Kylin embedding 维度 768", probe_dim == 768, "dim=%s" % probe_dim, severity=severity))
        else:
            checks.append(
                Check(
                    "openKylin指标",
                    "Kylin embedding 真实推理",
                    False,
                    "inference_ready=%s；核心检索使用 fallback=%s，诊断=%s" % (inference_ready, fallback, bool(diagnosis)),
                    severity="P1",
                )
            )
            checks.append(
                Check(
                    "openKylin指标",
                    "Kylin embedding fallback/诊断",
                    bool(fallback or diagnosis),
                    "fallback=%s，diagnosis=%s" % (fallback, bool(diagnosis)),
                    severity=severity,
                )
            )

    context_text = _read_text_if_exists(evidence_dir / "build_context_explainability.txt")
    checks.append(
        Check(
            "openKylin指标",
            "build-context 解释性证据",
            "explainability" in context_text and "selection_trace" in context_text,
            "包含 explainability 与 selection_trace" if context_text else "证据缺失",
            severity=severity,
        )
    )
    graph_text = _read_text_if_exists(evidence_dir / "hierarchical_graph_search.txt")
    graph_ok = all(token in graph_text for token in ('"summary"', '"entity_relation"', '"chunk"', '"retrieval_path"'))
    checks.append(
        Check(
            "openKylin指标",
            "层次图谱三层证据",
            graph_ok,
            "包含 summary/entity_relation/chunk/retrieval_path" if graph_text else "证据缺失",
            severity=severity,
        )
    )
    peony_smoke = _load_evidence_json_from_capture(evidence_dir / "peony_file_scan_smoke.txt")
    if peony_smoke is None:
        checks.append(Check("openKylin指标", "Peony 文件扫描闭环", False, "peony_file_scan_smoke.txt 不存在或无法解析 JSON", severity=severity))
    else:
        peony_ok = bool(peony_smoke.get("ok") and peony_smoke.get("file_ingest_stored") and peony_smoke.get("audit_ok") and int(peony_smoke.get("search_hits", 0)) >= 1)
        checks.append(
            Check(
                "openKylin指标",
                "Peony 文件扫描闭环",
                peony_ok,
                "stored=%s search_hits=%s audit_ok=%s" % (
                    peony_smoke.get("file_ingest_stored"),
                    peony_smoke.get("search_hits"),
                    peony_smoke.get("audit_ok"),
                ),
                severity=severity,
            )
        )
    return checks


def check_performance_evidence(evidence_dir: Path, severity: str) -> List[Check]:
    checks: List[Check] = [
        Check(
            "性能与消融证据",
            "证据目录",
            evidence_dir.exists(),
            _display_path(evidence_dir),
            severity=severity,
        )
    ]
    optimized = _load_evidence_json(evidence_dir / "eval_result_openkylin_10k_optimized.json")
    suite = _load_evidence_json(evidence_dir / "performance_suite_openkylin.json")
    if optimized is None:
        checks.append(
            Check(
                "性能与消融证据",
                "优化后 10k 评测 JSON",
                False,
                "eval_result_openkylin_10k_optimized.json 不存在或无法解析",
                severity=severity,
            )
        )
    else:
        checks.extend(
            [
                _metric_check(
                    optimized,
                    "性能与消融证据",
                    "优化后 10k 负载 P95 < 500ms",
                    ["latency_load", "p95_latency_ms"],
                    500.0,
                    upper_bound=True,
                ),
                _metric_check(
                    optimized,
                    "性能与消融证据",
                    "优化后 10k 负载 P99 < 500ms",
                    ["latency_load", "p99_latency_ms"],
                    500.0,
                    upper_bound=True,
                ),
                _metric_check(
                    optimized,
                    "性能与消融证据",
                    "优化后 10k RSS < 256MB",
                    ["resource_usage", "process_rss_kb"],
                    256 * 1024,
                    upper_bound=True,
                ),
                _metric_check(
                    optimized,
                    "性能与消融证据",
                    "优化后 Agent 批量检索 P95 < 100ms",
                    ["agent_batch_search", "p95_latency_ms"],
                    100.0,
                    upper_bound=True,
                ),
            ]
        )

    if suite is None:
        checks.append(
            Check(
                "性能与消融证据",
                "多轮并发与消融 JSON",
                False,
                "performance_suite_openkylin.json 不存在或无法解析",
                severity=severity,
            )
        )
        return checks

    summary = suite.get("concurrent_summary", {})
    checks.extend(
        [
            Check(
                "性能与消融证据",
                "多轮并发全部通过",
                summary.get("all_ok") is True,
                "all_ok=%s" % summary.get("all_ok"),
                severity=severity,
            ),
            _metric_check(
                suite,
                "性能与消融证据",
                "多轮并发最差 P95 < 500ms",
                ["concurrent_summary", "worst_p95_latency_ms"],
                500.0,
                upper_bound=True,
            ),
            _metric_check(
                suite,
                "性能与消融证据",
                "多轮并发最差 P99 < 500ms",
                ["concurrent_summary", "worst_p99_latency_ms"],
                500.0,
                upper_bound=True,
            ),
        ]
    )
    variants = _nested_get(suite, ["ablation", "variants"]) or {}
    text_recall = _nested_get(variants, ["text_only", "recall_at_5"])
    full_recall = _nested_get(variants, ["full_no_cache", "recall_at_5"])
    recall_ok = (
        isinstance(text_recall, (int, float))
        and isinstance(full_recall, (int, float))
        and float(full_recall) >= 0.90
        and float(full_recall) > float(text_recall)
    )
    checks.append(
        Check(
            "性能与消融证据",
            "消融实验证明混合检索提升召回",
            recall_ok,
            "text_only=%s full_no_cache=%s" % (text_recall, full_recall),
            severity=severity,
        )
    )
    kylin = variants.get("full_kylin_sdk", {}) if isinstance(variants, dict) else {}
    kylin_status = kylin.get("status")
    kylin_has_boundary = kylin_status == "skipped" or (
        kylin_status == "completed"
        and isinstance(kylin.get("p95_latency_ms"), (int, float))
        and isinstance(kylin.get("p99_latency_ms"), (int, float))
    )
    checks.append(
        Check(
            "性能与消融证据",
            "Kylin SDK 消融边界已记录",
            kylin_has_boundary,
            "status=%s P95=%s P99=%s"
            % (
                kylin_status,
                kylin.get("p95_latency_ms"),
                kylin.get("p99_latency_ms"),
            ),
            severity=severity,
        )
    )
    return checks


def check_memory_value_evidence(
    evidence_dir: Path,
    severity: str,
) -> List[Check]:
    checks = [
        Check(
            "Agent记忆价值证据",
            "证据目录",
            evidence_dir.exists(),
            _display_path(evidence_dir),
            severity=severity,
        )
    ]
    deterministic = _load_evidence_json(
        evidence_dir / "deterministic" / "metrics.json"
    )
    real_agent = _load_evidence_json(
        evidence_dir / "real_agent" / "metrics.json"
    )
    deterministic_ok = bool(
        deterministic
        and int(deterministic.get("case_count", 0)) == 16
        and int(
            _nested_get(deterministic, ["system_validation", "passed"]) or 0
        )
        == 16
        and int(
            _nested_get(deterministic, ["system_validation", "failed"]) or 0
        )
        == 0
        and not deterministic.get("failures")
    )
    checks.append(
        Check(
            "Agent记忆价值证据",
            "16 组确定性配对实验",
            deterministic_ok,
            (
                "case_count=%s passed=%s failed=%s"
                % (
                    deterministic.get("case_count"),
                    _nested_get(deterministic, ["system_validation", "passed"]),
                    _nested_get(deterministic, ["system_validation", "failed"]),
                )
                if deterministic
                else "metrics.json 缺失或无法解析"
            ),
            severity=severity,
        )
    )
    real_case_count = int(real_agent.get("case_count", 0)) if real_agent else 0
    completed_count = (
        int(real_agent.get("completed_case_count", 0)) if real_agent else 0
    )
    checks.append(
        Check(
            "Agent记忆价值证据",
            "至少 12 组真实 OpenHands 案例",
            real_case_count >= 12 and completed_count == real_case_count,
            "case_count=%s completed=%s" % (real_case_count, completed_count),
            severity=severity,
        )
    )
    unclassified = (
        int(real_agent.get("unclassified_failure_count", -1))
        if real_agent
        else -1
    )
    checks.append(
        Check(
            "Agent记忆价值证据",
            "真实案例失败均已归因",
            unclassified == 0,
            "unclassified_failure_count=%s" % unclassified,
            severity=severity,
        )
    )
    memory_pass_rate = (
        float(real_agent.get("memory_system_pass_rate", 0.0))
        if real_agent
        else 0.0
    )
    checks.append(
        Check(
            "Agent记忆价值证据",
            "真实记忆系统层通过率",
            memory_pass_rate >= 0.75,
            "当前 %.4f，要求 >= 0.7500" % memory_pass_rate,
            severity=severity,
        )
    )
    behavior_pass_rate = (
        float(real_agent.get("agent_behavior_pass_rate", 0.0))
        if real_agent
        else 0.0
    )
    checks.append(
        Check(
            "Agent记忆价值证据",
            "真实 Agent 行为层结果已记录",
            real_agent is not None and 0.0 <= behavior_pass_rate <= 1.0,
            "agent_behavior_pass_rate=%.4f" % behavior_pass_rate,
            severity=severity,
        )
    )
    for name in ("manifest.json", "SHA256SUMS.txt"):
        path = evidence_dir / name
        checks.append(
            Check(
                "Agent记忆价值证据",
                name,
                path.exists() and path.stat().st_size > 0,
                "存在且非空" if path.exists() and path.stat().st_size > 0 else "缺失或为空",
                severity=severity,
            )
        )
    return checks


def check_real_agent_extension_evidence(
    evidence_dir: Path,
    severity: str,
) -> List[Check]:
    checks = [
        Check(
            "真实Agent扩展证据",
            "R13-R20 证据目录",
            evidence_dir.exists(),
            _display_path(evidence_dir),
            severity=severity,
        )
    ]
    metrics = _load_evidence_json(evidence_dir / "metrics.json")
    case_count = int(metrics.get("case_count", 0)) if metrics else 0
    completed_count = int(metrics.get("completed_case_count", 0)) if metrics else 0
    memory_rate = (
        float(metrics.get("memory_system_pass_rate", 0.0)) if metrics else 0.0
    )
    behavior_rate = (
        float(metrics.get("agent_behavior_pass_rate", 0.0)) if metrics else 0.0
    )
    unclassified = (
        int(metrics.get("unclassified_failure_count", -1)) if metrics else -1
    )
    checks.append(
        Check(
            "真实Agent扩展证据",
            "R13-R20 新增 8 组真实 OpenHands 案例",
            case_count == 8
            and completed_count == 8
            and memory_rate == 1.0
            and behavior_rate == 1.0
            and unclassified == 0,
            (
                "case_count=%s completed=%s memory=%.4f behavior=%.4f "
                "unclassified=%s"
            )
            % (
                case_count,
                completed_count,
                memory_rate,
                behavior_rate,
                unclassified,
            ),
            severity=severity,
        )
    )
    for name in ("manifest.json", "SHA256SUMS.txt"):
        path = evidence_dir / name
        ok = path.exists() and path.stat().st_size > 0
        checks.append(
            Check(
                "真实Agent扩展证据",
                name,
                ok,
                "存在且非空" if ok else "缺失或为空",
                severity=severity,
            )
        )
    return checks


def check_real_agent_holdout_evidence(
    evidence_dir: Path,
    severity: str,
) -> List[Check]:
    checks = [
        Check(
            "真实Agent留出证据",
            "留出集证据目录",
            evidence_dir.exists(),
            _display_path(evidence_dir),
            severity=severity,
        )
    ]
    metrics = _load_evidence_json(evidence_dir / "metrics.json")
    case_count = int(metrics.get("case_count", 0)) if metrics else 0
    completed_count = int(metrics.get("completed_case_count", 0)) if metrics else 0
    memory_rate = (
        float(metrics.get("memory_system_pass_rate", 0.0)) if metrics else 0.0
    )
    behavior_rate = (
        float(metrics.get("agent_behavior_pass_rate", 0.0)) if metrics else 0.0
    )
    unclassified = (
        int(metrics.get("unclassified_failure_count", -1)) if metrics else -1
    )
    failed_ids = metrics.get("failed_case_ids", ["<missing>"]) if metrics else ["<missing>"]
    checks.append(
        Check(
            "真实Agent留出证据",
            "8 组留出真实 OpenHands 案例",
            case_count == 8
            and completed_count == 8
            and memory_rate == 1.0
            and behavior_rate == 1.0
            and unclassified == 0
            and failed_ids == [],
            (
                "case_count=%s completed=%s memory=%.4f behavior=%.4f "
                "unclassified=%s failed=%s"
            )
            % (
                case_count,
                completed_count,
                memory_rate,
                behavior_rate,
                unclassified,
                failed_ids,
            ),
            severity=severity,
        )
    )
    for name in ("manifest.json", "SHA256SUMS.txt"):
        path = evidence_dir / name
        ok = path.exists() and path.stat().st_size > 0
        checks.append(
            Check(
                "真实Agent留出证据",
                name,
                ok,
                "存在且非空" if ok else "缺失或为空",
                severity=severity,
            )
        )
    return checks


def check_deb_install_evidence(evidence_dir: Path, severity: str) -> List[Check]:
    checks: List[Check] = []
    install_text = _read_text_if_exists(evidence_dir / "deb_apt_install_maintenance.txt")
    verify_text = _read_text_if_exists(evidence_dir / "deb_apt_install_verify.txt")
    sdk_text = _read_text_if_exists(evidence_dir / "deb_apt_install_sdk_after_fix.txt")
    sdk_json = _load_evidence_json_from_capture(evidence_dir / "deb_apt_install_sdk_after_fix.txt")
    sdk_probe = _nested_get(sdk_json or {}, ["embedding", "embedding_probe"])
    sdk_dim = _nested_get(sdk_json or {}, ["embedding", "embedding_probe", "dim"])
    sdk_inference_ready = _nested_get(sdk_json or {}, ["embedding", "inference_ready"]) is True
    sdk_dim_ok = sdk_dim == 768 or (
        isinstance(sdk_probe, dict)
        and sdk_probe.get("dim") == 768
    )
    plugin_deps_ok = "plugin_ldd_missing=0" in verify_text
    audit_ok = '"ok": true' in verify_text and ("memctl verify-audit" in verify_text or "--- verify audit ---" in verify_text)
    checks.append(
        Check(
            "openKylin指标",
            ".deb 维护模式 apt 安装",
            "正在设置 memguardos" in install_text or "memguardos (0.1.0)" in install_text,
            "apt install 输出包含 memguardos 安装过程" if install_text else "证据缺失",
            severity=severity,
        )
    )
    checks.append(
        Check(
            "openKylin指标",
            ".deb 安装后 systemd 服务",
            "Status: install ok installed" in verify_text and "Active: active (running)" in verify_text,
            "dpkg 状态正常且 memguardd.service active" if verify_text else "证据缺失",
            severity=severity,
        )
    )
    checks.append(
        Check(
            "openKylin指标",
            ".deb 安装后桌面插件依赖",
            plugin_deps_ok,
            "Peony/UKUI 插件已打包且 ldd 无缺失依赖" if verify_text else "证据缺失",
            severity=severity,
        )
    )
    checks.append(
        Check(
            "openKylin指标",
            ".deb 安装后审计链",
            audit_ok,
            "memctl verify-audit 返回 ok=true" if verify_text else "证据缺失",
            severity=severity,
        )
    )
    checks.append(
        Check(
            "openKylin指标",
            ".deb 安装后 Kylin embedding 推理",
            (sdk_inference_ready and sdk_dim_ok)
            or (("inference_ready=true" in sdk_text or '"inference_ready": true' in sdk_text) and "embedding.length=768" in sdk_text),
            "Python/C++ 探针均返回 768 维 embedding" if sdk_text else "证据缺失",
            severity=severity,
        )
    )
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


def check_supply_chain_artifacts(sbom_path: Path, markdown_path: Path) -> List[Check]:
    checks: List[Check] = []
    checks.append(
        Check(
            "供应链与SBOM",
            "SBOM JSON",
            sbom_path.exists() and sbom_path.stat().st_size > 0,
            _display_path(sbom_path) if sbom_path.exists() else "文件不存在",
        )
    )
    markdown_text = _read_text_if_exists(markdown_path)
    checks.append(
        Check(
            "供应链与SBOM",
            "SBOM 中文说明",
            markdown_path.exists() and "软件物料清单" in markdown_text,
            _display_path(markdown_path) if markdown_path.exists() else "文件不存在",
        )
    )
    sbom = _load_evidence_json(sbom_path)
    if sbom is None:
        checks.extend(
            [
                Check("供应链与SBOM", "SBOM 关键源码覆盖", False, "SBOM JSON 无法解析"),
                Check("供应链与SBOM", "SBOM 依赖分组", False, "SBOM JSON 无法解析"),
            ]
        )
        return checks
    artifacts = {
        str(item.get("path")): item
        for item in sbom.get("artifacts", [])
        if isinstance(item, dict)
    }
    required_artifacts = {
        "memguard/service.py",
        "scripts/run_openhands_behavior_suite.py",
        "native/bpf_lsm/memguard_lsm.bpf.c",
    }
    missing_artifacts = sorted(
        rel
        for rel in required_artifacts
        if not artifacts.get(rel, {}).get("present")
        or not artifacts.get(rel, {}).get("sha256")
    )
    checks.append(
        Check(
            "供应链与SBOM",
            "SBOM 关键源码覆盖",
            not missing_artifacts,
            "覆盖核心服务、真实 Agent runner 和 BPF LSM"
            if not missing_artifacts
            else "缺失：%s" % "、".join(missing_artifacts),
        )
    )
    dependencies = sbom.get("dependencies", {})
    optional = dependencies.get("optional", {}) if isinstance(dependencies, dict) else {}
    runtime = dependencies.get("runtime") if isinstance(dependencies, dict) else None
    dependency_ok = (
        isinstance(runtime, list)
        and isinstance(optional, dict)
        and "dev" in optional
        and "agent-openhands" in optional
        and "security" in optional
        and "openhands-sdk==1.28.1" in optional.get("agent-openhands", [])
        and "bandit>=1.7" in optional.get("security", [])
        and "pip-audit>=2.7" in optional.get("security", [])
    )
    checks.append(
        Check(
            "供应链与SBOM",
            "SBOM 依赖分组",
            dependency_ok,
            "包含 runtime/dev/agent-openhands 分组" if dependency_ok else "依赖分组不完整",
        )
    )
    return checks


def check_security_scan_artifacts(report_path: Path, markdown_path: Path) -> List[Check]:
    checks: List[Check] = []
    checks.append(
        Check(
            "安全扫描",
            "安全扫描 JSON",
            report_path.exists() and report_path.stat().st_size > 0,
            _display_path(report_path) if report_path.exists() else "文件不存在",
        )
    )
    markdown_text = _read_text_if_exists(markdown_path)
    checks.append(
        Check(
            "安全扫描",
            "安全扫描中文报告",
            markdown_path.exists() and "MemGuardOS 安全扫描报告" in markdown_text,
            _display_path(markdown_path) if markdown_path.exists() else "文件不存在",
        )
    )
    report = _load_evidence_json(report_path)
    if report is None:
        checks.append(Check("安全扫描", "安全扫描门禁通过", False, "security_scan.json 不存在或无法解析"))
        return checks
    summary = report.get("summary", {}) if isinstance(report, dict) else {}
    tools = report.get("tools", {}) if isinstance(report, dict) else {}
    bandit_status = _nested_get(tools, ["bandit", "status"]) if isinstance(tools, dict) else None
    pip_status = _nested_get(tools, ["pip_audit", "status"]) if isinstance(tools, dict) else None
    missing_tools = summary.get("missing_tools", []) if isinstance(summary, dict) else []
    bandit_high = int(summary.get("bandit_high_count", -1)) if isinstance(summary, dict) else -1
    bandit_medium = int(summary.get("bandit_medium_count", -1)) if isinstance(summary, dict) else -1
    vulnerabilities = int(summary.get("pip_audit_vulnerability_count", -1)) if isinstance(summary, dict) else -1
    ok = (
        report.get("schema") == "memguardos-security-scan-v1"
        and report.get("ok") is True
        and bandit_high == 0
        and bandit_medium == 0
        and vulnerabilities == 0
        and missing_tools == []
        and bandit_status in {"clean", "warnings"}
        and pip_status == "clean"
    )
    checks.append(
        Check(
            "安全扫描",
            "安全扫描门禁通过",
            ok,
            (
                "bandit_high=%s bandit_medium=%s pip_audit_vulnerabilities=%s "
                "missing_tools=%s bandit=%s pip_audit=%s"
            )
            % (
                bandit_high,
                bandit_medium,
                vulnerabilities,
                missing_tools,
                bandit_status,
                pip_status,
            ),
        )
    )
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


def _load_evidence_json(path: Path) -> Optional[Dict[str, Any]]:
    if not path.exists():
        return None
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return None
    return payload if isinstance(payload, dict) else None


def _load_evidence_json_from_capture(path: Path) -> Optional[Dict[str, Any]]:
    text = _read_text_if_exists(path)
    if not text:
        return None
    start = text.find("{")
    if start < 0:
        return None
    decoder = json.JSONDecoder()
    try:
        payload, _ = decoder.raw_decode(text[start:])
    except json.JSONDecodeError:
        return None
    return payload if isinstance(payload, dict) else None


def _read_text_if_exists(path: Path) -> str:
    return path.read_text(encoding="utf-8", errors="replace") if path.exists() else ""


def _parse_result_key_values(text: str) -> Dict[str, str]:
    values: Dict[str, str] = {}
    for raw_line in text.splitlines():
        line = raw_line.strip()
        if not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*=[^\s]+", line):
            continue
        key, value = line.split("=", 1)
        values[key] = value
    return values


def _resolve(path: str) -> Path:
    raw = Path(path)
    return raw if raw.is_absolute() else ROOT / raw


def _display_path(path: Path) -> str:
    try:
        return path.relative_to(ROOT).as_posix()
    except ValueError:
        return str(path)


def _git_head() -> str:
    try:
        completed = subprocess.run(
            ["git", "rev-parse", "HEAD"],
            cwd=ROOT,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            text=True,
            timeout=5,
            check=False,
        )
    except (OSError, subprocess.SubprocessError):
        return ""
    return completed.stdout.strip().lower()


def _revision_delta_paths(source_revision: str, target_revision: str) -> Optional[List[str]]:
    try:
        ancestor = subprocess.run(
            ["git", "merge-base", "--is-ancestor", source_revision, target_revision],
            cwd=ROOT,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            timeout=5,
            check=False,
        )
        if ancestor.returncode != 0:
            return None
        completed = subprocess.run(
            ["git", "-c", "core.quotePath=false", "diff", "--name-only", f"{source_revision}..{target_revision}"],
            cwd=ROOT,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=10,
            check=False,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    if completed.returncode != 0:
        return None
    return [line.strip() for line in completed.stdout.splitlines() if line.strip()]


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

