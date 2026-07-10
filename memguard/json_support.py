from __future__ import annotations

from dataclasses import asdict, is_dataclass
import json
import math
from pathlib import Path
from typing import Any


_COMPOSITE_KEY_PREFIX = "__memguard_key__:"


def _canonical_json(value: Any) -> str:
    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    )


def _key_component(value: Any) -> Any:
    if isinstance(value, str):
        return value
    if value is None or isinstance(value, (bool, int)):
        return value
    if isinstance(value, float):
        if not math.isfinite(value):
            raise ValueError("non-finite floats are not supported as dictionary keys")
        return value
    if isinstance(value, Path):
        return {"type": "path", "value": str(value)}
    if isinstance(value, tuple):
        return [_key_component(item) for item in value]
    if isinstance(value, frozenset):
        items = [_key_component(item) for item in value]
        return {
            "type": "frozenset",
            "value": sorted(items, key=_canonical_json),
        }
    raise TypeError("unsupported dictionary key type: %s" % type(value).__name__)


def _json_key(value: Any) -> str:
    if isinstance(value, str):
        return value
    if isinstance(value, tuple):
        payload = {"type": "tuple", "value": [_key_component(item) for item in value]}
    elif isinstance(value, frozenset):
        payload = _key_component(value)
    elif isinstance(value, Path):
        payload = {"type": "path", "value": str(value)}
    elif value is None:
        payload = {"type": "null", "value": None}
    elif isinstance(value, bool):
        payload = {"type": "bool", "value": value}
    elif isinstance(value, int):
        payload = {"type": "int", "value": value}
    elif isinstance(value, float):
        if not math.isfinite(value):
            raise ValueError("non-finite floats are not supported as dictionary keys")
        payload = {"type": "float", "value": value}
    else:
        raise TypeError("unsupported dictionary key type: %s" % type(value).__name__)
    return _COMPOSITE_KEY_PREFIX + _canonical_json(payload)


def jsonable(value: Any) -> Any:
    """Convert service-domain objects into JSON-compatible values."""
    if hasattr(value, "to_dict") and callable(value.to_dict):
        return jsonable(value.to_dict())
    if is_dataclass(value):
        return jsonable(asdict(value))
    if isinstance(value, dict):
        converted = {}
        original_keys = {}
        for key, item in value.items():
            normalized_key = _json_key(key)
            if normalized_key in converted:
                raise ValueError(
                    "dictionary key collision after normalization: %r and %r"
                    % (original_keys[normalized_key], key)
                )
            converted[normalized_key] = jsonable(item)
            original_keys[normalized_key] = key
        return dict(sorted(converted.items()))
    if isinstance(value, (set, frozenset)):
        items = [jsonable(item) for item in value]
        return sorted(
            items,
            key=_canonical_json,
        )
    if isinstance(value, (list, tuple)):
        return [jsonable(item) for item in value]
    if isinstance(value, Path):
        return str(value)
    if isinstance(value, float):
        return value if math.isfinite(value) else None
    if value is None or isinstance(value, (bool, int, str)):
        return value
    return str(value)
