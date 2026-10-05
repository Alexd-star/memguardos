#!/usr/bin/env bash
set -euo pipefail

cd "$(dirname "$0")/.."

PROBE_TIMEOUT=180
RUN_PROBE=0
FIX_MODEL_REPO=0
RESTART_KYTENSOR=0
RESTART_RUNTIME=0

usage() {
  cat <<'EOF'
Usage: bash scripts/fix_kylin_embedding_runtime.sh [options]
Default: read-only diagnostics; no repository changes, restarts or inference.
  --fix-model-repo       Back up duplicate /opt links to the SAME /usr model.
                        Never create links or choose between distinct models.
  --restart-kytensor     Explicitly restart kytensor after checking unique names.
  --restart-runtime      Restart only an existing, unambiguous user systemd unit.
  --probe               Explicitly run Python and C++ embedding probes.
  --probe-timeout N     Positive timeout in seconds; does not enable probes.
  --no-model-repo-fix, --no-restart-kytensor, --no-restart-runtime, --no-probe
                        Legacy opt-outs remain supported; last option wins.
Repository checks cover only the two paths below, not extra configured roots:
  MEMGUARD_SOURCE_MODEL_REPO (default /usr/share/kylin-ai/model-repository)
  MEMGUARD_KYTENSOR_MODEL_REPO (default /opt/appdata/kylin-ai/model-repository)
  MEMGUARD_MODEL_REPO_BACKUP_ROOT
    (default /opt/appdata/kylin-ai/model-repository-backups)
Backups retain links and restore.json; restore only after resolving duplicates.
No models, packages, credentials or runtime sockets are deleted.
EOF
}

while [ "$#" -gt 0 ]; do
  case "$1" in
    --probe-timeout)
      if [ "$#" -lt 2 ] || [[ ! "$2" =~ ^[1-9][0-9]*$ ]]; then
        echo "--probe-timeout requires a positive integer" >&2
        exit 2
      fi
      PROBE_TIMEOUT="$2"
      shift 2
      ;;
    --fix-model-repo) FIX_MODEL_REPO=1; shift ;;
    --restart-kytensor) RESTART_KYTENSOR=1; shift ;;
    --restart-runtime) RESTART_RUNTIME=1; shift ;;
    --probe) RUN_PROBE=1; shift ;;
    --help|-h) usage; exit 0 ;;
    --no-probe)
      RUN_PROBE=0
      shift
      ;;
    --no-model-repo-fix)
      FIX_MODEL_REPO=0
      shift
      ;;
    --no-restart-kytensor)
      RESTART_KYTENSOR=0
      shift
      ;;
    --no-restart-runtime)
      RESTART_RUNTIME=0
      shift
      ;;
    *)
      echo "unknown argument: $1" >&2
      exit 2
      ;;
  esac
done

uid_value="$(id -u)"
export XDG_RUNTIME_DIR="${XDG_RUNTIME_DIR:-/run/user/${uid_value}}"
export DBUS_SESSION_BUS_ADDRESS="${DBUS_SESSION_BUS_ADDRESS:-unix:path=${XDG_RUNTIME_DIR}/bus}"

RUNTIME_BASE="/tmp/.kylin-ai-runtime-unix/${uid_value}"
SOURCE_REPO="${MEMGUARD_SOURCE_MODEL_REPO:-/usr/share/kylin-ai/model-repository}"
KYTENSOR_REPO="${MEMGUARD_KYTENSOR_MODEL_REPO:-/opt/appdata/kylin-ai/model-repository}"
BACKUP_ROOT="${MEMGUARD_MODEL_REPO_BACKUP_ROOT:-/opt/appdata/kylin-ai/model-repository-backups}"
PYTHON_BIN="${PYTHON_BIN:-python3}"
KYTENSOR_UNIT="kytensor.service"
RUNTIME_UNIT="kylin-ai-runtime.service"

run_sudo() {
  if sudo -n true >/dev/null 2>&1; then
    sudo -n "$@"
  else
    echo "sudo without password is required for: $*" >&2
    return 1
  fi
}

print_section() {
  echo
  echo "== $1 =="
}

