"""Unit tests for Atomic Memory Transactions in MemoryDatabase and MemoryPersistenceLayer."""
from __future__ import annotations

import pytest
from companion.memory.governor import MemoryGovernor, MemoryRecommendation
from companion.memory.persistence import MemoryPersistenceLayer
from companion.memory.policies.base import PolicyDecision
from companion.storage.sqlite_db import MemoryDatabase


def test_atomic_transaction_commit(tmp_path):
    db_path = str(tmp_path / "test_atomicity.db")
    db = MemoryDatabase(db_path)
    try:
        with db._conn() as conn:
            conn.execute(
                "INSERT INTO facts (id, fact, importance, status, version) VALUES (?, ?, ?, ?, ?)",
                ("f-atom-1", "Original fact", 5, "active", 1),
            )

        with db.atomic_memory_transaction():
            db.update_fact_fields("f-atom-1", {"importance": 9}, expected_version=1)
            db.log_mutation(
                entity_id="f-atom-1",
                action="boost",
                reason="test commit",
                state_before={"importance": 5},
                state_after={"importance": 9},
            )

        updated = db.get_fact("f-atom-1")
        assert updated["importance"] == 9
        assert updated["version"] == 2

        logs = db.list_mutations(entity_id="f-atom-1")
        assert len(logs) == 1
        assert logs[0]["action"] == "boost"
    finally:
        db.close()


def test_atomic_transaction_rollback(tmp_path):
    db_path = str(tmp_path / "test_atomicity_rollback.db")
    db = MemoryDatabase(db_path)
    try:
        with db._conn() as conn:
            conn.execute(
                "INSERT INTO facts (id, fact, importance, status, version) VALUES (?, ?, ?, ?, ?)",
                ("f-atom-2", "Original fact", 5, "active", 1),
            )

        with pytest.raises(RuntimeError, match="Simulated failure"):
            with db.atomic_memory_transaction():
                db.update_fact_fields("f-atom-2", {"importance": 10}, expected_version=1)
                db.log_mutation(
                    entity_id="f-atom-2",
                    action="boost",
                    reason="test rollback",
                    state_before={"importance": 5},
                    state_after={"importance": 10},
                )
                raise RuntimeError("Simulated failure inside transaction")

        # Verify fact was NOT updated (rolled back)
        unchanged = db.get_fact("f-atom-2")
        assert unchanged["importance"] == 5
        assert unchanged["version"] == 1

        # Verify mutation log was NOT inserted (rolled back)
        logs = db.list_mutations(entity_id="f-atom-2")
        assert len(logs) == 0
    finally:
        db.close()


def test_fact_insert_rolls_back_world_model_projection(tmp_path, monkeypatch):
    import companion.config as cfg
    from companion.memory.store import MemoryStore
    from companion.models import Fact

    monkeypatch.setattr(cfg, "SQLITE_PATH", str(tmp_path / "world_atomicity.db"))
    store = MemoryStore()
    store.vector.embeddings_enabled = False
    try:
        def fail_embedding(*args, **kwargs):
            raise RuntimeError("forced vector failure")

        monkeypatch.setattr(store.vector, "compute_and_cache", fail_embedding)
        fact = Fact(
            id="f-world-rollback",
            fact="У Ивана есть собака Морзик",
            date="2026-08-02",
            importance=8,
            confidence=0.9,
            source="test",
        )

        with pytest.raises(RuntimeError, match="forced vector failure"):
            store.add_fact(fact)

        assert store.get_fact(fact.id) is None
        with store.db._conn() as conn:
            mentions = conn.execute(
                "SELECT COUNT(*) FROM entity_mentions WHERE fact_id=?", (fact.id,)
            ).fetchone()[0]
            attributes = conn.execute(
                "SELECT COUNT(*) FROM entity_attributes WHERE source_fact_id=?", (fact.id,)
            ).fetchone()[0]
        assert mentions == 0
        assert attributes == 0
    finally:
        store.db.close()
