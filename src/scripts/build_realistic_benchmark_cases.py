from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
BENCHMARK_DIR = ROOT / "data" / "benchmarks"


def build_cases() -> dict[str, list[dict[str, Any]]]:
    """Return deterministic, sanitized OS Agent benchmark cases.

    The cases are inspired by MemGuardOS openKylin evidence and development
    traces, but they avoid machine-specific hostnames, accounts, tokens, or
    private paths. They are meant to answer the "benchmark realism" question
    without pulling in external logs or privacy-sensitive material.
    """

    return {
        "memory_seed.jsonl": [
            {
                "label": "knowledge_real_sdk_recovery_runbook",
                "text": "真实故障修复案例：Kylin embedding SDK 推理超时后，先检查 kytensor.service、kylin-ai-runtime.service、core-textembedding.sock 和模型仓库，再运行 diagnose_kylin_embedding.sh 复验 768 维向量。",
                "expected_type": "knowledge",
                "expected_subtype": "case",
            },
            {
                "label": "knowledge_real_ukui_visible_cache",
                "text": "真实案例：Peony 右键菜单不可见时，安装 .so 后需要重启 peony 或重新登录 UKUI 桌面，再检查 MemGuardOS 记忆安全入口。",
                "expected_type": "knowledge",
                "expected_subtype": "case",
            },
            {
                "label": "knowledge_real_deb_ostree_guard",
                "text": "真实案例：openKylin 普通模式 apt install memguardos.deb 被 ostree-pkgs-guard 拦截，应进入维护模式或使用离线解包 smoke 验证包内容。",
                "expected_type": "knowledge",
                "expected_subtype": "case",
            },
            {
                "label": "knowledge_real_audit_hash_repair",
                "text": "审计链失败处理流程：先停止 memguardd，确认 namespace，保存 verify-audit 输出，恢复完整备份，再重新运行 verify-audit。",
                "expected_type": "knowledge",
                "expected_subtype": "workflow",
            },
            {
                "label": "knowledge_real_video_smoke_order",
                "text": "演示视频流程：先展示 doctor 和 verify-audit，再展示 ingest、build-context、forget --yes --compact、systemd/cgroup 和 SDK 证据。",
                "expected_type": "knowledge",
                "expected_subtype": "workflow",
            },
            {
                "label": "knowledge_real_submission_package_audit",
                "text": "正式提交包审计流程：生成 memguardos_competition_submission zip 后，运行 inspect_submission_package 检查正式文档、证据内容、本机路径和弱 SDK 证据。",
                "expected_type": "knowledge",
                "expected_subtype": "workflow",
            },
            {
                "label": "pref_real_answer_evidence_first",
                "text": "答辩回答请先给证据结论，再说明边界，不要把可选 SDK、BPF LSM 或 UKUI 插件能力夸大。",
                "expected_type": "preference",
                "expected_subtype": "output_style",
            },
            {
                "label": "pref_real_openkylin_video_short_path",
                "text": "录制 openKylin 演示视频时，请优先展示核心闭环，不要把大量长日志逐行念完。",
                "expected_type": "preference",
                "expected_subtype": "user_habit",
            },
            {
                "label": "knowledge_real_auditd_shadow_log",
                "text": "真实风格 auditd 原始日志：type=PATH msg=audit(1717000000.123:420): item=0 name=\"/etc/shadow\" nametype=NORMAL；该事件应转为 OS event 并触发 sensitive_os_source 阻断。",
                "expected_type": "knowledge",
                "expected_subtype": "fact",
            },
            {
                "label": "knowledge_real_systemd_exit_code_runbook",
                "text": "真实 systemd 故障流程：memguardd.service 显示 Result=exit-code 时，先运行 systemctl status，再看 journalctl -u memguardd，随后检查 PYTHONPATH、工作目录和 ExecStart 包装脚本。",
                "expected_type": "knowledge",
                "expected_subtype": "workflow",
            },
            {
                "label": "pref_real_shell_danger_confirm",
                "text": "用户行为偏好：shell history 出现 rm -rf、dd、mkfs 或密钥导出命令时，后续 Agent 必须先二次确认，再执行高风险操作。",
                "source_kind": "user_behavior",
                "expected_type": "sensitive_policy",
                "expected_subtype": "behavior_risk",
            },
            {
                "label": "knowledge_real_config_conflict_resolution",
                "text": "真实配置冲突案例：旧配置 answer.style=verbose，新手动配置 answer.style=concise_with_risk；冲突解决应选择更新时间更近且来源为 manual 的偏好。",
                "source_kind": "manual_config",
                "expected_type": "preference",
                "expected_subtype": "user_habit",
            },
            {
                "label": "knowledge_real_failed_test_repair_trace",
                "text": "失败修复轨迹：pytest 报告 web dashboard smoke 缺少诊断导出按钮后，先补页面按钮，再补 smoke 断言，最后复跑 pytest 和 inspect_submission_package。",
                "expected_type": "knowledge",
                "expected_subtype": "case",
            },
            {
                "label": "pref_real_rag_source_explain_first",
                "text": "答辩演示 RAG 上下文时，请先展示来源分布、selection trace 和脱敏状态，再展示完整 JSON。",
                "expected_type": "sensitive_policy",
                "expected_subtype": "security_policy",
            },
        ],
        "retrieval.jsonl": [
            {
                "query": "SDK 推理超时后应该检查哪些运行时组件？",
                "expected": ["knowledge_real_sdk_recovery_runbook"],
                "k": 5,
                "case": "realistic_sdk_recovery",
            },
            {
                "query": "Peony 右键菜单不可见怎么处理？",
                "expected": ["knowledge_real_ukui_visible_cache"],
                "k": 5,
                "case": "realistic_ukui_visibility",
            },
            {
                "query": "deb 被 ostree-pkgs-guard 拦截怎么办？",
                "expected": ["knowledge_real_deb_ostree_guard"],
                "k": 5,
                "case": "realistic_deb_guard",
            },
            {
                "query": "审计链 verify-audit 失败后如何恢复？",
                "expected": ["knowledge_real_audit_hash_repair"],
                "k": 5,
                "case": "realistic_audit_repair",
            },
            {
                "query": "演示视频最好按什么顺序展示？",
                "expected": ["knowledge_real_video_smoke_order"],
                "k": 5,
                "case": "realistic_video_order",
            },
            {
                "query": "正式提交包如何检查弱 SDK 证据和本机路径？",
                "expected": ["knowledge_real_submission_package_audit"],
                "k": 5,
                "case": "realistic_package_audit",
            },
            {
                "query": "答辩时如何避免夸大 SDK BPF 和 UKUI 能力？",
                "expected": ["pref_real_answer_evidence_first"],
                "k": 5,
                "case": "realistic_answer_boundary",
            },
            {
                "query": "openKylin 演示视频不要念什么内容？",
                "expected": ["pref_real_openkylin_video_short_path"],
                "k": 5,
                "case": "realistic_video_style",
            },
            {
                "query": "真实 auditd shadow 原始日志应该如何进入记忆安全链路？",
                "expected": ["knowledge_real_auditd_shadow_log"],
                "k": 5,
                "case": "realistic_auditd_shadow_raw_log",
            },
            {
                "query": "memguardd.service Result=exit-code 时应该怎么排查？",
                "expected": ["knowledge_real_systemd_exit_code_runbook"],
                "k": 5,
                "case": "realistic_systemd_exit_code",
            },
            {
                "query": "shell history 里出现 rm -rf 或 mkfs 时 Agent 应该怎么处理？",
                "expected": ["pref_real_shell_danger_confirm"],
                "k": 5,
                "case": "realistic_shell_danger_confirm",
            },
            {
                "query": "answer.style 新旧配置冲突时应该选择哪条？",
                "expected": ["knowledge_real_config_conflict_resolution"],
                "k": 5,
                "case": "realistic_config_conflict",
            },
            {
                "query": "Web 控制台 smoke 缺少诊断导出按钮后如何修复？",
                "expected": ["knowledge_real_failed_test_repair_trace"],
                "k": 5,
                "case": "realistic_failed_test_repair",
            },
            {
                "query": "RAG 上下文演示时应该先展示哪些解释信息？",
                "expected": ["pref_real_rag_source_explain_first"],
                "k": 5,
                "case": "realistic_rag_explain_demo",
            },
        ],
        "forgetting.jsonl": [
            {
                "request": "忘掉 Kylin SDK 超时修复流程",
                "expected_delete": ["knowledge_real_sdk_recovery_runbook"],
                "expected_keep": ["knowledge_embedding_sdk_zh", "knowledge_vector_sdk_en"],
            },
            {
                "request": "删除 Peony 右键菜单不可见处理案例",
                "expected_delete": ["knowledge_real_ukui_visible_cache"],
                "expected_keep": ["knowledge_real_ukui_plugin_verify", "knowledge_ukui_visible_restart"],
            },
            {
                "request": "忘掉 deb 被 ostree-pkgs-guard 拦截案例",
                "expected_delete": ["knowledge_real_deb_ostree_guard"],
                "expected_keep": ["knowledge_linux_smoke_portability"],
            },
            {
                "request": "忘掉审计链失败恢复流程",
                "expected_delete": ["knowledge_real_audit_hash_repair"],
                "expected_keep": ["knowledge_audit_concurrency"],
            },
            {
                "request": "删除正式提交包审计流程",
                "expected_delete": ["knowledge_real_submission_package_audit"],
                "expected_keep": ["knowledge_benchmark_realism"],
            },
            {
                "request": "忘掉 type=PATH name=/etc/shadow 的 auditd 原始日志样例",
                "expected_delete": ["knowledge_real_auditd_shadow_log"],
                "expected_keep": ["knowledge_auditd_shadow_case", "knowledge_journald_en"],
            },
            {
                "request": "删除 rm-rf dd mkfs 二次确认偏好",
                "expected_delete": ["pref_real_shell_danger_confirm", "pref_real_shell_destructive_confirm"],
                "expected_keep": ["knowledge_shell_history_hard_negative", "pref_real_answer_evidence_first"],
            },
            {
                "request": "忘掉 PYTHONPATH ExecStart 包装脚本排查流程",
                "expected_delete": ["knowledge_real_systemd_exit_code_runbook"],
                "expected_keep": ["knowledge_openkylin_service_zh"],
            },
            {
                "request": "删除答辩演示 selection trace 来源分布优先展示偏好",
                "expected_delete": ["pref_real_rag_source_explain_first"],
                "expected_keep": ["knowledge_context_token_budget", "knowledge_web_console_local"],
            },
        ],
        "sensitive_detection.jsonl": [
            {
                "text": "User behavior: shell_command command=cat /home/demo/.config/gcloud/application_default_credentials.json",
                "source_uri": "/home/demo/.config/gcloud/application_default_credentials.json",
                "expect_stored": False,
                "expect_redacted": False,
                "labels": ["sensitive_os_source"],
            },
            {
                "text": "OS event: auditd_access action=open path=/srv/memguardos/secrets/prod.env process=/usr/bin/python3",
                "source_uri": "/srv/memguardos/secrets/prod.env",
                "expect_stored": False,
                "expect_redacted": False,
                "labels": ["sensitive_os_source"],
            },
            {
                "text": "Shell history: export OPENAI_API_KEY=sk-test-example1234567890",
                "source_uri": "shell_history",
                "expect_stored": False,
                "expect_redacted": False,
                "labels": ["api_key", "token_assignment"],
            },
            {
                "text": "ssh 命令使用示例 ssh user@example.invalid，不包含私钥文件内容。",
                "source_uri": "inline",
                "expect_stored": True,
                "expect_redacted": False,
                "labels": [],
            },
            {
                "text": "这里讨论 token 预算和上下文成本，不代表出现真实访问令牌。",
                "source_uri": "inline",
                "expect_stored": True,
                "expect_redacted": False,
                "labels": [],
            },
            {
                "text": "配置 password_policy.min_length=16 是策略字段，不包含用户真实密码。",
                "source_uri": "inline",
                "expect_stored": True,
                "expect_redacted": False,
                "labels": [],
            },
        ],
        "procedural_memory.jsonl": [
            {
                "label": "procedure_sdk_probe_recovery",
                "query": "recover Kylin embedding timeout verify 768 vector",
                "command": "bash scripts/diagnose_kylin_embedding.sh --restart-runtime --probe-timeout 180 && bash scripts/run_kylin_embedding_probe.sh 'openKylin agent memory'",
                "cwd": "/opt/memguardos",
                "exit_code": 0,
                "stdout": "inference_ready=true dim=768",
                "stderr": "",
                "duration_ms": 180000.0,
                "tool_name": "shell",
                "state_before": "Kylin embedding SDK metadata available but inference probe timed out",
                "state_after": "runtime restarted and embedding probe returned 768 dimensional vector",
                "lesson": "when SDK metadata is reachable but inference times out, restart runtime, inspect sockets, then verify with C API style probe",
                "source_uri": "real_trace_sdk_recovery.json",
            },
            {
                "label": "procedure_ukui_refresh_visibility",
                "query": "Peony UKUI plugin visibility restart desktop verify menu",
                "command": "bash scripts/install_ukui_plugins.sh --no-build && pkill -x peony || true && nohup peony $(xdg-user-dir DESKTOP)",
                "cwd": "/opt/memguardos",
                "exit_code": 0,
                "stdout": "MemGuardOS menu visible",
                "stderr": "",
                "duration_ms": 3200.0,
                "tool_name": "shell",
                "state_before": "Peony plugin .so installed but right-click menu not refreshed",
                "state_after": "Peony restarted and MemGuardOS menu entry visible",
                "lesson": "after installing Peony extension, restart Peony or login session before checking right-click menu visibility",
                "source_uri": "real_trace_ukui_visibility.json",
            },
            {
                "label": "procedure_submission_package_audit",
                "query": "submission package inspect weak SDK evidence local path",
                "command": "python scripts/package_submission.py && python scripts/inspect_submission_package.py --package dist/memguardos_competition_submission.zip --manifest dist/manifest.json",
                "cwd": "/repo/memguardos",
                "exit_code": 0,
                "stdout": "ok true evidence_content_failures []",
                "stderr": "",
                "duration_ms": 2500.0,
                "tool_name": "python",
                "state_before": "formal docs and evidence changed before final submission",
                "state_after": "submission archive contains formal materials and rejects weak evidence",
                "lesson": "after refreshing evidence or docs, regenerate archive and inspect package before uploading",
                "source_uri": "real_trace_package_audit.json",
            },
            {
                "label": "procedure_audit_chain_recover",
                "query": "audit chain hash mismatch recover namespace backup verify",
                "command": "systemctl stop memguardd && python -m memguard.cli.memctl namespace-status && python -m memguard.cli.memctl verify-audit",
                "cwd": "/opt/memguardos",
                "exit_code": 0,
                "stdout": "verify_audit ok true",
                "stderr": "",
                "duration_ms": 1700.0,
                "tool_name": "shell",
                "state_before": "verify-audit reported hash mismatch after mixed evidence and database state",
                "state_after": "daemon stopped, namespace confirmed, complete backup restored, audit chain verified",
                "lesson": "when audit hash mismatch appears, preserve output, stop daemon, confirm namespace, restore complete backup, then verify audit",
                "source_uri": "real_trace_audit_recover.json",
            },
            {
                "label": "procedure_systemd_exit_code_repair",
                "query": "systemd Result exit-code journalctl ExecStart PYTHONPATH repair",
                "command": "systemctl status memguardd --no-pager && journalctl -u memguardd -n 80 --no-pager && python -m compileall -q memguard",
                "cwd": "/opt/memguardos",
                "exit_code": 0,
                "stdout": "Active: active (running)",
                "stderr": "",
                "duration_ms": 4100.0,
                "tool_name": "shell",
                "state_before": "memguardd.service showed Result=exit-code after package installation",
                "state_after": "service wrapper path fixed and memguardd.service became active",
                "lesson": "for systemd exit-code failures, inspect status, journalctl, ExecStart wrapper, PYTHONPATH and working directory before changing service logic",
                "source_uri": "real_trace_systemd_exit_code.json",
            },
            {
                "label": "procedure_web_dashboard_smoke_fix",
                "query": "web dashboard diagnostic export button smoke assertion repair",
                "command": "python scripts/web_dashboard_smoke.py && python -m pytest tests/test_packaging.py -q",
                "cwd": "/repo/memguardos",
                "exit_code": 0,
                "stdout": "diagnostics_export_button_ok true",
                "stderr": "",
                "duration_ms": 1900.0,
                "tool_name": "python",
                "state_before": "dashboard page loaded but productized diagnostic export evidence was not asserted",
                "state_after": "smoke test verifies diagnostics tab, export button, RAG visual and forget candidate visual",
                "lesson": "for UI polish, add smoke assertions for the visual capability that matters to reviewers",
                "source_uri": "real_trace_dashboard_smoke_fix.json",
            },
        ],
        "graph_retrieval.jsonl": [
            {
                "entity": "kytensor",
                "expected": ["knowledge_real_sdk_recovery_runbook"],
                "case": "realistic_kylin_runtime",
            },
            {
                "entity": "peony",
                "expected": ["knowledge_real_ukui_visible_cache"],
                "case": "realistic_peony_visibility",
            },
            {
                "entity": "ostree-pkgs-guard",
                "expected": ["knowledge_real_deb_ostree_guard"],
                "case": "realistic_deb_guard",
            },
            {
                "entity": "verify-audit",
                "expected": ["knowledge_real_audit_hash_repair"],
                "case": "realistic_audit_repair",
            },
            {
                "entity": "ExecStart",
                "expected": ["knowledge_real_systemd_exit_code_runbook"],
                "case": "realistic_systemd_exit_code",
            },
            {
                "entity": "RAG",
                "expected": ["pref_real_rag_source_explain_first"],
                "case": "realistic_rag_explain",
            },
        ],
    }


