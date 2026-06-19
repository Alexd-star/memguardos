#!/usr/bin/env bash
set -euo pipefail

cd "$(dirname "$0")/.."

PYTHON_BIN="${PYTHON_BIN:-.venv/bin/python}"
AGENT_PYTHON="${AGENT_PYTHON:-.venv-agent/bin/python}"
MODEL="${OPENHANDS_MODEL:-ollama/qwen3:1.7b}"
BASE_URL="${OPENHANDS_BASE_URL:-http://127.0.0.1:11434}"
USER_TOOL_ROOT="${MEMGUARD_AGENT_TOOL_ROOT:-${HOME}/.local/openhands-tools}"
export PATH="${USER_TOOL_ROOT}/usr/bin:${HOME}/.local/ollama/bin:${PATH}"
export LD_LIBRARY_PATH="${USER_TOOL_ROOT}/usr/lib/x86_64-linux-gnu:${LD_LIBRARY_PATH:-}"
FINAL_DIR="${1:-docs/evidence/memory_value_latest}"
STAMP="$(date +%Y%m%d_%H%M%S)"
PARENT_DIR="$(dirname "${FINAL_DIR}")"
TMP_DIR="${PARENT_DIR}/.memory_value_${STAMP}.tmp"
BACKUP_DIR="${PARENT_DIR}/.memory_value_${STAMP}.backup"

case "${FINAL_DIR}" in
  ""|"/"|".") echo "Refusing unsafe evidence path: ${FINAL_DIR}" >&2; exit 2 ;;
esac

mkdir -p "${PARENT_DIR}"
if [[ -e "${TMP_DIR}" || -e "${BACKUP_DIR}" ]]; then
  echo "Temporary evidence path already exists." >&2
  exit 2
fi
mkdir -p "${TMP_DIR}"

cleanup() {
  if [[ -d "${TMP_DIR}" ]]; then
    rm -rf -- "${TMP_DIR}"
  fi
}
trap cleanup EXIT

run_capture() {
  local name="$1"
  shift
  {
    printf '$'
    printf ' %q' "$@"
    printf '\n\n'
    "$@"
  } >"${TMP_DIR}/${name}.txt" 2>&1
}

run_capture "python_environment" "${PYTHON_BIN}" -c \
  "import json,platform,sys; print(json.dumps({'python':sys.version,'platform':platform.platform()}, ensure_ascii=False, indent=2))"
run_capture "openhands_environment" "${AGENT_PYTHON}" -c \
  "import json; from memguard.integrations.openhands_adapter import detect_openhands_runtime; print(json.dumps(detect_openhands_runtime(), ensure_ascii=False, indent=2))"

if command -v ollama >/dev/null 2>&1; then
  run_capture "ollama_version" ollama --version
  run_capture "ollama_models" ollama list
else
  echo "Ollama executable not found." >"${TMP_DIR}/ollama_version.txt"
  echo "Ollama executable not found." >"${TMP_DIR}/ollama_models.txt"
  exit 3
fi

"${PYTHON_BIN}" scripts/evaluate_memory_value.py \
  --output-dir "${TMP_DIR}/deterministic" \
  >"${TMP_DIR}/deterministic_run.txt" 2>&1
rm -rf -- "${TMP_DIR}/deterministic/work"

"${AGENT_PYTHON}" scripts/run_openhands_behavior_suite.py \
  --output-dir "${TMP_DIR}/real_agent" \
  --model "${MODEL}" \
  --base-url "${BASE_URL}" \
  >"${TMP_DIR}/real_agent_run.txt" 2>&1

run_capture "pytest_openkylin" "${PYTHON_BIN}" -m pytest -q
run_capture "audit_verify" "${PYTHON_BIN}" -m memguard.cli.memctl verify-audit
run_capture "git_head" git rev-parse HEAD
run_capture "git_status" git status --short

cat >"${TMP_DIR}/README.md" <<EOF
# MemGuardOS 智能体记忆价值证据

- 采集时间：${STAMP}
- 操作系统：openKylin
- OpenHands 模型：${MODEL}
- 确定性实验：16 组
- 真实 OpenHands 场景：12 组

`deterministic/` 保存可复现配对评测；`real_agent/` 保存真实 OpenHands
会话、上下文、工具事件、工作区前后清单和逐案例判定。真实失败不会被删除，
记忆系统层与 Agent 行为层分别统计。
EOF

(
  cd "${TMP_DIR}"
  find . -type f ! -name SHA256SUMS.txt ! -name manifest.json -print0 |
    sort -z |
    xargs -0 sha256sum >SHA256SUMS.txt
)

"${PYTHON_BIN}" - "${TMP_DIR}" <<'PY'
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
    json.dumps(
        {
            "schema_version": "1.0",
            "file_count": len(files),
            "files": files,
        },
        ensure_ascii=False,
        indent=2,
    ),
    encoding="utf-8",
)
PY

if [[ -e "${FINAL_DIR}" ]]; then
  mv -- "${FINAL_DIR}" "${BACKUP_DIR}"
fi
mv -- "${TMP_DIR}" "${FINAL_DIR}"
if [[ -e "${BACKUP_DIR}" ]]; then
  rm -rf -- "${BACKUP_DIR}"
fi
trap - EXIT

printf 'Memory-value evidence written to %s\n' "${FINAL_DIR}"
