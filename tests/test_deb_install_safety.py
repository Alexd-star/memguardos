import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
from types import SimpleNamespace

import pytest


ROOT = (Path(__file__).resolve().parents[1] / "src")
PACKAGES = ["memguardos-core", "memguardos-ukui", "memguardos-agent-bridge", "memguardos-doc", "memguardos"]
VERSION = "1.0.1-4"

DRIVER = r'''
dpkg() { :; }
readlink() { printf '%s\n' "$TEST_DPKG_TARGET"; }
findmnt() { printf '%s\n' "$TEST_MOUNT_OPTIONS"; }
gdbus() { printf '%s\n' "$*" >> "$TEST_MODE_LOG"; printf '%s\n' "$TEST_BOOT_MODE"; return "$TEST_MODE_EXIT"; }
sudo() { printf 'sudo %s\n' "$*" >> "$TEST_CALL_LOG"; case "$*" in *apt-get*) return "$TEST_APT_EXIT";; esac; }
systemctl() { printf 'active\n'; }
test() {
  case "$*" in
    '-x /usr/bin/memguard'|'-x /usr/bin/memctl'|'-x /usr/bin/memguardd'|'-f /usr/share/polkit-1/actions/org.memguardos.policy'|'-f /etc/xdg/autostart/memguardos-tray.desktop') return 0;;
  esac
  builtin test "$@"
}
function /usr/bin/memguard { printf 'mock status\n'; }
function /usr/bin/memctl { printf 'mock audit\n'; }
source "$0" "$@"
'''

# Loaded only in the isolated installer Python subprocess. All package-manager
# queries are simulated; Path('/') maps solely to this test's temporary payload.
SITECUSTOMIZE = r'''
import hashlib
import io
import json
import os
import pathlib
import subprocess
import tarfile
from types import SimpleNamespace

os.ST_RDONLY = 1
def statvfs(path):
    assert path == "/usr"
    if os.environ["TEST_STATVFS_ERROR"] == "1":
        raise OSError("mount inspection failed")
    return SimpleNamespace(f_flag=int(os.environ["TEST_USR_FLAGS"]))
os.statvfs = statvfs

BasePath = type(pathlib.Path.cwd())
def TestPath(*args, **kwargs):
    if args == ("/",):
        args = (os.environ["TEST_INSTALL_ROOT"],)
    return BasePath(*args, **kwargs)
pathlib.Path = TestPath

def check_output(command, **kwargs):
    state = json.loads(TestPath(os.environ["TEST_STATE"]).read_text())
    if command[0] == "dpkg-query":
        item = state["installed"][command[-1]]
        if item.get("query_error"):
            raise subprocess.CalledProcessError(1, command)
        value = item["status"] if "Status-Status" in command[2] else item["version"]
        return value + "\n"
    if command[0] == "dpkg-deb":
        item = json.loads(TestPath(command[2]).read_text())
        if command[1] == "-f":
            return item[command[3]] + "\n"
        if command[1] == "--ctrl-tarfile":
            output = io.BytesIO()
            with tarfile.open(fileobj=output, mode="w") as archive:
                if not item.get("missing_md5sums"):
                    data = "".join(digest + "  " + path + "\n" for path, digest in item["files"].items()).encode()
                    entry = tarfile.TarInfo("./md5sums")
                    entry.size = len(data)
                    archive.addfile(entry, io.BytesIO(data))
            return output.getvalue()
    raise AssertionError("Unexpected external command: " + repr(command))
subprocess.check_output = check_output
'''


