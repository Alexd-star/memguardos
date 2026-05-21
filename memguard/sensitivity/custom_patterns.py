from pathlib import Path
from typing import Dict
import json
import re


class PatternValidationError(ValueError):
    pass


def load_patterns(path: Path) -> Dict[str, str]:
    if not path.exists():
        return {}
    data = json.loads(path.read_text(encoding="utf-8"))
    return {str(key): str(value) for key, value in data.items()}


def save_pattern(path: Path, name: str, regex: str) -> Dict[str, str]:
    validate_pattern(name, regex)
    patterns = load_patterns(path)
    patterns[name] = regex
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(patterns, ensure_ascii=False, indent=2, sort_keys=True), encoding="utf-8")
    return patterns


def validate_pattern(name: str, regex: str) -> None:
    if not re.fullmatch(r"[A-Za-z0-9_.-]{1,64}", name):
        raise PatternValidationError("pattern name must be 1-64 chars of letters, digits, dot, underscore or dash")
    if not regex or len(regex) > 512:
        raise PatternValidationError("pattern regex must be non-empty and at most 512 chars")
    try:
        compiled = re.compile(regex)
    except re.error as exc:
        raise PatternValidationError("invalid regex: %s" % exc) from exc
    if compiled.search(""):
        raise PatternValidationError("pattern must not match empty text")
    ordinary = "this is an ordinary linux memory sentence without secrets"
    match = compiled.search(ordinary)
    if match and len(match.group(0)) >= max(8, len(ordinary) // 2):
        raise PatternValidationError("pattern is too broad for a sensitive detector")
