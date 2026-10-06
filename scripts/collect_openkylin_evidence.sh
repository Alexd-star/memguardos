#!/usr/bin/env bash
set -euo pipefail

cd "$(dirname "$0")/.."

if [ -z "${PYTHON_BIN:-}" ]; then
  if [ -x ".venv/bin/python" ]; then
    PYTHON_BIN=".venv/bin/python"
  else
    PYTHON_BIN="python3"
  fi
fi
export PYTHON_BIN

RUN_10K=0
if [ "${1:-}" = "--with-10k" ]; then
  RUN_10K=1
fi

STAMP="$(date +%Y%m%d_%H%M%S)"
JOURNAL_SINCE="$(date '+%Y-%m-%d %H:%M:%S')"
OUT_DIR="output/evidence/openkylin_${STAMP}"
LATEST_DIR="output/evidence/openkylin_latest"
EVIDENCE_MEMGUARD_HOME="${TMPDIR:-/tmp}/memguardos_evidence_${STAMP}"
mkdir -p "$OUT_DIR"
rm -rf "$EVIDENCE_MEMGUARD_HOME"
mkdir -p "$EVIDENCE_MEMGUARD_HOME"
export MEMGUARD_HOME="$EVIDENCE_MEMGUARD_HOME"

PRESERVED_LATEST_FILES=(
  "default_audit_after_reset.txt"
  "diagnostics_probe_after_reset.txt"
  "ukui_control_center_visible_manual_confirmation.txt"
  "ukui_enterprise_panel.txt"
  "ukui_peony_visible_manual_confirmation.txt"
  "ukui_plugin_visible_after_version_metadata.txt"
  "ukui_plugin_visible_final.txt"
)

preserve_latest_evidence() {
  local rel
  for rel in "${PRESERVED_LATEST_FILES[@]}"; do
    if [ -f "${LATEST_DIR}/${rel}" ]; then
      mkdir -p "${OUT_DIR}/$(dirname "${rel}")"
      cp "${LATEST_DIR}/${rel}" "${OUT_DIR}/${rel}"
    fi
  done
}

run_capture() {
  local name="$1"
  shift
  local file="${OUT_DIR}/${name}.txt"
  {
    echo "\$ $*"
    echo
    "$@"
  } >"$file" 2>&1 || {
    local code=$?
    echo "command failed with exit code ${code}" >>"$file"
    return 0
  }
}

run_shell_capture() {
  local name="$1"
  local script="$2"
  local file="${OUT_DIR}/${name}.txt"
  {
    echo "\$ ${script}"
    echo
    bash -lc "$script"
  } >"$file" 2>&1 || {
    local code=$?
    echo "command failed with exit code ${code}" >>"$file"
    return 0
  }
}

run_self_logging_capture() {
  local name="$1"
  shift
  local file="${OUT_DIR}/${name}.txt"
  local stdout_file="${OUT_DIR}/${name}.stdout.tmp"
  local merged_file="${OUT_DIR}/${name}.merged.tmp"
  rm -f "$file" "$stdout_file" "$merged_file"
  local code=0
  "$@" >"$stdout_file" 2>&1 || code=$?
  {
    echo "\$ $*"
    echo
    if [ -s "$file" ]; then
      cat "$file"
    else
      cat "$stdout_file"
    fi
    if [ "$code" -ne 0 ]; then
      echo "command failed with exit code ${code}"
    fi
  } >"$merged_file"
  mv "$merged_file" "$file"
  rm -f "$stdout_file"
  return 0
}

