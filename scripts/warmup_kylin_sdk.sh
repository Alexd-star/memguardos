#!/usr/bin/env bash
set -euo pipefail

TIMEOUT="${MEMGUARD_KYLIN_WARMUP_TIMEOUT:-90}"
PYTHON_BIN="${PYTHON_BIN:-python3}"

if command -v memctl >/dev/null 2>&1; then
  MEMCTL=(memctl)
else
  MEMCTL=("${PYTHON_BIN}" -m memguard.cli.memctl)
fi

if ! OUTPUT="$("${MEMCTL[@]}" sdk-status --probe-embedding --probe-timeout "${TIMEOUT}" 2>&1)"; then
  python3 - <<'PY' "${OUTPUT}"
from datetime import datetime, timezone
from pathlib import Path
import json
import os
import sys

payload = {
    "schema": "memguardos-kylin-sdk-warmup-v1",
    "ok": False,
    "inference_ready": False,
    "fallback": "hash_embedding",
    "error": sys.argv[1][-2000:],
    "recommendation": "保留 MemGuardOS 稳定 fallback，并检查 kylin-ai-runtime、kytensor.service 和模型仓库。",
    "completed_at": datetime.now(timezone.utc).astimezone().isoformat(timespec="seconds"),
}
state_home = Path(os.environ.get("XDG_STATE_HOME") or Path.home() / ".local" / "state")
state_path = state_home / "memguardos" / "kylin-sdk-warmup.json"
state_path.parent.mkdir(parents=True, exist_ok=True)
state_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
print(json.dumps(payload, ensure_ascii=False, indent=2))
PY
  exit 0
fi

python3 - <<'PY' "${OUTPUT}"
from datetime import datetime, timezone
from pathlib import Path
import json
import os
import sys

raw = sys.argv[1]
start = raw.find("{")
payload = json.loads(raw[start:]) if start >= 0 else {}
embedding = payload.get("embedding", {})
probe = embedding.get("embedding_probe") or {}
ready = bool(embedding.get("inference_ready") or probe.get("ok"))
payload = {
    "schema": "memguardos-kylin-sdk-warmup-v1",
    "ok": True,
    "available": bool(embedding.get("available")),
    "inference_ready": ready,
    "model": (embedding.get("model_info") or {}).get("name", ""),
    "dim": (embedding.get("model_info") or {}).get("dim") or probe.get("dim"),
    "fallback": embedding.get("fallback", "hash_embedding"),
    "recommendation": (
        "Kylin embedding SDK 已预热，可作为端侧检索增强。"
        if ready else
        "SDK 元数据可达但真实推理未就绪，默认继续使用稳定 fallback。"
    ),
    "completed_at": datetime.now(timezone.utc).astimezone().isoformat(timespec="seconds"),
}
state_home = Path(os.environ.get("XDG_STATE_HOME") or Path.home() / ".local" / "state")
state_path = state_home / "memguardos" / "kylin-sdk-warmup.json"
state_path.parent.mkdir(parents=True, exist_ok=True)
state_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
print(json.dumps(payload, ensure_ascii=False, indent=2))
PY
