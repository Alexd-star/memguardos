#!/usr/bin/env bash
set -euo pipefail

cd "$(dirname "$0")/.."

export XDG_RUNTIME_DIR="${XDG_RUNTIME_DIR:-/run/user/$(id -u)}"
export DBUS_SESSION_BUS_ADDRESS="${DBUS_SESSION_BUS_ADDRESS:-unix:path=${XDG_RUNTIME_DIR}/bus}"

RUN_PROBE=1
RESTART_RUNTIME=0
OUT_DIR=""
PROBE_TIMEOUT=90

while [ "$#" -gt 0 ]; do
  case "$1" in
    --no-probe)
      RUN_PROBE=0
      shift
      ;;
    --restart-runtime)
      RESTART_RUNTIME=1
      shift
      ;;
    --out-dir)
      OUT_DIR="${2:-}"
      shift 2
      ;;
    --probe-timeout)
      PROBE_TIMEOUT="${2:-90}"
      shift 2
      ;;
    *)
      echo "unknown argument: $1" >&2
      exit 2
      ;;
  esac
done

if [ -z "$OUT_DIR" ]; then
  OUT_DIR="output/evidence/kylin_embedding_diag_$(date +%Y%m%d_%H%M%S)"
fi
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

UID_VALUE="$(id -u)"
RUNTIME_DIR="/tmp/.kylin-ai-runtime-unix/${UID_VALUE}"
TEXT_SOCKET="${RUNTIME_DIR}/core-textembedding.sock"
KYTENSOR_REPO="/opt/appdata/kylin-ai/model-repository"
SOURCE_REPO="/usr/share/kylin-ai/model-repository"

run_shell_capture "environment" "cat /etc/os-release 2>/dev/null || true; uname -a; id; python3 --version; echo DBUS_SESSION_BUS_ADDRESS=\${DBUS_SESSION_BUS_ADDRESS:-}; echo XDG_RUNTIME_DIR=\${XDG_RUNTIME_DIR:-}; systemctl --user show-environment 2>/dev/null | grep -E 'DBUS|XDG' || true"
run_shell_capture "packages" "dpkg-query -W 'kytensor-server' 'kylin-gte-base-model' 'kylin-ai-runtime' 'kylin-ai-engine-plugins' 'libkylin-coreai-embedding*' 'libkylin-ondevice*embedding*' 'libkylin-ondevice*ai-engine-plugin*' 2>/dev/null || true"
run_shell_capture "kytensor_service" "systemctl cat kytensor.service --no-pager 2>/dev/null || true; systemctl status kytensor.service --no-pager 2>/dev/null || true; curl -sS -X POST --max-time 10 http://127.0.0.1:8000/v2/repository/index 2>/dev/null || true"
run_shell_capture "model_repositories" "echo '=== ${KYTENSOR_REPO}'; find '${KYTENSOR_REPO}' -maxdepth 2 -printf '%M %u:%g %p -> %l\n' 2>/dev/null | sort || true; echo '=== ${SOURCE_REPO}'; find '${SOURCE_REPO}' -maxdepth 2 -printf '%M %u:%g %p -> %l\n' 2>/dev/null | sort | sed -n '1,220p' || true"
run_shell_capture "runtime_service" "systemctl --user show kylin-ai-runtime.service -p ActiveState -p SubState -p Environment -p ExecStart --no-pager || true; systemctl --user status kylin-ai-runtime.service --no-pager || true; systemctl --user is-active kylin-ai-runtime.service || true; pgrep -a kylin-ai-runtime || true"
run_shell_capture "runtime_sockets" "ls -la '${RUNTIME_DIR}' 2>/dev/null || true; test -S '${TEXT_SOCKET}' && echo 'core-textembedding socket: present' || echo 'core-textembedding socket: missing'"
run_shell_capture "plugin_dirs" "for d in /usr/lib/kylin-ai/plugins/ai-engines /usr/lib/*/kylin-ai/plugins/ai-engines; do echo === \$d; ls -la \$d 2>/dev/null || true; done"
run_shell_capture "plugin_dependencies" "for so in /usr/lib/kylin-ai/plugins/ai-engines/*.so /usr/lib/*/kylin-ai/plugins/ai-engines/*.so; do [ -e \"\$so\" ] || continue; echo === \$so; ldd \"\$so\" 2>/dev/null | grep -Ei 'not found|onnx|openvino|kylin|embedding|engine' || true; done"
run_shell_capture "headers_and_symbols" "for h in /usr/include/kylin-ai/coreai/embedding/embedding.h /usr/include/kylin-ai/plugins/ai-engines/ondeviceembeddingengine.h; do echo === \$h; test -f \$h && sed -n '1,220p' \$h || echo missing; done; echo === symbols; find /usr/lib /lib -name 'libkysdk-coreai-embedding.so*' -print 2>/dev/null | while read -r lib; do echo === \$lib; nm -D \"\$lib\" 2>/dev/null | grep -E 'text_embedding|embedding_result' || true; done"
run_capture "sdk_status" python3 -m memguard.cli.memctl sdk-status

