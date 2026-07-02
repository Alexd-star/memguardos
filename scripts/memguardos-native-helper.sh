#!/usr/bin/env bash
set -euo pipefail

ACTION="${1:-}"
PROJECT_ROOT="${MEMGUARD_PROJECT_ROOT:-/opt/memguardos}"

case "${ACTION}" in
  install-services)
    install -m 0644 "${PROJECT_ROOT}/scripts/memguardd.service" /lib/systemd/system/memguardd.service
    install -m 0644 "${PROJECT_ROOT}/scripts/memguardd@.service" /lib/systemd/system/memguardd@.service
    install -m 0644 "${PROJECT_ROOT}/scripts/memguard-agent@.service" /lib/systemd/system/memguard-agent@.service
    systemctl daemon-reload
    ;;
  install-auditd-rules)
    "${PROJECT_ROOT}/scripts/install_auditd_rules.sh"
    ;;
  run-bpf-lsm-test)
    "${PROJECT_ROOT}/scripts/run_bpf_lsm_enforcer.sh"
    ;;
  collect-native-evidence)
    "${PROJECT_ROOT}/scripts/collect_openkylin_native_evidence.sh"
    ;;
  *)
    echo "Unsupported MemGuardOS native helper action: ${ACTION}" >&2
    echo "Allowed actions: install-services, install-auditd-rules, run-bpf-lsm-test, collect-native-evidence" >&2
    exit 2
    ;;
esac
