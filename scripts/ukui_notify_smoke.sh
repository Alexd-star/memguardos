#!/usr/bin/env bash
set -euo pipefail

TITLE="${1:-MemGuardOS 安全记忆通知}"
BODY="${2:-检测到敏感记忆或遗忘操作，请查看 memctl audit 审计链。}"
UID_VALUE="$(id -u)"

if ! command -v notify-send >/dev/null 2>&1; then
  echo "notify-send not found; install libnotify-bin or use UKUI desktop environment."
  exit 2
fi

export DISPLAY="${DISPLAY:-:0}"
export DBUS_SESSION_BUS_ADDRESS="${DBUS_SESSION_BUS_ADDRESS:-unix:path=/run/user/${UID_VALUE}/bus}"

notify-send -a "MemGuardOS" "$TITLE" "$BODY"
echo "ukui notification sent via notify-send"
