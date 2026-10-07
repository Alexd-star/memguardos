from __future__ import annotations

from dataclasses import dataclass
import json
from pathlib import Path
from typing import Any, Dict, Iterable, List


@dataclass
class ConfigEntry:
    path: str
    value: Any


@dataclass
class ConfigQualityIssue:
    path: str
    reason: str


def load_config_entries(path: Path) -> List[ConfigEntry]:
    text = path.read_text(encoding="utf-8")
    data = _parse_config_text(text, path.suffix.lower())
    return list(_flatten_config(data))


def config_entry_to_memory_text(entry: ConfigEntry) -> str:
    key = entry.path.lower()
    value = _stringify(entry.value)
    if any(marker in key for marker in ["security", "safety", "policy", "sensitive", "secret", "token", "password"]):
        return "手动配置安全策略：%s = %s。" % (entry.path, value)
    if any(marker in key for marker in ["tool", "command", "shell", "editor"]):
        return "手动配置偏好：以后工具选择遵循 %s = %s。" % (entry.path, value)
    if any(marker in key for marker in ["style", "language", "verbosity", "answer", "output", "format"]):
        return "手动配置偏好：以后回答输出风格遵循 %s = %s。" % (entry.path, value)
    if any(marker in key for marker in ["workflow", "case", "template"]):
        return "手动配置知识：%s = %s。" % (entry.path, value)
    return "手动配置项：%s = %s。" % (entry.path, value)


def validate_config_entry(entry: ConfigEntry) -> ConfigQualityIssue | None:
    if not entry.path.strip():
        return ConfigQualityIssue(entry.path, "empty_path")
    if entry.value is None:
        return ConfigQualityIssue(entry.path, "empty_value")
    if isinstance(entry.value, str) and not entry.value.strip():
        return ConfigQualityIssue(entry.path, "empty_value")
    if isinstance(entry.value, (dict, list)):
        return ConfigQualityIssue(entry.path, "non_leaf_value")
    return None


def _parse_config_text(text: str, suffix: str) -> Any:
    stripped = text.strip()
    if suffix == ".json" or stripped.startswith("{") or stripped.startswith("["):
        return json.loads(stripped or "{}")
    return _parse_simple_yaml(stripped)


def _parse_simple_yaml(text: str) -> Dict[str, Any]:
    root: Dict[str, Any] = {}
    stack: List[tuple[int, Dict[str, Any]]] = [(-1, root)]
    for raw_line in text.splitlines():
        if not raw_line.strip() or raw_line.lstrip().startswith("#"):
            continue
        if ":" not in raw_line:
            continue
        indent = len(raw_line) - len(raw_line.lstrip(" "))
        key, value = raw_line.strip().split(":", 1)
        key = key.strip().strip("'\"")
        while stack and indent <= stack[-1][0]:
            stack.pop()
        parent = stack[-1][1] if stack else root
        value = value.strip()
        if value == "":
            child: Dict[str, Any] = {}
            parent[key] = child
            stack.append((indent, child))
        else:
            parent[key] = _parse_scalar(value)
    return root


def _parse_scalar(value: str) -> Any:
    clean = value.strip().strip("'\"")
    lowered = clean.lower()
    if lowered in {"true", "false"}:
        return lowered == "true"
    if lowered in {"null", "none"}:
        return None
    try:
        if "." in clean:
            return float(clean)
        return int(clean)
    except ValueError:
        return clean


def _flatten_config(data: Any, prefix: str = "") -> Iterable[ConfigEntry]:
    if isinstance(data, dict):
        for key, value in data.items():
            next_prefix = "%s.%s" % (prefix, key) if prefix else str(key)
            yield from _flatten_config(value, next_prefix)
        return
    if isinstance(data, list):
        for idx, value in enumerate(data):
            next_prefix = "%s[%d]" % (prefix, idx)
            yield from _flatten_config(value, next_prefix)
        return
    if prefix:
        yield ConfigEntry(prefix, data)


def _stringify(value: Any) -> str:
    if isinstance(value, str):
        return value
    return json.dumps(value, ensure_ascii=False, sort_keys=True)