write_summary() {
  {
    cat <<'EOF'
# openKylin 证据采集包

EOF
    echo "采集时间：${STAMP}"
    cat <<'EOF'

## 文件说明

| 文件 | 内容 |
|---|---|
| `environment.txt` | OS、内核、Python、SQLite、项目解释器等基础环境 |
| `doctor.txt` | MemGuardOS doctor 输出 |
| `pytest.txt` | 单元测试输出 |
| `embedding_cache_status.txt` | 持久化 Kylin 向量缓存 ready、pending、failed、due 与退避状态 |
| `retrieval_v2.txt` | 三轮检索质量和并发 P95/P99 门禁命令输出 |
| `retrieval_v2.json` | 带 Git HEAD、数据 SHA256、消融原始轮次和门禁结论的检索 v2 证据 |
| `eval_1k.txt` | 1k 负载评测命令输出 |
| `eval_result_openkylin.json` | 1k 负载评测 JSON |
| `eval_report_openkylin.md` | 1k 负载评测 Markdown |
| `sdk_status.txt` | Kylin SDK 状态 |
| `sdk_embedding_probe.txt` | Kylin embedding 推理探测 |
| `embedding_cpp_probe.txt` | Kylin embedding SDK 官方 C API 风格 C++ 探针 |
| `kylin_retrieval.txt` | Kylin embedding 作为实际检索重排后端的证据 |
| `kylin_retrieval_budget_fallback.txt` | Kylin 重排超过交互延迟预算后受控降级到稳定检索的证据 |
| `kylin_embedding_diag/` | Kylin embedding runtime、socket、插件、包版本和日志诊断 |
| `vector_probe.txt` | Kylin 向量数据库 SDK 探针 |
| `quality_report.txt` | 输入清洗、格式标准化与质量校验证据 |
| `natural_patterns.txt` | 自然语言自定义敏感规则证据 |
| `semantic_extraction.txt` | 可解释混合抽取与可选语义增强入口证据 |
| `behavior_history.txt` | shell 用户行为轨迹接入证据 |
| `build_prompt.txt` | RAG 上下文与 LLM prompt 渲染证据 |
| `build_context_explainability.txt` | RAG 上下文来源分布、过滤策略和 selection trace 证据 |
| `hierarchical_graph_search.txt` | 摘要层、实体关系层和原文层的层次图谱检索证据 |
| `generate_answer.txt` | 默认关闭的可选 LLM adapter dry-run 证据 |
| `web_dashboard.txt` | 本地 Web 控制台 smoke 证据 |
| `peony_file_scan_smoke.txt` | Peony 右键文件扫描、文本导入、检索和审计链闭环证据 |
| `agent_batch_search.txt` | OS Agent 任务规划、工具调用、结果生成多查询并发检索证据 |
| `temporal_status.txt` | 时间有效性窗口、旧版本失效和 tombstone 时间线证据 |
| `api_auth.txt` | 本地 Agent API token 鉴权证据 |
| `namespace_isolation.txt` | namespace 多会话记忆隔离证据 |
| `systemd_namespace.txt` | systemd 模板实例与独立 cgroup 证据 |
| `audit_concurrency.txt` | 并发审计写入与哈希链连续性证据 |
| `ukui_notify.txt` | UKUI/freedesktop 桌面通知 smoke 证据 |
| `ukui_deep_plugins.txt` | Peony/UKUI 控制中心深度插件源码与构建环境探针 |
| `ukui_plugin_build.txt` | Peony 右键插件与 UKUI 控制中心插件真实构建证据 |
| `ukui_plugin_install.txt` | UKUI 插件系统目录安装尝试与只读根文件系统边界 |
| `ukui_plugin_visible.txt` | UKUI 插件安装产物、依赖和人工可见性检查步骤 |
| `ukui_plugin_visible_final.txt` | 保留自上次人工复核的 UKUI 插件最终可见性证据 |
| `ukui_peony_visible_manual_confirmation.txt` | 保留自上次人工复核的 Peony 右键菜单可见性确认 |
| `ukui_control_center_visible_manual_confirmation.txt` | 保留自上次人工复核的 UKUI 控制中心入口可见性确认 |
| `ukui_enterprise_panel.txt` | 保留自上次人工复核的控制中心企业化面板证据 |
| `deb_package.txt` | MemGuardOS Debian 安装包构建、包内容和离线解包 smoke 证据 |
| `bpf_lsm_policy.txt` | BPF LSM JSON denylist 策略生成证据 |
| `bpf_lsm_enforcer.txt` | BPF LSM 强制拦截增强环境、构建和 attach 探针 |
| `systemd_cgroup.txt` | systemd/cgroup 服务限制证据 |
| `journald.txt` | journald 日志证据 |
| `auditd_rules.txt` | auditd 规则证据 |
| `auditd_ingest.txt` | auditd 真实日志接入证据 |
| `verify_audit.txt` | 审计哈希链验证 |
| `memory_policy.txt` | 记忆冷热分层、存储占用、cgroup 状态和淘汰候选证据 |
| `consolidation_evidence.json` | 会话记忆巩固、人工确认、版本 CAS 与短期保留证据 |
| `decay_evidence.json` | 检索强化、健康度衰减、可逆降级与旧计划 CAS 证据 |
| `context_compaction_evidence.json` | 规划、工具选择、回答阶段的上下文表示与预算证据 |
| `manifest.json` | 证据包文件大小与 SHA256 摘要清单 |
| `SHA256SUMS.txt` | 可由 `sha256sum -c SHA256SUMS.txt` 复核的证据包哈希 |
EOF
  } >"${OUT_DIR}/README.md"
  if [ "$RUN_10K" -eq 1 ]; then
    cat >>"${OUT_DIR}/README.md" <<'EOF'
| `eval_10k.txt` | 10k 规模评测命令输出 |
| `eval_result_openkylin_10k.json` | 10k 规模评测 JSON |
| `eval_report_openkylin_10k.md` | 10k 规模评测 Markdown |
EOF
  fi
}

