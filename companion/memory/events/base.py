"""Event definitions for the Memory Event Bus."""
from __future__ import annotations

from dataclasses import dataclass, field
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
