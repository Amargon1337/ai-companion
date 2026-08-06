"""Event definitions for the Memory Event Bus."""
from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field, fields
from datetime import datetime
from typing import Any


@dataclass
class MemoryEvent:
    """Base class for all memory lifecycle events."""
    timestamp: str = field(default_factory=lambda: datetime.now().isoformat())
    initiator: str = "system"
    entity_id: str = ""


@dataclass
class FactCreatedEvent(MemoryEvent):
    fact_id: str = ""
    fact_text: str = ""
    importance: int = 5
    source: str = "msg"

    def __post_init__(self):
        if not self.entity_id and self.fact_id:
            self.entity_id = self.fact_id


@dataclass
class FactUpdatedEvent(MemoryEvent):
    fact_id: str = ""
    old_state: dict[str, Any] = field(default_factory=dict)
    new_state: dict[str, Any] = field(default_factory=dict)
    reason: str = ""

    def __post_init__(self):
        if not self.entity_id and self.fact_id:
            self.entity_id = self.fact_id


@dataclass
class FactArchivedEvent(MemoryEvent):
    fact_id: str = ""
    fact_text: str = ""
    reason: str = ""

    def __post_init__(self):
        if not self.entity_id and self.fact_id:
            self.entity_id = self.fact_id


@dataclass
class FactSupersededEvent(MemoryEvent):
    fact_id: str = ""
    fact_text: str = ""
    superseded_by: str = ""
    reason: str = ""

    def __post_init__(self):
        if not self.entity_id and self.fact_id:
            self.entity_id = self.fact_id


@dataclass
class FactRetrievedEvent(MemoryEvent):
    fact_id: str = ""
    retrieval_reason: str = "semantic"
    query: str = ""

    def __post_init__(self):
        if not self.entity_id and self.fact_id:
            self.entity_id = self.fact_id


@dataclass
class MutationAppliedEvent(MemoryEvent):
    mutation_id: str = ""
    fact_id: str = ""
    action: str = ""
    reason: str = ""

    def __post_init__(self):
        if not self.entity_id and self.fact_id:
            self.entity_id = self.fact_id


# -- Journal serialization (Phase B) ------------------------------------------
# The journal stores events as JSON so a crash between SQLite commit and the
# FAISS drain can be replayed at startup. Only dataclass fields are kept; the
# class registry maps type name -> class without importing the bus.

_EVENT_REGISTRY: dict[str, type[MemoryEvent]] = {}


def _register(cls: type[MemoryEvent]) -> type[MemoryEvent]:
    _EVENT_REGISTRY[cls.__name__] = cls
    return cls


for _cls in (FactCreatedEvent, FactUpdatedEvent, FactArchivedEvent,
             FactSupersededEvent, FactRetrievedEvent, MutationAppliedEvent):
    _register(_cls)


def event_to_journal(event: MemoryEvent) -> tuple[str, str]:
    """Return (event_type, json_payload) for durable journal storage."""
    payload = asdict(event)
    return event.__class__.__name__, json.dumps(payload, ensure_ascii=False)


def event_from_journal(event_type: str, payload_json: str) -> MemoryEvent | None:
    """Rebuild an event from journal storage; None if the type is unknown."""
    cls = _EVENT_REGISTRY.get(event_type)
    if cls is None:
        return None
    try:
        data = json.loads(payload_json or "{}")
        known = {f.name for f in fields(cls)}
        return cls(**{k: v for k, v in data.items() if k in known})
    except (TypeError, ValueError, json.JSONDecodeError):
        return None
