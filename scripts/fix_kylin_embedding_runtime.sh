#!/usr/bin/env bash
set -euo pipefail

cd "$(dirname "$0")/.."

PROBE_TIMEOUT=180
RUN_PROBE=1
FIX_MODEL_REPO=1
RESTART_KYTENSOR=1
RESTART_RUNTIME=1

while [ "$#" -gt 0 ]; do
  case "$1" in
    --probe-timeout)
      PROBE_TIMEOUT="${2:-180}"
      shift 2
      ;;
    --no-probe)
      RUN_PROBE=0
      shift
      ;;
    --no-model-repo-fix)
      FIX_MODEL_REPO=0
      shift
      ;;
    --no-restart-kytensor)
      RESTART_KYTENSOR=0
      shift
      ;;
    --no-restart-runtime)
      RESTART_RUNTIME=0
      shift
      ;;
    *)
      echo "unknown argument: $1" >&2
      exit 2
      ;;
  esac
done

uid_value="$(id -u)"
export XDG_RUNTIME_DIR="${XDG_RUNTIME_DIR:-/run/user/${uid_value}}"
export DBUS_SESSION_BUS_ADDRESS="${DBUS_SESSION_BUS_ADDRESS:-unix:path=${XDG_RUNTIME_DIR}/bus}"

RUNTIME_DEPENDS="/usr/lib/kylin-ai/depends"
RUNTIME_BASE="/tmp/.kylin-ai-runtime-unix/${uid_value}"
SOURCE_REPO="/usr/share/kylin-ai/model-repository"
KYTENSOR_REPO="/opt/appdata/kylin-ai/model-repository"
KYTENSOR_UNIT="kytensor.service"
RUNTIME_UNIT="kylin-ai-runtime.service"

run_sudo() {
  if sudo -n true >/dev/null 2>&1; then
    sudo -n "$@"
  else
    echo "sudo without password is required for: $*" >&2
    return 1
  fi
}

print_section() {
  echo
  echo "== $1 =="
}

