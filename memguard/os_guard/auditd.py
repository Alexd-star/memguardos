from pathlib import Path
from typing import Iterable, List
import glob


DEFAULT_SENSITIVE_PATHS = [
    "~/.memguardos",
    "~/.ssh",
    "~/.config",
    "~/.env",
    "/etc/ssh",
    "/etc/passwd",
    "/etc/shadow",
    "/var/lib/memguardos",
]


def resolve_paths(paths: Iterable[str] = None, existing_only: bool = False) -> List[str]:
    selected = list(paths or DEFAULT_SENSITIVE_PATHS)
    resolved = []
    for path in selected:
        expanded = str(Path(path).expanduser()) if path.startswith("~") else path
        matches = sorted(glob.glob(expanded)) if any(ch in expanded for ch in "*?[") else [expanded]
        for match in matches:
            if existing_only and not Path(match).exists():
                continue
            if match not in resolved:
                resolved.append(match)
    return resolved


def auditctl_rules(paths: Iterable[str] = None, key: str = "memguard_sensitive", existing_only: bool = False) -> List[str]:
    selected = resolve_paths(paths, existing_only=existing_only)
    rules = []
    for path in selected:
        perms = "rwxa"
        rules.append("auditctl -w %s -p %s -k %s" % (path, perms, key))
    return rules


def audit_rules_file(paths: Iterable[str] = None, key: str = "memguard_sensitive", existing_only: bool = False) -> str:
    selected = resolve_paths(paths, existing_only=existing_only)
    lines = [
        "# MemGuardOS sensitive access audit rules",
        "# Install to /etc/audit/rules.d/memguard.rules and reload auditd.",
    ]
    for path in selected:
        lines.append("-w %s -p rwxa -k %s" % (path, key))
    return "\n".join(lines) + "\n"


def ausearch_command(key: str = "memguard_sensitive") -> str:
    return "ausearch -k %s -i" % key