run_shell_capture "environment" "cat /etc/os-release 2>/dev/null || true; uname -a; echo PYTHON_BIN=${PYTHON_BIN}; \"${PYTHON_BIN}\" --version; \"${PYTHON_BIN}\" - <<'PY'
import sqlite3, platform
print('platform=' + platform.platform())
print('sqlite=' + sqlite3.sqlite_version)
PY"
echo "MEMGUARD_HOME=${MEMGUARD_HOME}" >>"${OUT_DIR}/environment.txt"
echo "JOURNAL_SINCE=${JOURNAL_SINCE}" >>"${OUT_DIR}/environment.txt"

run_capture "doctor" "${PYTHON_BIN}" -m memguard.cli.memctl doctor
run_capture "consolidation_evidence" "${PYTHON_BIN}" scripts/collect_consolidation_evidence.py \
  --output "${OUT_DIR}/consolidation_evidence.json"
run_capture "decay_evidence" "${PYTHON_BIN}" scripts/collect_decay_evidence.py \
  --output "${OUT_DIR}/decay_evidence.json"
run_capture "context_compaction_evidence" "${PYTHON_BIN}" scripts/collect_context_compaction_evidence.py \
  --output "${OUT_DIR}/context_compaction_evidence.json"
run_capture "sdk_status" "${PYTHON_BIN}" -m memguard.cli.memctl sdk-status
run_capture "sdk_embedding_probe" "${PYTHON_BIN}" -m memguard.cli.memctl sdk-status --probe-embedding --probe-timeout 90
if [ -f scripts/diagnose_kylin_embedding.sh ]; then
  run_capture "kylin_embedding_diag" bash scripts/diagnose_kylin_embedding.sh --no-probe --out-dir "${OUT_DIR}/kylin_embedding_diag"
fi

if [ -f scripts/kylin_embedding_probe.cpp ] && command -v g++ >/dev/null 2>&1; then
  run_capture "embedding_cpp_probe" bash scripts/run_kylin_embedding_probe.sh "openKylin agent memory"
else
  echo "g++ or embedding probe source unavailable; skipped" >"${OUT_DIR}/embedding_cpp_probe.txt"
fi

run_shell_capture "kylin_retrieval" "KYLIN_RETRIEVAL_HOME='${OUT_DIR}/kylin_retrieval_home'; rm -rf \"\${KYLIN_RETRIEVAL_HOME}\"; mkdir -p \"\${KYLIN_RETRIEVAL_HOME}\"; MEMGUARD_HOME=\"\${KYLIN_RETRIEVAL_HOME}\" MEMGUARD_EMBEDDING_BACKEND=auto MEMGUARD_KYLIN_EMBEDDING_TIMEOUT=90 MEMGUARD_KYLIN_RERANK_BUDGET_MS=5000 \"${PYTHON_BIN}\" - <<'PY'
import json

from memguard.service import MemGuardService

