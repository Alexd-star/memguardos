#!/usr/bin/env python3
from __future__ import annotations

from pathlib import Path
from typing import Any, Callable
import argparse
import json
import re
import subprocess
import sys
import time


ROOT = Path(__file__).resolve().parents[1]


Runner = Callable[..., subprocess.CompletedProcess]


def run_security_scan(
    *,
    output_json: Path,
    output_markdown: Path,
    runner: Runner = subprocess.run,
    python: str = sys.executable,
    allow_missing_tools: bool = False,
    dependency_scope: str = "runtime",
) -> dict[str, Any]:
    started = time.perf_counter()
    bandit = _run_bandit(runner=runner, python=python)
    pip_audit = _run_pip_audit(
        runner=runner,
        python=python,
        dependency_scope=dependency_scope,
    )
    bandit["command"] = _portable_command(bandit.get("command", []), python=python)
    pip_audit["command"] = _portable_command(pip_audit.get("command", []), python=python)
    missing_tools = [
        name
        for name, result in (("bandit", bandit), ("pip_audit", pip_audit))
        if result["status"] == "missing"
    ]
    bandit_high_count = int(bandit.get("high_count", 0))
    bandit_medium_count = int(bandit.get("medium_count", 0))
    pip_vulnerability_count = int(pip_audit.get("vulnerability_count", 0))
    ok = (
        bandit_high_count == 0
        and bandit_medium_count == 0
        and pip_vulnerability_count == 0
        and (allow_missing_tools or not missing_tools)
        and bandit["status"] not in {"error"}
        and pip_audit["status"] not in {"error"}
    )
    report = {
        "schema": "memguardos-security-scan-v1",
        "ok": ok,
        "elapsed_s": round(time.perf_counter() - started, 3),
        "policy": {
            "bandit_high_severity_allowed": 0,
            "bandit_medium_severity_allowed": 0,
            "pip_audit_known_vulnerabilities_allowed": 0,
            "missing_tools_allowed": bool(allow_missing_tools),
            "dependency_scope": dependency_scope,
        },
        "summary": {
            "bandit_high_count": bandit_high_count,
            "bandit_medium_count": bandit_medium_count,
            "bandit_low_count": int(bandit.get("low_count", 0)),
            "pip_audit_vulnerability_count": pip_vulnerability_count,
            "missing_tools": missing_tools,
        },
        "tools": {
            "bandit": bandit,
            "pip_audit": pip_audit,
        },
    }
    output_json.parent.mkdir(parents=True, exist_ok=True)
    output_json.write_text(
        json.dumps(report, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    output_markdown.parent.mkdir(parents=True, exist_ok=True)
    output_markdown.write_text(_render_markdown(report), encoding="utf-8")
    return report


def _run_bandit(*, runner: Runner, python: str) -> dict[str, Any]:
    command = [
        python,
        "-m",
        "bandit",
        "-q",
        "-r",
        "memguard",
        "scripts",
        "-f",
        "json",
    ]
    completed = runner(
        command,
        cwd=ROOT,
        text=True,
        capture_output=True,
        check=False,
    )
    if _module_missing(completed, "bandit"):
        return _missing_tool("bandit", command, completed)
    payload = _parse_json_output(completed.stdout)
    if payload is None:
        return _tool_error("bandit", command, completed, "无法解析 Bandit JSON 输出")
    results = [item for item in payload.get("results", []) if isinstance(item, dict)]
    severity_counts = {"HIGH": 0, "MEDIUM": 0, "LOW": 0}
    issues = []
    for item in results:
        severity = str(item.get("issue_severity") or "").upper()
        if severity in severity_counts:
            severity_counts[severity] += 1
        issues.append(
            {
                "filename": item.get("filename"),
                "line_number": item.get("line_number"),
                "severity": severity or item.get("issue_severity"),
                "confidence": item.get("issue_confidence"),
                "test_id": item.get("test_id"),
                "issue_text": item.get("issue_text"),
            }
        )
    high_count = severity_counts["HIGH"]
    status = "issues" if high_count else ("warnings" if issues else "clean")
    return {
        "status": status,
        "command": command,
        "returncode": completed.returncode,
        "high_count": severity_counts["HIGH"],
        "medium_count": severity_counts["MEDIUM"],
        "low_count": severity_counts["LOW"],
        "issue_count": len(issues),
        "issues": issues[:50],
        "stderr_tail": _tail(completed.stderr),
    }


def _run_pip_audit(
    *,
    runner: Runner,
    python: str,
    dependency_scope: str,
) -> dict[str, Any]:
    if dependency_scope == "runtime":
        runtime_dependencies = _runtime_dependencies(ROOT / "pyproject.toml")
        if not runtime_dependencies:
            return {
                "status": "clean",
                "command": [],
                "returncode": 0,
                "dependency_scope": "runtime",
                "dependency_count": 0,
                "vulnerability_count": 0,
                "vulnerable_dependencies": [],
                "note": "项目默认运行时依赖为空，pip-audit 无需审计第三方运行时包。",
                "stderr_tail": "",
            }
        requirements = ROOT / ".security-runtime-requirements.txt"
        requirements.write_text(
            "\n".join(runtime_dependencies) + "\n",
            encoding="utf-8",
        )
        command = [
            python,
            "-m",
            "pip_audit",
            "-r",
            str(requirements),
            "-f",
            "json",
            "--progress-spinner",
            "off",
        ]
    elif dependency_scope == "environment":
        command = [
            python,
            "-m",
            "pip_audit",
            "-f",
            "json",
            "--progress-spinner",
            "off",
        ]
    else:
        return {
            "status": "error",
            "command": [],
            "returncode": 2,
            "dependency_scope": dependency_scope,
            "reason": "unsupported dependency scope",
            "vulnerability_count": -1,
            "stderr_tail": "",
        }
    completed = runner(
        command,
        cwd=ROOT,
        text=True,
        capture_output=True,
        check=False,
    )
    if _module_missing(completed, "pip_audit"):
        return _missing_tool("pip_audit", command, completed)
    payload = _parse_json_output(completed.stdout)
    if payload is None:
        return _tool_error("pip_audit", command, completed, "无法解析 pip-audit JSON 输出")
    dependencies = payload.get("dependencies", [])
    vulnerability_count = 0
    vulnerable_dependencies = []
    if isinstance(dependencies, list):
        for dependency in dependencies:
            if not isinstance(dependency, dict):
                continue
            vulns = dependency.get("vulns", [])
            if not isinstance(vulns, list):
                continue
            vulnerability_count += len(vulns)
            if vulns:
                vulnerable_dependencies.append(
                    {
                        "name": dependency.get("name"),
                        "version": dependency.get("version"),
                        "vulnerability_count": len(vulns),
                        "ids": [
                            str(vuln.get("id"))
                            for vuln in vulns
                            if isinstance(vuln, dict) and vuln.get("id")
                        ][:10],
                    }
                )
    status = "clean" if vulnerability_count == 0 else "issues"
    return {
        "status": status,
        "command": command,
        "returncode": completed.returncode,
        "dependency_scope": dependency_scope,
        "dependency_count": len(dependencies) if isinstance(dependencies, list) else 0,
        "vulnerability_count": vulnerability_count,
        "vulnerable_dependencies": vulnerable_dependencies[:50],
        "stderr_tail": _tail(completed.stderr),
    }


def _module_missing(completed: subprocess.CompletedProcess, module_name: str) -> bool:
    combined = "%s\n%s" % (completed.stdout or "", completed.stderr or "")
    return (
        completed.returncode != 0
        and (
            "No module named %s" % module_name in combined
            or "No module named '%s'" % module_name in combined
        )
    )


def _missing_tool(
    name: str,
    command: list[str],
    completed: subprocess.CompletedProcess,
) -> dict[str, Any]:
    return {
        "status": "missing",
        "command": command,
        "returncode": completed.returncode,
        "install_hint": "python -m pip install %s" % ("pip-audit" if name == "pip_audit" else name),
        "stderr_tail": _tail(completed.stderr),
    }


def _tool_error(
    name: str,
    command: list[str],
    completed: subprocess.CompletedProcess,
    reason: str,
) -> dict[str, Any]:
    return {
        "status": "error",
        "command": command,
        "returncode": completed.returncode,
        "reason": reason,
        "stdout_tail": _tail(completed.stdout),
        "stderr_tail": _tail(completed.stderr),
    }


def _parse_json_output(text: str) -> dict[str, Any] | None:
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


def _runtime_dependencies(pyproject: Path) -> list[str]:
    text = pyproject.read_text(encoding="utf-8")
    match = re.search(
        r"^\[project\](.*?)(?:^\[.+\]|\Z)",
        text,
        re.MULTILINE | re.DOTALL,
    )
    if not match:
        return []
    block = match.group(1)
    dep_match = re.search(
        r"^dependencies\s*=\s*\[(.*?)\]\s*$",
        block,
        re.MULTILINE | re.DOTALL,
    )
    if not dep_match:
        return []
    return re.findall(r'"([^"]+)"', dep_match.group(1))


def _tail(text: str, limit: int = 2000) -> str:
    text = text or ""
    return text[-limit:]


def _portable_command(command: list[str], *, python: str) -> list[str]:
    if not command:
        return []
    portable = list(command)
    if portable[0] == python:
        portable[0] = "python"
    return portable


def _render_markdown(report: dict[str, Any]) -> str:
    bandit = report["tools"]["bandit"]
    pip_audit = report["tools"]["pip_audit"]
    lines = [
        "# MemGuardOS 安全扫描报告",
        "",
        "本文档记录当前代码树的源码静态安全检查和 Python 依赖已知漏洞审计结果。Bandit 用于扫描 Python 源码中的高风险用法，pip-audit 用于检查当前 Python 环境中的依赖是否命中公开漏洞库。两者覆盖范围不同，不能互相替代。",
        "",
        "## 结果摘要",
        "",
        "| 项目 | 结果 |",
        "|---|---:|",
        "| 总体状态 | %s |" % ("通过" if report["ok"] else "未通过"),
        "| Bandit 高危问题 | %s |" % report["summary"]["bandit_high_count"],
        "| Bandit 中危问题 | %s |" % report["summary"]["bandit_medium_count"],
        "| pip-audit 已知漏洞 | %s |"
        % report["summary"]["pip_audit_vulnerability_count"],
        "| 缺失工具 | %s |"
        % ("、".join(report["summary"]["missing_tools"]) or "无"),
        "",
        "## Bandit 源码静态检查",
        "",
        "- 状态：`%s`" % bandit["status"],
        "- 命令：`%s`" % " ".join(str(part) for part in bandit.get("command", [])),
        "- 策略：不允许未审查的中危或高危问题进入最终提交。",
        "",
    ]
    if bandit["status"] == "missing":
        lines.append("- 说明：Bandit 未安装。正式提交前应安装并重新运行扫描。")
    elif bandit["status"] in {"issues", "warnings"}:
        lines.extend(
            [
                "| 文件 | 行号 | 严重级别 | 规则 | 说明 |",
                "|---|---:|---|---|---|",
            ]
        )
        for issue in bandit.get("issues", []):
            lines.append(
                "| %s | %s | %s | %s | %s |"
                % (
                    issue.get("filename"),
                    issue.get("line_number"),
                    issue.get("severity"),
                    issue.get("test_id"),
                    str(issue.get("issue_text")).replace("|", "/"),
                )
            )
    else:
        lines.append("- 结果：未发现高危源码安全问题。")
    lines.extend(
        [
            "",
            "## pip-audit 依赖漏洞审计",
            "",
            "- 状态：`%s`" % pip_audit["status"],
            "- 审计范围：`%s`" % pip_audit.get("dependency_scope", "runtime"),
            "- 命令：`%s`" % " ".join(str(part) for part in pip_audit.get("command", [])),
            "- 策略：不允许已知依赖漏洞进入最终提交。",
            "",
        ]
    )
    if pip_audit["status"] == "missing":
        lines.append("- 说明：pip-audit 未安装。正式提交前应安装并重新运行扫描。")
    elif pip_audit["status"] == "issues":
        lines.extend(
            [
                "| 依赖 | 版本 | 漏洞数量 | 漏洞编号 |",
                "|---|---|---:|---|",
            ]
        )
        for dependency in pip_audit.get("vulnerable_dependencies", []):
            lines.append(
                "| %s | %s | %s | %s |"
                % (
                    dependency.get("name"),
                    dependency.get("version"),
                    dependency.get("vulnerability_count"),
                    "、".join(dependency.get("ids", [])),
                )
            )
    else:
        lines.append("- 结果：未发现当前环境依赖命中公开漏洞库。")
    lines.extend(
        [
            "",
            "## 边界说明",
            "",
            "安全扫描是企业级交付中的自动化质量门禁之一。它能发现常见源码高风险写法和公开依赖漏洞，但不能替代人工代码审查、运行时隔离、权限最小化、审计链验证和 openKylin 真实环境测试。本项目将扫描结果与 SBOM、测试报告、openKylin 证据和提交包审计共同使用。",
            "",
        ]
    )
    return "\n".join(lines)


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Run MemGuardOS security scan gates")
    parser.add_argument("--output", default="docs/security_scan.json")
    parser.add_argument("--markdown", default="output/security_scan.md")
    parser.add_argument(
        "--allow-missing-tools",
        action="store_true",
        help="Record missing Bandit/pip-audit tools without failing the command",
    )
    parser.add_argument(
        "--dependency-scope",
        choices=["runtime", "environment"],
        default="runtime",
        help="Audit project runtime dependencies by default; use environment for full local Python environment diagnostics",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    report = run_security_scan(
        output_json=(ROOT / args.output).resolve()
        if not Path(args.output).is_absolute()
        else Path(args.output),
        output_markdown=(ROOT / args.markdown).resolve()
        if not Path(args.markdown).is_absolute()
        else Path(args.markdown),
        allow_missing_tools=bool(args.allow_missing_tools),
        dependency_scope=args.dependency_scope,
    )
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0 if report["ok"] else 2


if __name__ == "__main__":
    raise SystemExit(main())
