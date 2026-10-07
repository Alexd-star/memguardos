#!/usr/bin/env bash
set -euo pipefail

if [ "$#" -ne 1 ]; then
  echo "Exactly one fixed action is required." >&2
  exit 2
fi

PATH='/usr/sbin:/usr/bin:/sbin:/bin'
export PATH
unset BASH_ENV ENV CDPATH GLOBIGNORE PYTHONPATH PYTHONHOME

ACTION="$1"
PROJECT_ROOT="/opt/memguardos"
SYSTEMCTL="/usr/bin/systemctl"

if [ "$(id -u)" -ne 0 ]; then
  echo "This helper must be started through Polkit as root." >&2
  exit 1
fi

case "${ACTION}" in
  start-service)
    "${SYSTEMCTL}" start memguardd.service
    "${SYSTEMCTL}" is-active --quiet memguardd.service
    ;;
  stop-service)
    "${SYSTEMCTL}" stop memguardd.service
    ! "${SYSTEMCTL}" is-active --quiet memguardd.service
    ;;
  restart-service)
    "${SYSTEMCTL}" restart memguardd.service
    "${SYSTEMCTL}" is-active --quiet memguardd.service
    ;;
  refresh-services)
    /usr/bin/install -m 0644 "${PROJECT_ROOT}/scripts/memguardd.service" /lib/systemd/system/memguardd.service
    /usr/bin/install -m 0644 "${PROJECT_ROOT}/scripts/memguardd@.service" /lib/systemd/system/memguardd@.service
    /usr/bin/install -m 0644 "${PROJECT_ROOT}/scripts/memguard-agent@.service" /lib/systemd/system/memguard-agent@.service
    "${SYSTEMCTL}" daemon-reload
    ;;
  install-auditd-rules)
    cd "${PROJECT_ROOT}"
    /usr/bin/python3 -m memguard.cli.memctl auditd-rules --format rules-file --existing-only > /etc/audit/rules.d/memguard.rules
    if [ -x /usr/sbin/augenrules ]; then
      /usr/sbin/augenrules --load
    else
      "${SYSTEMCTL}" restart auditd.service
    fi
    ;;
  run-bpf-lsm-test)
    cd "${PROJECT_ROOT}"
    exec /usr/bin/env -i PATH="${PATH}" HOME=/root /usr/bin/bash "${PROJECT_ROOT}/scripts/run_bpf_lsm_enforcer.sh"
    ;;
  *)
    echo "Unsupported MemGuardOS native helper action: ${ACTION}" >&2
    exit 2
    ;;
esac
