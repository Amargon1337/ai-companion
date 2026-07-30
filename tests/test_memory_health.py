"""Unit tests for MemoryHealthMonitor (Stage 7)."""
from __future__ import annotations

import os
import tempfile

from companion.memory.health import MemoryHealthMonitor, memory_index_health
from companion.memory.store import MemoryStore
from companion.models import Fact
from companion.storage.sqlite_db import MemoryDatabase


def test_memory_health_monitor_metrics() -> None:
    with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as tf:
        db_path = tf.name

    db = MemoryDatabase(db_path)
    try:
        f1 = Fact(
            id="f-hlth-1",
            fact="Активный факт 1",
            date="2026-07-20",
            importance=5,
            confidence=0.9,
            source="msg",
            status="active",
        )
        f2 = Fact(
            id="f-hlth-2",
            fact="Архивный факт",
            date="2024-01-01",
            importance=3,
            confidence=0.8,
            source="msg",
            status="archived",
        )
        f3 = Fact(
            id="f-hlth-3",
            fact="Старый активный факт",
            date="2024-01-01",
            importance=5,
            confidence=0.9,
            source="msg",
            status="active",
        )
        db.batch_insert_facts([f1.to_dict(), f2.to_dict(), f3.to_dict()])

        # Set retrieval/usage counts
        db.update_fact_fields("f-hlth-1", {"retrieved_count": 10, "used_count": 8})
        db.update_fact_fields("f-hlth-3", {"retrieved_count": 10, "used_count": 2})

        # Log a mutation and a rollback
        db.log_mutation(entity_id="f-hlth-1", action="boost", reason="usage", state_before={"importance": 5}, state_after={"importance": 6})
        db.log_mutation(entity_id="f-hlth-3", action="rollback", reason="error", state_before={"importance": 6}, state_after={"importance": 5})

        monitor = MemoryHealthMonitor(db, stale_days=90)
        metrics = monitor.get_health_metrics()

        assert metrics["total_facts"] == 3
        assert metrics["active_facts"] == 2
        assert metrics["archived_facts"] == 1
        assert metrics["archive_rate"] == round(1.0 / 3.0, 4)

        # retrieval precision: (8 + 2) / (10 + 10) = 10 / 20 = 0.5
        assert metrics["retrieval_precision"] == 0.5

        # citation rate: both active facts have used_count > 0 -> 2/2 = 1.0
        assert metrics["citation_rate"] == 1.0

        # stale memory ratio: f-hlth-3 is from 2024 -> 1 out of 2 active facts = 0.5
        assert metrics["stale_memory_ratio"] == 0.5

        assert metrics["mutation_count"] == 2
        assert metrics["rollback_count"] == 1
    finally:
        db.close()
        try:
            os.unlink(db_path)
        except OSError:
            pass


def test_memory_index_health() -> None:
    from companion.memory.vector_index import VectorIndex

    class DummyStore:
        def __init__(self, db_obj: MemoryDatabase, vec_obj: VectorIndex):
            self.db = db_obj
            self.vector = vec_obj

    with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as tf:
        db_path = tf.name

    db = MemoryDatabase(db_path)
    vec = VectorIndex(db_path)
    store = DummyStore(db, vec)
    try:
        f1 = Fact(id="idx-1", fact="Python is great", date="2026-07-28", importance=5, confidence=0.9, source="msg", status="active")
        db._insert_fact(f1.to_dict())
        stats = memory_index_health(store)
        assert stats["active_facts"] == 1
        assert stats["indexed_vectors"] == 0
        assert stats["orphan_vectors"] == 0
        assert stats["missing_vectors"] == 1
    finally:
        db.close()
        try:
            os.unlink(db_path)
        except OSError:
            pass

