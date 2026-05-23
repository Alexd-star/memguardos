#!/usr/bin/env bash
set -euo pipefail

cd "$(dirname "$0")/.."

echo "# MemGuardOS UKUI visible-plugin verification"
date -Is
echo "XDG_CURRENT_DESKTOP=${XDG_CURRENT_DESKTOP:-}"
echo "DISPLAY=${DISPLAY:-}"
echo

PEONY_INSTALLED="/usr/lib/x86_64-linux-gnu/peony-extensions/libpeony-menu-plugin-memguardos.so"
UKCC_INSTALLED="/usr/lib/x86_64-linux-gnu/ukui-control-center/libmemguardos-ukcc-plugin.so"
PEONY_BUILT="native/ukui/build/peony-memguard/libpeony-menu-plugin-memguardos.so"
UKCC_BUILT="native/ukui/build/ukcc-memguard/libmemguardos-ukcc-plugin.so"

for item in "$PEONY_BUILT" "$UKCC_BUILT" "$PEONY_INSTALLED" "$UKCC_INSTALLED"; do
  echo "## ${item}"
  if [ -f "$item" ]; then
    ls -l "$item"
    file "$item" || true
    ldd "$item" 2>/dev/null | grep -E 'not found|peony|ukcc|Qt5|libc' || true
    strings "$item" | grep -E 'MemGuardOS|memguardos|发送到|记忆安全|运行 doctor|验证审计链' || true
  else
    echo "missing"
  fi
  echo
done

echo "## runtime processes"
pgrep -a peony || true
pgrep -a ukui-control-center || true
echo

echo "## manual visible checks"
cat <<'EOF'
1. Run: peony -q 2>/dev/null || killall peony
2. Open Peony file manager and right-click a local file.
3. Confirm the menu item "发送到 MemGuardOS 记忆安全扫描" is visible.
4. Click the menu item and confirm notify-send shows a MemGuardOS message.
5. Open UKUI control center, security category, and confirm "MemGuardOS 记忆安全" is visible.
6. Click "运行 doctor", "验证审计链", and "检查 Kylin SDK"; confirm command output appears.
EOF
