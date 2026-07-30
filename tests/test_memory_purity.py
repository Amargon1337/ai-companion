"""Unit tests for search purity and governed background mutations (Phase 1.6)."""
from __future__ import annotations

import os
import tempfile
from unittest.mock import MagicMock, patch

from companion.models import Fact
from companion.memory.store import MemoryStore


def test_search_facts_is_read_only_no_mutation() -> None:
    with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as tf:
        db_path = tf.name

    store = MemoryStore()
    store.db.path = db_path
    try:
        # Create a dormant fact
        f = Fact(id="f-dormant-1", fact="Python 3.14 is great", date="2026-07-29", importance=5, confidence=0.9, source="msg", status="dormant")
        store.db.batch_insert_facts([f.to_dict()])

        # Mock vector search to return high score for dormant fact
        store.vector.search = MagicMock(return_value=[{"content_hash": store.vector._content_hash("Python 3.14 is great"), "score": 0.95}])

        with store.db._conn() as conn:
            cnt_before = conn.execute("SELECT COUNT(*) FROM memory_mutation_log").fetchone()[0]

        hits = store.search_facts("Python 3.14", limit=5)
        assert len(hits) == 1
        assert hits[0][0].id == "f-dormant-1"

        # Verify no database mutation happened during search_facts
        db_fact = store.get_fact("f-dormant-1")
        assert db_fact is not None
        assert db_fact.status == "dormant"

        with store.db._conn() as conn:
            cnt_after = conn.execute("SELECT COUNT(*) FROM memory_mutation_log").fetchone()[0]
        assert cnt_after == cnt_before
    finally:
        store.db.close()
        try:
            os.unlink(db_path)
        except OSError:
            pass


def test_revive_dormant_fact_uses_governed_mutation() -> None:
    with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as tf:
        db_path = tf.name

    store = MemoryStore()
    store.db.path = db_path
    try:
        f = Fact(id="f-dormant-2", fact="Python async", date="2026-07-29", importance=5, confidence=0.9, source="msg", status="dormant")
        store.db.batch_insert_facts([f.to_dict()])

        store.revive_dormant_fact("f-dormant-2")

        db_fact = store.get_fact("f-dormant-2")
        assert db_fact is not None
        assert db_fact.status == "active"

        with store.db._conn() as conn:
            log_entry = conn.execute("SELECT * FROM memory_mutation_log WHERE entity_id='f-dormant-2'").fetchone()
        assert log_entry is not None
        assert dict(log_entry)["action"] == "revive"
        assert dict(log_entry)["initiator"] == "governor"
    finally:
        store.db.close()
        try:
            os.unlink(db_path)
        except OSError:
            pass
