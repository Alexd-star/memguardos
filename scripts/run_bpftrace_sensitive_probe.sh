#!/usr/bin/env bash
set -euo pipefail

cd "$(dirname "$0")/.."

SCRIPT="scripts/bpftrace_sensitive_open.bt"
if ! command -v bpftrace >/dev/null 2>&1; then
  echo "bpftrace not found; optional eBPF probe skipped."
  exit 2
fi

if [ ! -f "$SCRIPT" ]; then
  echo "probe script not found: $SCRIPT"
  exit 2
fi

echo "Starting optional bpftrace sensitive-open probe."
echo "Open /etc/shadow or /etc/passwd in another terminal, then stop with Ctrl+C."
echo "Each emitted JSON line can be ingested by:"
echo "  python3 -m memguard.cli.memctl ingest-os-event --json '<line>'"
echo

sudo bpftrace "$SCRIPT"
