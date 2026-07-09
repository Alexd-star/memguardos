#!/usr/bin/env bash
set -euo pipefail

cd "$(dirname "$0")/.."

# The split builder owns org.memguardos.policy, memguard-kylin-warmup.service,
# the Peony/UKUI plugins and the tray as separate package payloads.
exec python3 scripts/build_deb_packages.py "$@"
