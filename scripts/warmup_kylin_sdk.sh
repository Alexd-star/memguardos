#!/usr/bin/env bash
set -euo pipefail

TIMEOUT="${MEMGUARD_KYLIN_WARMUP_TIMEOUT:-120}"
PYTHON_BIN="${PYTHON_BIN:-python3}"
RUNTIME_DIR="${MEMGUARD_KYLIN_RUNTIME_DIR:-/tmp/.kylin-ai-runtime-unix/$(id -u)}"

# openKylin Kylin AI runtime may create the per-user socket directory as 0777.
# Tighten only the directory owned by the current user; never chmod another
# user's runtime path or the shared parent directory.
RUNTIME_HARDENED=false
if [ -d "${RUNTIME_DIR}" ] && [ "$(stat -c %u "${RUNTIME_DIR}")" = "$(id -u)" ]; then
  chmod 0700 "${RUNTIME_DIR}"
  RUNTIME_HARDENED=true
fi
export MEMGUARD_KYLIN_RUNTIME_HARDENED="${RUNTIME_HARDENED}"

if command -v memctl >/dev/null 2>&1; then
  MEMCTL=(memctl)
else
  MEMCTL=("${PYTHON_BIN}" -m memguard.cli.memctl)
fi

"${PYTHON_BIN}" - "${TIMEOUT}" "${MEMCTL[@]}" <<'PY'
from datetime import datetime, timezone
from pathlib import Path
import json
import math
import os
import signal
import subprocess
import sys
import time
from urllib.request import ProxyHandler, Request, build_opener

started = time.monotonic()
payload = {
    "schema": "memguardos-kylin-sdk-warmup-v1",
    "ok": False, "inference_ready": False, "degraded": True,
    "status": "warming", "available": False, "fallback": "hash_embedding",
    "runtime_directory_hardened": os.environ.get("MEMGUARD_KYLIN_RUNTIME_HARDENED") == "true",
    "embedding_probe": None, "attempts": [],
}
state_home = Path(os.environ.get("XDG_STATE_HOME") or Path.home() / ".local" / "state")
state_path = state_home / "memguardos" / "kylin-sdk-warmup.json"
state_path.parent.mkdir(parents=True, exist_ok=True)

def save():
    # Invalidate an earlier boot's ready state before starting, then persist each attempt.
    payload["elapsed_seconds"] = round(time.monotonic() - started, 3)
    temporary = state_path.with_name(f".{state_path.name}.{os.getpid()}.tmp")
    temporary.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    os.replace(temporary, state_path)

def positive(value):
    result = float(value)
    if not math.isfinite(result) or result <= 0:
        raise ValueError("warmup budgets and retry interval must be finite and positive")
    return result

def decode_report(raw):
    decoder = json.JSONDecoder()
    for index, char in enumerate(raw):
        if char != "{":
            continue
        try:
            report, _ = decoder.raw_decode(raw[index:])
            if isinstance(report, dict) and isinstance(report.get("embedding"), dict):
                return report["embedding"]
        except ValueError:
            pass
    raise ValueError("SDK CLI did not return an embedding status object")

def text(value):
    return value.decode("utf-8", "replace") if isinstance(value, bytes) else value or ""

def cleanup_owned_group(proc, deadline):
    # Popen creates this session/group. Never address shared runtime PIDs or sockets.
    for sig in (signal.SIGTERM, signal.SIGKILL):
        try:
            os.killpg(proc.pid, sig)
        except ProcessLookupError:
            pass
        if sig == signal.SIGTERM:
            try:
                proc.wait(timeout=max(0, min(0.2, deadline - time.monotonic())))
            except subprocess.TimeoutExpired:
                pass
    return proc.communicate(timeout=max(0.01, min(0.3, deadline - time.monotonic())))

def interrupted(signum, frame):
    raise KeyboardInterrupt("warmup interrupted")

