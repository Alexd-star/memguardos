#!/usr/bin/env bash
set -euo pipefail

cd "$(dirname "$0")/.."

PYTHON_BIN="${PYTHON_BIN:-.venv/bin/python}"
AGENT_PYTHON="${AGENT_PYTHON:-.venv-agent/bin/python}"
NAMESPACE="${MEMGUARD_AGENT_EVIDENCE_NAMESPACE:-realagent-demo}"
SESSION_A="${MEMGUARD_AGENT_SESSION_A:-real-agent-session-a2}"
SESSION_B="${MEMGUARD_AGENT_SESSION_B:-real-agent-session-b5}"
STAMP="$(date +%Y%m%d_%H%M%S)"
OUT_DIR="${1:-output/evidence/agent_${STAMP}}"
export OLLAMA_HOST="${OLLAMA_HOST:-127.0.0.1:11434}"
export OLLAMA_MODELS="${OLLAMA_MODELS:-${HOME}/.local/share/ollama/models}"

if [[ -e "${OUT_DIR}" ]]; then
  case "${OUT_DIR}" in
    ""|"/"|".") echo "Refusing to clear unsafe evidence path: ${OUT_DIR}" >&2; exit 2 ;;
  esac
  rm -rf "${OUT_DIR}"
fi
mkdir -p "${OUT_DIR}"

run_capture() {
  local name="$1"
  shift
  {
    printf '$'
    printf ' %q' "$@"
    printf '\n\n'
    "$@"
  } >"${OUT_DIR}/${name}.txt" 2>&1
}

run_capture_optional() {
  local name="$1"
  shift
  local exit_code=0
  {
    printf '$'
    printf ' %q' "$@"
    printf '\n\n'
    "$@" || exit_code=$?
    printf '\noptional_command_exit_code=%s\n' "${exit_code}"
  } >"${OUT_DIR}/${name}.txt" 2>&1
  return 0
}

run_capture "openhands_runtime" "${AGENT_PYTHON}" -c \
  "import json; from memguard.integrations.openhands_adapter import detect_openhands_runtime; print(json.dumps(detect_openhands_runtime(), ensure_ascii=False, indent=2))"
run_capture "pytest_openkylin" "${PYTHON_BIN}" -m pytest -q

if command -v ollama >/dev/null 2>&1; then
  run_capture_optional "local_models" ollama list
elif [[ -x "${HOME}/.local/ollama/bin/ollama" ]]; then
  run_capture_optional "local_models" "${HOME}/.local/ollama/bin/ollama" list
else
  printf 'Ollama executable not found.\n' >"${OUT_DIR}/local_models.txt"
fi
sed -i 's/[[:space:]]\+$//' "${OUT_DIR}/local_models.txt"

run_capture "agent_bridge_systemd" systemctl status "memguard-agent@${USER}.service" --no-pager
run_capture "agent_bridge_security" systemctl show "memguard-agent@${USER}.service" \
  -p User -p Group -p NoNewPrivileges -p PrivateTmp -p PrivateDevices \
  -p ProtectSystem -p MemoryMax -p CPUQuotaPerSecUSec -p ControlGroup --no-pager
run_capture "agent_bridge_journal" journalctl -u "memguard-agent@${USER}.service" -n 50 --no-pager

"${PYTHON_BIN}" - "${OUT_DIR}" "${NAMESPACE}" "${SESSION_A}" "${SESSION_B}" <<'PY'
from pathlib import Path
import json
import sys

from memguard.service import MemGuardService

out = Path(sys.argv[1])
namespace, session_a, session_b = sys.argv[2:5]
service = MemGuardService(namespace=namespace)
try:
    for name, session_id in (("session_a_events", session_a), ("session_b_events", session_b)):
        events = service.meta.list_agent_events(session_id=session_id, limit=500)
        (out / (name + ".json")).write_text(
            json.dumps(events, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
    status = service.interaction_status()
    (out / "interaction_status.json").write_text(
        json.dumps(status, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    snapshot = service.agent_dashboard_snapshot(
        session_id=session_b,
        task="定位精准遗忘实现位置",
    )
    (out / "agent_dashboard_snapshot.json").write_text(
        json.dumps(snapshot, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    audits = service.audit(limit=500)
    relevant = [
        item
        for item in audits
        if item["event_type"]
        in {
            "interaction_ingest",
            "agent_context_build",
            "agent_forget_dry_run",
            "agent_forget",
        }
    ]
    (out / "agent_audit.json").write_text(
        json.dumps(relevant, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    (out / "audit_verify.json").write_text(
        json.dumps(service.verify_audit(), ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
finally:
    service.close()
PY

for session_id in "${SESSION_A}" "${SESSION_B}"; do
  context_dir="${HOME}/.memguardos/agent-runs/${session_id}"
  if [[ -d "${context_dir}" ]]; then
    find "${context_dir}" -maxdepth 1 -name 'ctx_*.json' -type f -print0 |
      xargs -0 -r -I{} cp "{}" "${OUT_DIR}/${session_id}_context.json"
  fi
done

run_capture "agent_benchmark" "${PYTHON_BIN}" scripts/evaluate_agent_interactions.py \
  --output-dir "${OUT_DIR}/benchmark"
rm -rf "${OUT_DIR}/benchmark/work"

cat >"${OUT_DIR}/README.md" <<EOF
# MemGuardOS 真实 Agent 闭环证据

- 采集时间：${STAMP}
- openKylin 用户：${USER}
- MemGuardOS namespace：${NAMESPACE}
- 会话 A：${SESSION_A}
- 会话 B：${SESSION_B}

本目录记录真实 OpenHands SDK、真实本地模型、systemd Agent bridge、交互事件、
三阶段上下文、跨会话偏好选择、精准遗忘审计、固定 Benchmark 与审计链结果。
Agent 原始持久化目录不纳入提交包；精准遗忘结果会单独报告该外部残留边界。
EOF

(
  cd "${OUT_DIR}"
  find . -type f ! -name SHA256SUMS.txt -print0 |
    sort -z |
    xargs -0 sha256sum >SHA256SUMS.txt
)

"${PYTHON_BIN}" - "${OUT_DIR}" <<'PY'
from pathlib import Path
import hashlib
import json
import sys

root = Path(sys.argv[1])
files = []
for path in sorted(root.rglob("*")):
    if not path.is_file() or path.name == "manifest.json":
        continue
    files.append(
        {
            "path": path.relative_to(root).as_posix(),
            "bytes": path.stat().st_size,
            "sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
        }
    )
(root / "manifest.json").write_text(
    json.dumps({"file_count": len(files), "files": files}, ensure_ascii=False, indent=2),
    encoding="utf-8",
)
print(json.dumps({"out_dir": str(root), "file_count": len(files)}, ensure_ascii=False))
PY
