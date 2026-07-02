#!/usr/bin/env bash
set -euo pipefail

cd "$(dirname "$0")/.."

OUT_DIR="${1:-docs/evidence/openkylin_native_latest}"
mkdir -p "${OUT_DIR}"

run_capture() {
  local name="$1"
  shift
  {
    echo "$ $*"
    "$@"
  } >"${OUT_DIR}/${name}" 2>&1 || {
    local code="$?"
    echo "exit_code=${code}" >>"${OUT_DIR}/${name}"
  }
}

export PYTHONPATH="$(pwd)${PYTHONPATH:+:$PYTHONPATH}"

run_capture environment.txt bash -lc 'cat /etc/os-release 2>/dev/null || true; uname -a; python3 --version'
run_capture git_head.txt bash -lc 'git rev-parse --verify HEAD 2>/dev/null || echo synchronized_tree_without_git_metadata'
run_capture openkylin_status.txt python3 -m memguard.cli.memctl openkylin-status
run_capture openkylin_status_probe_sdk.txt python3 -m memguard.cli.memctl openkylin-status --probe-sdk --probe-timeout 90
run_capture kylin_warmup.txt bash scripts/warmup_kylin_sdk.sh

TMP_SAMPLE_DIR="$(mktemp -d)"
trap 'rm -rf "${TMP_SAMPLE_DIR}"' EXIT

SAMPLE="${TMP_SAMPLE_DIR}/peony_sample.md"
printf '以后回答 openKylin 问题时先给可复制命令。\n' >"${SAMPLE}"
run_capture peony_scan_preview.txt python3 -m memguard.cli.memctl desktop scan-file --file "${SAMPLE}"
run_capture peony_scan_ingest.txt python3 -m memguard.cli.memctl desktop scan-file --file "${SAMPLE}" --ingest

SECRET="${TMP_SAMPLE_DIR}/peony_secret.env"
printf 'OPENAI_API_KEY=sk-test-example1234567890\n' >"${SECRET}"
run_capture peony_scan_sensitive.txt python3 -m memguard.cli.memctl desktop scan-file --file "${SECRET}" --ingest

run_capture systemd_units.txt bash -lc 'systemctl status memguardd.service --no-pager 2>/dev/null || true; systemctl --user status memguard-kylin-warmup.service --no-pager 2>/dev/null || true'
run_capture desktop_plugins.txt bash -lc 'find /usr/lib -path "*peony-extensions*memguardos*.so" -o -path "*ukui-control-center*memguardos*.so" 2>/dev/null || true'
run_capture polkit_assets.txt bash -lc 'ls -l /usr/share/polkit-1/actions/org.memguardos.policy /usr/lib/memguardos/memguardos-native-helper.sh 2>/dev/null || true'

python3 scripts/refresh_evidence_manifest.py "${OUT_DIR}" >"${OUT_DIR}/manifest_refresh.txt"
echo "openkylin_native_evidence=${OUT_DIR}"
