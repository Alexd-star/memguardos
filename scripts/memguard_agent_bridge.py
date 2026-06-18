#!/usr/bin/env python3
from __future__ import annotations

from pathlib import Path
import argparse
import json
import os
import signal
import sys
import time


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from memguard.integrations.bridge import AgentJobQueue
from memguard.service import MemGuardService


def main() -> int:
    parser = argparse.ArgumentParser(description="MemGuardOS OpenHands Agent task bridge")
    parser.add_argument("--queue-root", required=True)
    parser.add_argument("--namespace", required=True)
    parser.add_argument("--poll-interval", type=float, default=1.0)
    parser.add_argument("--once", action="store_true")
    args = parser.parse_args()

    queue = AgentJobQueue(
        Path(args.queue_root),
        model=os.environ.get("OPENHANDS_MODEL", ""),
        base_url=os.environ.get("OPENHANDS_BASE_URL", ""),
        api_key=os.environ.get("OPENHANDS_API_KEY", ""),
    )
    service = MemGuardService(namespace=args.namespace)
    stopping = {"value": False}

    def stop(_signum, _frame):
        stopping["value"] = True

    signal.signal(signal.SIGTERM, stop)
    signal.signal(signal.SIGINT, stop)
    print(
        json.dumps(
            {
                "event": "agent_bridge_started",
                "namespace": args.namespace,
                "queue_root": str(queue.root),
                "model_configured": bool(queue.model),
                "base_url_configured": bool(queue.base_url),
                "api_key_configured": bool(queue.api_key),
            },
            ensure_ascii=False,
        ),
        flush=True,
    )
    try:
        while not stopping["value"]:
            result = queue.process_next(service)
            if result is not None:
                print(
                    json.dumps(
                        {
                            "event": "agent_job_finished",
                            "job_id": result.get("job_id"),
                            "ok": result.get("ok"),
                            "error_id": result.get("error_id", ""),
                        },
                        ensure_ascii=False,
                    ),
                    flush=True,
                )
            if args.once:
                break
            if result is None:
                time.sleep(max(0.1, args.poll_interval))
    finally:
        service.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
