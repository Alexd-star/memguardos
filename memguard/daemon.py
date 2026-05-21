from __future__ import annotations

import argparse
import json
import signal
import threading
import time
from pathlib import Path
from typing import Dict, Optional

from memguard.api.server import serve_unix_socket
import platform

from memguard.os_guard.cgroup import cgroup_status
from memguard.os_guard.journald import JournaldLogger
from memguard.retrieval.kylin_sdk import kylin_sdk_status
from memguard.service import MemGuardService


def daemon_health_snapshot(probe_sdk: bool = False) -> Dict[str, object]:
    cgroup = cgroup_status()
    snapshot: Dict[str, object] = {
        "status": "ready",
        "python": platform.python_version(),
        "cgroup_v2": cgroup.get("cgroup_v2_mounted"),
        "proc_cgroup": cgroup.get("proc_cgroup"),
    }
    if probe_sdk:
        sdk = kylin_sdk_status(probe_embedding=False)
        snapshot["kylin_embedding_available"] = sdk.get("embedding", {}).get("available")
        snapshot["kylin_vector_available"] = sdk.get("vector_store", {}).get("available")
    return snapshot


def run_daemon(
    interval: float = 60.0,
    once: bool = False,
    probe_sdk: bool = False,
    socket_path: str = "",
    socket_mode: int = 0o660,
    auth_token: Optional[str] = None,
) -> int:
    logger = JournaldLogger()
    stopping = {"value": False}

    def _stop(signum, _frame):
        stopping["value"] = True
        logger.log("daemon_signal", None, {"signal": signum})

    signal.signal(signal.SIGTERM, _stop)
    signal.signal(signal.SIGINT, _stop)

    service = MemGuardService()
    try:
        snapshot = daemon_health_snapshot(probe_sdk=probe_sdk)
        service._audit("daemon_started", None, snapshot)
        print(json.dumps(snapshot, ensure_ascii=False), flush=True)
        if once:
            return 0
        socket_thread = None
        if socket_path:
            socket_thread = threading.Thread(
                target=serve_unix_socket,
                args=(
                    Path(socket_path),
                    service,
                    lambda: stopping["value"],
                    lambda path: logger.log("api_socket_ready", None, {"path": path, "mode": oct(socket_mode), "auth_enabled": bool(auth_token)}),
                    socket_mode,
                    auth_token,
                ),
                daemon=True,
            )
            socket_thread.start()
        while not stopping["value"]:
            logger.log("daemon_heartbeat", None, daemon_health_snapshot(probe_sdk=probe_sdk))
            time.sleep(max(1.0, interval))
        if socket_thread is not None:
            socket_thread.join(timeout=2.0)
        service._audit("daemon_stopped", None, {"reason": "signal"})
        return 0
    finally:
        service.close()


def main() -> int:
    parser = argparse.ArgumentParser(description="MemGuardOS systemd daemon")
    parser.add_argument("--interval", type=float, default=60.0, help="Heartbeat interval in seconds")
    parser.add_argument("--once", action="store_true", help="Run startup health checks and exit")
    parser.add_argument("--probe-sdk", action="store_true", help="Include Kylin SDK availability in daemon health")
    parser.add_argument("--socket", default="", help="Optional Unix domain socket path for local Agent API")
    parser.add_argument("--socket-mode", default="660", help="Octal permission mode for the Unix socket, for example 660 or 666")
    parser.add_argument("--auth-token-file", default="", help="Optional file containing the local API bearer token")
    args = parser.parse_args()
    auth_token = ""
    if args.auth_token_file:
        auth_token = Path(args.auth_token_file).read_text(encoding="utf-8").strip()
    return run_daemon(
        interval=args.interval,
        once=args.once,
        probe_sdk=args.probe_sdk,
        socket_path=args.socket,
        socket_mode=int(args.socket_mode, 8),
        auth_token=auth_token or None,
    )


if __name__ == "__main__":
    raise SystemExit(main())
