#!/usr/bin/env python3
"""Compatibility entry point for existing source and Debian workflows."""

from pathlib import Path
import sys


root = Path(__file__).resolve().parents[1]
if (root / "memguard" / "integrations" / "openhands_memory_hook.py").is_file():
    # Source and /opt entry points must not silently use an older installed hook.
    sys.path.insert(0, str(root))

from memguard.integrations.openhands_memory_hook import main  # noqa: E402


if __name__ == "__main__":
    raise SystemExit(main())
