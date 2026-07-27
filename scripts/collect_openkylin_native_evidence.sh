#!/usr/bin/env bash
set -euo pipefail

cd "$(dirname "$0")/.."

OUT_DIR="${1:-docs/evidence/openkylin_native_latest}"
rm -rf "${OUT_DIR}"
mkdir -p "${OUT_DIR}"
export PYTHONPATH="$(pwd)${PYTHONPATH:+:$PYTHONPATH}"

# Evidence collection must never write into the operator's real memory store.
# The collector runs user-facing CLI and desktop flows, so isolate all generated
# records while retaining only the exported evidence files in OUT_DIR.
EVIDENCE_HOME="$(mktemp -d "${TMPDIR:-/tmp}/memguardos-native-home.XXXXXX")"
TMP_SAMPLE_DIR=""
cleanup() {
  rm -rf "${EVIDENCE_HOME}"
  if [ -n "${TMP_SAMPLE_DIR}" ]; then
    rm -rf "${TMP_SAMPLE_DIR}"
  fi
}
trap cleanup EXIT
export MEMGUARD_HOME="${EVIDENCE_HOME}"

print_command() {
  printf '$'
  printf ' %q' "$@"
  printf '\n'
}

run_capture() {
  local name="$1"
  shift
  {
    print_command "$@"
    "$@"
  } >"${OUT_DIR}/${name}" 2>&1 || {
    local code="$?"
    echo "exit_code=${code}" >>"${OUT_DIR}/${name}"
  }
}

run_required_capture() {
  local name="$1"
  shift
  {
    print_command "$@"
    "$@"
  } >"${OUT_DIR}/${name}" 2>&1
}

find_pytest_python() {
  if [ -n "${PYTHON_BIN:-}" ] && [ -x "${PYTHON_BIN}" ] && "${PYTHON_BIN}" -m pytest --version >/dev/null 2>&1; then
    printf '%s\n' "${PYTHON_BIN}"
    return 0
  fi
  local project_parent
  project_parent="$(dirname "$(pwd)")"
  local candidates=(
    "$(pwd)/.venv/bin/python"
    "$(pwd)/.venv/Scripts/python.exe"
    "${project_parent}/memguardos/.venv/bin/python"
    "${project_parent}/memguardos/.venv/Scripts/python.exe"
    "${HOME:-}/memguardos/.venv/bin/python"
    "python3"
  )
  local candidate
  for candidate in "${candidates[@]}"; do
    if command -v "${candidate}" >/dev/null 2>&1 && "${candidate}" -m pytest --version >/dev/null 2>&1; then
      printf '%s\n' "${candidate}"
      return 0
    fi
  done
  return 1
}

SOURCE_REVISION="${MEMGUARD_SOURCE_REVISION:-}"
SOURCE_MODE="${MEMGUARD_SOURCE_MODE:-git_worktree}"
DIRTY=false
if git rev-parse --is-inside-work-tree >/dev/null 2>&1; then
  ACTUAL_REVISION="$(git rev-parse HEAD)"
  if [ -n "${SOURCE_REVISION}" ] && [ "${SOURCE_REVISION}" != "${ACTUAL_REVISION}" ]; then
    echo "MEMGUARD_SOURCE_REVISION does not match Git HEAD" >&2
    exit 2
  fi
  SOURCE_REVISION="${ACTUAL_REVISION}"
  test -z "$(git status --porcelain)" || DIRTY=true
else
  SOURCE_MODE="${MEMGUARD_SOURCE_MODE:-git_archive}"
fi
if ! [[ "${SOURCE_REVISION}" =~ ^[0-9a-f]{40}$ ]]; then
  echo "A verified 40-character MEMGUARD_SOURCE_REVISION is required when Git metadata is absent." >&2
  exit 2
fi
if [ "${DIRTY}" = true ]; then
  echo "Refusing to collect final evidence from a dirty worktree." >&2
  exit 2
