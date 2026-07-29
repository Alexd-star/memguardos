#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import re
import shutil
import stat
import subprocess
import sys
from tempfile import TemporaryDirectory

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from memguard.cli.memguard import _demo_result  # noqa: E402
from memguard.integrations.openhands_adapter import detect_openhands_runtime  # noqa: E402
from memguard.service import MemGuardService  # noqa: E402


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="录制 MemGuardOS 演示视频前的只读预检")
    parser.add_argument("--workspace", default=str(ROOT), help="真实 Agent 演示使用的工作区")
    parser.add_argument("--require-agent", action="store_true", help="将真实 OpenHands 和模型配置缺失视为失败")
    parser.add_argument("--require-current-evidence", action="store_true", help="要求 openKylin 证据修订与当前 Git HEAD 完全一致")
    parser.add_argument("--json", action="store_true", help="输出机器可读结果")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    checks: list[dict[str, object]] = []

    def add(name: str, ok: bool, detail: str, *, required: bool = True) -> None:
        checks.append({"name": name, "ok": bool(ok), "detail": detail, "required": required})

    add("Python 版本", sys.version_info >= (3, 10), "%s.%s.%s" % sys.version_info[:3])
    workspace = Path(args.workspace).expanduser().resolve()
    add("Agent 工作区", workspace.is_dir() and (workspace / "memguard").is_dir(), str(workspace))
    add("代码搜索工具", shutil.which("rg") is not None, shutil.which("rg") or "未找到 rg")

    with TemporaryDirectory(prefix="memguardos-video-preflight-") as temporary:
        root = Path(temporary)
        service = MemGuardService(
            store_path=root / "memory.segment",
            db_path=root / "memory.sqlite3",
            namespace="video-preflight",
        )
        try:
            result = _demo_result(service, "查找精准遗忘的实现位置")
            verification = (result.get("forget_result") or {}).get("verification_report") or {}
            failed_steps = [
                item.get("name")
                for item in verification.get("verification_steps") or []
                if item.get("status") == "failed"
            ]
            add(
                "离线完整闭环",
                bool((result.get("audit") or {}).get("ok")) and not failed_steps and bool(result.get("sensitive_notice")),
                "写入、上下文、敏感阻断、遗忘和审计已执行" if not failed_steps else "失败步骤：%s" % "、".join(failed_steps),
            )
        finally:
            service.close()

    runtime = detect_openhands_runtime()
    add(
        "OpenHands 运行时",
        bool(runtime.get("available")),
        str(runtime.get("version") or runtime.get("reason") or runtime),
        required=args.require_agent,
    )
    config = Path.home() / ".config" / "memguardos"
    key_path = config / "openhands_api_key"
    key_ok = key_path.is_file() and key_path.stat().st_size > 0
    if key_ok and os.name == "posix":
        key_ok = stat.S_IMODE(key_path.stat().st_mode) == 0o600
    add("模型凭据", key_ok, "已配置且不输出内容" if key_ok else "缺少权限为 600 的 openhands_api_key", required=args.require_agent)
    model_path = config / "openhands_model"
    base_url_path = config / "openhands_base_url"
    add("模型名称", model_path.is_file() and bool(model_path.read_text(encoding="utf-8").strip()), str(model_path), required=args.require_agent)
    add("模型服务地址", base_url_path.is_file() and bool(base_url_path.read_text(encoding="utf-8").strip()), str(base_url_path), required=args.require_agent)

    benchmark_manifest = _load_json(ROOT / "data" / "benchmarks_v2" / "manifest.json")
    sample_counts = benchmark_manifest.get("sample_counts") or {}
    total_samples = sum(int(value) for value in sample_counts.values()) if isinstance(sample_counts, dict) else 0
    holdout_samples = (
        sum(int(value) for name, value in sample_counts.items() if str(name).startswith("holdout/"))
        if isinstance(sample_counts, dict)
        else 0
    )
    self_check = benchmark_manifest.get("self_check") or {}
    add(
        "EvidenceBench v2 数据",
        total_samples == 260 and holdout_samples == 60 and all(bool(value) for value in self_check.values()),
        "%d 条，其中 holdout %d 条，数据自检%s"
        % (total_samples, holdout_samples, "通过" if self_check and all(bool(value) for value in self_check.values()) else "未通过"),
    )

    agent_metrics = _load_json(ROOT / "docs" / "evidence" / "agent" / "base-benchmark-metrics.json")
    add(
        "Agent 专项指标",
        agent_metrics.get("case_count") == 200 and not agent_metrics.get("failures"),
        "200 条，P95 %.3f ms" % float((agent_metrics.get("latency_ms") or {}).get("p95") or 0.0),
    )
    performance = _load_json(ROOT / "docs" / "evidence" / "performance_latest" / "eval_result_openkylin_10k_optimized.json")
    latency = performance.get("latency_load") or {}
    add(
        "10k 性能证据",
        float(latency.get("p95_latency_ms") or 999999) < 500 and float(latency.get("p99_latency_ms") or 999999) < 500,
        "P95 %.3f ms，P99 %.3f ms" % (float(latency.get("p95_latency_ms") or 0.0), float(latency.get("p99_latency_ms") or 0.0)),
    )
    retrieval = _load_json(ROOT / "docs" / "evidence" / "openkylin" / "retrieval_v2.json")
    gate = retrieval.get("gate") or {}
    add(
        "三轮检索证据",
        bool(gate.get("pass")) and int(gate.get("evaluated_rounds") or 0) >= 3,
        "%s 轮，门禁%s" % (gate.get("evaluated_rounds", 0), "通过" if gate.get("pass") else "未通过"),
    )

    head, revision_mode = _source_revision(ROOT)
    dirty = _source_dirty(ROOT, revision_mode)
    add(
        "Git 工作树",
        not dirty,
        (
            "已提交且无本地改动"
            if revision_mode == "git" and not dirty
            else "已验证同步修订 %s" % head[:12]
            if revision_mode in {"environment", "revision_file"} and not dirty
            else "存在未提交改动或缺少可信修订标记，正式录制前需要形成稳定提交"
        ),
        required=args.require_current_evidence,
    )
    source_revision = _load_json(ROOT / "docs" / "evidence" / "openkylin" / "source_revision.json").get("source_revision", "")
    revision_ok = bool(head and source_revision and head == source_revision)
    add(
        "证据修订一致性",
        revision_ok,
        "当前 %s；openKylin 证据 %s" % (head[:12] or "未知", str(source_revision)[:12] or "未知"),
        required=args.require_current_evidence,
    )

    required_failures = [item for item in checks if item["required"] and not item["ok"]]
    result = {"ok": not required_failures, "checks": checks, "required_failures": len(required_failures)}
    if args.json:
        print(json.dumps(result, ensure_ascii=False, indent=2))
    else:
        print("MemGuardOS 演示录制预检")
        print("=" * 56)
        for item in checks:
            state = "通过" if item["ok"] else ("需要处理" if item["required"] else "提示")
            print("[%s] %s：%s" % (state, item["name"], item["detail"]))
        print("=" * 56)
        if required_failures:
            print("结论：尚不适合正式录制，请先处理 %d 项必需条件。" % len(required_failures))
        else:
            print("结论：核心演示链路可用。正式录制前再运行一次带 --require-agent 的预检。")
    return 0 if result["ok"] else 2


