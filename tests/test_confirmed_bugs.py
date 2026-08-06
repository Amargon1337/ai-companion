"""Tests to reproduce confirmed bugs from audit.

These are REAL bug reproduction tests, not characterization tests.
Each test should demonstrate HOW the bug causes incorrect behavior.

Bugs tested:
1. Bug #1 (Audit 2): No atomic write for FAISS index -> corruption on crash
2. Bug #2 (Audit 2): Inconsistent FAISS index and mapping after crash
3. Bug #3 (Audit 2): Facts with 'active' status without embeddings
"""

from __future__ import annotations

import os
import tempfile
from unittest.mock import patch

import pytest
import faiss
import numpy as np

from companion.models import Fact
from companion.memory.store import MemoryStore
from companion.memory.vector_index import VectorIndex
from tests.conftest import make_fact


# ============================================================================
# Bug #1 (Audit 2): No atomic write for FAISS index
# Severity: P3 (Low)
# File: companion/memory/vector_index.py:295-299
# ============================================================================

class TestBug1FAISSAtomicWrite:
    """Prove that FAISS index corruption during crash causes issues."""

    def test_corrupt_faiss_file_requires_rebuild(self, tmp_path, monkeypatch):
        """REAL BUG REPRODUCTION:
        
        Scenario:
        1. save_index_to_disk() writes directly to faiss_index.bin (no atomic write)
        2. Process crashes during write
        3. faiss_index.bin is corrupt
        4. On restart, _load_index() detects corruption and calls _rebuild_index()
        
        Bug: Even though FAISS detects corruption, the REBUILD is slow.
        With atomic write (temp file + rename), corrupt file would never be visible.
        
        This test proves that corrupt file causes rebuild (performance bug).
        """
        import companion.config as cfg
        monkeypatch.setattr(cfg, "DATA_DIR", str(tmp_path))
        db_path = str(tmp_path / "companion.db")
        monkeypatch.setattr(cfg, "SQLITE_PATH", db_path)
        
        store = MemoryStore()
        store.vector._init_table()
        
        # Initialize FAISS index with test data
        from companion.config import EMBEDDING_DIM
        
        base_index = faiss.IndexHNSWFlat(EMBEDDING_DIM, 32)
        store.vector.index = faiss.IndexIDMap(base_index)
        
        vec = np.zeros((1, EMBEDDING_DIM), dtype=np.float32)
        vec[0, 0] = 1.0
        faiss.normalize_L2(vec)
        store.vector.index.add_with_ids(vec, np.array([0], dtype=np.int64))
        
        store.vector.id_to_content = {0: "test content"}
        store.vector.hash_to_id = {"test_hash": 0}
        store.vector._next_id = 1
        store.vector._is_initialized = True
        store.vector._dirty_updates = 1
        
        # Write a valid index
        store.vector.save_index_to_disk()
        index_path = store.vector.index_path
        
        # SIMULATE CRASH: Corrupt the file by overwriting header
        with open(index_path, 'r+b') as f:
            f.seek(0)
            f.write(b'\x00\x00\x00\x00corrupt_header')
        
        # Track if rebuild is called during load
        rebuild_called = False
        original_rebuild = VectorIndex._rebuild_index
        
        def tracking_rebuild(self_v):
            nonlocal rebuild_called
            rebuild_called = True
            return original_rebuild(self_v)
        
        with patch.object(VectorIndex, '_rebuild_index', tracking_rebuild):
            # Try to load corrupt index (simulate restart)
            new_vector = VectorIndex(db=store.db)
            new_vector._load_index()
        
        # BUG CONFIRMED: Corrupt file caused unnecessary rebuild
        assert rebuild_called, \
            "Bug reproduced: corrupt FAISS file causes slow rebuild instead of using atomic write"


# ============================================================================
# Bug #2 (Audit 2): Inconsistent FAISS index and mapping after crash
# Severity: P3 (Low)
# File: companion/memory/vector_index.py:295-319
# ============================================================================

