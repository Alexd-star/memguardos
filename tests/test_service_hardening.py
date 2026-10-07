import os
import stat
from pathlib import Path

import pytest

from memguard.service import MemGuardService
from memguard.sensitivity.custom_patterns import save_pattern


ROOT = (Path(__file__).resolve().parents[1] / "src")


@pytest.mark.parametrize("unit", ["memguardd.service", "memguardd@.service"])
def test_core_systemd_units_run_unprivileged_and_sandboxed(unit):
    source = (ROOT / "scripts" / unit).read_text(encoding="utf-8")
    required = [
        "User=memguard",
        "Group=memguard",
        "UMask=0077",
        "StateDirectory=memguardos",
        "StateDirectoryMode=0700",
        "NoNewPrivileges=true",
        "PrivateTmp=true",
        "PrivateDevices=true",
        "ProtectSystem=strict",
        "ProtectHome=true",
        "ProtectKernelTunables=true",
        "ProtectKernelModules=true",
        "ProtectControlGroups=true",
        "RestrictSUIDSGID=true",
        "RestrictNamespaces=true",
        "MemoryDenyWriteExecute=true",
        "CapabilityBoundingSet=",
        "RestrictAddressFamilies=AF_UNIX",
        "SystemCallFilter=@system-service",
        "IPAddressDeny=any",
    ]
    for directive in required:
        assert directive in source


def test_installers_create_dedicated_service_account_and_private_state():
    installer = (ROOT / "scripts" / "install_openkylin.sh").read_text(encoding="utf-8")
    builder = (ROOT / "scripts" / "build_deb_packages.py").read_text(encoding="utf-8")

    for source in (installer, builder):
        assert "adduser --system" in source
        assert "/var/lib/memguardos" in source
        assert "0700" in source
    assert '"python3 (>= 3.10)' in builder


@pytest.mark.skipif(os.name == "nt", reason="POSIX mode bits are enforced on openKylin/Linux")
def test_managed_storage_permissions_self_heal_and_are_audited(tmp_path):
    root = tmp_path / "state"
    root.mkdir(mode=0o755)
    store = root / "memory.segment"
    database = root / "memguard.sqlite3"
    store.touch(mode=0o644)
    database.touch(mode=0o644)

    service = MemGuardService(store, database)
    try:
        assert stat.S_IMODE(root.stat().st_mode) == 0o700
        assert stat.S_IMODE(store.stat().st_mode) == 0o600
        assert stat.S_IMODE(database.stat().st_mode) == 0o600
        events = service.audit(event_type="storage_permissions_corrected")
        assert events
    finally:
        service.close()


@pytest.mark.skipif(os.name == "nt", reason="POSIX mode bits are enforced on openKylin/Linux")
def test_sensitive_pattern_file_is_owner_only(tmp_path):
    path = tmp_path / "state" / "sensitive_patterns.json"
    save_pattern(path, "employee", r"EMP-\d{6}")
    assert stat.S_IMODE(path.parent.stat().st_mode) == 0o700
    assert stat.S_IMODE(path.stat().st_mode) == 0o600
