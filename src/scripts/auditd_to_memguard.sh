#!/usr/bin/env bash
set -euo pipefail

PROJECT_DIR="${1:-$(pwd)}"
KEY="${2:-memguard_sensitive}"
LIMIT="${3:-2000}"
SUMMARY="${4:-}"
PYTHON_BIN="${PYTHON_BIN:-python3}"

cd "${PROJECT_DIR}"

if [ -r /var/log/audit/audit.log ]; then
  "${PYTHON_BIN}" -m memguard.cli.memctl ingest-auditd --file /var/log/audit/audit.log --key "${KEY}" --limit "${LIMIT}" ${SUMMARY}
else
  sudo tail -n "${LIMIT}" /var/log/audit/audit.log | "${PYTHON_BIN}" -m memguard.cli.memctl ingest-auditd --file - --key "${KEY}" --limit "${LIMIT}" ${SUMMARY}
fi