fi
python3 - "${SOURCE_REVISION}" "${SOURCE_MODE}" "${DIRTY}" >"${OUT_DIR}/source_revision.json" <<'PY'
import json, sys
print(json.dumps({
    "schema": "memguardos-source-revision-v1",
    "source_revision": sys.argv[1],
    "source_mode": sys.argv[2],
    "dirty": sys.argv[3].lower() == "true",
}, ensure_ascii=False, indent=2))
PY

run_required_capture environment.txt bash -lc 'cat /etc/os-release; uname -a; python3 --version; printf "desktop=%s\nsession=%s\n" "${XDG_CURRENT_DESKTOP:-}" "${DESKTOP_SESSION:-}"'
run_required_capture openkylin_status.txt python3 -m memguard.cli.memctl openkylin-status
run_required_capture openkylin_status_probe_sdk.txt python3 -m memguard.cli.memctl openkylin-status --probe-sdk --probe-timeout 90
run_required_capture kylin_warmup.txt bash scripts/warmup_kylin_sdk.sh
run_required_capture user_cli.txt python3 -m memguard.cli.memguard demo --task "查找精准遗忘的实现位置"

TMP_SAMPLE_DIR="$(mktemp -d)"
SAMPLE_A="${TMP_SAMPLE_DIR}/peony_preference.md"
SAMPLE_B="${TMP_SAMPLE_DIR}/peony_knowledge.txt"
SECRET="${TMP_SAMPLE_DIR}/peony_secret.env"
printf '以后回答 openKylin 问题时先给可复制命令。\n' >"${SAMPLE_A}"
printf 'systemd MemoryMax 可以限制后台记忆服务的内存。\n' >"${SAMPLE_B}"
printf 'OPENAI_API_KEY=sk-test-example1234567890\n' >"${SECRET}"

python3 -m memguard.cli.memctl desktop scan-files --file "${SAMPLE_A}" --file "${SAMPLE_B}" >"${OUT_DIR}/peony_batch_preview.json"
MANIFEST_PATH="$(python3 -c 'import json,sys; print(json.load(open(sys.argv[1], encoding="utf-8"))["manifest_path"])' "${OUT_DIR}/peony_batch_preview.json")"
python3 -m memguard.cli.memctl desktop apply-scan --manifest "${MANIFEST_PATH}" --select "${SAMPLE_A}" --select "${SAMPLE_B}" >"${OUT_DIR}/peony_batch_apply.json"
python3 -m memguard.cli.memctl desktop scan-files --file "${SECRET}" >"${OUT_DIR}/peony_sensitive_preview.json"
python3 - "${OUT_DIR}" <<'PY'
import json, pathlib, sys
root = pathlib.Path(sys.argv[1])
preview = json.loads((root / "peony_batch_preview.json").read_text(encoding="utf-8"))
applied = json.loads((root / "peony_batch_apply.json").read_text(encoding="utf-8"))
sensitive = json.loads((root / "peony_sensitive_preview.json").read_text(encoding="utf-8"))
assert preview["ok"] and len(preview["items"]) == 2 and all(item["selectable"] for item in preview["items"])
assert applied["ok"] and applied["summary"]["stored"] == 2 and applied["audit_chain"]["ok"]
assert sensitive["items"][0]["recommended_action"] == "block_sensitive"
assert sensitive["items"][0]["selectable"] is False
PY

