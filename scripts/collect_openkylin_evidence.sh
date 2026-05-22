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
OUT_DIR="docs/evidence/openkylin_${STAMP}"
LATEST_DIR="docs/evidence/latest"
EVIDENCE_MEMGUARD_HOME="${TMPDIR:-/tmp}/memguardos_evidence_${STAMP}"
mkdir -p "$OUT_DIR"
rm -rf "$EVIDENCE_MEMGUARD_HOME"
mkdir -p "$EVIDENCE_MEMGUARD_HOME"
export MEMGUARD_HOME="$EVIDENCE_MEMGUARD_HOME"

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
| `eval_1k.txt` | 1k 负载评测命令输出 |
| `eval_result_openkylin.json` | 1k 负载评测 JSON |
| `eval_report_openkylin.md` | 1k 负载评测 Markdown |
| `sdk_status.txt` | Kylin SDK 状态 |
| `sdk_embedding_probe.txt` | Kylin embedding 推理探测 |
| `embedding_cpp_probe.txt` | Kylin embedding SDK 官方 C API 风格 C++ 探针 |
| `kylin_retrieval.txt` | Kylin embedding 作为实际检索重排后端的证据 |
| `kylin_embedding_diag/` | Kylin embedding runtime、socket、插件、包版本和日志诊断 |
| `vector_probe.txt` | Kylin 向量数据库 SDK 探针 |
| `quality_report.txt` | 输入清洗、格式标准化与质量校验证据 |
| `natural_patterns.txt` | 自然语言自定义敏感规则证据 |
| `behavior_history.txt` | shell 用户行为轨迹接入证据 |
| `build_prompt.txt` | RAG 上下文与 LLM prompt 渲染证据 |
| `generate_answer.txt` | 默认关闭的可选 LLM adapter dry-run 证据 |
| `api_auth.txt` | 本地 Agent API token 鉴权证据 |
| `namespace_isolation.txt` | namespace 多会话记忆隔离证据 |
| `systemd_namespace.txt` | systemd 模板实例与独立 cgroup 证据 |
| `audit_concurrency.txt` | 并发审计写入与哈希链连续性证据 |
| `ukui_notify.txt` | UKUI/freedesktop 桌面通知 smoke 证据 |
| `systemd_cgroup.txt` | systemd/cgroup 服务限制证据 |
| `journald.txt` | journald 日志证据 |
| `auditd_rules.txt` | auditd 规则证据 |
| `auditd_ingest.txt` | auditd 真实日志接入证据 |
| `verify_audit.txt` | 审计哈希链验证 |
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

run_shell_capture "kylin_retrieval" "KYLIN_RETRIEVAL_HOME='${OUT_DIR}/kylin_retrieval_home'; rm -rf \"\${KYLIN_RETRIEVAL_HOME}\"; mkdir -p \"\${KYLIN_RETRIEVAL_HOME}\"; MEMGUARD_HOME=\"\${KYLIN_RETRIEVAL_HOME}\" MEMGUARD_EMBEDDING_BACKEND=auto MEMGUARD_KYLIN_EMBEDDING_TIMEOUT=90 \"${PYTHON_BIN}\" - <<'PY'
import json

from memguard.service import MemGuardService

service = MemGuardService()
service.ingest_text('openKylin 服务用 systemd MemoryMax 和 CPUQuota 做端侧资源限制。')
service.ingest_text('auditd 会审计 /etc/shadow 等敏感路径访问，并阻断高风险记忆写入。')
hits = service.search('openKylin 服务内存限制', limit=2)
print(json.dumps({'hits': hits, 'retrieval_status': service.retrieval_status()}, ensure_ascii=False, indent=2))
raise SystemExit(0 if hits and hits[0].get('vector_backend') == 'kylin_sdk' else 2)
PY"

if [ -f scripts/kylin_vector_probe.cpp ] && command -v g++ >/dev/null 2>&1; then
  run_capture "vector_probe" bash scripts/run_kylin_vector_probe.sh "$(pwd)"
else
  echo "g++ or probe source unavailable; skipped" >"${OUT_DIR}/vector_probe.txt"
fi

run_capture "pytest" "${PYTHON_BIN}" -m pytest -q
run_capture "eval_1k" "${PYTHON_BIN}" -m memguard.eval.run_eval \
  --latency-size 1000 \
  --latency-queries 120 \
  --output "${OUT_DIR}/eval_result_openkylin.json" \
  --markdown "${OUT_DIR}/eval_report_openkylin.md"
cp "${OUT_DIR}/eval_result_openkylin.json" docs/eval_result_openkylin.json
cp "${OUT_DIR}/eval_report_openkylin.md" docs/eval_report_openkylin.md