service = MemGuardService()
service.ingest_text('openKylin 服务用 systemd MemoryMax 和 CPUQuota 做端侧资源限制。')
service.ingest_text('auditd 会审计 /etc/shadow 等敏感路径访问，并阻断高风险记忆写入。')
hits = service.search('openKylin 服务内存限制', limit=2)
print(json.dumps({'hits': hits, 'retrieval_status': service.retrieval_status()}, ensure_ascii=False, indent=2))
raise SystemExit(0 if hits and hits[0].get('vector_backend') == 'kylin_sdk' else 2)
PY"

run_shell_capture "kylin_retrieval_budget_fallback" "KYLIN_FALLBACK_HOME='${OUT_DIR}/kylin_fallback_home'; rm -rf \"\${KYLIN_FALLBACK_HOME}\"; mkdir -p \"\${KYLIN_FALLBACK_HOME}\"; MEMGUARD_HOME=\"\${KYLIN_FALLBACK_HOME}\" MEMGUARD_EMBEDDING_BACKEND=auto MEMGUARD_KYLIN_EMBEDDING_TIMEOUT=90 MEMGUARD_KYLIN_RERANK_BUDGET_MS=1 \"${PYTHON_BIN}\" - <<'PY'
import json

from memguard.service import MemGuardService

service = MemGuardService()
service.ingest_text('openKylin 服务用 systemd MemoryMax 和 CPUQuota 做端侧资源限制。')
hits = service.search('openKylin 服务内存限制', limit=1)
status = service.retrieval_status()
payload = {'hits': hits, 'retrieval_status': status}
print(json.dumps(payload, ensure_ascii=False, indent=2))
reason = status.get('kylin', {}).get('disabled_reason', '')
controlled_fallback = bool(hits) and hits[0].get('vector_backend') == 'hash' and 'exceeded latency budget' in reason
raise SystemExit(0 if controlled_fallback else 2)
PY"

if [ -f scripts/kylin_vector_probe.cpp ] && command -v g++ >/dev/null 2>&1; then
  run_capture "vector_probe" bash scripts/run_kylin_vector_probe.sh "$(pwd)"
else
  echo "g++ or probe source unavailable; skipped" >"${OUT_DIR}/vector_probe.txt"
fi

run_capture "pytest" "${PYTHON_BIN}" -m pytest -q
run_capture "embedding_cache_status" "${PYTHON_BIN}" -m memguard.cli.memctl embedding-cache status
run_capture "retrieval_v2" "${PYTHON_BIN}" scripts/evaluate_retrieval_v2.py \
  --rounds 3 \
  --size 1000 \
  --queries 120 \
  --workers 6 \
  --output "${OUT_DIR}/retrieval_v2.json"
run_capture "eval_1k" "${PYTHON_BIN}" -m memguard.eval.run_eval \
  --latency-size 1000 \
  --latency-queries 120 \
  --output "${OUT_DIR}/eval_result_openkylin.json" \
  --markdown "${OUT_DIR}/eval_report_openkylin.md"