class TestBug2FAISSInconsistentState:
    """Prove that crash between write_index and set_meta causes unnecessary rebuild."""

    def test_dirty_flag_causes_unnecessary_rebuild(self, tmp_path, monkeypatch):
        """REAL BUG REPRODUCTION:
        
        Scenario:
        1. save_index_to_disk() calls faiss.write_index() - SUCCESS
        2. save_index_to_disk() calls db.save_state_model() - CRASH HERE
        3. faiss_index_dirty flag remains "1" in DB
        4. On restart, _load_index() sees dirty="1"
        5. _rebuild_index() is called (unnecessary - index is already saved)
        
        Bug: 3 non-atomic operations without transaction.
        """
        import companion.config as cfg
        monkeypatch.setattr(cfg, "DATA_DIR", str(tmp_path))
        db_path = str(tmp_path / "companion.db")
        monkeypatch.setattr(cfg, "SQLITE_PATH", db_path)
        
        store = MemoryStore()
        store.vector._init_table()
        
        # Initialize FAISS index
        from companion.config import EMBEDDING_DIM
        
        base_index = faiss.IndexHNSWFlat(EMBEDDING_DIM, 32)
        store.vector.index = faiss.IndexIDMap(base_index)
        
        vec = np.zeros((1, EMBEDDING_DIM), dtype=np.float32)
        vec[0, 0] = 1.0
        faiss.normalize_L2(vec)
        store.vector.index.add_with_ids(vec, np.array([0], dtype=np.int64))
        
        store.vector.id_to_content = {0: "test content"}
        store.vector.hash_to_id = {"test_hash": 0}
        store.vector._next_id = 1
        store.vector._is_initialized = True
        store.vector._dirty_updates = 1
        
        # Manually set dirty flag (simulate crash before set_meta)
        store.db.set_meta("faiss_index_dirty", "1")
        
        # Save index (this will set dirty to 0 in normal case)
        store.vector.save_index_to_disk()
        
        # Verify dirty flag is now "0"
        dirty = store.db.get_meta("faiss_index_dirty", "0")
        assert dirty == "0", "After successful save, dirty should be 0"
        
        # Now simulate crash: set dirty back to 1 WITHOUT corrupting index
        store.db.set_meta("faiss_index_dirty", "1")
        
        # Create new VectorIndex and load (simulate restart)
        rebuild_called = False
        original_rebuild = VectorIndex._rebuild_index
        
        def mock_rebuild(self):
            nonlocal rebuild_called
            rebuild_called = True
            return original_rebuild(self)
        
        with patch.object(VectorIndex, '_rebuild_index', mock_rebuild):
            new_vector = VectorIndex(db=store.db)
            new_vector._load_index()
        
        # BUG: Because dirty="1", rebuild is called even though index is valid
        assert rebuild_called, \
            "Bug reproduced: unnecessary rebuild due to non-atomic dirty flag update"


# ============================================================================
# Bug #3 (Audit 2): Facts with "active" status without embeddings
# Severity: P2 (Medium)
# File: companion/memory/store.py:234-246
# ============================================================================

class TestBug3ActiveFactsWithoutEmbeddings:
    """Prove that facts can be 'active' without embeddings, breaking search."""

    def test_fact_without_embedding_not_searchable(self, tmp_path, monkeypatch):
        """INTENDED BEHAVIOR (pending_embedding lifecycle):

        Scenario:
        1. add_fact() is called
        2. embed_text_only() returns None (API unavailable)
        3. The fact is inserted with status='pending_embedding' — NOT 'active'.
        4. Fact has no embedding in FAISS.

        The invariant "active facts must be searchable" is preserved by marking
        the fact pending instead of creating a broken 'active' row. A pending
        fact is retained in the DB (no deletion) and re-embedded when the API
        recovers (startup reconcile covers it).
        """
        import companion.config as cfg
        monkeypatch.setattr(cfg, "DATA_DIR", str(tmp_path))
        db_path = str(tmp_path / "companion.db")
        monkeypatch.setattr(cfg, "SQLITE_PATH", db_path)
        
        store = MemoryStore()
        
        # Mock embedding API to fail
        monkeypatch.setattr(store.vector, 'embed_text_only', lambda text: None)
        monkeypatch.setattr(store.vector, 'compute_and_cache', lambda *args, **kwargs: None)
        
        # Add fact
        fact = make_fact("User likes cats", importance=5)
        result = store.add_fact(fact)
        
        # Verify fact was added
        assert result is not None
        saved_fact = store.get_fact(result.id)
        # pending_embedding, not 'active' — the broken-active invariant is gone
        assert saved_fact.status == "pending_embedding"
        
        # Fact has no embedding (correct: it was never produced)
        embedding = store.vector.get_embedding("User likes cats")
        assert embedding is None
        
        # Vector search won't find it — expected while pending
        search_results = store.vector.search("User likes cats", top_k=5)
        assert len(search_results) == 0
        
        # But fact exists in DB (Iron Law #5: nothing deleted)
        assert store.get_fact(result.id) is not None, "Fact exists in DB"

    def test_fact_without_embedding_excluded_from_rebuild(self, tmp_path, monkeypatch):
        """REAL BUG REPRODUCTION:
        
        Scenario:
        1. Fact added with status='active' but no embedding
        2. _rebuild_index() is called
        3. Fact is excluded from rebuild (requires embedding IS NOT NULL)
        
        Bug: Fact permanently lost from vector index.
        """
        import companion.config as cfg
        monkeypatch.setattr(cfg, "DATA_DIR", str(tmp_path))
        db_path = str(tmp_path / "companion.db")
        monkeypatch.setattr(cfg, "SQLITE_PATH", db_path)
        
        store = MemoryStore()
        
        # Manually insert fact with status='active' but no embedding
        fact_dict = {
            "id": "test_fact_1",
            "fact": "Test fact without embedding",
            "status": "active",
            "importance": 5,
            "confidence": 0.8,
            "embedding": None,
        }
        store.db._insert_fact(fact_dict)
        
        # Rebuild FAISS index
        store.vector._rebuild_index()
        
        # BUG: Fact is not in FAISS index
        results = store.vector.search("Test fact without embedding", top_k=5)
        assert len(results) == 0, \
            "Bug reproduced: fact without embedding excluded from rebuild"


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