model_repository() {
  local mode="$1"
  local -a runner=("$PYTHON_BIN")
  if [ "$mode" = repair ]; then
    runner=(run_sudo "$PYTHON_BIN")
  fi
  "${runner[@]}" - "$mode" "$SOURCE_REPO" "$KYTENSOR_REPO" "$BACKUP_ROOT" <<'PY'
import json
import os
from pathlib import Path
import sys
import tempfile

mode, source_name, target_name, backup_name = sys.argv[1:]
source, target, backup = map(Path, (source_name, target_name, backup_name))

def models(root):
    return {entry.name: entry for entry in root.iterdir() if entry.is_dir()} if root.is_dir() else {}

try:
    source_models, target_models = models(source), models(target)
    duplicates = sorted(source_models.keys() & target_models.keys())
    print(json.dumps({"source": str(source), "target": str(target),
                      "source_available": source.is_dir(), "target_available": target.is_dir(),
                      "duplicate_models": duplicates}, indent=2))
    if mode == "diagnose":
        raise SystemExit(0)
    if not source.is_dir() or (target.exists() and not target.is_dir()):
        raise ValueError("source repository missing or target is not a directory")
    if source.resolve() == target.resolve():
        raise ValueError("the same repository is configured twice")
    if mode == "check":
        if duplicates:
            raise ValueError("duplicate model names; inspect repositories before restarting")
        raise SystemExit(0)
    if any(backup.resolve().is_relative_to(root.resolve()) for root in (source, target)):
        raise ValueError("backup directory must be outside both active repositories")
    # Validate every duplicate before touching any link; distinct models need human selection.
    plan = []
    for name in duplicates:
        entry = target_models[name]
        if not entry.is_symlink() or not entry.samefile(source_models[name]):
            raise ValueError(f"ambiguous duplicate {name}; nothing changed")
        plan.append({"name": name, "original_path": str(entry),
                     "link_target": os.readlink(entry),
                     "retained_model": str(source_models[name].resolve()), "moved": False})
    if not plan:
        print("No duplicate model links found; nothing changed. No new links are created.")
        raise SystemExit(0)
    original_names = source_models.keys() | target_models.keys()
    backup.mkdir(parents=True, exist_ok=True)
    receipt_dir = Path(tempfile.mkdtemp(prefix="memguardos-", dir=backup))
    receipt = receipt_dir / "restore.json"
    receipt.write_text(json.dumps(plan, indent=2), encoding="utf-8")
    for item in plan:
        entry = Path(item["original_path"])
        if not entry.is_symlink() or not entry.samefile(source_models[item["name"]]):
            raise ValueError(f"repository changed during repair; inspect {receipt_dir}")
        os.rename(entry, receipt_dir / item["name"])
        item["moved"] = True
        receipt.write_text(json.dumps(plan, indent=2), encoding="utf-8")
    source_after, target_after = models(source), models(target)
    if (source_after.keys() & target_after.keys()
            or (source_after.keys() | target_after.keys()) != original_names):
        raise ValueError(f"repository changed during repair; inspect {receipt_dir}")
    print(f"Unique model names verified; duplicate links backed up to {receipt_dir}")
except (OSError, ValueError) as exc:
    raise SystemExit(f"Model repository action stopped: {exc}")
PY
}

restart_kytensor() {
  print_section "Kytensor service"
  model_repository check
  run_sudo systemctl restart "${KYTENSOR_UNIT}"
  systemctl status "${KYTENSOR_UNIT}" --no-pager
}

runtime_pids() {
  pgrep -u "${uid_value}" -f "^kylin-ai-runtime$|^/usr/bin/kylin-ai-runtime$" || true
}

wait_for_runtime_socket() {
  local attempt
  for attempt in $(seq 1 15); do
    if test -S "${RUNTIME_BASE}/core-textembedding.sock"; then
      return 0
    fi
    sleep 1
  done
  echo "runtime socket not ready; no sockets or processes were removed" >&2
  return 1
}

restart_runtime() {
  print_section "Kylin AI runtime"
  model_repository check
  if [ "${uid_value}" -eq 0 ]; then
    echo "run runtime actions as the desktop user, not root" >&2
    return 1
  fi
  if ! systemctl --user cat "${RUNTIME_UNIT}" >/dev/null 2>&1; then
    echo "no managed user ${RUNTIME_UNIT}; use desktop/session controls, no manual restart" >&2
    return 1
  fi
  local pids managed_pid
  pids="$(runtime_pids)"
  managed_pid="$(systemctl --user show "${RUNTIME_UNIT}" --property MainPID --value)"
  if [ -n "$pids" ] && { [[ ! "$managed_pid" =~ ^[1-9][0-9]*$ ]] || [ "$pids" != "$managed_pid" ]; }; then
    echo "unmanaged or multiple user runtimes; refusing to select or terminate processes" >&2
    return 1
  fi
  systemctl --user restart "${RUNTIME_UNIT}"
  wait_for_runtime_socket
}

print_section "MemGuardOS Kylin embedding runtime fix"
echo "user=$(id -un) uid=${uid_value}"
echo "XDG_RUNTIME_DIR=${XDG_RUNTIME_DIR}"
echo "DBUS_SESSION_BUS_ADDRESS=${DBUS_SESSION_BUS_ADDRESS}"
print_section "Read-only repository and service diagnostics"
model_repository diagnose
systemctl status "${KYTENSOR_UNIT}" --no-pager 2>/dev/null || true
runtime_pids

if [ "${FIX_MODEL_REPO}" -eq 1 ]; then
  model_repository repair
fi

if [ "${RESTART_KYTENSOR}" -eq 1 ]; then
  restart_kytensor
fi

if [ "${RESTART_RUNTIME}" -eq 1 ]; then
  restart_runtime
fi

print_section "Runtime sockets"
ls -la "${RUNTIME_BASE}" 2>/dev/null || true
test -S "${RUNTIME_BASE}/core-textembedding.sock" && echo "core-textembedding socket: present" || echo "core-textembedding socket: missing"

if [ "${RUN_PROBE}" -eq 1 ]; then
  print_section "Python SDK embedding probe"
  "$PYTHON_BIN" -m memguard.cli.memctl sdk-status --probe-embedding --probe-timeout "${PROBE_TIMEOUT}"

  print_section "C++ SDK embedding probe"
  PROBE_TIMEOUT="${PROBE_TIMEOUT}" bash scripts/run_kylin_embedding_probe.sh "openKylin agent memory"
fi

print_section "Logs"
journalctl -u "${KYTENSOR_UNIT}" -n 80 --no-pager 2>/dev/null || true
if [ -f "${HOME}/.log/kylin-ai-runtime/kylin-ai-runtime.log" ]; then
  tail -n 120 "${HOME}/.log/kylin-ai-runtime/kylin-ai-runtime.log" || true
fi

echo
echo "done"
