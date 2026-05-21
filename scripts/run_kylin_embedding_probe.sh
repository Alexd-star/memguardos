#!/usr/bin/env bash
set -euo pipefail

cd "$(dirname "$0")/.."

OUT_BIN="${TMPDIR:-/tmp}/memguardos_kylin_embedding_probe"
PROBE_TIMEOUT="${PROBE_TIMEOUT:-90}"
TEXT="${1:-openKylin agent memory}"

if ! command -v g++ >/dev/null 2>&1; then
  echo "g++ not found; install build-essential first" >&2
  exit 2
fi

PKG_CFLAGS=""
PKG_LIBS="-lkysdk-coreai-embedding"
if command -v pkg-config >/dev/null 2>&1 && pkg-config --exists kylin-coreai-embedding; then
  PKG_CFLAGS="$(pkg-config --cflags kylin-coreai-embedding)"
  PKG_LIBS="$(pkg-config --libs kylin-coreai-embedding)"
fi

echo "build: g++ -std=c++17 scripts/kylin_embedding_probe.cpp ${PKG_CFLAGS} ${PKG_LIBS}"
g++ -std=c++17 scripts/kylin_embedding_probe.cpp ${PKG_CFLAGS} ${PKG_LIBS} -o "${OUT_BIN}"

echo "run: timeout ${PROBE_TIMEOUT} ${OUT_BIN}"
timeout "${PROBE_TIMEOUT}" "${OUT_BIN}" "${TEXT}"
