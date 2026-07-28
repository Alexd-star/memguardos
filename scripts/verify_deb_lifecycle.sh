#!/usr/bin/env bash
set -euo pipefail

cd "$(dirname "$0")/.."

VERSION="${1:-1.0.0-2}"
ARCH="${2:-$(dpkg --print-architecture)}"
ROOT="${TMPDIR:-/tmp}/memguardos-deb-lifecycle"
PACKAGES=(
  "dist/memguardos-core_${VERSION}_${ARCH}.deb"
  "dist/memguardos-ukui_${VERSION}_${ARCH}.deb"
  "dist/memguardos-agent-bridge_${VERSION}_${ARCH}.deb"
  "dist/memguardos-doc_${VERSION}_all.deb"
  "dist/memguardos_${VERSION}_all.deb"
)

for package in "${PACKAGES[@]}"; do
  test -s "${package}" || { echo "missing package: ${package}" >&2; exit 2; }
  dpkg-deb --info "${package}" >/dev/null
done

test "$(dpkg-deb -f "${PACKAGES[0]}" Package)" = "memguardos-core"
test "$(dpkg-deb -f "${PACKAGES[1]}" Depends)" = "memguardos-core (= ${VERSION}), libqt5core5a, libqt5widgets5, peony, ukui-control-center, libpeony3, libukcc3"
test "$(dpkg-deb -f "${PACKAGES[2]}" Depends)" = "memguardos-core (= ${VERSION})"
dpkg-deb -f "${PACKAGES[4]}" Depends | grep -Fq "memguardos-doc (= ${VERSION})"

rm -rf "${ROOT}"
mkdir -p "${ROOT}"
for package in "${PACKAGES[@]}"; do
  dpkg-deb -x "${package}" "${ROOT}"
done

test -x "${ROOT}/usr/bin/memctl"
test -x "${ROOT}/usr/bin/memguardos-tray"
test -f "${ROOT}/lib/systemd/system/memguard-agent@.service"
test -f "${ROOT}/usr/share/doc/memguardos-doc/README.md"
test -f "${ROOT}/usr/share/polkit-1/actions/org.memguardos.policy"
PYTHONPATH="${ROOT}/opt/memguardos" python3 -m memguard.cli.memctl doctor >/dev/null

mapfile -t UKUI_FILES < <(dpkg-deb -c "${PACKAGES[1]}" | awk '$1 !~ /^d/ {sub(/^\.\//, "", $6); print $6}')
for relative in "${UKUI_FILES[@]}"; do
  rm -f "${ROOT}/${relative}"
done
test -x "${ROOT}/usr/bin/memctl"
test ! -e "${ROOT}/usr/bin/memguardos-tray"
test -f "${ROOT}/lib/systemd/system/memguardd.service"

dpkg-deb -x "${PACKAGES[1]}" "${ROOT}"
test -x "${ROOT}/usr/bin/memguardos-tray"

for package in "${PACKAGES[@]}"; do
  control_dir="${ROOT}/control-$(basename "${package}" .deb)"
  rm -rf "${control_dir}"
  dpkg-deb -e "${package}" "${control_dir}"
  if grep -R -E 'rm +-rf +.*\.memguardos|rm +-r +.*\.memguardos' "${control_dir}"; then
    echo "maintainer script attempts to delete user memory data: ${package}" >&2
    exit 3
  fi
done

echo "package_count=5"
echo "offline_install=ok"
echo "desktop_remove_core_preserved=ok"
echo "desktop_reinstall=ok"
echo "user_data_delete_guard=ok"