@pytest.fixture
def installer(tmp_path):
    bash = shutil.which("bash")
    if os.name == "nt":
        git_bash = Path("C:/Program Files/Git/bin/bash.exe")
        bash = str(git_bash) if git_bash.is_file() else None
    if not bash:
        pytest.skip("bash is required for isolated installer tests")
    scripts = tmp_path / "scripts"
    scripts.mkdir()
    script = scripts / "install_deb_package_set.sh"
    script.write_text((ROOT / "scripts/install_deb_package_set.sh").read_text(encoding="utf-8"), encoding="utf-8", newline="\n")
    (scripts / "build_deb.sh").write_text('printf "build\n" >> "$TEST_CALL_LOG"\n', newline="\n")
    mocks = tmp_path / "mocks"
    mocks.mkdir()
    (mocks / "sitecustomize.py").write_text(SITECUSTOMIZE, newline="\n")
    installed_root = tmp_path / "installed"
    installed_root.mkdir()
    manifest = {"version": VERSION, "packages": []}
    state = {"installed": {}}
    artifacts = {}
    for package in PACKAGES:
        files = {}
        if package != "memguardos":
            relative = "usr/lib/memguardos/plugin.so" if package == "memguardos-ukui" else f"opt/memguardos/{package}.py"
            content = f"new payload {package}".encode()
            path = installed_root / relative
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(content)
            files[relative] = hashlib.md5(content, usedforsecurity=False).hexdigest()
        archive = {"Package": package, "Version": VERSION, "Architecture": "amd64", "files": files}
        artifact = tmp_path / f"{package}.deb"
        artifact.write_text(json.dumps(archive))
        artifacts[package] = artifact
        manifest["packages"].append({
            "package": package, "version": VERSION, "architecture": "amd64", "path": str(artifact),
            "sha256": hashlib.sha256(artifact.read_bytes()).hexdigest(),
        })
        state["installed"][package] = {"status": "installed", "version": VERSION}
    manifest_path = tmp_path / "manifest.json"
    state_path = tmp_path / "state.json"
    call_log = tmp_path / "calls.txt"
    mode_log = tmp_path / "mode-calls.txt"

    def change_archive(package, **changes):
        artifact = artifacts[package]
        archive = json.loads(artifact.read_text())
        archive.update(changes)
        artifact.write_text(json.dumps(archive))
        next(item for item in manifest["packages"] if item["package"] == package)["sha256"] = hashlib.sha256(artifact.read_bytes()).hexdigest()

    def run(*, target="/usr/bin/dpkg", mount="rw,relatime", readonly=False, statvfs_error=False, apt_exit=0,
            mode="('maintain',)", mode_exit=0, build=False):
        manifest_path.write_text(json.dumps(manifest))
        state_path.write_text(json.dumps(state))
        environment = dict(os.environ)
        environment.update({
            "PYTHON_BIN": sys.executable.replace("\\", "/"), "PYTHONPATH": str(mocks),
            "PYTHONDONTWRITEBYTECODE": "1", "MEMGUARD_DEB_MANIFEST": str(manifest_path),
            "TEST_DPKG_TARGET": target, "TEST_MOUNT_OPTIONS": mount,
            "TEST_USR_FLAGS": "1" if readonly else "0", "TEST_STATVFS_ERROR": "1" if statvfs_error else "0",
            "TEST_APT_EXIT": str(apt_exit), "TEST_CALL_LOG": str(call_log),
            "TEST_INSTALL_ROOT": str(installed_root), "TEST_STATE": str(state_path),
            "TEST_BOOT_MODE": mode, "TEST_MODE_EXIT": str(mode_exit), "TEST_MODE_LOG": str(mode_log),
        })
        args = [bash, "-c", DRIVER, str(script).replace("\\", "/")]
        if not build:
            args.append("--no-build")
        result = subprocess.run(args, text=True, capture_output=True, env=environment, timeout=30)
        calls = call_log.read_text() if call_log.exists() else ""
        return result, calls

    return SimpleNamespace(
        run=run, manifest=manifest, state=state, artifacts=artifacts,
        installed_root=installed_root, change_archive=change_archive,
        mode_log=mode_log,
    )


@pytest.mark.parametrize("target,readonly", [
    ("/usr/bin/dpkg_wrapper", False),
    ("/usr/lib/ostree-pkgs-guard", False),
    ("/usr/bin/dpkg", True),
])
def test_immutable_mode_stops_before_build_or_privileged_commands(installer, target, readonly):
    result, calls = installer.run(target=target, readonly=readonly, mode="('normal',)", build=True)
    assert result.returncode == 2
    assert "Maintenance mode is required" in result.stderr
    assert "native UKUI" in result.stderr
    assert "No OS mode change" in result.stderr
    assert calls == ""
    assert "apt_install=ok" not in result.stdout


@pytest.mark.parametrize("target", ["/usr/bin/dpkg_wrapper", "/usr/bin/dpkg-wrapper", "/usr/lib/ostree-pkgs-guard"])
def test_maintenance_mode_allows_unchanged_wrapper_only_when_usr_is_writable(installer, target):
    result, calls = installer.run(target=target)
    assert result.returncode == 0, result.stderr
    assert "apt-get install" in calls
    assert "apt_install=ok" in result.stdout
    query = installer.mode_log.read_text()
    assert "--system" in query
    assert "--dest com.kylin.MaintainMode" in query
    assert "--object-path /com/kylin/MaintainMode/Object" in query
    assert "--method com.kylin.MaintainMode.interface.GetBootMode" in query
    assert "SetBootMode" not in query


