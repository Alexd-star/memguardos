from typing import Dict, List
import re


ENTITY_ALIASES: Dict[str, List[str]] = {
    "openKylin": ["openkylin", "open麒麟"],
    "Linux": ["linux"],
    "systemd": ["systemd", "systemctl"],
    "cgroup": ["cgroup", "cgroup v2", "MemoryMax", "CPUQuota"],
    "journald": ["journald", "journalctl", "日志"],
    "auditd": ["auditd", "auditctl", "ausearch", "审计"],
    "fanotify": ["fanotify"],
    "eBPF": ["ebpf", "bpftrace"],
    "mmap": ["mmap"],
    "COW": ["cow", "copy-on-write", "写时复制"],
    "precise_forgetting": ["精准遗忘", "forget", "tombstone", "compact"],
    "sensitive_data": ["敏感", "token", "api key", "password", "private key", "密钥"],
    "agent_memory": ["记忆", "memory", "preference", "knowledge"],
}


def extract_entities(text: str) -> List[str]:
    lowered = text.lower()
    entities = []
    for canonical, aliases in ENTITY_ALIASES.items():
        if any(alias.lower() in lowered for alias in aliases):
            entities.append(canonical)

    # Preserve important identifier-like tokens that often represent tools,
    # services, or configuration keys.
    for token in re.findall(r"\b[A-Za-z][A-Za-z0-9_\-]{2,}\b", text):
        if token.lower() in {"the", "and", "with", "can", "use", "after", "editing"}:
            continue
        if token not in entities and len(entities) < 16:
            entities.append(token)
    return entities[:16]


def extract_relations(entities: List[str]) -> List[Dict[str, str]]:
    relations: List[Dict[str, str]] = []
    for idx, source in enumerate(entities):
        for target in entities[idx + 1 :]:
            relations.append({"source": source, "relation": "co_occurs", "target": target})
            if len(relations) >= 24:
                return relations
    return relations

