#!/usr/bin/env bash
set -euo pipefail

cd "$(dirname "$0")/.."

echo "[1/11] doctor"
python3 -m memguard.cli.memctl doctor

echo "[2/11] Kylin AI SDK status"
python3 -m memguard.cli.memctl sdk-status

echo "[3/11] Kylin embedding probe if SDK is available"
python3 -m memguard.cli.memctl sdk-status --probe-embedding --probe-timeout 90 || echo "Kylin embedding probe unavailable; stable hash fallback remains active"

echo "[4/11] Kylin vector DB probe if SDK is available"
if [ -f scripts/kylin_vector_probe.cpp ] && command -v g++ >/dev/null 2>&1; then
  bash scripts/run_kylin_vector_probe.sh "$(pwd)"
else
  echo "Kylin vector probe skipped; g++ or source unavailable"
fi

echo "[5/11] Kylin optional retrieval backend smoke test"
MEMGUARD_EMBEDDING_BACKEND=auto MEMGUARD_KYLIN_EMBEDDING_TIMEOUT=90 python3 - <<'PY'
import json

from memguard.service import MemGuardService

service = MemGuardService()
service.ingest_text("openKylin 服务用 systemd MemoryMax 和 CPUQuota 做端侧资源限制。")
hits = service.search("openKylin 服务内存限制", limit=1)
print(json.dumps(hits, ensure_ascii=False, indent=2))
if hits and hits[0].get("vector_backend") == "kylin_sdk":
    print("kylin optional retrieval backend: ok")
else:
    print("kylin optional retrieval backend unavailable; stable hash fallback remains active")
PY

echo "[6/11] natural-language sensitive pattern smoke test"
python3 -m memguard.cli.memctl patterns suggest "把员工工号，格式以 MG- 开头后接 6 位数字，作为敏感信息"

echo "[7/11] tests"
python3 -m pytest -q

echo "[8/11] eval"
mkdir -p output
python3 -m memguard.eval.run_eval --output output/eval_result.json --markdown output/eval_report.md

echo "[9/11] auditd rule preview"
python3 -m memguard.cli.memctl auditd-rules --format auditctl

echo "[10/11] OS event ingest smoke test"
python3 -m memguard.cli.memctl ingest-os-event --json '{"event_type":"fanotify_open","path":"/home/test/.ssh/id_rsa","process":"cat","action":"read"}'

echo "[11/11] auditd real-log ingestion if available"
if command -v auditctl >/dev/null 2>&1 && [ -f /var/log/audit/audit.log ]; then
  bash scripts/auditd_to_memguard.sh "$(pwd)" memguard_sensitive 200 --summary
else
  echo "auditd log unavailable; skipping real auditd ingestion"
fi

echo "[final] audit verification"
python3 -m memguard.cli.memctl verify-audit
