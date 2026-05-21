#!/usr/bin/env bash
set -euo pipefail

cd "$(dirname "$0")/.."

RUN_10K=0
if [ "${1:-}" = "--with-10k" ]; then
  RUN_10K=1
fi

STAMP="$(date +%Y%m%d_%H%M%S)"
OUT_DIR="docs/evidence/openkylin_${STAMP}"
LATEST_DIR="docs/evidence/latest"
mkdir -p "$OUT_DIR"

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
| `environment.txt` | OS、内核、Python、SQLite 等基础环境 |
| `doctor.txt` | MemGuardOS doctor 输出 |
| `pytest.txt` | 单元测试输出 |
| `eval_1k.txt` | 1k 负载评测命令输出 |
| `eval_result_openkylin.json` | 1k 负载评测 JSON |
| `eval_report_openkylin.md` | 1k 负载评测 Markdown |
| `sdk_status.txt` | Kylin SDK 状态 |
| `sdk_embedding_probe.txt` | Kylin embedding 推理探测 |
| `vector_probe.txt` | Kylin 向量数据库 SDK 探针 |
| `behavior_history.txt` | shell 用户行为轨迹接入证据 |
| `build_prompt.txt` | RAG 上下文与 LLM prompt 渲染证据 |
| `generate_answer.txt` | 默认关闭的可选 LLM adapter dry-run 证据 |
| `api_auth.txt` | 本地 Agent API token 鉴权证据 |
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

run_shell_capture "environment" "cat /etc/os-release 2>/dev/null || true; uname -a; python3 --version; python3 - <<'PY'
import sqlite3, platform
print('platform=' + platform.platform())
print('sqlite=' + sqlite3.sqlite_version)
PY"

run_capture "doctor" python3 -m memguard.cli.memctl doctor
run_capture "sdk_status" python3 -m memguard.cli.memctl sdk-status
run_capture "sdk_embedding_probe" python3 -m memguard.cli.memctl sdk-status --probe-embedding

if [ -f scripts/kylin_vector_probe.cpp ] && command -v g++ >/dev/null 2>&1; then
  run_capture "vector_probe" bash scripts/run_kylin_vector_probe.sh "$(pwd)"
else
  echo "g++ or probe source unavailable; skipped" >"${OUT_DIR}/vector_probe.txt"
fi

run_capture "pytest" python3 -m pytest -q
run_capture "eval_1k" python3 -m memguard.eval.run_eval \
  --latency-size 1000 \
  --latency-queries 120 \
  --output "${OUT_DIR}/eval_result_openkylin.json" \
  --markdown "${OUT_DIR}/eval_report_openkylin.md"

run_capture "behavior_history" python3 -m memguard.cli.memctl ingest-history --file "$HOME/.bash_history" --limit 20
run_capture "build_prompt" python3 -m memguard.cli.memctl build-prompt "openKylin 服务内存限制" --top-k 5 --token-budget 1200
run_capture "generate_answer" python3 -m memguard.cli.memctl generate-answer "openKylin 服务内存限制" --top-k 5 --token-budget 1200 --provider dry-run
run_shell_capture "api_auth" "python3 - <<'PY'
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
  run_capture "eval_10k" python3 -m memguard.eval.run_eval \
    --latency-size 10000 \
    --latency-queries 150 \
    --output "${OUT_DIR}/eval_result_openkylin_10k.json" \
    --markdown "${OUT_DIR}/eval_report_openkylin_10k.md"
fi

run_shell_capture "systemd_cgroup" "systemctl status memguardd --no-pager || true; systemctl show memguardd -p MemoryMax -p CPUQuotaPerSecUSec || true; cat /sys/fs/cgroup/system.slice/memguardd.service/memory.max 2>/dev/null || true; cat /sys/fs/cgroup/system.slice/memguardd.service/cpu.max 2>/dev/null || true; python3 -m memguard.cli.memctl status || true"
run_shell_capture "journald" "python3 -m memguard.cli.memctl ingest --text 'openKylin journald evidence collection smoke test'; python3 -m memguard.cli.memctl verify-audit; journalctl -t memguardd -n 80 --no-pager || true"
run_shell_capture "auditd_rules" "python3 -m memguard.cli.memctl auditd-rules --format auditctl; sudo auditctl -l 2>/dev/null | grep memguard_sensitive || true"
run_shell_capture "auditd_ingest" "sudo cat /etc/shadow >/dev/null 2>/dev/null || true; bash scripts/auditd_to_memguard.sh \"$(pwd)\" memguard_sensitive 200 --summary || true"
run_capture "verify_audit" python3 -m memguard.cli.memctl verify-audit

write_summary
rm -rf "$LATEST_DIR"
mkdir -p "$(dirname "$LATEST_DIR")"
cp -a "$OUT_DIR" "$LATEST_DIR"

echo "证据采集完成：${OUT_DIR}"
echo "最新证据副本：${LATEST_DIR}"
