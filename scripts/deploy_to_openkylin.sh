#!/usr/bin/env bash
# 把当前工作副本部署到 openKylin 的系统安装位置。
#
# 背景：/usr/bin/memctl 与 /usr/bin/memguard 是薄封装，强制
# PYTHONPATH=/opt/memguardos。桌面入口（托盘、Peony 右键、UKUI 控制中心）
# 全部经由它们启动，因此只同步 ~/memguardos 不会影响桌面表现——
# 必须同步到 /opt/memguardos，否则会出现"代码已改但界面没变"的假象。
set -euo pipefail

cd "$(dirname "$0")/.."
SOURCE_ROOT="$(pwd)"
TARGET_ROOT="${MEMGUARDOS_DEPLOY_ROOT:-/opt/memguardos}"

if [ ! -d "${TARGET_ROOT}" ]; then
  echo "target ${TARGET_ROOT} does not exist; run the .deb install or create it first" >&2
  exit 2
fi

echo "deploying ${SOURCE_ROOT} -> ${TARGET_ROOT}"
sudo rsync -a --delete \
  --exclude='__pycache__' \
  "${SOURCE_ROOT}/memguard/" "${TARGET_ROOT}/memguard/"
sudo rsync -a --exclude='__pycache__' "${SOURCE_ROOT}/scripts/" "${TARGET_ROOT}/scripts/"
sudo rsync -a "${SOURCE_ROOT}/data/" "${TARGET_ROOT}/data/"
sudo find "${TARGET_ROOT}" -name '__pycache__' -type d -exec rm -rf {} + 2>/dev/null || true

# 校验部署结果，避免"以为部署了其实没有"。
cd /tmp
PYTHONPATH="${TARGET_ROOT}" python3 - <<'PYEOF'
import memguard
import memguard.web.dashboard as dashboard

print("deployed package:", memguard.__file__)
html = dashboard._dashboard_html()
missing = [marker for marker in ("brand-mark", "light=1", "crumbs") if marker not in html]
if missing:
    raise SystemExit("deployed dashboard is missing expected markers: %s" % missing)
print("dashboard markers verified")
PYEOF

echo "restarting desktop entry points"
pkill -f '^/usr/bin/ukui-control-center' 2>/dev/null || true
if pgrep -x memguardos-tray >/dev/null 2>&1; then
  echo "tray is running; restart it to pick up new tray behaviour if the binary changed"
fi
echo "deploy done: reopen Settings or the tray menu to see the current build"
