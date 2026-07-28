#!/usr/bin/env bash
set -euo pipefail

cd "$(dirname "$0")/.."

PYTHON_BIN="${PYTHON_BIN:-python3}"
MANIFEST="${MEMGUARD_DEB_MANIFEST:-dist/memguardos_debian_packages.json}"
BUILD=1
if [ "${1:-}" = "--no-build" ]; then
  BUILD=0
fi

if [ "$BUILD" -eq 1 ]; then
  bash scripts/build_deb.sh
fi
test -s "$MANIFEST"

mapfile -t PACKAGE_PATHS < <("$PYTHON_BIN" - "$MANIFEST" <<'PY'
import json
import sys
from pathlib import Path

manifest = json.loads(Path(sys.argv[1]).read_text(encoding="utf-8"))
expected = {
    "memguardos",
    "memguardos-agent-bridge",
    "memguardos-core",
    "memguardos-doc",
    "memguardos-ukui",
}
packages = manifest.get("packages", [])
if {item.get("package") for item in packages} != expected:
    raise SystemExit("Debian package manifest does not contain the expected five packages")
for item in packages:
    path = Path(str(item["path"])).resolve()
    if not path.is_file():
        raise SystemExit(f"missing package: {path}")
    print(path)
PY
)

echo "package_manifest=$MANIFEST"
echo "package_count=${#PACKAGE_PATHS[@]}"
sudo env DEBIAN_FRONTEND=noninteractive apt-get install -y --reinstall "${PACKAGE_PATHS[@]}"
sudo systemctl daemon-reload
sudo systemctl enable --now memguardd.service

for package in memguardos-core memguardos-ukui memguardos-agent-bridge memguardos-doc memguardos; do
  status="$(dpkg-query -W -f='${db:Status-Status}' "$package")"
  version="$(dpkg-query -W -f='${Version}' "$package")"
  test "$status" = installed
  printf 'installed[%s]=%s version=%s\n' "$package" "$status" "$version"
done

test "$(systemctl is-active memguardd.service)" = active
test -x /usr/bin/memguard
test -x /usr/bin/memctl
test -x /usr/bin/memguardd
test -f /usr/share/polkit-1/actions/org.memguardos.policy
test -f /etc/xdg/autostart/memguardos-tray.desktop
/usr/bin/memguard status
/usr/bin/memctl verify-audit
echo "apt_install=ok"