run_shell_capture "quality_report" "\"${PYTHON_BIN}\" -m memguard.cli.memctl ingest --text '  普通知识：   systemd   可用 MemoryMax 限制服务内存。'; \"${PYTHON_BIN}\" -m memguard.cli.memctl ingest --text '!!! --- ***' || true; \"${PYTHON_BIN}\" -m memguard.cli.memctl quality-report"
run_shell_capture "natural_patterns" "\"${PYTHON_BIN}\" -m memguard.cli.memctl patterns suggest '把员工工号，格式以 MG- 开头后接 6 位数字，作为敏感信息'; \"${PYTHON_BIN}\" -m memguard.cli.memctl patterns add-natural '把员工工号，格式以 MG- 开头后接 6 位数字，作为敏感信息'; \"${PYTHON_BIN}\" -m memguard.cli.memctl ingest --text '员工工号 MG-123456 需要脱敏保存。'; \"${PYTHON_BIN}\" -m memguard.cli.memctl search '员工工号' --top-k 3; \"${PYTHON_BIN}\" -m memguard.cli.memctl patterns list"
run_capture "behavior_history" "${PYTHON_BIN}" -m memguard.cli.memctl ingest-history --file "$HOME/.bash_history" --limit 20
run_capture "build_prompt" "${PYTHON_BIN}" -m memguard.cli.memctl build-prompt "openKylin 服务内存限制" --top-k 5 --token-budget 1200
run_capture "generate_answer" "${PYTHON_BIN}" -m memguard.cli.memctl generate-answer "openKylin 服务内存限制" --top-k 5 --token-budget 1200 --provider dry-run
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

if [ "$RUN_10K" -eq 1 ]; then
  run_capture "eval_10k" "${PYTHON_BIN}" -m memguard.eval.run_eval \
    --latency-size 10000 \
    --latency-queries 150 \
    --output "${OUT_DIR}/eval_result_openkylin_10k.json" \
    --markdown "${OUT_DIR}/eval_report_openkylin_10k.md"
  cp "${OUT_DIR}/eval_result_openkylin_10k.json" docs/eval_result_openkylin_10k.json
  cp "${OUT_DIR}/eval_report_openkylin_10k.md" docs/eval_report_openkylin_10k.md
fi

run_shell_capture "systemd_cgroup" "systemctl status memguardd --no-pager || true; systemctl show memguardd -p MemoryMax -p CPUQuotaPerSecUSec || true; cat /sys/fs/cgroup/system.slice/memguardd.service/memory.max 2>/dev/null || true; cat /sys/fs/cgroup/system.slice/memguardd.service/cpu.max 2>/dev/null || true; \"${PYTHON_BIN}\" -m memguard.cli.memctl status || true"
run_shell_capture "systemd_namespace" "systemctl cat 'memguardd@.service' --no-pager 2>/dev/null || true; sudo systemctl start 'memguardd@demo-a.service' 'memguardd@demo-b.service' 2>/dev/null || true; systemctl status 'memguardd@demo-a.service' 'memguardd@demo-b.service' --no-pager || true; systemctl show 'memguardd@demo-a.service' 'memguardd@demo-b.service' -p ControlGroup -p MemoryMax -p CPUQuotaPerSecUSec --no-pager || true; for unit in 'memguardd@demo-a.service' 'memguardd@demo-b.service'; do cg=\$(systemctl show \"\${unit}\" -p ControlGroup --value 2>/dev/null || true); echo \"--- \${unit} cgroup files: /sys/fs/cgroup\${cg}\"; test -n \"\${cg}\" && cat \"/sys/fs/cgroup\${cg}/memory.max\" 2>/dev/null || true; test -n \"\${cg}\" && cat \"/sys/fs/cgroup\${cg}/cpu.max\" 2>/dev/null || true; done; MEMGUARD_NAMESPACE=demo-a \"${PYTHON_BIN}\" -m memguard.cli.memctl namespace-status || true; MEMGUARD_NAMESPACE=demo-b \"${PYTHON_BIN}\" -m memguard.cli.memctl namespace-status || true"
run_shell_capture "journald" "\"${PYTHON_BIN}\" -m memguard.cli.memctl ingest --text 'openKylin journald evidence collection smoke test'; \"${PYTHON_BIN}\" -m memguard.cli.memctl verify-audit; journalctl -t memguardd --since '${JOURNAL_SINCE}' -n 120 --no-pager || true"
run_shell_capture "auditd_rules" "\"${PYTHON_BIN}\" -m memguard.cli.memctl auditd-rules --format auditctl; sudo auditctl -l 2>/dev/null | grep memguard_sensitive || true"
run_shell_capture "auditd_ingest" "sudo cat /etc/shadow >/dev/null 2>/dev/null || true; bash scripts/auditd_to_memguard.sh \"$(pwd)\" memguard_sensitive 200 --summary || true"
run_capture "verify_audit" "${PYTHON_BIN}" -m memguard.cli.memctl verify-audit

write_summary
rm -rf "${OUT_DIR}/namespace_home"
rm -rf "${OUT_DIR}/kylin_retrieval_home"
rm -rf "$LATEST_DIR"
mkdir -p "$(dirname "$LATEST_DIR")"
cp -a "$OUT_DIR" "$LATEST_DIR"

echo "证据采集完成：${OUT_DIR}"
echo "最新证据副本：${LATEST_DIR}"
