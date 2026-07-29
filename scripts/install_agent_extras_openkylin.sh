#!/usr/bin/env bash
set -euo pipefail

PROJECT_ROOT="${1:-}"
PROXY="${2:-}"

if [[ -z "${PROJECT_ROOT}" ]]; then
  if [[ -f /opt/memguardos/pyproject.toml ]]; then
    PROJECT_ROOT=/opt/memguardos
  else
    PROJECT_ROOT="$(pwd)"
  fi
fi

if [[ ! -f "${PROJECT_ROOT}/pyproject.toml" ]]; then
  echo "未找到 MemGuardOS 安装目录：${PROJECT_ROOT}" >&2
  echo "请先安装 memguardos-agent-bridge，或将项目目录作为第一个参数传入。" >&2
  exit 2
fi

if [[ -n "${MEMGUARD_AGENT_VENV:-}" ]]; then
  VENV_PATH="${MEMGUARD_AGENT_VENV}"
elif [[ "${PROJECT_ROOT}" == /opt/memguardos ]]; then
  VENV_PATH="${XDG_DATA_HOME:-${HOME}/.local/share}/memguardos/agent-venv"
else
  VENV_PATH="${PROJECT_ROOT}/.venv-agent"
fi

cd "${PROJECT_ROOT}"

PYTHON_BIN="${MEMGUARD_AGENT_SETUP_PYTHON:-}"
if [[ -z "${PYTHON_BIN}" ]]; then
  for candidate in python3.12 python3; do
    if command -v "${candidate}" >/dev/null 2>&1 && "${candidate}" -c 'import sys; raise SystemExit(0 if sys.version_info >= (3, 12) else 1)' 2>/dev/null; then
      PYTHON_BIN="$(command -v "${candidate}")"
      break
    fi
  done
fi

if [[ -z "${PYTHON_BIN}" ]]; then
  echo "真实 Agent 需要 Python 3.12 或更高版本。" >&2
  exit 2
fi

PYTHON_VERSION="$(${PYTHON_BIN} -c 'import sys; print("%d.%d" % sys.version_info[:2])')"
"${PYTHON_BIN}" - <<'PY'
import sys
if sys.version_info < (3, 12):
    raise SystemExit("OpenHands SDK 1.28.1 requires Python 3.12 or newer")
PY

mkdir -p "$(dirname "${VENV_PATH}")"
"${PYTHON_BIN}" -m venv "${VENV_PATH}"
"${VENV_PATH}/bin/python" -m pip install --upgrade pip

PIP_ARGS=()
if [[ -n "${PROXY}" ]]; then
  PIP_ARGS+=(--proxy "${PROXY}")
fi

"${VENV_PATH}/bin/python" -m pip install "${PIP_ARGS[@]}" --progress-bar off --timeout 120 --retries 5 -e ".[agent-openhands]"
"${VENV_PATH}/bin/python" - <<'PY'
import json
from memguard.integrations.openhands_adapter import detect_openhands_runtime
status = detect_openhands_runtime()
print(json.dumps(status, ensure_ascii=False, indent=2))
if not status["available"]:
    raise SystemExit(3)
PY

echo "真实 Agent 运行环境已准备完成。"
echo "运行环境：${VENV_PATH}（Python ${PYTHON_VERSION}）"
echo "下一步：在任意目录运行 memguard-agent-showcase [工作目录]"