save()
try:
    budget = positive(sys.argv[1])
    probe_budget = positive(os.environ.get("MEMGUARD_KYLIN_WARMUP_PROBE_TIMEOUT", "15"))
    retry_interval = positive(os.environ.get("MEMGUARD_KYLIN_WARMUP_RETRY_INTERVAL", "2"))
    deadline = started + budget
    payload["timeout_seconds"] = budget
    signal.signal(signal.SIGTERM, interrupted)
    opener = build_opener(ProxyHandler({}))
    while deadline - time.monotonic() > 0.5:
        record = {"attempt": len(payload["attempts"]) + 1, "ok": False,
                  "embedding_probe": None, "error": None, "returncode": None,
                  "timed_out": False, "stdout": "", "stderr": ""}
        proc = None
        stop = False
        try:
            # Triton readiness is diagnostic only: unavailable HTTP never gates the SDK.
            remaining = deadline - time.monotonic() - 0.5
            try:
                with opener.open(Request("http://127.0.0.1:8000/v2/health/ready"),
                                 timeout=min(0.25, remaining)) as response:
                    record["triton_http_ready"] = response.status == 200
            except OSError as exc:
                record["triton_http_ready"] = None
                record["triton_http_error"] = str(exc)
            remaining = deadline - time.monotonic() - 0.5
            if remaining <= 0:
                break
            # Leave room for metadata overhead and bounded cleanup, not an unbounded CLI call.
            attempt_timeout = min(probe_budget + 10, remaining)
            command = sys.argv[2:] + ["sdk-status", "--probe-embedding", "--probe-timeout",
                                      str(min(probe_budget, attempt_timeout))]
            proc = subprocess.Popen(command, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                                    text=True, start_new_session=True)
            stdout, stderr = proc.communicate(
                timeout=max(0, min(attempt_timeout, deadline - time.monotonic() - 0.5)))
            record.update(returncode=proc.returncode, stdout=stdout, stderr=stderr)
            embedding = decode_report(stdout)
            probe = embedding.get("embedding_probe")
            record["embedding_probe"] = probe
            record["ok"] = (proc.returncode == 0 and isinstance(probe, dict)
                            and probe.get("ok") is True and embedding.get("inference_ready") is True)
            info = embedding.get("model_info")
            info = info if isinstance(info, dict) else {}
            payload.update(available=bool(embedding.get("available")), model=info.get("name", ""),
                           dim=info.get("dim") or (probe.get("dim") if isinstance(probe, dict) else None),
                           fallback=embedding.get("fallback", "hash_embedding"), embedding_probe=probe)
            if not record["ok"]:
                record["error"] = ((probe.get("error") or probe.get("error_message"))
                                   if isinstance(probe, dict) else None)
                record["error"] = (record["error"] or embedding.get("error")
                                   or embedding.get("disabled_reason") or stderr
                                   or "SDK embedding inference not ready")
        except subprocess.TimeoutExpired as exc:
            record.update(timed_out=True, error="SDK CLI attempt exceeded its overall timeout",
                          stdout=text(exc.output), stderr=text(exc.stderr))
        except (OSError, ValueError, KeyboardInterrupt) as exc:
            record["error"] = str(exc)
            stop = isinstance(exc, KeyboardInterrupt)
        finally:
            if proc is not None and (record["timed_out"] or stop or proc.poll() is None):
                try:
                    stdout, stderr = cleanup_owned_group(proc, deadline)
                    record.update(stdout=text(stdout) or record["stdout"],
                                  stderr=text(stderr) or record["stderr"], returncode=proc.returncode)
                except (OSError, subprocess.TimeoutExpired) as exc:
                    record["cleanup_error"] = str(exc)
            if record["embedding_probe"] is None and record["stdout"]:
                try:
                    record["embedding_probe"] = decode_report(record["stdout"]).get("embedding_probe")
                except ValueError:
                    pass
            payload["attempts"].append(record)
        payload.update(ok=record["ok"], inference_ready=record["ok"], degraded=not record["ok"],
                       embedding_probe=record["embedding_probe"], error=record["error"],
                       status="ready" if record["ok"] else "warming")
        save()
        if record["ok"] or stop:
            break
        time.sleep(max(0, min(retry_interval, deadline - time.monotonic() - 0.5)))
    payload["deadline_exhausted"] = not payload["ok"] and deadline - time.monotonic() <= 0.5
except (ValueError, KeyboardInterrupt) as exc:
    payload["error"] = str(exc)

payload["status"] = "ready" if payload["ok"] else "degraded"
payload["attempt_count"] = len(payload["attempts"])
payload["last_error"] = next((item["error"] for item in reversed(payload["attempts"]) if item["error"]), None)
if not payload["ok"] and not payload.get("error"):
    payload["error"] = "warmup deadline exhausted before inference became ready"
payload["recommendation"] = (
    "Kylin embedding SDK 已预热，可作为端侧检索增强。" if payload["ok"] else
    "真实推理未就绪，继续使用稳定 fallback；检查 kylin-ai-runtime、kytensor.service 和模型仓库。"
)
payload["completed_at"] = datetime.now(timezone.utc).astimezone().isoformat(timespec="seconds")
save()
# A written degraded report remains compatible with the optional warmup unit's fallback path.
print(json.dumps(payload, ensure_ascii=False, indent=2))
PY
