from pathlib import Path

from scripts.build_deb_packages import build_package_plan


def test_user_service_keeps_sdk_in_user_context_and_bounds_resources():
    root = Path(__file__).resolve().parents[1]
    unit = (root / "scripts/memguardd-user.service").read_text()
    assert "MEMGUARD_ALLOW_ROOT_KYLIN_USER_SOCKET" not in unit
    assert "User=root" not in unit and "User=memguard" not in unit
    assert "MEMGUARD_EMBEDDING_BACKEND=auto" in unit
    assert "--socket-mode 600" in unit
    for directive in ("MemoryHigh=384M", "MemoryMax=512M", "CPUQuota=50%", "UMask=0077"):
        assert directive in unit
    plan = build_package_plan("1.0.1-3", "amd64", "x86_64-linux-gnu")
    assert "/usr/lib/systemd/user/memguardd-user.service" in plan["memguardos-core"]["files"]
