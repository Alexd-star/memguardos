#!/usr/bin/env bash
set -euo pipefail

cd "$(dirname "$0")/.."

PROJECT_ROOT="$(pwd)"
PACKAGE_NAME="${PACKAGE_NAME:-memguardos}"
VERSION="${VERSION:-$(python3 - <<'PY'
from pathlib import Path
import re
text = Path("pyproject.toml").read_text(encoding="utf-8")
match = re.search(r'^version\s*=\s*"([^"]+)"', text, re.M)
print(match.group(1) if match else "0.1.0")
PY
)}"
ARCH="${ARCH:-$(dpkg --print-architecture 2>/dev/null || uname -m)}"
MULTIARCH="${MULTIARCH:-$(dpkg-architecture -qDEB_HOST_MULTIARCH 2>/dev/null || echo x86_64-linux-gnu)}"
BUILD_ROOT="${BUILD_ROOT:-dist/deb}"
STAGING="${BUILD_ROOT}/${PACKAGE_NAME}_${VERSION}_${ARCH}"
OUTPUT_DIR="${OUTPUT_DIR:-dist}"
WITH_UKUI="${WITH_UKUI:-auto}"

if ! command -v dpkg-deb >/dev/null 2>&1; then
  echo "dpkg-deb is required to build a Debian package." >&2
  exit 2
fi
if ! command -v rsync >/dev/null 2>&1; then
  echo "rsync is required to stage MemGuardOS files." >&2
  exit 2
fi

rm -rf "${STAGING}"
mkdir -p \
  "${STAGING}/DEBIAN" \
  "${STAGING}/opt/memguardos" \
  "${STAGING}/usr/bin" \
  "${STAGING}/lib/systemd/system" \
  "${STAGING}/usr/share/doc/${PACKAGE_NAME}"

rsync -a --delete \
  --exclude='.git/' \
  --exclude='.mypy_cache/' \
  --exclude='.pytest_cache/' \
  --exclude='.ruff_cache/' \
  --exclude='.venv/' \
  --exclude='.venv-agent/' \
  --exclude='__pycache__/' \
  --exclude='dist/' \
  --exclude='memguardos.egg-info/' \
  --exclude='local_notes/' \
  --exclude='docs/evidence/' \
  --exclude='native/ukui/build/' \
  "${PROJECT_ROOT}/" "${STAGING}/opt/memguardos/"

install -m 0644 scripts/memguardd.service "${STAGING}/lib/systemd/system/memguardd.service"
install -m 0644 scripts/memguardd@.service "${STAGING}/lib/systemd/system/memguardd@.service"
install -m 0644 scripts/memguard-agent@.service "${STAGING}/lib/systemd/system/memguard-agent@.service"
install -m 0644 README.md "${STAGING}/usr/share/doc/${PACKAGE_NAME}/README.md"
install -m 0644 LICENSE "${STAGING}/usr/share/doc/${PACKAGE_NAME}/copyright"

cat >"${STAGING}/usr/bin/memctl" <<'SH'
#!/bin/sh
export PYTHONPATH="/opt/memguardos${PYTHONPATH:+:$PYTHONPATH}"
exec /usr/bin/python3 -m memguard.cli.memctl "$@"
SH
cat >"${STAGING}/usr/bin/memguardd" <<'SH'
#!/bin/sh
export PYTHONPATH="/opt/memguardos${PYTHONPATH:+:$PYTHONPATH}"
exec /usr/bin/python3 -m memguard.daemon "$@"
SH
chmod 0755 "${STAGING}/usr/bin/memctl" "${STAGING}/usr/bin/memguardd"

