"""Repository layer for Amargon's Void Memory OS.

Repositories encapsulate SQL operations for specific domain entities.
They receive a MemoryDatabase reference (not a raw connection) and use
its transaction primitives (_conn, atomic_memory_transaction).

MemoryDatabase remains the facade that callers use directly — repositories
are an internal decomposition that keeps SQL logic organized without
breaking the public API.

Architecture:
    MemoryDatabase (facade, 2700+ lines)
        ├── FactRepository       — facts, fact_relations
        ├── EntityRepository     — entities, attributes, relations, mentions
        ├── MessageRepository    — messages
        ├── BeliefRepository     — beliefs
        ├── ReflectionRepository — reflections
        ├── PatternRepository    — patterns
        ├── AuditRepository      — audit_log, mutation_log, access_log
        └── MetaRepository       — meta, sessions, state_models
"""
from companion.storage.repositories.base import BaseRepository
from companion.storage.repositories.fact_repository import FactRepository
from companion.storage.repositories.entity_repository import EntityRepository
from companion.storage.repositories.message_repository import MessageRepository

__all__ = [
    "BaseRepository",
    "FactRepository",
    "EntityRepository",
    "MessageRepository",
]
