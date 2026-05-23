#!/usr/bin/env bash
set -euo pipefail

cd "$(dirname "$0")/.."

if [ "${1:-}" != "--no-build" ]; then
  bash scripts/build_ukui_plugins.sh
fi

PEONY_PLUGIN="native/ukui/build/peony-memguard/libpeony-menu-plugin-memguardos.so"
UKCC_PLUGIN="native/ukui/build/ukcc-memguard/libmemguardos-ukcc-plugin.so"
PEONY_DEST_DIR="/usr/lib/x86_64-linux-gnu/peony-extensions"
UKCC_DEST_DIR="/usr/lib/x86_64-linux-gnu/ukui-control-center"
BIND_BASE="${MEMGUARDOS_UKUI_BIND_BASE:-/var/tmp/memguardos-ukui-visible}"

test -f "${PEONY_PLUGIN}"
test -f "${UKCC_PLUGIN}"

install_plugin() {
  local plugin="$1"
  local dest_dir="$2"
  local dest_name="$3"
  local bind_name="$4"

  if sudo test -w "${dest_dir}" 2>/dev/null; then
    sudo install -m 755 "${plugin}" "${dest_dir}/${dest_name}"
    echo "installed plugin: ${dest_dir}/${dest_name}"
    return 0
  fi

  echo "destination is not writable, using bind-mount demo directory: ${dest_dir}"
  local bind_dir="${BIND_BASE}/${bind_name}"
  sudo mkdir -p "${bind_dir}"
  sudo cp -a "${dest_dir}/." "${bind_dir}/" 2>/dev/null || true
  sudo install -m 755 "${plugin}" "${bind_dir}/${dest_name}"
  if ! mountpoint -q "${dest_dir}"; then
    sudo mount --bind "${bind_dir}" "${dest_dir}"
  fi
  sudo install -m 755 "${plugin}" "${dest_dir}/${dest_name}"
  echo "bind-mounted plugin: ${bind_dir} -> ${dest_dir}"
  echo "installed plugin: ${dest_dir}/${dest_name}"
}

install_plugin "${PEONY_PLUGIN}" "${PEONY_DEST_DIR}" "libpeony-menu-plugin-memguardos.so" "peony-extensions"
install_plugin "${UKCC_PLUGIN}" "${UKCC_DEST_DIR}" "libmemguardos-ukcc-plugin.so" "ukui-control-center"
sudo ldconfig || true
echo "Restart peony or ukui-control-center before UI verification."