run_required_capture desktop_build.txt bash scripts/build_ukui_plugins.sh
run_required_capture tray_assets.txt bash -lc 'file native/ukui/build/tray/memguardos-tray; ldd native/ukui/build/tray/memguardos-tray; test -f packaging/desktop/memguardos-tray.desktop; grep -E "^(Exec|OnlyShowIn)=" packaging/desktop/memguardos-tray.desktop'
run_required_capture desktop_plugins.txt bash -lc 'file native/ukui/build/peony-memguard/libpeony-menu-plugin-memguardos.so native/ukui/build/ukcc-memguard/libmemguardos-ukcc-plugin.so; ldd native/ukui/build/peony-memguard/libpeony-menu-plugin-memguardos.so; ldd native/ukui/build/ukcc-memguard/libmemguardos-ukcc-plugin.so'
run_required_capture polkit_assets.txt bash -lc 'set -e
POLICY_TARGET=/usr/share/polkit-1/actions/org.memguardos.policy
HELPER_TARGET=/usr/lib/memguardos/memguardos-native-helper.sh
STAGING_ROOT=/var/tmp/memguardos-polkit-staging
STAGED_POLICY=${STAGING_ROOT}/org.memguardos.policy
STAGED_HELPER=${STAGING_ROOT}/memguardos-native-helper.sh
run_root() {
  if [ "$(id -u)" -eq 0 ]; then
    "$@"
  else
    sudo "$@"
  fi
}
install_polkit_assets() {
  if ! { [ "$(id -u)" -eq 0 ] || { command -v sudo >/dev/null 2>&1 && sudo -n true >/dev/null 2>&1; }; }; then
    echo polkit_install_skipped=needs_passwordless_sudo
    return 0
  fi
  if run_root mkdir -p /usr/share/polkit-1/actions /usr/lib/memguardos 2>/tmp/memguardos-polkit-install.err \
    && run_root chmod 0755 /usr/lib/memguardos \
    && run_root install -m 0644 packaging/polkit/org.memguardos.policy "${POLICY_TARGET}" \
    && run_root install -m 0755 scripts/memguardos-native-helper.sh "${HELPER_TARGET}"; then
    echo polkit_install_mode=system
    return 0
  fi
  if findmnt -no OPTIONS /usr 2>/dev/null | grep -q "\bro\b"; then
    echo policy_install_blocked=readonly_usr
  else
    echo policy_install_failed=true
  fi
  sed "s/^/install_error=/" /tmp/memguardos-polkit-install.err 2>/dev/null || true
  run_root mkdir -p "${STAGING_ROOT}"
  run_root install -m 0644 packaging/polkit/org.memguardos.policy "${STAGED_POLICY}"
  run_root install -m 0755 scripts/memguardos-native-helper.sh "${STAGED_HELPER}"
  test -s "${STAGED_POLICY}" && echo policy_staged=true || echo policy_staged=false
  test -x "${STAGED_HELPER}" && echo helper_staged=true || echo helper_staged=false
  return 0
}
install_polkit_assets
python3 - <<"PY"
import xml.etree.ElementTree as ET
root = ET.parse("packaging/polkit/org.memguardos.policy").getroot()
actions = [item.attrib["id"] for item in root.findall("action")]
required = {"org.memguardos.start-service", "org.memguardos.stop-service", "org.memguardos.restart-service", "org.memguardos.install-auditd-rules", "org.memguardos.run-bpf-lsm-test"}
assert required.issubset(actions)
print("actions=" + ",".join(actions))
PY
HELPER_TO_TEST="${HELPER_TARGET}"
test -x "${HELPER_TO_TEST}" || HELPER_TO_TEST="${STAGED_HELPER}"
if run_root "${HELPER_TO_TEST}" unsupported-action; then exit 3; else echo unknown_action_rejected=true; fi
test -s "${POLICY_TARGET}" && echo policy_installed=true || echo policy_installed=false
test -x "${HELPER_TARGET}" && echo helper_installed=true || echo helper_installed=false
ls -l "${POLICY_TARGET}" "${HELPER_TARGET}" "${STAGED_POLICY}" "${STAGED_HELPER}" 2>/dev/null || true'
run_required_capture systemd_cgroup.txt bash -lc 'systemctl show memguardd.service --property=LoadState,ActiveState,SubState,ControlGroup,MemoryMax,CPUQuotaPerSecUSec --no-pager; CG=$(systemctl show memguardd.service -p ControlGroup --value); test -n "$CG"; for f in memory.current memory.max cpu.max cpu.stat; do printf "%s\n" "--- ${f} ---"; cat "/sys/fs/cgroup${CG}/${f}" 2>/dev/null || true; done'
run_required_capture systemd_security.txt bash -lc '
REPORT=$(systemd-analyze security memguardd.service --no-pager)
printf "%s\n" "$REPORT"
SCORE=$(printf "%s\n" "$REPORT" | sed -n "s/.*Overall exposure level.*: \([0-9][0-9.]*\).*/\1/p" | tail -n 1)
test -n "$SCORE"
python3 - "$SCORE" <<"PY"
import sys
score = float(sys.argv[1])
if score > 3.0:
    raise SystemExit("systemd exposure score exceeds 3.0: %s" % score)
