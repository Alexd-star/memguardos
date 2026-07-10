import os
import signal
import subprocess
import sys
import time
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parents[1]


@pytest.mark.skipif(os.name == "nt", reason="POSIX signal handling is verified on openKylin")
def test_sigterm_interrupts_the_heartbeat_wait(tmp_path):
    env = os.environ.copy()
    env["PYTHONPATH"] = str(ROOT)
    env["MEMGUARD_HOME"] = str(tmp_path / "home")
    process = subprocess.Popen(
        [sys.executable, "-m", "memguard.daemon", "--interval", "60"],
        cwd=ROOT,
        env=env,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )
    try:
        assert process.stdout is not None
        startup = process.stdout.readline()
        assert '"status": "ready"' in startup

        started = time.monotonic()
        process.send_signal(signal.SIGTERM)
        process.wait(timeout=3)

        assert process.returncode == 0
        assert time.monotonic() - started < 3
    finally:
        if process.poll() is None:
            process.kill()
            process.wait(timeout=3)
