#!/bin/sh
set -eu

PYTHON="${MEMGUARD_AGENT_PYTHON:-}"
if [ -z "${PYTHON}" ]; then
  for candidate in \
    "${XDG_DATA_HOME:-${HOME}/.local/share}/memguardos/agent-venv/bin/python" \
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

if [ -z "${PYTHON}" ] || [ ! -x "${PYTHON}" ]; then
  echo "真实 Agent 组件尚未准备。" >&2
  echo "请先运行一次：memguard-agent-setup" >&2
  exit 127
fi

SOURCE_ROOT="${MEMGUARD_SOURCE_ROOT:-}"
if [ -z "${SOURCE_ROOT}" ]; then
  for candidate in /opt/memguardos "${HOME}/memguardos"
  do
    if [ -d "${candidate}/memguard" ]; then
      SOURCE_ROOT="${candidate}"
      break
    fi
  done
fi

if [ -z "${SOURCE_ROOT}" ] || [ ! -d "${SOURCE_ROOT}/memguard" ]; then
  echo "无法启动真实 Agent：未找到 MemGuardOS 程序目录。" >&2
  exit 78
fi

WORKSPACE="${MEMGUARD_AGENT_WORKSPACE:-${1:-${SOURCE_ROOT}}}"
NAMESPACE="${MEMGUARD_DEMO_NAMESPACE:-final-showcase}"
CONFIG_DIR="${HOME}/.config/memguardos"

if [ ! -d "${WORKSPACE}" ]; then
  echo "无法启动真实 Agent：工作目录不存在：${WORKSPACE}" >&2
  exit 66
fi
WORKSPACE="$(cd "${WORKSPACE}" && pwd -P)"

if [ -z "${OPENHANDS_API_KEY:-}" ] && [ ! -s "${CONFIG_DIR}/openhands_api_key" ]; then
  echo "真实 Agent 尚未配置模型凭据。" >&2
  echo "请将凭据保存到 ${CONFIG_DIR}/openhands_api_key，并设置权限为 600。" >&2
  exit 78
fi

export PYTHONPATH="${SOURCE_ROOT}${PYTHONPATH:+:${PYTHONPATH}}"
export PYTHONNOUSERSITE=1
export OPENHANDS_SUPPRESS_BANNER=1
export LOG_LEVEL=WARNING
export LITELLM_LOCAL_MODEL_COST_MAP=True
unset PYTHONHOME

# Resolve the installed package before importing Python modules so a caller's
# working directory cannot shadow the memguard package with memguard.py.
cd "${SOURCE_ROOT}"

if ! "${PYTHON}" -c 'import memguard.integrations.openhands_memory_hook' >/dev/null 2>&1; then
  echo "无法启动真实 Agent：MemGuardOS Hook 模块不可用。" >&2
  echo "请完整同步或重新安装 memguardos-agent-bridge，不能只复制单个启动脚本。" >&2
  exit 78
fi

if ! "${PYTHON}" -c 'from memguard.integrations.openhands_adapter import detect_openhands_runtime; raise SystemExit(0 if detect_openhands_runtime()["available"] else 1)' >/dev/null 2>&1; then
  echo "真实 Agent 组件尚未安装完整。" >&2
  echo "请运行：memguard-agent-setup" >&2
  exit 78
fi

printf '%s\n' "MemGuardOS 真实 Agent 演示"
printf '%s\n' "记忆空间：${NAMESPACE}"
printf '%s\n' "工作区：${WORKSPACE}"
printf '%s\n' "进入后输入 /guide，可按四步完成学习、复用和遗忘演示。"

exec "${PYTHON}" -m memguard.cli.memguard \
  --namespace "${NAMESPACE}" \
  --workspace "${WORKSPACE}" \
  shell
