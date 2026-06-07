from __future__ import annotations

from collections import Counter
from datetime import datetime
from pathlib import Path
from zipfile import ZIP_DEFLATED, ZipFile
import fnmatch
import json


ROOT = Path(__file__).resolve().parents[1]
DIST = ROOT / "dist"

EXCLUDED_DIRS = {
    ".git",
    ".mypy_cache",
    ".pytest_cache",
    ".ruff_cache",
    ".venv",
    "__pycache__",
    "dist",
    "local_notes",
}

EXCLUDED_PATTERNS = [
    "*.pyc",
    "*.pyo",
    "*.sqlite3",
    "*.segment",
    "*.log",
    "*.tmp",
    ".DS_Store",
]

ROLE_DESCRIPTIONS = {
    "作品主体/核心代码": "memguard/",
    "作品主体/脚本": "运行、部署、验证、证据采集和操作系统集成脚本",
    "作品主体/原生增强": "native/",
    "作品主体/测试": "tests/",
    "作品主体/数据与评测样例": "data/",
    "正式中文文档": "README.md 以及面向竞赛评审的精选 docs/ 文档",
    "证据包": "docs/evidence/",
    "过程与工程支撑文档": "需求追踪、变更日志、贡献指南、CI、模板和自检报告",
    "GitHub工程化支撑": ".github/, .devcontainer/, CONTRIBUTING.md, SECURITY.md, CHANGELOG.md, CODE_OF_CONDUCT.md, requirements-dev.txt, .dockerignore, .gitattributes, .gitignore",
    "开发环境支撑": "Dockerfile",
    "项目配置": "pyproject.toml, LICENSE",
}

FORMAL_DOCS = {
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
    "docs/赛题符合性说明.md",
    "docs/可解释抽取答辩说明.md",
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
    "docs/评委证据包索引.md",
    "docs/答辩提纲.md",
    "docs/答辩稿.md",
    "docs/演示视频脚本.md",
    "docs/交付文档/MemGuardOS中文操作手册.md",
    "docs/交付文档/MemGuardOS中文操作手册.docx",
    "docs/交付文档/MemGuardOS中文操作手册.pdf",
}

FORMAL_DOC_PATTERNS = [
    "docs/eval_report*.md",
    "docs/eval_result*.json",
]

FORMAL_SCRIPTS = {
    "scripts/auditd_to_memguard.sh",
    "scripts/bpftrace_sensitive_open.bt",
    "scripts/build_ukui_plugins.sh",
    "scripts/build_deb.sh",
    "scripts/collect_openkylin_evidence.sh",
    "scripts/demo_smoke.py",
    "scripts/diagnose_kylin_embedding.sh",
    "scripts/fix_kylin_embedding_runtime.sh",
    "scripts/generate_bpf_lsm_policy.py",
    "scripts/install_auditd_rules.sh",
    "scripts/install_openkylin.sh",
    "scripts/install_ukui_plugins.sh",
    "scripts/kylin_embedding_probe.cpp",
    "scripts/kylin_vector_probe.cpp",
    "scripts/memguardd.service",
    "scripts/memguardd@.service",
    "scripts/openkylin_verify.sh",
    "scripts/peony_file_scan_smoke.sh",
    "scripts/probe_ukui_deep_plugins.sh",
    "scripts/run_bpf_lsm_enforcer.sh",
    "scripts/run_bpftrace_sensitive_probe.sh",
    "scripts/run_kylin_embedding_probe.sh",
    "scripts/run_kylin_vector_probe.sh",
    "scripts/ukui_notify_smoke.sh",
    "scripts/verify_ukui_plugins.sh",
    "scripts/web_dashboard_smoke.py",
    "scripts/write_evidence_manifest.py",
}

PROCESS_TESTS = {
    "tests/test_packaging.py",
}

COMPETITION_ROLES = {
    "作品主体/核心代码",
    "作品主体/脚本",
    "作品主体/原生增强",
    "作品主体/测试",
    "作品主体/数据与评测样例",
    "正式中文文档",
    "证据包",
    "项目配置",
}


