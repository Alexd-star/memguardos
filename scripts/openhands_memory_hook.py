#!/usr/bin/env python3
"""Compatibility entry point for existing source and Debian workflows."""

from memguard.integrations.openhands_memory_hook import main


if __name__ == "__main__":
    raise SystemExit(main())