if [ "$RESTART_RUNTIME" -eq 1 ]; then
  run_shell_capture "restart_runtime" "systemctl --user restart kylin-ai-runtime.service; sleep 3; systemctl --user status kylin-ai-runtime.service --no-pager || true; ls -la '${RUNTIME_DIR}' 2>/dev/null || true"
fi

if [ "$RUN_PROBE" -eq 1 ]; then
  run_shell_capture "embedding_probe" "timeout '${PROBE_TIMEOUT}' python3 -m memguard.cli.memctl sdk-status --probe-embedding --probe-timeout '${PROBE_TIMEOUT}'"
fi

run_shell_capture "kytensor_journal" "journalctl -u kytensor.service -n 220 --no-pager || true"
run_shell_capture "runtime_journal" "journalctl --user -u kylin-ai-runtime.service -n 220 --no-pager || true"
run_shell_capture "runtime_file_log" "test -f ~/.log/kylin-ai-runtime/kylin-ai-runtime.log && tail -n 300 ~/.log/kylin-ai-runtime/kylin-ai-runtime.log || true"

cat >"${OUT_DIR}/README.md" <<EOF
# Kylin embedding SDK 诊断包

采集时间：$(date +%Y-%m-%d\ %H:%M:%S)

## 重点判断

- 如果 \`sdk_status.txt\` 里 \`model_info\` 有 768 维模型，但 \`embedding_probe.txt\` 返回 \`Timeout was reached\`，优先检查 \`kytensor_service.txt\` 和 \`model_repositories.txt\`：常见根因是 \`kytensor.service\` 使用的 \`${KYTENSOR_REPO}\` 未包含 \`ensemble-embd_gte-base_uint8-text\` 等模型。
- 如果 \`${TEXT_SOCKET}\` 不存在，优先检查 \`kylin-ai-runtime.service\` 是否运行。
- 如果 socket 存在但 probe 超时，优先看 \`runtime_journal.txt\` 中的模型加载、插件加载、ondevice engine 错误。
- 如果插件目录没有 ondevice 相关 so，检查 \`libkylin-ondevice-ai-engine-plugin\` 或 \`libkylin-ondevice-traditional-ai-engine-plugin\` 是否安装。

## 文件说明

| 文件 | 内容 |
|---|---|
| \`environment.txt\` | OS、用户、DBus 和 XDG 环境 |
| \`packages.txt\` | Kylin AI runtime、kytensor、embedding、ondevice 插件包版本 |
| \`kytensor_service.txt\` | kytensor/Triton 服务、启动参数和 repository index |
| \`model_repositories.txt\` | kytensor 运行仓库与系统模型仓库的目录/软链接状态 |
| \`runtime_service.txt\` | 用户态 \`kylin-ai-runtime.service\` 状态 |
| \`runtime_sockets.txt\` | runtime Unix socket 状态 |
| \`plugin_dirs.txt\` | AI engine 插件目录 |
| \`plugin_dependencies.txt\` | AI engine 插件动态依赖摘要 |
| \`headers_and_symbols.txt\` | embedding 头文件和动态符号 |
| \`sdk_status.txt\` | MemGuardOS SDK 结构化诊断 |
| \`embedding_probe.txt\` | 真实 embedding 调用探测 |
| \`kytensor_journal.txt\` | kytensor/Triton 系统服务日志 |
| \`runtime_journal.txt\` | runtime 用户服务日志 |
| \`runtime_file_log.txt\` | runtime 文件日志尾部 |
EOF

echo "Kylin embedding SDK 诊断完成：${OUT_DIR}"
