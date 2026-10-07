from __future__ import annotations

import argparse
import hashlib
import json
import re
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scripts.release_inventory import project_version  # noqa: E402
DEFAULT_SBOM = ROOT / "docs" / "sbom.json"
DEFAULT_MARKDOWN = ROOT / "output" / "sbom.md"

CORE_ARTIFACTS = [
    "pyproject.toml",
    "LICENSE",
    "README.md",
    "memguard/service.py",
    "memguard/api/server.py",
    "memguard/web/dashboard.py",
    "memguard/integrations/openhands_adapter.py",
    "memguard/integrations/openhands_runner.py",
    "memguard/retrieval/hybrid.py",
    "memguard/retrieval/kylin_sdk.py",
    "memguard/store/mmap_store.py",
    "memguard/store/sqlite_meta.py",
    "memguard/extraction/semantic.py",
    "memguard/forgetting/planner.py",
    "memguard/eval/memory_value.py",
    "memguard/eval/real_agent_behavior.py",
    "scripts/run_openhands_behavior_suite.py",
    "scripts/run_security_scan.py",
    "scripts/collect_openkylin_evidence.sh",
    "scripts/verify_deb_package_set.sh",
    "scripts/collect_memory_value_evidence.sh",
    "scripts/build_deb.sh",
    "native/bpf_lsm/memguard_lsm.bpf.c",
    "native/ukui/peony-memguard/memguard_peony_plugin.cpp",
    "native/ukui/ukcc-memguard/memguard_ukcc_plugin.cpp",
    "data/benchmarks/agent_memory_value.jsonl",
    "data/benchmarks/real_agent_behavior.jsonl",
    "data/benchmarks/real_agent_behavior_holdout.jsonl",
]


