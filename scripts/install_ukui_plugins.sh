#!/usr/bin/env bash
set -euo pipefail

cd "$(dirname "$0")/.."

if [ "${1:-}" != "--no-build" ]; then
  bash scripts/build_ukui_plugins.sh
fi

PEONY_PLUGIN="native/ukui/build/peony-memguard/libpeony-menu-plugin-memguardos.so"
UKCC_PLUGIN="native/ukui/build/ukcc-memguard/libmemguardos-ukcc-plugin.so"

test -f "${PEONY_PLUGIN}"
test -f "${UKCC_PLUGIN}"

sudo install -m 755 "${PEONY_PLUGIN}" /usr/lib/x86_64-linux-gnu/peony-extensions/libpeony-menu-plugin-memguardos.so
sudo install -m 755 "${UKCC_PLUGIN}" /usr/lib/x86_64-linux-gnu/ukui-control-center/libmemguardos-ukcc-plugin.so

echo "installed Peony plugin: /usr/lib/x86_64-linux-gnu/peony-extensions/libpeony-menu-plugin-memguardos.so"
echo "installed UKCC plugin: /usr/lib/x86_64-linux-gnu/ukui-control-center/libmemguardos-ukcc-plugin.so"
echo "Restart peony or ukui-control-center before UI verification."
