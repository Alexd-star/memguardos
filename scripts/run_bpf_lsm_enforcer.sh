#!/usr/bin/env bash
set -euo pipefail

cd "$(dirname "$0")/.."

OUT_DIR="${1:-docs/evidence/latest}"
POLICY_JSON="${2:-}"
mkdir -p "${OUT_DIR}"
LOG="${OUT_DIR}/bpf_lsm_enforcer.txt"
: >"${LOG}"

{
  echo "# MemGuardOS BPF LSM enforcer probe"
  date -Is
  echo "kernel=$(uname -r)"
  echo "lsm=$(cat /sys/kernel/security/lsm 2>/dev/null || true)"
  test -r /sys/kernel/btf/vmlinux && echo "btf=yes" || echo "btf=no"
  command -v clang || true
  command -v pkg-config || true
  pkg-config --exists libbpf && echo "libbpf_dev=yes" || echo "libbpf_dev=no"
  echo
} | tee -a "${LOG}"

if ! grep -qw bpf /sys/kernel/security/lsm 2>/dev/null; then
  echo "BPF LSM is not active; skip enforcement attach." | tee -a "${LOG}"
  exit 2
fi
if ! test -r /sys/kernel/btf/vmlinux; then
  echo "BTF is unavailable; skip enforcement attach." | tee -a "${LOG}"
  exit 2
fi
if ! command -v clang >/dev/null 2>&1 || ! command -v pkg-config >/dev/null 2>&1 || ! pkg-config --exists libbpf; then
  echo "clang/pkg-config/libbpf-dev missing; enter openKylin maintenance mode and install development packages." | tee -a "${LOG}"
  exit 2
fi

if [ -n "${POLICY_JSON}" ]; then
  echo "generating BPF LSM policy from ${POLICY_JSON}" | tee -a "${LOG}"
  python3 scripts/generate_bpf_lsm_policy.py --policy "${POLICY_JSON}" | tee -a "${LOG}"
fi

make -C native/bpf_lsm clean all | tee -a "${LOG}"

echo "secret" >/tmp/memguardos_lsm_secret
chmod 0644 /tmp/memguardos_lsm_secret

sudo native/bpf_lsm/build/memguard_lsm_loader native/bpf_lsm/build/memguard_lsm.bpf.o >>"${LOG}" 2>&1 &
LOADER_PID=$!
trap 'sudo kill ${LOADER_PID} >/dev/null 2>&1 || true; wait ${LOADER_PID} >/dev/null 2>&1 || true; rm -f /tmp/memguardos_lsm_secret /tmp/memguard_lsm_secret_probe' EXIT

sleep 2
echo "loader_pid=${LOADER_PID}" | tee -a "${LOG}"
echo "trying cat /tmp/memguardos_lsm_secret; expected Permission denied when enforcement works" | tee -a "${LOG}"
if cat /tmp/memguardos_lsm_secret >/tmp/memguard_lsm_secret_probe 2>>"${LOG}"; then
  echo "enforcement_result=not_denied" | tee -a "${LOG}"
  exit 1
else
  echo "enforcement_result=denied" | tee -a "${LOG}"
fi
