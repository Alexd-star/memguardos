#!/usr/bin/env bash
set -euo pipefail

PROJECT_DIR="${1:-$(pwd)}"
BUILD_DIR="${PROJECT_DIR}/.build"
SRC="${PROJECT_DIR}/scripts/kylin_vector_probe.cpp"
BIN="${BUILD_DIR}/kylin_vector_probe"

if ! command -v g++ >/dev/null 2>&1; then
  echo "g++ not found; install build-essential or g++ first" >&2
  exit 1
fi

mkdir -p "${BUILD_DIR}"
g++ -std=c++17 "${SRC}" -o "${BIN}" -lkysdk-vector-engine-client
"${BIN}"
