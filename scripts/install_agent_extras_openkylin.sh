#!/usr/bin/env bash
set -euo pipefail

PROJECT_ROOT="${1:-$(pwd)}"
PROXY="${2:-}"
VENV_PATH="${MEMGUARD_AGENT_VENV:-${PROJECT_ROOT}/.venv-agent}"

cd "${PROJECT_ROOT}"

if ! command -v python3 >/dev/null 2>&1; then
  echo "python3 is required" >&2
  exit 2
fi

PYTHON_VERSION="$(python3 -c 'import sys; print("%d.%d" % sys.version_info[:2])')"
python3 - <<'PY'
import sys
if sys.version_info < (3, 12):
    raise SystemExit("OpenHands SDK 1.28.1 requires Python 3.12 or newer")
PY

python3 -m venv "${VENV_PATH}"
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

echo "OpenHands Agent environment ready: ${VENV_PATH} (Python ${PYTHON_VERSION})"
