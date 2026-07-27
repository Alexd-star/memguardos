from __future__ import annotations

import os
import stat
from pathlib import Path
from typing import Dict, Optional


def enforce_private_mode(path: Path, mode: int) -> Optional[Dict[str, object]]:
    """Converge a managed path to an owner-only POSIX mode."""

    path = Path(path)
    if os.name == "nt" or not path.exists():
        return None
    current = stat.S_IMODE(path.stat().st_mode)
    if current == mode:
        return None
    path.chmod(mode)
    return {
        "path": str(path),
        "previous_mode": oct(current),
        "mode": oct(mode),
    }


def ensure_private_directory(path: Path) -> Optional[Dict[str, object]]:
    path = Path(path)
    path.mkdir(parents=True, exist_ok=True, mode=0o700)
    return enforce_private_mode(path, 0o700)


def ensure_private_file(path: Path) -> Optional[Dict[str, object]]:
    return enforce_private_mode(Path(path), 0o600)
