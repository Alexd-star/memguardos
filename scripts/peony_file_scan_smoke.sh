#!/usr/bin/env bash
set -euo pipefail

ROOT="${1:-$(pwd)}"
PYTHON_BIN="${PYTHON_BIN:-python3}"
WORKDIR="${TMPDIR:-/tmp}/memguardos-peony-smoke"
mkdir -p "${WORKDIR}"

SAMPLE="${WORKDIR}/memguardos-peony-sample.md"
cat >"${SAMPLE}" <<'EOF'
openKylin Peony 右键闭环样例：这个文件描述 systemd MemoryMax、审计链和 MemGuardOS 记忆安全扫描。
EOF

EVENT_JSON=$(PYTHONPATH="${ROOT}${PYTHONPATH:+:${PYTHONPATH}}" "${PYTHON_BIN}" - <<PY
import json
print(json.dumps({
  "event_type": "peony_context_menu",
  "path": "${SAMPLE}",
  "process": "peony",
  "action": "inspect"
}, ensure_ascii=False))
PY
)

cd "${ROOT}"
export PYTHONPATH="${ROOT}${PYTHONPATH:+:${PYTHONPATH}}"
export MEMGUARD_NAMESPACE="${MEMGUARD_NAMESPACE:-peony-smoke}"

"${PYTHON_BIN}" -m memguard.cli.memctl ingest-os-event --json "${EVENT_JSON}" >/tmp/memguardos_peony_os_event.json
"${PYTHON_BIN}" -m memguard.cli.memctl ingest --file "${SAMPLE}" --source-kind peony_file >/tmp/memguardos_peony_file_ingest.json
"${PYTHON_BIN}" -m memguard.cli.memctl search "Peony systemd MemoryMax 记忆安全扫描" --top-k 5 >/tmp/memguardos_peony_search.json
"${PYTHON_BIN}" -m memguard.cli.memctl verify-audit >/tmp/memguardos_peony_audit.json

"${PYTHON_BIN}" - <<'PY'
import json
from pathlib import Path

ingest = json.loads(Path("/tmp/memguardos_peony_file_ingest.json").read_text(encoding="utf-8"))
search = json.loads(Path("/tmp/memguardos_peony_search.json").read_text(encoding="utf-8"))
audit = json.loads(Path("/tmp/memguardos_peony_audit.json").read_text(encoding="utf-8"))
ok = bool(ingest.get("stored")) and bool(search) and bool(audit.get("ok"))
print(json.dumps({
    "ok": ok,
    "namespace": "peony-smoke",
    "file_ingest_stored": ingest.get("stored"),
    "file_memory_id": ingest.get("memory_id"),
    "search_hits": len(search),
    "audit_ok": audit.get("ok"),
}, ensure_ascii=False, indent=2))
raise SystemExit(0 if ok else 2)
PY
