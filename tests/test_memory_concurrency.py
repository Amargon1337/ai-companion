"""Unit tests for Optimistic Concurrency Control (OCC) in MemoryDatabase."""
from __future__ import annotations

import pytest
from companion.exceptions import ConcurrentModificationError
from companion.memory.store import MemoryStore
from companion.models import Fact
from companion.storage.sqlite_db import MemoryDatabase


def test_concurrent_update_detection(tmp_path):
    db_path = str(tmp_path / "test_concurrency.db")
    db = MemoryDatabase(db_path)
    try:
        # 1. Insert a fact
        with db._conn() as conn:
            conn.execute(
                "INSERT INTO facts (id, fact, importance, status, version) VALUES (?, ?, ?, ?, ?)",
                ("f-concur-1", "Original fact text", 5, "active", 1),
            )

        # 2. Update with expected_version=1 -> should succeed and bump version to 2
        db.update_fact_fields(
            "f-concur-1",
            {"importance": 8},
            expected_version=1,
        )
        updated = db.get_fact("f-concur-1")
        assert updated is not None
        assert updated["importance"] == 8
        assert updated["version"] == 2

        # 3. Try updating with expected_version=1 -> should raise ConcurrentModificationError
        with pytest.raises(ConcurrentModificationError) as exc_info:
            db.update_fact_fields(
                "f-concur-1",
                {"importance": 10},
                expected_version=1,
            )
        assert exc_info.value.record_id == "f-concur-1"
        assert exc_info.value.expected_version == 1
        assert exc_info.value.actual_version == 2

        # Verify fact was not modified
        unchanged = db.get_fact("f-concur-1")
        assert unchanged["importance"] == 8
        assert unchanged["version"] == 2

        # 4. Try update_fact_status with expected_version=2 -> should succeed (version=3)
        db.update_fact_status("f-concur-1", "archived", expected_version=2)
        archived = db.get_fact("f-concur-1")
        assert archived["status"] == "archived"
        assert archived["version"] == 3

        # 5. Try update_fact_status with expected_version=2 -> should raise ConcurrentModificationError
        with pytest.raises(ConcurrentModificationError):
            db.update_fact_status("f-concur-1", "active", expected_version=2)

        # 6. Check goals table OCC
        db.upsert_goal({
            "goal_id": "g-concur-1",
            "title": "Learn concurrency",
            "status": "active",
            "version": 1,
        })
        success = db.update_goal(
            "g-concur-1",
            {"title": "Master concurrency"},
            expected_version=1,
        )
        assert success is True
        with pytest.raises(ConcurrentModificationError):
            db.update_goal(
                "g-concur-1",
                {"title": "Break concurrency"},
                expected_version=1,
            )
    finally:
        db.close()


def test_store_update_uses_fact_version_for_occ(tmp_path, monkeypatch):
    import companion.config as cfg

    monkeypatch.setattr(cfg, "SQLITE_PATH", str(tmp_path / "store_occ.db"))
    store = MemoryStore()
    store.vector.embeddings_enabled = False
    try:
        fact = Fact(
            id="f-store-occ",
            fact="Original text",
            date="2026-08-02",
            importance=5,
            confidence=0.8,
            source="test",
        )
        store.add_fact(fact)

        advanced = {"done": False}

        def simulate_concurrent_write(text):
            if not advanced["done"]:
                advanced["done"] = True
                store.db.update_fact_fields(fact.id, {"importance": 9}, expected_version=1)
            return None

        monkeypatch.setattr(store.vector, "embed_text_only", simulate_concurrent_write)

        with pytest.raises(ConcurrentModificationError):
            store.update_fact(fact.id, fact="Updated text")

        persisted = store.get_fact(fact.id)
        assert persisted is not None
        assert persisted.fact == "Original text"
        assert persisted.importance == 9
        assert persisted.version == 2
    finally:
        store.db.close()
