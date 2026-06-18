from pathlib import Path
from typing import List
import json

from memguard.integrations.events import AgentInteractionEvent


def load_event_file(path: Path) -> AgentInteractionEvent:
    return AgentInteractionEvent.from_json(Path(path).read_text(encoding="utf-8"))


def load_event_jsonl(path: Path, limit: int = 500) -> List[AgentInteractionEvent]:
    events = []
    for line_number, raw in enumerate(Path(path).read_text(encoding="utf-8").splitlines(), start=1):
        line = raw.strip()
        if not line:
            continue
        if len(events) >= limit:
            raise ValueError("event JSONL must contain at most %s events" % limit)
        try:
            payload = json.loads(line)
        except json.JSONDecodeError as exc:
            raise ValueError("invalid event JSON on line %s: %s" % (line_number, exc)) from exc
        if not isinstance(payload, dict):
            raise ValueError("event JSON on line %s must be an object" % line_number)
        events.append(AgentInteractionEvent.from_dict(payload))
    return events
