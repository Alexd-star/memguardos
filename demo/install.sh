#!/usr/bin/env bash
set -euo pipefail
cd -- "$(dirname -- "${BASH_SOURCE[0]}")/packages"
sha256sum -c SHA256SUMS.txt
sudo apt install ./memguardos-core_*.deb ./memguardos-ukui_*.deb \
  ./memguardos-agent-bridge_*.deb ./memguardos-doc_*.deb ./memguardos_[0-9]*_all.deb
memguard status
