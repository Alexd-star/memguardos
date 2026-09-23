#!/usr/bin/env python3
"""Compatibility entry point for existing source and Debian workflows."""

try:
    from memguard.integrations.openhands_memory_hook import main
except ModuleNotFoundError as exc:
    if exc.name != "memguard":
        raise
    from pathlib import Path
    import sys

    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
    from memguard.integrations.openhands_memory_hook import main


if __name__ == "__main__":
    raise SystemExit(main())
