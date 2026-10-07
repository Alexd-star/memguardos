from __future__ import annotations

import ast
from functools import lru_cache
import json
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
INVENTORY_PATH = ROOT / "packaging" / "release_manifest.json"


@lru_cache(maxsize=1)
def load_release_inventory() -> dict[str, Any]:
    payload = json.loads(INVENTORY_PATH.read_text(encoding="utf-8"))
    if payload.get("schema_version") != "memguardos-release-inventory-v1":
        raise ValueError("unsupported release inventory schema")
    return payload


def inventory_set(name: str) -> set[str]:
    values = load_release_inventory().get(name, [])
    if not isinstance(values, list) or not all(isinstance(item, str) for item in values):
        raise ValueError(f"release inventory field {name!r} must be a string list")
    return set(values)


def is_competition_path(relative: str) -> bool:
    inventory = load_release_inventory()
    if relative in inventory_set("competition_include_files"):
        return True
    return any(relative.startswith(prefix) for prefix in inventory["competition_include_prefixes"])


def project_version() -> str:
    tree = ast.parse((ROOT / "memguard" / "__init__.py").read_text(encoding="utf-8"))
    for node in tree.body:
        if not isinstance(node, ast.Assign):
            continue
        if any(isinstance(target, ast.Name) and target.id == "__version__" for target in node.targets):
            value = ast.literal_eval(node.value)
            if isinstance(value, str) and value:
                return value
    raise ValueError("memguard.__version__ is missing")
