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
    strings "$item" | grep -E 'MemGuardOS|memguardos|记忆安全|检查记忆内容|校验操作记录|本机智能检索|系统维护|系统报告|本地控制台' || true
  else
    echo "missing"
  fi
  echo
done

echo "## runtime processes"
pgrep -a peony || true
pgrep -af ukui-control-center || true
pgrep -af memguardos-tray || true
echo

echo "## manual visible checks"
cat <<'EOF'
1. Run: bash scripts/refresh_ukui_desktop.sh
2. Open Peony file manager and right-click a local file.
3. Confirm the menu item "使用 MemGuardOS 检查记忆内容" is visible.
4. Run: bash scripts/smoke_peony_file_scan.sh; confirm peony_file_scan_dialog=visible and preview_rows is at least 1.
5. Click the menu item, review the file preview dialog, select safe text files, and confirm the result dialog.
6. Open UKUI control center, security category, and confirm "MemGuardOS 记忆安全" is visible.
7. Confirm the tabs "日常使用", "管理员", and "系统维护" are visible. Use "重新检查状态", "校验操作记录", "检查本机智能检索", and "导出系统报告"; confirm the operation history and "维护结果" area update.
8. Click "打开本地控制台"; confirm the authenticated local console opens.
EOF