link_model_repository() {
  print_section "Kytensor model repository"
  echo "source repository: ${SOURCE_REPO}"
  echo "kytensor repository: ${KYTENSOR_REPO}"

  if [ ! -d "${SOURCE_REPO}" ]; then
    echo "source repository is missing; cannot fix kytensor model path" >&2
    return 1
  fi

  run_sudo mkdir -p "${KYTENSOR_REPO}"
  local linked=0
  for entry in "${SOURCE_REPO}"/*; do
    [ -e "${entry}" ] || continue
    local name
    name="$(basename "${entry}")"
    if [ -e "${KYTENSOR_REPO}/${name}" ]; then
      echo "keep existing ${KYTENSOR_REPO}/${name}"
      continue
    fi
    echo "link ${name}"
    run_sudo ln -s "${SOURCE_REPO}/${name}" "${KYTENSOR_REPO}/${name}"
    linked=$((linked + 1))
  done

  echo "linked entries: ${linked}"
  find "${KYTENSOR_REPO}" -maxdepth 1 -mindepth 1 -printf "%M %u:%g %p -> %l\n" 2>/dev/null | sort | sed -n "1,120p" || true
}

restart_kytensor() {
  print_section "Kytensor service"
  if ! command -v systemctl >/dev/null 2>&1; then
    echo "systemctl not found; skip kytensor restart"
    return 0
  fi
  systemctl cat "${KYTENSOR_UNIT}" --no-pager 2>/dev/null | sed -n "1,80p" || true
  run_sudo systemctl restart "${KYTENSOR_UNIT}"
  sleep 6
  systemctl status "${KYTENSOR_UNIT}" --no-pager 2>/dev/null | sed -n "1,90p" || true
  curl -sS -X POST --max-time 10 http://127.0.0.1:8000/v2/repository/index || true
  echo
}

stop_runtime_processes() {
  local pids
  for attempt in 1 2 3; do
    pids="$(runtime_pids)"
    [ -z "${pids}" ] && return 0
    echo "stop kylin-ai-runtime attempt ${attempt}: ${pids}"
    kill ${pids} 2>/dev/null || true
    sleep 2
  done
  pids="$(runtime_pids)"
  [ -z "${pids}" ] || kill -9 ${pids} 2>/dev/null || true
}

runtime_pids() {
  pgrep -f "^kylin-ai-runtime$|^/usr/bin/kylin-ai-runtime$" || true
}

start_runtime_manually() {
  nohup env \
    LD_LIBRARY_PATH="${RUNTIME_DEPENDS}" \
    XDG_RUNTIME_DIR="${XDG_RUNTIME_DIR}" \
    DBUS_SESSION_BUS_ADDRESS="${DBUS_SESSION_BUS_ADDRESS}" \
    kylin-ai-runtime >/tmp/memguardos_kylin_ai_runtime.out 2>&1 &
}

dedupe_runtime_processes() {
  local pids keep extras
  pids="$(runtime_pids)"
  [ -n "${pids}" ] || return 0
  keep="$(printf "%s\n" ${pids} | head -n 1)"
  extras="$(printf "%s\n" ${pids} | tail -n +2 || true)"
  if [ -n "${extras}" ]; then
    echo "multiple kylin-ai-runtime processes detected; keep ${keep}, stop extras: ${extras}"
    kill ${extras} 2>/dev/null || true
    sleep 2
    extras="$(runtime_pids | grep -v "^${keep}$" || true)"
    [ -z "${extras}" ] || kill -9 ${extras} 2>/dev/null || true
  fi
}

wait_for_runtime_socket() {
  local attempt
  for attempt in $(seq 1 15); do
    if [ -S "${RUNTIME_BASE}/core-textembedding.sock" ]; then
      return 0
    fi
    sleep 1
  done
  return 1
}

restart_runtime() {
  print_section "Kylin AI runtime"
  if command -v systemctl >/dev/null 2>&1 && systemctl --user cat "${RUNTIME_UNIT}" >/dev/null 2>&1; then
    mkdir -p "${HOME}/.config/systemd/user/${RUNTIME_UNIT}.d"
    cat >"${HOME}/.config/systemd/user/${RUNTIME_UNIT}.d/override.conf" <<EOF
[Service]
Environment=LD_LIBRARY_PATH=${RUNTIME_DEPENDS}
EOF
    systemctl --user daemon-reload || true
    systemctl --user restart "${RUNTIME_UNIT}" || true
  else
    echo "no user ${RUNTIME_UNIT}; use openKylin desktop autostart style"
    stop_runtime_processes
    rm -rf "${RUNTIME_BASE}"
    sleep 3
    if [ -n "$(runtime_pids)" ]; then
      echo "desktop session already restarted kylin-ai-runtime; reuse existing process"
    else
      echo "start kylin-ai-runtime manually"
      start_runtime_manually
    fi
  fi
  wait_for_runtime_socket || true
  dedupe_runtime_processes
  pgrep -fa "^kylin-ai-runtime$|^/usr/bin/kylin-ai-runtime$" || true
  ss -lxnp 2>/dev/null | grep "core-textembedding.sock" || true
  tail -n 80 /tmp/memguardos_kylin_ai_runtime.out 2>/dev/null || true
}

print_section "MemGuardOS Kylin embedding runtime fix"
echo "user=$(id -un) uid=${uid_value}"
echo "XDG_RUNTIME_DIR=${XDG_RUNTIME_DIR}"
echo "DBUS_SESSION_BUS_ADDRESS=${DBUS_SESSION_BUS_ADDRESS}"

if [ "${FIX_MODEL_REPO}" -eq 1 ]; then
  link_model_repository
fi

if [ "${RESTART_KYTENSOR}" -eq 1 ]; then
  restart_kytensor
fi

if [ "${RESTART_RUNTIME}" -eq 1 ]; then
  restart_runtime
fi

print_section "Runtime sockets"
ls -la "${RUNTIME_BASE}" 2>/dev/null || true
test -S "${RUNTIME_BASE}/core-textembedding.sock" && echo "core-textembedding socket: present" || echo "core-textembedding socket: missing"

if [ "${RUN_PROBE}" -eq 1 ]; then
  print_section "Python SDK embedding probe"
  python3 -m memguard.cli.memctl sdk-status --probe-embedding --probe-timeout "${PROBE_TIMEOUT}" || true

  print_section "C++ SDK embedding probe"
  PROBE_TIMEOUT="${PROBE_TIMEOUT}" bash scripts/run_kylin_embedding_probe.sh "openKylin agent memory" || true
fi

print_section "Logs"
journalctl -u "${KYTENSOR_UNIT}" -n 80 --no-pager 2>/dev/null || true
if [ -f "${HOME}/.log/kylin-ai-runtime/kylin-ai-runtime.log" ]; then
  tail -n 120 "${HOME}/.log/kylin-ai-runtime/kylin-ai-runtime.log" || true
fi

echo
echo "done"
