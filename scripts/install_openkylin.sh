#!/usr/bin/env bash
set -euo pipefail

PROJECT_DIR="${1:-$(pwd)}"
INSTALL_DIR="/opt/memguardos"

echo "[memguard] installing from ${PROJECT_DIR} to ${INSTALL_DIR}"
sudo mkdir -p "${INSTALL_DIR}"
sudo rsync -a --delete \
  --exclude='.git/' \
  --exclude='.pytest_cache/' \
  --exclude='.ruff_cache/' \
  --exclude='.venv/' \
  --exclude='__pycache__/' \
  --exclude='dist/' \
  --exclude='memguardos.egg-info/' \
  --exclude='local_notes/' \
  "${PROJECT_DIR}/" "${INSTALL_DIR}/"
sudo cp "${INSTALL_DIR}/scripts/memguardd.service" /etc/systemd/system/memguardd.service
sudo cp "${INSTALL_DIR}/scripts/memguardd@.service" /etc/systemd/system/memguardd@.service
sudo systemctl daemon-reload

echo "[memguard] installed. Start with:"
echo "  sudo systemctl start memguardd"
echo "  sudo systemctl start memguardd@demo-a memguardd@demo-b"
echo "  journalctl -u memguardd -n 50"