print("overall_exposure=%s" % score)
print("security_gate=passed")
PY'
run_required_capture storage_permissions.txt bash -lc '
run_root() { if [ "$(id -u)" -eq 0 ]; then "$@"; else sudo -n "$@"; fi; }
service_user=$(systemctl show memguardd.service -p User --value)
service_group=$(systemctl show memguardd.service -p Group --value)
state_mode=$(run_root stat -c %a /var/lib/memguardos)
db_mode=$(run_root stat -c %a /var/lib/memguardos/memguard.sqlite3)
segment_mode=$(run_root stat -c %a /var/lib/memguardos/memory.segment)
runtime_mode=$(run_root stat -c %a /run/memguardos)
socket_mode=$(run_root stat -c %a /run/memguardos/memguard.sock)
state_owner=$(run_root stat -c %U:%G /var/lib/memguardos)
db_owner=$(run_root stat -c %U:%G /var/lib/memguardos/memguard.sqlite3)
printf "service_user=%s\nservice_group=%s\n" "$service_user" "$service_group"
printf "state_mode=%s\ndb_mode=%s\nsegment_mode=%s\nruntime_mode=%s\nsocket_mode=%s\n" "$state_mode" "$db_mode" "$segment_mode" "$runtime_mode" "$socket_mode"
printf "state_owner=%s\ndb_owner=%s\n" "$state_owner" "$db_owner"
test "$service_user" = memguard
test "$service_group" = memguard
test "$state_mode" = 700
test "$db_mode" = 600
test "$segment_mode" = 600
test "$runtime_mode" = 750
test "$socket_mode" = 660
test "$state_owner" = memguard:memguard
test "$db_owner" = memguard:memguard
echo permissions_ok=true'
run_required_capture service_restart.txt bash -lc '
START_MS=$(date +%s%3N)
if [ "$(id -u)" -eq 0 ]; then
  systemctl restart memguardd.service
  RC=$?
else
  sudo -n systemctl restart memguardd.service
  RC=$?
fi
END_MS=$(date +%s%3N)
ELAPSED_MS=$((END_MS - START_MS))
echo restart_rc=${RC}
echo restart_ms=${ELAPSED_MS}
systemctl show memguardd.service --property=ActiveState,SubState --no-pager
test "${RC}" -eq 0
test "${ELAPSED_MS}" -le 5000
test "$(systemctl is-active memguardd.service)" = active'

run_required_capture debian_packages.txt bash scripts/build_deb.sh
run_required_capture deb_lifecycle.txt bash scripts/verify_deb_lifecycle.sh
PYTEST_PYTHON="$(find_pytest_python)"
run_required_capture pytest_openkylin.txt "${PYTEST_PYTHON}" -m pytest -q
run_required_capture embedding_cache_status.txt \
  "${PYTEST_PYTHON}" -m memguard.cli.memctl embedding-cache status
run_required_capture retrieval_v2.txt \
  "${PYTEST_PYTHON}" scripts/evaluate_retrieval_v2.py \
  --rounds 3 \
  --size 1000 \
  --queries 120 \
  --workers 6 \
  --output "${OUT_DIR}/retrieval_v2.json"

run_capture installed_packages.txt bash -lc 'dpkg-query -W -f="\${Package} \${Version} \${Status}\n" "memguardos*" 2>/dev/null || true'
run_capture installed_desktop.txt bash -lc 'find /usr/lib -path "*peony-extensions*memguardos*.so" -o -path "*ukui-control-center*memguardos*.so" 2>/dev/null || true; command -v memguardos-tray || true; pgrep -a memguardos-tray || true'

python3 scripts/refresh_evidence_manifest.py "${OUT_DIR}" >"${OUT_DIR}/manifest_refresh.txt"
python3 scripts/refresh_evidence_manifest.py "${OUT_DIR}" >/dev/null
echo "openkylin_native_evidence=${OUT_DIR}"
