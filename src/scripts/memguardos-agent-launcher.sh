#!/bin/sh
set -eu

PYTHON="${MEMGUARD_AGENT_PYTHON:-}"
if [ -n "${PYTHON}" ]; then
  if [ ! -x "${PYTHON}" ]; then
    echo "MemGuardOS Agent 解释器不可执行: ${PYTHON}" >&2
    exit 78
  fi
else
  for candidate in \
    /opt/memguardos/.venv-agent/bin/python \
    "${HOME}/memguardos/.venv-agent/bin/python" \
    /usr/bin/python3
  do
    if [ -x "${candidate}" ]; then
      PYTHON="${candidate}"
      break
    fi
  done
fi

if [ -z "${PYTHON}" ]; then
  echo "未找到可用的 MemGuardOS Agent Python 解释器。" >&2
  exit 127
fi

exec "${PYTHON}" /opt/memguardos/scripts/memguard_agent_bridge.py "$@"
