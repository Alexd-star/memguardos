#!/usr/bin/env bash
set -euo pipefail

cd "$(dirname "$0")/.."

PYTHON_BIN="${PYTHON_BIN:-python3}"
MANIFEST="dist/memguardos_debian_packages.json"
EXTRACT_ROOT="${TMPDIR:-/tmp}/memguardos-deb-root"

bash scripts/build_deb.sh
test -s "$MANIFEST"

mapfile -t PACKAGE_PATHS < <("$PYTHON_BIN" - "$MANIFEST" <<'PY'
import json
import sys
from pathlib import Path

manifest_path = Path(sys.argv[1])
payload = json.loads(manifest_path.read_text(encoding="utf-8"))
expected = {
    "memguardos",
    "memguardos-agent-bridge",
    "memguardos-core",
    "memguardos-doc",
    "memguardos-ukui",
}
packages = payload.get("packages", [])
names = {str(item.get("package", "")) for item in packages}
if names != expected:
    raise SystemExit(f"unexpected package set: {sorted(names)}")
for item in packages:
    path = Path(str(item["path"]))
    if not path.is_file():
        raise SystemExit(f"missing package: {path}")
    print(path)
PY
)

echo "package_manifest=$MANIFEST"
echo "package_count=${#PACKAGE_PATHS[@]}"
rm -rf "$EXTRACT_ROOT"
mkdir -p "$EXTRACT_ROOT"

for package_path in "${PACKAGE_PATHS[@]}"; do
  echo "--- package: $package_path"
  sha256sum "$package_path"
  dpkg-deb --info "$package_path"
  dpkg-deb -x "$package_path" "$EXTRACT_ROOT"
done

test -x "$EXTRACT_ROOT/usr/bin/memctl"
test -f "$EXTRACT_ROOT/opt/memguardos/memguard/service.py"
test -f "$EXTRACT_ROOT/lib/systemd/system/memguardd.service"

PYTHONPATH="$EXTRACT_ROOT/opt/memguardos" python3 -m memguard.cli.memctl doctor \
  >"${TMPDIR:-/tmp}/memguardos-deb-doctor.txt"
echo "offline_extract_doctor=ok"

MULTIARCH="$(dpkg-architecture -qDEB_HOST_MULTIARCH)"
PEONY_PLUGIN="$EXTRACT_ROOT/usr/lib/$MULTIARCH/peony-extensions/libpeony-menu-plugin-memguardos.so"
UKCC_PLUGIN="$EXTRACT_ROOT/usr/lib/$MULTIARCH/ukui-control-center/libmemguardos-ukcc-plugin.so"
test -f "$PEONY_PLUGIN"
test -f "$UKCC_PLUGIN"

if ldd "$PEONY_PLUGIN" | grep -q 'not found'; then
  echo "Peony plugin has unresolved shared-library dependencies" >&2
  exit 2
fi
if ldd "$UKCC_PLUGIN" | grep -q 'not found'; then
  echo "UKUI control-center plugin has unresolved shared-library dependencies" >&2
  exit 2
fi
echo "plugin_ldd_missing=0"

for package_name in memguardos-core memguardos-ukui memguardos-agent-bridge memguardos-doc memguardos; do
  if dpkg-query -W -f='${db:Status-Status}' "$package_name" 2>/dev/null | grep -qx installed; then
    echo "apt_install_state[$package_name]=installed"
  else
    echo "apt_install_state[$package_name]=not_installed"
  fi
done
