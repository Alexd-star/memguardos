#!/usr/bin/env bash
set -euo pipefail

cd "$(dirname "$0")/.."

OUT_DIR="${1:-}"
if [ -n "$OUT_DIR" ]; then
  mkdir -p "${OUT_DIR}"
  LOG="${OUT_DIR}/ukui_deep_plugins.txt"
  exec >"${LOG}"
fi

echo "# MemGuardOS UKUI deep plugin probe"
date -Is
echo "XDG_CURRENT_DESKTOP=${XDG_CURRENT_DESKTOP:-}"
echo
echo "## runtime packages"
dpkg -l 2>/dev/null | grep -E 'peony|ukui-control-center|libpeony|libukcc' || true
echo
echo "## plugin directories"
ls -la /usr/lib/x86_64-linux-gnu/peony-extensions 2>/dev/null || true
ls -la /usr/lib/x86_64-linux-gnu/ukui-control-center 2>/dev/null || true
echo
echo "## development headers"
test -f /usr/include/peony-qt/menu-plugin-iface.h && echo "peony_headers=yes" || echo "peony_headers=no"
test -f /usr/include/ukcc/interface/interface.h && echo "ukcc_headers=yes" || echo "ukcc_headers=no"
command -v qmake || command -v qmake-qt5 || true
pkg-config --exists peony && pkg-config --modversion peony || true
echo
echo "## source prototype files"
find native/ukui -maxdepth 3 -type f | sort
