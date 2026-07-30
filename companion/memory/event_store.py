"""Event Store — Phase C0: Хранилище потока событий памяти.

Event Sourcing подход: все изменения памяти записываются как неизменяемые события.
Текущее состояние фактов — это проекция (projection) потока событий.
"""
from __future__ import annotations

import sqlite3
from contextlib import contextmanager
from typing import Any, Generator

from companion.memory.events import MemoryEvent, MemoryEventType


class EventStore:
    """Хранилище событий памяти с поддержкой event sourcing."""
    
    def __init__(self, db_path: str | None = None) -> None:
        from companion.config import SQLITE_PATH as _SQLITE_PATH
        self.db_path = db_path if db_path is not None else _SQLITE_PATH
        self._init_schema()
    
    @contextmanager
    def _conn(self) -> Generator[sqlite3.Connection, None, None]:
        conn = sqlite3.connect(self.db_path)
        conn.execute("PRAGMA busy_timeout = 5000;")
        conn.execute("PRAGMA journal_mode = WAL;")
        conn.row_factory = sqlite3.Row
        try:
            yield conn
            conn.commit()
        finally:
            conn.close()
    
    def _init_schema(self) -> None:
        """Инициализация таблицы событий."""
        with self._conn() as conn:
            conn.execute("""
                CREATE TABLE IF NOT EXISTS memory_events (
                    id TEXT PRIMARY KEY,
                    aggregate_id TEXT NOT NULL,
                    event_type TEXT NOT NULL,
                    timestamp TEXT NOT NULL,
                    actor TEXT NOT NULL,
                    payload TEXT NOT NULL,
                    metadata TEXT
                )
            """)
            conn.execute("CREATE INDEX IF NOT EXISTS idx_events_aggregate ON memory_events(aggregate_id)")
            conn.execute("CREATE INDEX IF NOT EXISTS idx_events_type ON memory_events(event_type)")
            conn.execute("CREATE INDEX IF NOT EXISTS idx_events_timestamp ON memory_events(timestamp)")
    
    def append(self, event: MemoryEvent) -> None:
        """Добавить событие в поток."""
        with self._conn() as conn:
            conn.execute(
                """
                INSERT INTO memory_events 
                (id, aggregate_id, event_type, timestamp, actor, payload, metadata)
                VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    event.id,
                    event.aggregate_id,
                    event.event_type.value,
                    event.timestamp,
                    event.actor,
                    event.to_dict()["payload"],
                    event.to_dict()["metadata"],
                ),
            )
    
    def append_batch(self, events: list[MemoryEvent]) -> None:
        """Добавить пакет событий атомарно."""
        if not events:
            return
        
        with self._conn() as conn:
            conn.executemany(
                """
                INSERT INTO memory_events 
                (id, aggregate_id, event_type, timestamp, actor, payload, metadata)
                VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                [
                    (
                        e.id,
                        e.aggregate_id,
                        e.event_type.value,
                        e.timestamp,
                        e.actor,
                        e.to_dict()["payload"],
                        e.to_dict()["metadata"],
                    )
                    for e in events
                ],
            )
    
    def get_events_for_aggregate(
        self,
        aggregate_id: str,
        event_types: list[MemoryEventType] | None = None,
        limit: int | None = None,
    ) -> list[MemoryEvent]:
        """Получить все события для агрегата (факта/паттерна)."""
        query = "SELECT * FROM memory_events WHERE aggregate_id = ?"
        params: list[Any] = [aggregate_id]
        
        if event_types:
            placeholders = ",".join("?" * len(event_types))
            query += f" AND event_type IN ({placeholders})"
            params.extend([et.value for et in event_types])
        
        query += " ORDER BY timestamp ASC"
        
        if limit:
            query += " LIMIT ?"
            params.append(limit)
        
        with self._conn() as conn:
            rows = conn.execute(query, tuple(params)).fetchall()
        
        return [MemoryEvent.from_row(dict(row)) for row in rows]
    
    def get_events_since(
        self,
        timestamp: str,
        aggregate_id: str | None = None,
        limit: int = 100,
    ) -> list[MemoryEvent]:
        """Получить события после указанной временной метки."""
        query = "SELECT * FROM memory_events WHERE timestamp > ?"
        params: list[Any] = [timestamp]
        
        if aggregate_id:
            query += " AND aggregate_id = ?"
            params.append(aggregate_id)
        
        query += " ORDER BY timestamp ASC LIMIT ?"
        params.append(limit)
        
        with self._conn() as conn:
            rows = conn.execute(query, tuple(params)).fetchall()
        
        return [MemoryEvent.from_row(dict(row)) for row in rows]
    
    def get_history(
        self,
        aggregate_id: str,
    ) -> list[MemoryEvent]:
        """Получить полную историю жизни агрегата."""
        return self.get_events_for_aggregate(aggregate_id)
    
    def replay_events(
        self,
        aggregate_id: str,
    ) -> dict[str, Any]:
        """Воспроизвести события для восстановления текущего состояния.
        
        Возвращает спроецированное состояние факта на основе всех событий.
        Это пример простой проекции; реальная логика зависит от типа события.
        """
        events = self.get_history(aggregate_id)
        state: dict[str, Any] = {}
        
        for event in events:
            state = self._apply_event(state, event)
        
        return state
    
    def _apply_event(self, state: dict[str, Any], event: MemoryEvent) -> dict[str, Any]:
        """Применить событие к состоянию (простая проекция)."""
        if event.event_type == MemoryEventType.FACT_CREATED:
            state.update(event.payload)
            state["status"] = "active"
        elif event.event_type == MemoryEventType.FACT_UPDATED:
            state.update(event.payload.get("updates", {}))
        elif event.event_type == MemoryEventType.FACT_STATUS_CHANGED:
            state["status"] = event.payload.get("new_status")
        elif event.event_type == MemoryEventType.FACT_SUPERSEDED:
            state["status"] = "superseded"
            state["superseded_by"] = event.payload.get("superseded_by")
        elif event.event_type == MemoryEventType.FACT_ARCHIVED:
            state["is_archived"] = True
        elif event.event_type == MemoryEventType.FACT_QUARANTINED:
            state["validation_status"] = "QUARANTINED"
            state["quarantine_reason"] = event.payload.get("reason")
        elif event.event_type == MemoryEventType.FACT_VERIFIED:
            state["validation_status"] = "VERIFIED"
        
        return state
    
    def count_events(self, aggregate_id: str | None = None) -> int:
        """Подсчитать количество событий."""
        with self._conn() as conn:
            if aggregate_id:
                row = conn.execute(
                    "SELECT COUNT(*) FROM memory_events WHERE aggregate_id = ?",
                    (aggregate_id,),
                ).fetchone()
            else:
                row = conn.execute("SELECT COUNT(*) FROM memory_events").fetchone()
        
        return row[0] if row else 0
