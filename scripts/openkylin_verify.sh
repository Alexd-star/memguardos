#!/usr/bin/env bash
set -euo pipefail

cd "$(dirname "$0")/.."

echo "[1/8] doctor"
python3 -m memguard.cli.memctl doctor

echo "[2/8] Kylin AI SDK status"
python3 -m memguard.cli.memctl sdk-status

echo "[3/9] Kylin vector DB probe if SDK is available"
if [ -f scripts/kylin_vector_probe.cpp ] && command -v g++ >/dev/null 2>&1; then
  bash scripts/run_kylin_vector_probe.sh "$(pwd)"
else
  echo "Kylin vector probe skipped; g++ or source unavailable"
fi

echo "[4/9] tests"
python3 -m pytest -q

echo "[5/9] eval"
python3 -m memguard.eval.run_eval --output docs/eval_result.json --markdown docs/eval_report.md

echo "[6/9] auditd rule preview"
python3 -m memguard.cli.memctl auditd-rules --format auditctl

echo "[7/9] OS event ingest smoke test"
python3 -m memguard.cli.memctl ingest-os-event --json '{"event_type":"fanotify_open","path":"/home/test/.ssh/id_rsa","process":"cat","action":"read"}'

echo "[8/9] auditd real-log ingestion if available"
if command -v auditctl >/dev/null 2>&1 && [ -f /var/log/audit/audit.log ]; then
  bash scripts/auditd_to_memguard.sh "$(pwd)" memguard_sensitive 200 --summary
else
  echo "auditd log unavailable; skipping real auditd ingestion"
fi

echo "[9/9] audit verification"
python3 -m memguard.cli.memctl verify-audit
