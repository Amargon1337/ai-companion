"""Test: FAISS rebuild error swallowing when embeddings table doesn't exist."""
import os, sys
os.environ.setdefault('API_TOKEN', 'test:token')
os.environ.setdefault('GOOGLE_API_KEY', 'test_key')
os.environ.setdefault('ADMIN_IDS', '12345')
os.environ.setdefault('LLM_TIMEOUT', '5')
os.environ.setdefault('LLM_RETRIES', '1')

import tempfile
import sqlite3
import pytest
import companion.config as cfg

from companion.memory.store import MemoryStore
from companion.models import Fact


def test_rebuild_without_embeddings_table(tmp_path, monkeypatch):
    """Verify behavior when embeddings table is missing during rebuild."""
    monkeypatch.setattr(cfg, "DATA_DIR", str(tmp_path))
    db_path = str(tmp_path / "test_rebuild.db")
    monkeypatch.setattr(cfg, "SQLITE_PATH", db_path)
    
    # Create a store and add facts with embeddings
    store = MemoryStore()
    store.vector.embeddings_enabled = False  # Skip embedding computation
    
    # Insert a fact with embedding blob directly
    from companion.memory.vector_index import _float_list_to_blob
    blob = _float_list_to_blob([0.1] * 768)
    
    with store.db._conn() as conn:
        conn.execute(
            "INSERT INTO facts (id, fact, date, importance, confidence, source, status, embedding) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            ("f-test-1", "Test fact content", "2026-08-02", 5, 0.9, "test", "active", blob),
        )
    
    # Mark dirty
    store.db.set_meta("faiss_index_dirty", "1")
    
    # Now corrupt the embeddings table by dropping it
    with store.db._conn() as conn:
        conn.execute("DROP TABLE IF EXISTS embeddings")
    
    # Create a new store — should it fail or recover?
    store2 = MemoryStore()
    store2.vector.embeddings_enabled = False
    
    # Should have rebuilt from facts table only
    assert len(store2.vector.hash_to_id) == 1, f"Expected 1 hash, got {len(store2.vector.hash_to_id)}"
    
    store.db.close()
    store2.db.close()
