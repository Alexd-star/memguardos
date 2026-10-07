#!/usr/bin/env bash
set -euo pipefail

cd "$(dirname "$0")/.."

QMAKE_BIN="${QMAKE_BIN:-$(command -v qmake || command -v qmake-qt5 || true)}"
OUT_DIR="native/ukui/build"
mkdir -p "${OUT_DIR}"

if [ -z "${QMAKE_BIN}" ]; then
  echo "qmake not found; install qtbase5-dev in openKylin maintenance mode." >&2
  exit 2
fi
if ! pkg-config --exists peony; then
  echo "peony.pc not found; install libpeony-dev in openKylin maintenance mode." >&2
  exit 2
fi
if [ ! -f /usr/include/ukcc/interface/interface.h ]; then
  echo "UKCC headers not found; install libukcc-dev in openKylin maintenance mode." >&2
  exit 2
fi

for plugin in peony-memguard ukcc-memguard; do
  build_dir="${OUT_DIR}/${plugin}"
  rm -rf "${build_dir}"
  mkdir -p "${build_dir}"
  (
    cd "${build_dir}"
    "${QMAKE_BIN}" "../../${plugin}/${plugin}.pro"
    make -j"$(nproc)"
  )
done

tray_build="${OUT_DIR}/tray"
rm -rf "${tray_build}"
mkdir -p "${tray_build}"
(
  cd "${tray_build}"
  "${QMAKE_BIN}" "../../tray/memguard-tray.pro"
  make -j"$(nproc)"
)

echo "UKUI desktop build completed:"
find "${OUT_DIR}" -maxdepth 3 -type f -name '*.so' -print
find "${OUT_DIR}" -maxdepth 3 -type f -name 'memguardos-tray' -print