@pytest.mark.parametrize("mode,mode_exit", [
    ("('maintain',)", 1), ("", 0), ("('unknown',)", 0),
    ("('not-maintain',)", 0), ("('normal', 'maintain')", 0),
])
def test_wrapper_requires_successful_unambiguous_maintenance_query(installer, mode, mode_exit):
    result, calls = installer.run(target="/usr/bin/dpkg_wrapper", mode=mode, mode_exit=mode_exit)
    assert result.returncode == 2
    assert "GetBootMode" in result.stderr
    assert "Maintenance mode is required" in result.stderr
    assert not calls
    assert "apt_install=ok" not in result.stdout


def test_maintenance_mode_does_not_override_read_only_usr(installer):
    result, calls = installer.run(target="/usr/bin/dpkg_wrapper", readonly=True)
    assert result.returncode == 2
    assert "/usr is read-only" in result.stderr
    assert not calls


def test_maintenance_mode_stops_when_effective_mount_query_fails(installer):
    result, calls = installer.run(target="/usr/bin/dpkg_wrapper", statvfs_error=True)
    assert result.returncode == 2
    assert "Cannot verify /usr" in result.stderr
    assert not calls


def test_unwrapped_debian_dpkg_does_not_require_openkylin_dbus_service(installer):
    result, _calls = installer.run(mode_exit=1)
    assert result.returncode == 0, result.stderr
    assert not installer.mode_log.exists()


def test_unknown_mount_state_fails_closed(installer):
    result, calls = installer.run(statvfs_error=True)
    assert result.returncode == 2
    assert "Cannot verify /usr" in result.stderr
    assert not calls


def test_hidden_read_only_layers_do_not_block_writable_maintenance_overlay(installer):
    result, calls = installer.run(target="/usr/bin/dpkg_wrapper", mount="ro,relatime\nro,lowerdir=/base\nrw,upperdir=/top")
    assert result.returncode == 0, result.stderr
    assert "apt-get install" in calls


def test_unresolved_dpkg_fails_closed(installer):
    result, calls = installer.run(target="")
    assert result.returncode == 2
    assert "Cannot identify dpkg" in result.stderr
    assert not calls


@pytest.mark.parametrize("defect", ["old_version", "not_installed", "query_error", "old_payload", "missing_native"])
def test_apt_exit_zero_does_not_hide_stale_or_missing_installation(installer, defect):
    if defect == "old_version":
        installer.state["installed"]["memguardos-core"]["version"] = "1.0.0-1"
    elif defect == "not_installed":
        installer.state["installed"]["memguardos-core"]["status"] = "unpacked"
    elif defect == "query_error":
        installer.state["installed"]["memguardos-core"]["query_error"] = True
    elif defect == "old_payload":
        (installer.installed_root / "opt/memguardos/memguardos-core.py").write_text("old product")
    else:
        (installer.installed_root / "usr/lib/memguardos/plugin.so").unlink()
    result, calls = installer.run()
    assert result.returncode != 0
    assert "Installation verification failed" in result.stderr
    assert "exit 0 is not proof" in result.stderr
    assert "apt-get install" in calls
    assert "systemctl" not in calls
    assert "apt_install=ok" not in result.stdout


@pytest.mark.parametrize("defect", ["duplicate", "checksum", "archive_version", "mixed_version"])
def test_bad_manifest_or_archive_stops_before_apt(installer, defect):
    if defect == "duplicate":
        installer.manifest["packages"].append(installer.manifest["packages"][0].copy())
    elif defect == "checksum":
        installer.artifacts["memguardos-core"].write_text("corrupt archive")
    elif defect == "archive_version":
        installer.change_archive("memguardos-core", Version="old")
    else:
        installer.manifest["version"] = "other-version"
    result, calls = installer.run()
    assert result.returncode != 0
    assert not calls
    assert "apt_install=ok" not in result.stdout


@pytest.mark.parametrize("changes", [{"missing_md5sums": True}, {"files": {}}])
def test_missing_payload_checksum_metadata_cannot_report_success(installer, changes):
    installer.change_archive("memguardos-core", **changes)
    result, calls = installer.run()
    assert result.returncode != 0
    assert "archive has no" in result.stderr
    assert "systemctl" not in calls


def test_failed_apt_does_not_activate_service(installer):
    result, calls = installer.run(apt_exit=7)
    assert result.returncode == 7
    assert "systemctl" not in calls
    assert "apt_install=ok" not in result.stdout


def test_all_five_versions_and_archive_payloads_are_verified_before_success(installer):
    result, calls = installer.run()
    assert result.returncode == 0, result.stderr
    assert "package_count=5" in result.stdout
    for package in PACKAGES:
        assert f"installed[{package}]=installed version={VERSION} verified_files=" in result.stdout
    assert "systemctl enable --now memguardd.service" in calls
    assert result.stdout.rstrip().endswith("apt_install=ok")
