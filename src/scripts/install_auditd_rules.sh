#!/usr/bin/env bash
set -euo pipefail

PROJECT_DIR="${1:-$(pwd)}"
RULE_FILE="/etc/audit/rules.d/memguard.rules"

cd "${PROJECT_DIR}"
echo "[memguard] generating auditd rules at ${RULE_FILE}"
python3 -m memguard.cli.memctl auditd-rules --format rules-file --existing-only | sudo tee "${RULE_FILE}" >/dev/null

if command -v augenrules >/dev/null 2>&1; then
  sudo augenrules --load
elif command -v service >/dev/null 2>&1; then
  sudo service auditd restart
else
  echo "[memguard] please reload auditd manually"
fi

echo "[memguard] auditd rules installed. Inspect with:"
echo "  sudo auditctl -l | grep memguard_sensitive"
echo "  sudo ausearch -k memguard_sensitive -i"
