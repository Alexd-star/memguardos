#!/usr/bin/env bash
set -euo pipefail

PROJECT_DIR="${1:-$(pwd)}"
INSTALL_DIR="/opt/memguardos"

echo "[memguard] installing from ${PROJECT_DIR} to ${INSTALL_DIR}"
if ! getent group memguard >/dev/null 2>&1; then
  sudo addgroup --system memguard >/dev/null
fi
if ! getent passwd memguard >/dev/null 2>&1; then
  sudo adduser --system --ingroup memguard --home /nonexistent --no-create-home \
    --shell /usr/sbin/nologin memguard >/dev/null
fi
sudo mkdir -p "${INSTALL_DIR}"
sudo rsync -a --delete \
  --exclude='.git/' \
  --exclude='.pytest_cache/' \
  --exclude='.ruff_cache/' \
  --exclude='.venv/' \
  --exclude='.venv-agent/' \
  --exclude='__pycache__/' \
  --exclude='dist/' \
  --exclude='memguardos.egg-info/' \
  --exclude='local_notes/' \
  "${PROJECT_DIR}/" "${INSTALL_DIR}/"
sudo cp "${INSTALL_DIR}/scripts/memguardd.service" /etc/systemd/system/memguardd.service
sudo cp "${INSTALL_DIR}/scripts/memguardd@.service" /etc/systemd/system/memguardd@.service
sudo cp "${INSTALL_DIR}/scripts/memguard-agent@.service" /etc/systemd/system/memguard-agent@.service
sudo install -d -m 0755 /etc/memguardos
sudo install -d -o memguard -g memguard -m 0700 /var/lib/memguardos
sudo install -d -m 0755 /var/lib/memguardos-agent
sudo systemctl daemon-reload

echo "[memguard] installed. Start with:"
echo "  sudo systemctl start memguardd"
echo "  sudo systemctl start memguardd@demo-a memguardd@demo-b"
echo "  sudo install -d -o \${USER} -g \${USER} /var/lib/memguardos-agent/\${USER}"
echo "  sudo systemctl start memguard-agent@\${USER}"
echo "  journalctl -u memguardd -n 50"