INCLUDE_UKUI=0
if [ "${WITH_UKUI}" != "no" ]; then
  if bash scripts/build_ukui_plugins.sh; then
    PEONY_PLUGIN="native/ukui/build/peony-memguard/libpeony-menu-plugin-memguardos.so"
    UKCC_PLUGIN="native/ukui/build/ukcc-memguard/libmemguardos-ukcc-plugin.so"
    if [ -f "${PEONY_PLUGIN}" ] && [ -f "${UKCC_PLUGIN}" ]; then
      mkdir -p \
        "${STAGING}/usr/lib/${MULTIARCH}/peony-extensions" \
        "${STAGING}/usr/lib/${MULTIARCH}/ukui-control-center"
      install -m 0755 "${PEONY_PLUGIN}" "${STAGING}/usr/lib/${MULTIARCH}/peony-extensions/libpeony-menu-plugin-memguardos.so"
      install -m 0755 "${UKCC_PLUGIN}" "${STAGING}/usr/lib/${MULTIARCH}/ukui-control-center/libmemguardos-ukcc-plugin.so"
      INCLUDE_UKUI=1
    fi
  elif [ "${WITH_UKUI}" = "yes" ]; then
    echo "UKUI plugin build failed and WITH_UKUI=yes was requested." >&2
    exit 2
  else
    echo "UKUI plugin build skipped; build dependencies are unavailable." >&2
  fi
fi

DEPENDS="python3 (>= 3.9), systemd"
if [ "${INCLUDE_UKUI}" = "1" ]; then
  DEPENDS="${DEPENDS}, libqt5core5a, libqt5widgets5, peony, ukui-control-center, libpeony3, libukcc3"
fi

cat >"${STAGING}/DEBIAN/postinst" <<'SH'
#!/bin/sh
set -e
for unit in memguardd.service memguardd@.service memguard-agent@.service; do
  legacy="/etc/systemd/system/${unit}"
  packaged="/lib/systemd/system/${unit}"
  if [ -f "${legacy}" ] && [ -f "${packaged}" ] && grep -q "MemGuardOS" "${legacy}"; then
    if ! cmp -s "${legacy}" "${packaged}"; then
      mv "${legacy}" "${legacy}.memguardos-legacy.bak"
    fi
  fi
done
if command -v ldconfig >/dev/null 2>&1; then
  ldconfig || true
fi
if command -v systemctl >/dev/null 2>&1; then
  systemctl daemon-reload || true
fi
exit 0
SH
cat >"${STAGING}/DEBIAN/prerm" <<'SH'
#!/bin/sh
set -e
if [ "$1" = "remove" ] || [ "$1" = "deconfigure" ]; then
  if command -v systemctl >/dev/null 2>&1; then
    systemctl stop memguardd.service >/dev/null 2>&1 || true
  fi
fi
exit 0
SH
cat >"${STAGING}/DEBIAN/postrm" <<'SH'
#!/bin/sh
set -e
if command -v systemctl >/dev/null 2>&1; then
  systemctl daemon-reload || true
fi
exit 0
SH
chmod 0755 "${STAGING}/DEBIAN/postinst" "${STAGING}/DEBIAN/prerm" "${STAGING}/DEBIAN/postrm"

INSTALLED_SIZE="$(du -sk "${STAGING}" | awk '{print $1}')"
cat >"${STAGING}/DEBIAN/control" <<EOF
Package: ${PACKAGE_NAME}
Version: ${VERSION}
Section: utils
Priority: optional
Architecture: ${ARCH}
Maintainer: MemGuardOS Project Team <memguardos@example.com>
Depends: ${DEPENDS}
Installed-Size: ${INSTALLED_SIZE}
Homepage: https://github.com/Alexd-star/memguardos
Description: Linux-oriented OS Agent memory extraction and precise forgetting
 MemGuardOS provides a local memory substrate for Linux/openKylin OS Agents.
 It supports multi-source preference and knowledge memory extraction,
 mmap-backed append-only storage, SQLite FTS metadata, graph relations,
 sensitive information filtering, auditable precise forgetting, systemd/cgroup
 deployment, auditd/journald integration and optional UKUI desktop plugins.
EOF

(
  cd "${STAGING}"
  find . -type f ! -path './DEBIAN/*' -print0 | sort -z | xargs -0 md5sum | sed 's#  ./#  #' > DEBIAN/md5sums
)

mkdir -p "${OUTPUT_DIR}"
DEB_PATH="${OUTPUT_DIR}/${PACKAGE_NAME}_${VERSION}_${ARCH}.deb"
dpkg-deb --build --root-owner-group "${STAGING}" "${DEB_PATH}"
echo "built_deb=${DEB_PATH}"
echo "architecture=${ARCH}"
echo "multiarch=${MULTIARCH}"
echo "ukui_plugins_included=${INCLUDE_UKUI}"
dpkg-deb --info "${DEB_PATH}"