def generate_sbom(
    sbom_path: Path | str = DEFAULT_SBOM,
    markdown_path: Path | str = DEFAULT_MARKDOWN,
) -> dict[str, Any]:
    project = _read_project_metadata(ROOT / "pyproject.toml")
    sbom = {
        "schema": "memguardos-sbom-v1",
        "name": project["name"],
        "version": project["version"],
        "generated_at": datetime.now(timezone.utc).replace(microsecond=0).isoformat(),
        "source": {
            "root": "project-root",
            "license": _license_name(),
            "python": project["requires_python"],
        },
        "dependencies": {
            "runtime": project["dependencies"],
            "optional": project["optional_dependencies"],
        },
        "artifacts": _artifact_entries(CORE_ARTIFACTS),
        "security_notes": [
            "默认运行时依赖为空，核心功能仅依赖 Python 标准库，降低供应链攻击面。",
            "OpenHands 真实 Agent 适配依赖被放入可选依赖组 agent-openhands，不进入默认安装路径。",
            "Bandit 与 pip-audit 被放入可选依赖组 security，用于发布前源码安全扫描和依赖漏洞审计。",
            "交付包同时保留 manifest、SHA-256 清单、审计链和 openKylin 证据，用于复验构建与运行结果。",
            "SBOM 记录核心源码、脚本、原生增强和 benchmark 数据的哈希，便于提交前后比对。",
        ],
    }
    sbom_path = _resolve(sbom_path)
    markdown_path = _resolve(markdown_path)
    sbom_path.parent.mkdir(parents=True, exist_ok=True)
    markdown_path.parent.mkdir(parents=True, exist_ok=True)
    sbom_path.write_text(
        json.dumps(sbom, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    markdown_path.write_text(_to_markdown(sbom), encoding="utf-8")
    return sbom


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Generate MemGuardOS lightweight SBOM")
    parser.add_argument("--output", default=str(DEFAULT_SBOM), help="SBOM JSON output path")
    parser.add_argument(
        "--markdown",
        default=str(DEFAULT_MARKDOWN),
        help="Chinese Markdown explanation output path",
    )
    args = parser.parse_args(argv)
    sbom = generate_sbom(args.output, args.markdown)
    print(json.dumps({"ok": True, "name": sbom["name"], "version": sbom["version"], "artifacts": len(sbom["artifacts"])}, ensure_ascii=False))
    return 0


def _read_project_metadata(pyproject: Path) -> dict[str, Any]:
    text = pyproject.read_text(encoding="utf-8")
    project_block = _section(text, "project")
    optional_block = _section(text, "project.optional-dependencies")
    return {
        "name": _string_value(project_block, "name"),
        "version": project_version(),
        "requires_python": _string_value(project_block, "requires-python"),
        "dependencies": _array_value(project_block, "dependencies"),
        "optional_dependencies": _optional_dependencies(optional_block),
    }


def _section(text: str, name: str) -> str:
    pattern = re.compile(r"^\[%s\]\s*$" % re.escape(name), re.MULTILINE)
    match = pattern.search(text)
    if not match:
        return ""
    start = match.end()
    next_match = re.search(r"^\[.+\]\s*$", text[start:], re.MULTILINE)
    end = start + next_match.start() if next_match else len(text)
    return text[start:end]


def _string_value(block: str, key: str) -> str:
    match = re.search(r'^%s\s*=\s*"([^"]*)"\s*$' % re.escape(key), block, re.MULTILINE)
    return match.group(1) if match else ""


def _array_value(block: str, key: str) -> list[str]:
    match = re.search(r"^%s\s*=\s*\[(.*?)\]\s*$" % re.escape(key), block, re.MULTILINE | re.DOTALL)
    if not match:
        return []
    return re.findall(r'"([^"]+)"', match.group(1))


def _optional_dependencies(block: str) -> dict[str, list[str]]:
    result: dict[str, list[str]] = {}
    for match in re.finditer(r'^([A-Za-z0-9_.-]+)\s*=\s*\[(.*?)\]\s*$', block, re.MULTILINE | re.DOTALL):
        result[match.group(1)] = re.findall(r'"([^"]+)"', match.group(2))
    return dict(sorted(result.items()))


def _artifact_entries(paths: list[str]) -> list[dict[str, Any]]:
    entries = []
    for rel in paths:
        path = ROOT / rel
        if not path.exists():
            entries.append({"path": rel, "present": False, "sha256": "", "bytes": 0})
            continue
        data = path.read_bytes()
        entries.append(
            {
                "path": rel,
                "present": True,
                "sha256": hashlib.sha256(data).hexdigest(),
                "bytes": len(data),
            }
        )
    return entries


def _license_name() -> str:
    license_path = ROOT / "LICENSE"
    if not license_path.exists():
        return "未检测到 LICENSE"
    first_line = license_path.read_text(encoding="utf-8", errors="replace").splitlines()[0]
    return first_line.strip() or "LICENSE 已提供"


def _to_markdown(sbom: dict[str, Any]) -> str:
    optional = sbom["dependencies"]["optional"]
    lines = [
        "# MemGuardOS 软件物料清单与供应链说明",
        "",
        "本文档面向提交审核、开源归档和后续维护，说明 MemGuardOS 的核心软件物料、依赖分组、哈希校验和供应链风险控制方式。SBOM JSON 机器可读清单位于 `docs/sbom.json`。",
        "",
        "## 基本信息",
        "",
        "| 项目 | 内容 |",
        "|---|---|",
        f"| 产品名称 | {sbom['name']} |",
        f"| 版本 | {sbom['version']} |",
        f"| Python 版本要求 | {sbom['source']['python']} |",
        f"| 许可证 | {sbom['source']['license']} |",
        f"| 生成时间 | {sbom['generated_at']} |",
        "",
        "## 依赖分组",
        "",
        "### 运行时依赖",
        "",
    ]
    runtime = sbom["dependencies"]["runtime"]
    if runtime:
        lines.extend(f"- `{item}`" for item in runtime)
    else:
        lines.append("- 默认运行时依赖为空，核心 CLI、daemon、检索、审计和精准遗忘链路只依赖 Python 标准库。")
    lines.extend(["", "### 开发与测试依赖", ""])
    for item in optional.get("dev", []):
        lines.append(f"- `{item}`")
    lines.extend(["", "### OpenHands 适配依赖", ""])
    for item in optional.get("agent-openhands", []):
        lines.append(f"- `{item}`")
    lines.extend(["", "### 安全扫描依赖", ""])
    for item in optional.get("security", []):
        lines.append(f"- `{item}`")
    lines.extend(
        [
            "",
            "## 核心交付物哈希",
            "",
            "| 路径 | 是否存在 | 字节数 | SHA-256 |",
            "|---|---:|---:|---|",
        ]
    )
    for item in sbom["artifacts"]:
        lines.append(
            "| `%s` | %s | %s | `%s` |"
            % (
                item["path"],
                "是" if item["present"] else "否",
                item["bytes"],
                item["sha256"] or "缺失",
            )
        )
    lines.extend(["", "## 供应链控制说明", ""])
    lines.extend(f"- {note}" for note in sbom["security_notes"])
    lines.extend(
        [
            "",
            "## 复验方式",
            "",
            "1. 运行 `python scripts/generate_sbom.py` 重新生成 `docs/sbom.json` 和本文档。",
            "2. 运行 `python scripts/check_competition_ready.py` 检查 SBOM、证据包、文档和源码门禁。",
            "3. 运行 `python scripts/package_submission.py` 后，再运行 `python scripts/inspect_submission_package.py` 与 `python scripts/smoke_submission_archive.py` 检查提交包。",
            "",
        ]
    )
    return "\n".join(lines)


def _resolve(path: Path | str) -> Path:
    raw = Path(path)
    return raw if raw.is_absolute() else ROOT / raw


if __name__ == "__main__":
    raise SystemExit(main())