run_shell_capture "quality_report" "\"${PYTHON_BIN}\" -m memguard.cli.memctl ingest --text '  普通知识：   systemd   可用 MemoryMax 限制服务内存。'; \"${PYTHON_BIN}\" -m memguard.cli.memctl ingest --text '!!! --- ***' || true; \"${PYTHON_BIN}\" -m memguard.cli.memctl quality-report"
run_shell_capture "natural_patterns" "\"${PYTHON_BIN}\" -m memguard.cli.memctl patterns suggest '把员工工号，格式以 MG- 开头后接 6 位数字，作为敏感信息'; \"${PYTHON_BIN}\" -m memguard.cli.memctl patterns add-natural '把员工工号，格式以 MG- 开头后接 6 位数字，作为敏感信息'; \"${PYTHON_BIN}\" -m memguard.cli.memctl ingest --text '员工工号 MG-123456 需要脱敏保存。'; \"${PYTHON_BIN}\" -m memguard.cli.memctl search '员工工号' --top-k 3; \"${PYTHON_BIN}\" -m memguard.cli.memctl patterns list"
run_shell_capture "semantic_extraction" "\"${PYTHON_BIN}\" -m memguard.cli.memctl explain-extraction --text '真实案例：systemd service failed with exit_code=1，修复后运行 pytest。'; \"${PYTHON_BIN}\" -m memguard.cli.memctl ingest --text '语义抽取增强流程：默认 deterministic semantic rules；设置 MEMGUARD_EXTRACTOR_COMMAND 后可调用本地 NER 或 LLM 返回 JSON schema。'; \"${PYTHON_BIN}\" -m memguard.cli.memctl audit --type ingest --limit 2"
run_capture "behavior_history" "${PYTHON_BIN}" -m memguard.cli.memctl ingest-history --file "$HOME/.bash_history" --limit 20
run_capture "build_prompt" "${PYTHON_BIN}" -m memguard.cli.memctl build-prompt "openKylin 服务内存限制" --top-k 5 --token-budget 1200
run_capture "build_context_explainability" "${PYTHON_BIN}" -m memguard.cli.memctl build-context "openKylin 服务内存限制" --top-k 5 --token-budget 1200
run_capture "hierarchical_graph_search" "${PYTHON_BIN}" -m memguard.cli.memctl hierarchical-graph-search systemd --limit 5
run_capture "generate_answer" "${PYTHON_BIN}" -m memguard.cli.memctl generate-answer "openKylin 服务内存限制" --top-k 5 --token-budget 1200 --provider dry-run
run_capture "web_dashboard" "${PYTHON_BIN}" scripts/web_dashboard_smoke.py
run_capture "peony_file_scan_smoke" bash scripts/peony_file_scan_smoke.sh "$(pwd)"
run_shell_capture "agent_batch_search" "\"${PYTHON_BIN}\" -m memguard.cli.memctl ingest --text '任务规划记忆：openKylin systemd cgroup MemoryMax 约束。'; \"${PYTHON_BIN}\" -m memguard.cli.memctl ingest --text '工具调用记忆：Linux 验证优先使用 rg、pytest、journalctl。'; \"${PYTHON_BIN}\" -m memguard.cli.memctl ingest --text '结果生成记忆：运维报告先给结论，再给指标证据和边界。'; \"${PYTHON_BIN}\" -m memguard.cli.memctl batch-search '任务规划 systemd cgroup' '工具调用 pytest journalctl' '结果生成 运维报告 证据' --top-k 3 --workers 3; \"${PYTHON_BIN}\" -m memguard.cli.memctl audit --type batch_search --limit 3; \"${PYTHON_BIN}\" -m memguard.cli.memctl verify-audit"
run_shell_capture "temporal_status" "\"${PYTHON_BIN}\" -m memguard.cli.memctl ingest --text '以后回答 Linux 命令时使用中文。'; MEMORY_ID=\$(\"${PYTHON_BIN}\" -m memguard.cli.memctl list | \"${PYTHON_BIN}\" -c 'import json,sys; print(json.load(sys.stdin)[0][\"memory_id\"])'); \"${PYTHON_BIN}\" -m memguard.cli.memctl update \"\${MEMORY_ID}\" --text '以后回答 Linux 命令时使用英文。' --reason 'temporal validity smoke'; echo '--- versions after update ---'; \"${PYTHON_BIN}\" -m memguard.cli.memctl versions \"\${MEMORY_ID}\"; echo '--- temporal status ---'; \"${PYTHON_BIN}\" -m memguard.cli.memctl temporal-status --limit 10; echo '--- versions after forget ---'; \"${PYTHON_BIN}\" -m memguard.cli.memctl forget '忘掉 Linux 命令语言偏好' --yes; \"${PYTHON_BIN}\" -m memguard.cli.memctl versions \"\${MEMORY_ID}\"; \"${PYTHON_BIN}\" -m memguard.cli.memctl verify-audit"
run_capture "memory_policy" "${PYTHON_BIN}" -m memguard.cli.memctl memory-policy --eviction-limit 10
run_shell_capture "namespace_isolation" "NS_HOME='${OUT_DIR}/namespace_home'; rm -rf \"\${NS_HOME}\"; mkdir -p \"\${NS_HOME}\"; MEMGUARD_HOME=\"\${NS_HOME}\" \"${PYTHON_BIN}\" -m memguard.cli.memctl --namespace alpha ingest --text 'alpha 会话只保存 openKylin systemd MemoryMax 方案。'; MEMGUARD_HOME=\"\${NS_HOME}\" \"${PYTHON_BIN}\" -m memguard.cli.memctl --namespace beta ingest --text 'beta 会话只保存 auditd shadow access 方案。'; echo '--- alpha search MemoryMax ---'; MEMGUARD_HOME=\"\${NS_HOME}\" \"${PYTHON_BIN}\" -m memguard.cli.memctl --namespace alpha search MemoryMax --top-k 3; echo '--- beta search MemoryMax should be empty ---'; MEMGUARD_HOME=\"\${NS_HOME}\" \"${PYTHON_BIN}\" -m memguard.cli.memctl --namespace beta search MemoryMax --top-k 3; echo '--- beta search auditd ---'; MEMGUARD_HOME=\"\${NS_HOME}\" \"${PYTHON_BIN}\" -m memguard.cli.memctl --namespace beta search auditd --top-k 3; echo '--- namespace status ---'; MEMGUARD_HOME=\"\${NS_HOME}\" \"${PYTHON_BIN}\" -m memguard.cli.memctl --namespace alpha namespace-status; MEMGUARD_HOME=\"\${NS_HOME}\" \"${PYTHON_BIN}\" -m memguard.cli.memctl --namespace beta namespace-status; MEMGUARD_HOME=\"\${NS_HOME}\" \"${PYTHON_BIN}\" -m memguard.cli.memctl namespaces"
run_shell_capture "audit_concurrency" "TMP_AUDIT_HOME=\$(mktemp -d /tmp/memguardos-audit-concurrency.XXXXXX); export TMP_AUDIT_HOME; \"${PYTHON_BIN}\" - <<'PY'
from pathlib import Path
import json
import os
import shutil
import sys
import threading

