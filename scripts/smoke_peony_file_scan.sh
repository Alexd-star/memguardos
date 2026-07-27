#!/usr/bin/env bash
set -euo pipefail

cd "$(dirname "$0")/.."

PLUGIN_PATH="${1:-/usr/lib/x86_64-linux-gnu/peony-extensions/libpeony-menu-plugin-memguardos.so}"
SAMPLE_FILE="${2:-}"
QMAKE_BIN="${QMAKE_BIN:-$(command -v qmake || command -v qmake-qt5 || true)}"
BUILD_DIR="native/ukui/build/peony-file-scan-smoke"

if [ -z "${QMAKE_BIN}" ]; then
  echo "qmake not found; install qtbase5-dev before running this smoke test." >&2
  exit 2
fi
if ! pkg-config --exists peony; then
  echo "peony development metadata is unavailable." >&2
  exit 2
fi
if [ ! -f "${PLUGIN_PATH}" ]; then
  echo "Peony plugin not found: ${PLUGIN_PATH}" >&2
  exit 2
fi

cleanup_file=""
if [ -z "${SAMPLE_FILE}" ]; then
  cleanup_file="$(mktemp --suffix=.md)"
  SAMPLE_FILE="${cleanup_file}"
  printf '%s\n' '用户偏好：以后代码搜索优先使用 rg。' >"${SAMPLE_FILE}"
fi
trap 'if [ -n "${cleanup_file}" ]; then rm -f "${cleanup_file}"; fi' EXIT

rm -rf "${BUILD_DIR}"
mkdir -p "${BUILD_DIR}"
(
  cd "${BUILD_DIR}"
  "${QMAKE_BIN}" ../../tests/peony-file-scan-smoke.pro
  make -j"$(nproc)"
)

if [ -z "${DISPLAY:-}" ]; then
  export QT_QPA_PLATFORM=offscreen
fi
"${BUILD_DIR}/peony-file-scan-smoke" "${PLUGIN_PATH}" "${SAMPLE_FILE}"
