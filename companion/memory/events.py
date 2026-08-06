"""Memory Events — Phase C0: Event Sourcing Foundation.

Истина хранится в потоке событий, а не только в текущем состоянии фактов.
Каждое изменение памяти (создание, обновление, архивация, конфликт) записывается как событие.
"""
from __future__ import annotations

import json
import uuid
from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from typing import Any


class MemoryEventType(Enum):
    """Типы событий жизненного цикла памяти."""
    # Жизненный цикл факта
    CANDIDATE_PROPOSED = "candidate_proposed"
    FACT_CREATED = "fact_created"
    FACT_UPDATED = "fact_updated"
    FACT_STATUS_CHANGED = "fact_status_changed"
    FACT_SUPERSEDED = "fact_superseded"
    FACT_ARCHIVED = "fact_archived"
    
    # Конфликты и верификация
    CONFLICT_DETECTED = "conflict_detected"
    CONFLICT_RESOLVED = "conflict_resolved"
    FACT_QUARANTINED = "fact_quarantined"
    FACT_VERIFIED = "fact_verified"
    
    # Консолидация и паттерны
    PATTERN_FORMED = "pattern_formed"
    SCHEMA_CREATED = "schema_created"
    EPISODE_CONSOLIDATED = "episode_consolidated"
    
    # Идентичность
    IDENTITY_LAYER_CHANGED = "identity_layer_changed"
    CORE_VALUE_PROTECTED = "core_value_protected"
    
    # Системные
    GOVERNANCE_DECISION = "governance_decision"
    METACOGNITION_ALERT = "metacognition_alert"


@dataclass
class MemoryEvent:
    """Событие в потоке памяти.
    
    Атрибуты:
        id: Уникальный ID события.
        aggregate_id: ID сущности (факта, паттерна), к которой относится событие.
        event_type: Тип события.
        timestamp: Время создания.
        actor: Кто инициировал (USER, LLM_EXTRACTOR, CONSOLIDATOR, ARBITER, GOVERNANCE).
        payload: Данные события (JSON-сериализуемые).
        metadata: Дополнительный контекст (версия модели, причина, etc).
    """
    aggregate_id: str
    event_type: MemoryEventType
    actor: str
    payload: dict[str, Any]
    metadata: dict[str, Any] = field(default_factory=dict)
    id: str = field(default_factory=lambda: str(uuid.uuid4()))
    timestamp: str = field(default_factory=lambda: datetime.utcnow().isoformat())
    
    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "aggregate_id": self.aggregate_id,
            "event_type": self.event_type.value,
            "timestamp": self.timestamp,
            "actor": self.actor,
            "payload": json.dumps(self.payload, ensure_ascii=False),
            "metadata": json.dumps(self.metadata, ensure_ascii=False),
        }
    
    @classmethod
    def from_row(cls, row: dict[str, Any]) -> MemoryEvent:
        return cls(
            id=row["id"],
            aggregate_id=row["aggregate_id"],
            event_type=MemoryEventType(row["event_type"]),
            actor=row["actor"],
            payload=json.loads(row["payload"]),
            metadata=json.loads(row["metadata"]),
            timestamp=row["timestamp"],
        )