from memguard.store.sqlite_meta import SQLiteMetaStore

root = Path(os.environ['TMP_AUDIT_HOME'])
db_path = root / 'audit.sqlite3'
workers = 6
per_worker = 12
barrier = threading.Barrier(workers)
errors = []

initializer = SQLiteMetaStore(db_path)
initializer.close()

def write_events(worker_id):
    store = SQLiteMetaStore(db_path)
    try:
        barrier.wait(timeout=5)
        for index in range(per_worker):
            store.add_audit('concurrent_probe', None, {'worker': worker_id, 'index': index})
    except BaseException as exc:
        errors.append(repr(exc))
    finally:
        store.close()

threads = [threading.Thread(target=write_events, args=(worker_id,)) for worker_id in range(workers)]
for thread in threads:
    thread.start()
for thread in threads:
    thread.join(timeout=10)

verifier = SQLiteMetaStore(db_path)
try:
    result = verifier.verify_audit_chain()
finally:
    verifier.close()
shutil.rmtree(root, ignore_errors=True)
payload = {'errors': errors, 'verify': result, 'expected_events': workers * per_worker}
print(json.dumps(payload, ensure_ascii=False, indent=2))
sys.exit(0 if not errors and result.get('ok') and result.get('checked') == workers * per_worker else 2)
PY"
run_shell_capture "api_auth" "\"${PYTHON_BIN}\" - <<'PY'
from memguard.api.server import handle_request
from memguard.service import MemGuardService

service = MemGuardService()
print(handle_request(service, {'command': 'health'}, auth_token='local-secret'))
print(handle_request(service, {'command': 'health', 'token': 'local-secret'}, auth_token='local-secret'))
PY"
if command -v notify-send >/dev/null 2>&1; then
  run_capture "ukui_notify" bash scripts/ukui_notify_smoke.sh "MemGuardOS 验证" "用户行为、审计和精准遗忘链路已通过验证。"
else
  echo "notify-send unavailable; skipped" >"${OUT_DIR}/ukui_notify.txt"
fi
run_self_logging_capture "ukui_deep_plugins" bash scripts/probe_ukui_deep_plugins.sh "${OUT_DIR}"
run_capture "ukui_plugin_build" bash scripts/build_ukui_plugins.sh
run_capture "ukui_plugin_install" bash scripts/install_ukui_plugins.sh --no-build
run_capture "ukui_plugin_visible" bash scripts/verify_ukui_plugins.sh
run_capture "deb_package" bash scripts/verify_deb_package_set.sh
run_capture "bpf_lsm_policy" "${PYTHON_BIN}" scripts/generate_bpf_lsm_policy.py --policy data/demo/bpf_lsm_policy.json --output "${OUT_DIR}/memguard_lsm_policy.generated.h"
run_self_logging_capture "bpf_lsm_enforcer" bash scripts/run_bpf_lsm_enforcer.sh "${OUT_DIR}"