def _load_json(path: Path) -> dict[str, object]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    return value if isinstance(value, dict) else {}


_REVISION_RE = re.compile(r"^[0-9a-f]{40}$")


def _source_revision(root: Path) -> tuple[str, str]:
    try:
        completed = subprocess.run(
            ["git", "rev-parse", "HEAD"],
            cwd=root,
            check=True,
            capture_output=True,
            text=True,
        )
    except (OSError, subprocess.CalledProcessError):
        revision = ""
    else:
        revision = completed.stdout.strip()
    if _REVISION_RE.fullmatch(revision):
        return revision, "git"

    environment_revision = os.environ.get("MEMGUARD_SOURCE_REVISION", "").strip()
    if _REVISION_RE.fullmatch(environment_revision):
        return environment_revision, "environment"

    revision_file = root / ".source_revision"
    if revision_file.is_file():
        file_revision = revision_file.read_text(encoding="utf-8").strip()
        if _REVISION_RE.fullmatch(file_revision):
            return file_revision, "revision_file"
    return "", "unknown"


def _source_dirty(root: Path, revision_mode: str) -> bool:
    if revision_mode in {"environment", "revision_file"}:
        return False
    if revision_mode != "git":
        return True
    try:
        completed = subprocess.run(
            ["git", "status", "--porcelain"],
            cwd=root,
            check=True,
            capture_output=True,
            text=True,
        )
    except (OSError, subprocess.CalledProcessError):
        return True
    return bool(completed.stdout.strip())


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