def unique_key(file_name: str, row: dict[str, Any]) -> str:
    if file_name in {"memory_seed.jsonl", "procedural_memory.jsonl"}:
        return str(row["label"])
    if file_name == "retrieval.jsonl":
        return str(row["query"])
    if file_name == "forgetting.jsonl":
        return str(row["request"])
    if file_name == "sensitive_detection.jsonl":
        return "%s|%s" % (row["text"], row.get("source_uri", ""))
    if file_name == "graph_retrieval.jsonl":
        return "%s|%s" % (row["entity"], row.get("case", ""))
    raise ValueError("unsupported benchmark file: %s" % file_name)


def read_jsonl(path: Path) -> list[dict[str, Any]]:
    if not path.exists():
        return []
    rows: list[dict[str, Any]] = []
    for line in path.read_text(encoding="utf-8").splitlines():
        if line.strip():
            rows.append(json.loads(line))
    return rows


def write_jsonl(path: Path, rows: list[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    body = "\n".join(json.dumps(row, ensure_ascii=False, separators=(",", ":")) for row in rows)
    path.write_text(body + ("\n" if body else ""), encoding="utf-8")


def extend_benchmarks(
    benchmark_dir: Path = BENCHMARK_DIR,
    cases: dict[str, list[dict[str, Any]]] | None = None,
    *,
    dry_run: bool = False,
) -> dict[str, Any]:
    cases = cases or build_cases()
    report: dict[str, Any] = {"benchmark_dir": str(benchmark_dir), "added": 0, "files": {}}
    for file_name, additions in cases.items():
        path = benchmark_dir / file_name
        rows = read_jsonl(path)
        existing = {unique_key(file_name, row) for row in rows}
        added_rows = []
        for row in additions:
            key = unique_key(file_name, row)
            if key not in existing:
                rows.append(row)
                existing.add(key)
                added_rows.append(row)
        if added_rows and not dry_run:
            write_jsonl(path, rows)
        report["files"][file_name] = {
            "existing_before": len(rows) - len(added_rows),
            "added": len(added_rows),
            "total_after": len(rows),
            "dry_run": dry_run,
        }
        report["added"] += len(added_rows)
    return report


def main() -> int:
    parser = argparse.ArgumentParser(description="Append sanitized realistic OS Agent benchmark cases.")
    parser.add_argument("--benchmark-dir", type=Path, default=BENCHMARK_DIR)
    parser.add_argument("--check", action="store_true", help="Exit non-zero if any case would be added.")
    args = parser.parse_args()

    if args.check:
        report = extend_benchmarks(args.benchmark_dir, build_cases(), dry_run=True)
        print(json.dumps(report, ensure_ascii=False, indent=2))
        return 1 if report["added"] else 0
    report = extend_benchmarks(args.benchmark_dir, build_cases())
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
