#!/usr/bin/env bash
set -euo pipefail

cd "$(dirname "$0")/.."

PYTHON_BIN="${PYTHON_BIN:-python3}"
MANIFEST="${MEMGUARD_DEB_MANIFEST:-dist/memguardos_debian_packages.json}"
BUILD=1
if [ "${1:-}" = "--no-build" ]; then
  BUILD=0
fi

stop_for_maintenance() {
  echo "Installation stopped: $1" >&2
  echo "Maintenance mode is required to install system services and native UKUI packages on an immutable openKylin system." >&2
  echo "Ask the user to enter maintenance mode and complete any required logout/reboot, then rerun this installer." >&2
  echo "No OS mode change, security bypass, or root remount will be attempted." >&2
  exit 2
}

DPKG_PATH="$(command -v dpkg)" || { echo "dpkg is unavailable; installation stopped." >&2; exit 2; }
DPKG_TARGET="$(readlink -f "$DPKG_PATH")" || { echo "Cannot identify dpkg; installation stopped." >&2; exit 2; }
test -n "$DPKG_TARGET" || { echo "Cannot identify dpkg; installation stopped." >&2; exit 2; }
case "$DPKG_TARGET" in
  */dpkg_wrapper|*/dpkg-wrapper|*/ostree-pkgs-guard)
    # The wrapper remains installed in maintenance mode. Ask the same read-only
    # D-Bus interface as openKylin's wrapper_common instead of rejecting its path.
    BOOT_MODE="$(gdbus call --system --timeout 5 \
      --dest com.kylin.MaintainMode \
      --object-path /com/kylin/MaintainMode/Object \
      --method com.kylin.MaintainMode.interface.GetBootMode 2>/dev/null)" \
      || stop_for_maintenance "GetBootMode failed; cannot confirm maintenance mode"
    case "$BOOT_MODE" in
      "('maintain',)") echo "boot_mode=maintain" ;;
      "('normal',)") stop_for_maintenance "GetBootMode reports normal mode" ;;
      *) stop_for_maintenance "GetBootMode returned an unrecognized mode: $BOOT_MODE" ;;
    esac
    ;;
esac
# Mount tables may contain both hidden read-only layers and a writable overlay.
# statvfs reports the effective mount visible in this process's namespace.
USR_MODE="$("$PYTHON_BIN" - <<'PY'
import os

try:
    flags = os.statvfs("/usr").f_flag
except OSError as exc:
    raise SystemExit(f"Cannot verify /usr mount mode; installation stopped: {exc}")
print("ro" if flags & os.ST_RDONLY else "rw")
PY
)" || exit 2
case "$USR_MODE" in
  ro) stop_for_maintenance "/usr is read-only" ;;
  rw) ;;
  *) echo "Cannot verify /usr mount mode; installation stopped." >&2; exit 2 ;;
esac

if [ "$BUILD" -eq 1 ]; then
  bash scripts/build_deb.sh
fi
test -s "$MANIFEST"

PACKAGE_LIST="$("$PYTHON_BIN" - "$MANIFEST" <<'PY'
import hashlib
import json
import subprocess
import sys
from pathlib import Path

manifest = json.loads(Path(sys.argv[1]).read_text(encoding="utf-8"))
expected = {
    "memguardos",
    "memguardos-agent-bridge",
    "memguardos-core",
    "memguardos-doc",
    "memguardos-ukui",
}
packages = manifest.get("packages", [])
if len(packages) != len(expected) or {item.get("package") for item in packages} != expected:
    raise SystemExit("Debian package manifest does not contain the expected five packages")
for item in packages:
    path = Path(str(item["path"])).resolve()
    if not path.is_file():
        raise SystemExit(f"missing package: {path}")
    if hashlib.sha256(path.read_bytes()).hexdigest() != item.get("sha256"):
        raise SystemExit(f"package checksum mismatch: {path}")
    for field, key in (("Package", "package"), ("Version", "version"), ("Architecture", "architecture")):
        actual = subprocess.check_output(["dpkg-deb", "-f", str(path), field], text=True).strip()
        if not item.get(key) or actual != item[key]:
            raise SystemExit(f"package {field} mismatch: {path}")
    if item["version"] != manifest.get("version"):
        raise SystemExit(f"inconsistent package version: {path}")
    print(path)
PY
)"
mapfile -t PACKAGE_PATHS <<< "$PACKAGE_LIST"

echo "package_manifest=$MANIFEST"
echo "package_count=${#PACKAGE_PATHS[@]}"
sudo env DEBIAN_FRONTEND=noninteractive apt-get install -y --reinstall "${PACKAGE_PATHS[@]}"

# Verify against the supplied archives, not just apt/KARE's exit status or an old
# installed checksum database. Read control metadata only; never unpack to root.
"$PYTHON_BIN" - "$MANIFEST" <<'PY'
import hashlib
import io
import json
import re
import subprocess
import sys
import tarfile
from pathlib import Path, PurePosixPath

manifest = json.loads(Path(sys.argv[1]).read_text(encoding="utf-8"))
for item in manifest["packages"]:
    package = item["package"]
    try:
        status = subprocess.check_output(["dpkg-query", "-W", "-f=${db:Status-Status}", package], text=True).strip()
        version = subprocess.check_output(["dpkg-query", "-W", "-f=${Version}", package], text=True).strip()
        if status != "installed" or version != item["version"]:
            raise ValueError(f"expected installed version {item['version']}, got status={status!r} version={version!r}")
        control = subprocess.check_output(["dpkg-deb", "--ctrl-tarfile", item["path"]])
        with tarfile.open(fileobj=io.BytesIO(control), mode="r:*") as archive:
            member = next((entry for entry in archive.getmembers() if entry.name in {"md5sums", "./md5sums"}), None)
            if member is None or not member.isfile():
                raise ValueError("archive has no regular md5sums metadata")
            checksums = archive.extractfile(member).read().decode("utf-8").splitlines()
        checked = 0
        for line in checksums:
            digest, relative = line.split(maxsplit=1)
            path = PurePosixPath(relative)
            if not re.fullmatch(r"[0-9a-f]{32}", digest) or path.is_absolute() or ".." in path.parts:
                raise ValueError("invalid archive file checksum entry")
            installed = Path("/").joinpath(*path.parts)
            if not installed.is_file() or hashlib.md5(installed.read_bytes(), usedforsecurity=False).hexdigest() != digest:
                raise ValueError(f"installed file missing or differs from archive: {installed}")
            checked += 1
        if package != "memguardos" and not checked:
            raise ValueError("archive has no payload file checksums")
    except (OSError, subprocess.SubprocessError, tarfile.TarError, ValueError) as exc:
        raise SystemExit(f"Installation verification failed for {package}: {exc}. apt/KARE exit 0 is not proof of installation; check maintenance mode and installed payload. No success will be reported.")
    print(f"installed[{package}]={status} version={version} verified_files={checked}")
PY

sudo systemctl daemon-reload
sudo systemctl enable --now memguardd.service

test "$(systemctl is-active memguardd.service)" = active
test -x /usr/bin/memguard
test -x /usr/bin/memctl
test -x /usr/bin/memguardd
test -f /usr/share/polkit-1/actions/org.memguardos.policy
test -f /etc/xdg/autostart/memguardos-tray.desktop
/usr/bin/memguard status
/usr/bin/memctl verify-audit
echo "apt_install=ok"
