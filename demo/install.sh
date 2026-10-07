#!/usr/bin/env bash
set -euo pipefail
SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
(cd -- "${SCRIPT_DIR}/packages" && sha256sum -c SHA256SUMS.txt)
cd -- "${SCRIPT_DIR}/../src"
MEMGUARD_DEB_MANIFEST="../demo/packages/memguardos_debian_packages.json" \
  bash scripts/install_deb_package_set.sh --no-build