if [ "$RUN_10K" -eq 1 ]; then
  run_capture "eval_10k" "${PYTHON_BIN}" -m memguard.eval.run_eval \
    --latency-size 10000 \
    --latency-queries 150 \
    --output "${OUT_DIR}/eval_result_openkylin_10k.json" \
    --markdown "${OUT_DIR}/eval_report_openkylin_10k.md"
fi

run_shell_capture "systemd_cgroup" "systemctl status memguardd --no-pager || true; systemctl show memguardd -p MemoryMax -p CPUQuotaPerSecUSec || true; cat /sys/fs/cgroup/system.slice/memguardd.service/memory.max 2>/dev/null || true; cat /sys/fs/cgroup/system.slice/memguardd.service/cpu.max 2>/dev/null || true; \"${PYTHON_BIN}\" -m memguard.cli.memctl status || true"
run_shell_capture "systemd_namespace" "systemctl cat 'memguardd@.service' --no-pager 2>/dev/null || true; sudo systemctl start 'memguardd@demo-a.service' 'memguardd@demo-b.service' 2>/dev/null || true; systemctl status 'memguardd@demo-a.service' 'memguardd@demo-b.service' --no-pager || true; systemctl show 'memguardd@demo-a.service' 'memguardd@demo-b.service' -p ControlGroup -p MemoryMax -p CPUQuotaPerSecUSec --no-pager || true; for unit in 'memguardd@demo-a.service' 'memguardd@demo-b.service'; do cg=\$(systemctl show \"\${unit}\" -p ControlGroup --value 2>/dev/null || true); echo \"--- \${unit} cgroup files: /sys/fs/cgroup\${cg}\"; test -n \"\${cg}\" && cat \"/sys/fs/cgroup\${cg}/memory.max\" 2>/dev/null || true; test -n \"\${cg}\" && cat \"/sys/fs/cgroup\${cg}/cpu.max\" 2>/dev/null || true; done; MEMGUARD_NAMESPACE=demo-a \"${PYTHON_BIN}\" -m memguard.cli.memctl namespace-status || true; MEMGUARD_NAMESPACE=demo-b \"${PYTHON_BIN}\" -m memguard.cli.memctl namespace-status || true"
run_shell_capture "journald" "\"${PYTHON_BIN}\" -m memguard.cli.memctl ingest --text 'openKylin journald evidence collection smoke test'; \"${PYTHON_BIN}\" -m memguard.cli.memctl verify-audit; journalctl -t memguardd --since '${JOURNAL_SINCE}' -n 120 --no-pager || true"
run_shell_capture "auditd_rules" "\"${PYTHON_BIN}\" -m memguard.cli.memctl auditd-rules --format auditctl; sudo auditctl -l 2>/dev/null | grep memguard_sensitive || true"
run_shell_capture "auditd_ingest" "sudo cat /etc/shadow >/dev/null 2>/dev/null || true; bash scripts/auditd_to_memguard.sh \"$(pwd)\" memguard_sensitive 200 --summary || true"
run_capture "verify_audit" "${PYTHON_BIN}" -m memguard.cli.memctl verify-audit

rm -rf "${OUT_DIR}/namespace_home"
rm -rf "${OUT_DIR}/kylin_retrieval_home"
rm -rf "${OUT_DIR}/kylin_fallback_home"
preserve_latest_evidence
write_summary
"${PYTHON_BIN}" scripts/write_evidence_manifest.py "$OUT_DIR" >"${OUT_DIR}/evidence_manifest.txt" 2>&1 || true
rm -rf "$LATEST_DIR"
mkdir -p "$(dirname "$LATEST_DIR")"
cp -a "$OUT_DIR" "$LATEST_DIR"

echo "证据采集完成：${OUT_DIR}"
echo "最新证据副本：${LATEST_DIR}"
