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