def classify_file(path: Path) -> str:
    path = Path(path)
    rel = path.as_posix() if not path.is_absolute() else path.relative_to(ROOT).as_posix()
    if rel.startswith("memguard/"):
        return "作品主体/核心代码"
    if rel in FORMAL_SCRIPTS:
        return "作品主体/脚本"
    if rel.startswith("scripts/"):
        return "过程与工程支撑文档"
    if rel.startswith("native/"):
        return "作品主体/原生增强"
    if rel in PROCESS_TESTS:
        return "过程与工程支撑文档"
    if rel.startswith("tests/"):
        return "作品主体/测试"
    if rel.startswith("data/"):
        return "作品主体/数据与评测样例"
    if rel.startswith("docs/evidence/"):
        return "证据包"
    if rel in FORMAL_DOCS or any(fnmatch.fnmatch(rel, pattern) for pattern in FORMAL_DOC_PATTERNS):
        return "正式中文文档"
    if rel.startswith("docs/") or rel in {
        "CONTRIBUTING.md",
        "SECURITY.md",
        "CHANGELOG.md",
        "CODE_OF_CONDUCT.md",
    }:
        return "过程与工程支撑文档"
    if rel.startswith(".github/") or rel.startswith(".devcontainer/") or rel in {
        "CONTRIBUTING.md",
        "SECURITY.md",
        "CHANGELOG.md",
        "CODE_OF_CONDUCT.md",
        "requirements-dev.txt",
        ".dockerignore",
        ".gitattributes",
        ".gitignore",
    }:
        return "GitHub工程化支撑"
    if rel == "Dockerfile":
        return "开发环境支撑"
    if rel in {"pyproject.toml", "LICENSE"}:
        return "项目配置"
    if rel == "Makefile":
        return "过程与工程支撑文档"
    return "其他"


def should_skip(path: Path) -> bool:
    rel = path.relative_to(ROOT).as_posix()
    if rel.startswith("docs/research/"):
        return True
    if rel.startswith("docs/evidence/openkylin_") or rel.startswith("docs/evidence/kylin_embedding_diag_"):
        return True
    rel_parts = path.relative_to(ROOT).parts
    if any(part in EXCLUDED_DIRS for part in rel_parts):
        return True
    if any(part.endswith(".egg-info") for part in rel_parts):
        return True
    return any(fnmatch.fnmatch(path.name, pattern) for pattern in EXCLUDED_PATTERNS)


def iter_files() -> list[Path]:
    files: list[Path] = []
    for path in ROOT.rglob("*"):
        if path.is_file() and not should_skip(path):
            files.append(path)
    return sorted(files)


def write_package(package_path: Path, manifest_path: Path, files: list[Path], package_type: str) -> None:
    with ZipFile(package_path, "w", ZIP_DEFLATED) as zf:
        for path in files:
            zf.write(path, path.relative_to(ROOT).as_posix())

    file_entries = [{"path": path.relative_to(ROOT).as_posix(), "role": classify_file(path)} for path in files]
    role_summary = dict(sorted(Counter(entry["role"] for entry in file_entries).items()))
    manifest = {
        "package": package_path.name,
        "package_type": package_type,
        "file_count": len(files),
        "excluded_dirs": sorted(EXCLUDED_DIRS),
        "excluded_patterns": EXCLUDED_PATTERNS,
        "roles": ROLE_DESCRIPTIONS,
        "role_summary": role_summary,
        "files": file_entries,
    }
    manifest_path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")

    print(f"生成 {package_type}: {package_path}")
    print(f"清单 {manifest_path}")
    print(f"文件数 {len(files)}")
    print("角色汇总")
    for role, count in role_summary.items():
        print(f"- {role}: {count}")


def main() -> int:
    DIST.mkdir(exist_ok=True)
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    all_files = iter_files()
    competition_files = [path for path in all_files if classify_file(path) in COMPETITION_ROLES]

    write_package(
        DIST / f"memguardos_competition_submission_{timestamp}.zip",
        DIST / f"memguardos_competition_submission_{timestamp}_manifest.json",
        competition_files,
        "竞赛提交包",
    )
    print()
    write_package(
        DIST / f"memguardos_full_repository_{timestamp}.zip",
        DIST / f"memguardos_full_repository_{timestamp}_manifest.json",
        all_files,
        "完整开源仓库包",
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
