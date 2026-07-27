#!/usr/bin/env bash
set -euo pipefail

desktop_pattern='^/usr/bin/peony-qt-desktop -w -d$'
old_pids="$(pgrep -f "${desktop_pattern}" || true)"
session_display=""
if [ -z "${DISPLAY:-}" ] && [ -n "${old_pids}" ]; then
  first_pid="${old_pids%%$'\n'*}"
  session_display="$(tr '\0' '\n' <"/proc/${first_pid}/environ" 2>/dev/null | sed -n 's/^DISPLAY=//p' | head -n 1)"
fi
export DISPLAY="${DISPLAY:-${session_display:-:0}}"
export XDG_RUNTIME_DIR="${XDG_RUNTIME_DIR:-/run/user/$(id -u)}"
export DBUS_SESSION_BUS_ADDRESS="${DBUS_SESSION_BUS_ADDRESS:-unix:path=${XDG_RUNTIME_DIR}/bus}"

if [ -n "${old_pids}" ]; then
  kill ${old_pids}
fi

desktop_pid=""
for _ in 1 2 3 4 5; do
  sleep 1
  desktop_pid="$(pgrep -f "${desktop_pattern}" || true)"
  if [ -n "${desktop_pid}" ]; then
    break
  fi
done
if [ -z "${desktop_pid}" ]; then
  nohup /usr/bin/peony-qt-desktop -w -d \
    >"${XDG_RUNTIME_DIR}/memguardos-peony-desktop.log" 2>&1 </dev/null &
  sleep 2
  desktop_pid="$(pgrep -f "${desktop_pattern}" || true)"
fi
if [ -z "${desktop_pid}" ]; then
  echo "Peony desktop did not restart." >&2
  exit 1
fi

pkill -f '^/usr/bin/ukui-control-center( |$)' 2>/dev/null || true
if pgrep -f '^/usr/bin/memguardos-tray$' >/dev/null; then
  pkill -f '^/usr/bin/memguardos-tray$'
fi
nohup /usr/bin/memguardos-tray \
  >"${XDG_RUNTIME_DIR}/memguardos-tray.log" 2>&1 </dev/null &

echo "Peony desktop refreshed: pid=${desktop_pid}"
echo "MemGuardOS tray refreshed."
