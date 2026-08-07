"""Advanced bug reproduction tests for Memory OS.

Focus: Concurrent access, recovery scenarios, data corruption, edge cases.

Bugs to test:
1. Concurrent add_fact() causing duplicate embeddings
2. Recovery from corrupt FAISS index
3. Recovery from missing mapping
4. Partial transaction rollback leaving inconsistent state
5. Dirty flag handling after failed operations
6. Memory leak in FAISS _deleted_ids
"""

from __future__ import annotations

import os
import tempfile
import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from unittest.mock import patch

import pytest
import faiss
import numpy as np

from companion.models import Fact
from companion.memory.store import MemoryStore
from companion.memory.vector_index import VectorIndex
from tests.conftest import make_fact


# ============================================================================
# Bug #1: Concurrent add_fact() causing race conditions
# Severity: P1 (High) - Data corruption
# ============================================================================

class TestConcurrentAddFact:
    """Prove that concurrent add_fact() causes race conditions."""

    def test_concurrent_add_fact_creates_duplicate_embeddings(self, tmp_path, monkeypatch):
        """BUG REPRODUCTION:
        
        Scenario:
        1. Two threads simultaneously add facts with SAME content
        2. Both pass dedup check (before either commits)
        3. Both add embeddings to FAISS
        4. Result: duplicate vectors in FAISS for same content
        
        Expected bug: Race condition in dedup + FAISS update.
        """
        import companion.config as cfg
        monkeypatch.setattr(cfg, "DATA_DIR", str(tmp_path))
        db_path = str(tmp_path / "companion.db")
        monkeypatch.setattr(cfg, "SQLITE_PATH", db_path)
        
        store = MemoryStore()
        
        # Mock embedding to return fixed vector
        from companion.config import EMBEDDING_DIM
        test_vec = np.zeros(EMBEDDING_DIM, dtype=np.float32)
        test_vec[0] = 1.0
        monkeypatch.setattr(store.vector, 'embed_text_only', lambda text: test_vec.tolist())
        
        # Track FAISS updates
        faiss_updates = []
        original_upsert = store.vector.upsert_embedding
        
        def tracking_upsert(text, *args, **kwargs):
            faiss_updates.append(text)
            return original_upsert(text, *args, **kwargs)
        
        # Add same fact from multiple threads
        num_threads = 10
        results = []
        
        def add_fact_thread():
            fact = make_fact("Duplicate content for race condition test", importance=5)
            return store.add_fact(fact)
        
        with patch.object(store.vector, 'upsert_embedding', tracking_upsert):
            with ThreadPoolExecutor(max_workers=num_threads) as executor:
                futures = [executor.submit(add_fact_thread) for _ in range(num_threads)]
                results = [f.result() for f in as_completed(futures)]
        
        # Check for duplicates in results
        unique_ids = set(r.id for r in results)
        
        # BUG CHECK: Should have only 1 unique fact (dedup should work)
        if len(unique_ids) < len(results):
            print(f"WARNING: Dedup may have race condition: {len(results)} results, {len(unique_ids)} unique")
        
        # BUG CHECK: FAISS should have only 1 vector for this content
        content_hash = store.vector._content_hash("Duplicate content for race condition test")
        search_results = store.vector.search("Duplicate content for race condition test", top_k=10)
        
        # If dedup worked, should have only 1 result
        assert len(search_results) <= 1, \
            f"Bug: duplicate vectors in FAISS: {len(search_results)} results for same content"


# ============================================================================
# Bug #2: Recovery from corrupt FAISS index
# Severity: P2 (Medium) - Slow recovery
# ============================================================================

class TestRecoveryFromCorruptIndex:
    """Prove that recovery from corrupt FAISS index works correctly."""

    def test_recovery_rebuilds_index_from_sqlite(self, tmp_path, monkeypatch):
        """BUG REPRODUCTION:
        
        Scenario:
        1. Add facts with embeddings
        2. Corrupt FAISS index file
        3. Restart (create new VectorIndex)
        4. Verify index is rebuilt from SQLite
        
        Expected: All facts become searchable again.
        """
        import companion.config as cfg
        monkeypatch.setattr(cfg, "DATA_DIR", str(tmp_path))
        db_path = str(tmp_path / "companion.db")
        monkeypatch.setattr(cfg, "SQLITE_PATH", db_path)
        
        store = MemoryStore()
        
        # Mock embeddings
        from companion.config import EMBEDDING_DIM
        test_vec = np.zeros(EMBEDDING_DIM, dtype=np.float32)
        test_vec[0] = 1.0
        monkeypatch.setattr(store.vector, 'embed_text_only', lambda text: test_vec.tolist())
        monkeypatch.setattr(store.vector, 'compute_and_cache', 
                         lambda text, *args, **kwargs: store.vector.upsert_embedding(text, test_vec.tolist(), *args, **kwargs))
        
        # Add facts
        added_facts = []
        for i in range(20):
            fact = make_fact(f"Recovery test fact {i}", importance=5)
            result = store.add_fact(fact)
            added_facts.append(result)
        
        # Verify all facts are searchable
        for fact in added_facts:
            results = store.vector.search(fact.fact, top_k=1)
            assert len(results) > 0, f"Fact {fact.id} should be searchable"
        
        # Corrupt FAISS index file
        index_path = store.vector.index_path
        with open(index_path, 'r+b') as f:
            f.seek(0)
            f.write(b'\x00\x00\x00\x00corrupt')
        
        # Create new VectorIndex (simulate restart)
        new_vector = VectorIndex(db=store.db)
        new_vector._load_index()
        
        # Verify index was rebuilt
        assert new_vector._is_initialized, "Index should be initialized after rebuild"
        
        # Verify all facts are searchable again
        for fact in added_facts:
            results = new_vector.search(fact.fact, top_k=1)
            assert len(results) > 0, f"Bug: fact {fact.id} not searchable after recovery"


# ============================================================================
# Bug #3: Dirty flag handling after failed operations
# Severity: P2 (Medium) - Unnecessary rebuilds
# ============================================================================

class TestDirtyFlagHandling:
    """Prove that dirty flag is handled correctly after failures."""

    def test_dirty_flag_not_cleared_on_failed_save(self, tmp_path, monkeypatch):
        """BUG REPRODUCTION:
        
        Scenario:
        1. upsert_embedding() marks dirty
        2. save_index_to_disk() fails (e.g., disk full)
        3. dirty flag should remain "1"
        4. On next restart, rebuild should happen
        
        Expected: dirty flag correctly indicates need for rebuild.
        """
        import companion.config as cfg
        monkeypatch.setattr(cfg, "DATA_DIR", str(tmp_path))
        db_path = str(tmp_path / "companion.db")
        monkeypatch.setattr(cfg, "SQLITE_PATH", db_path)
        
        store = MemoryStore()
        
        # Mock embedding
        from companion.config import EMBEDDING_DIM
        test_vec = np.zeros(EMBEDDING_DIM, dtype=np.float32)
        test_vec[0] = 1.0
        monkeypatch.setattr(store.vector, 'embed_text_only', lambda text: test_vec.tolist())
        
        # Add fact (this marks dirty)
        fact = make_fact("Dirty flag test fact", importance=5)
        store.add_fact(fact)
        
        # Verify dirty flag is set
        dirty = store.db.get_meta("faiss_index_dirty", "0")
        assert dirty == "1", "Dirty flag should be set after embedding update"
        
        # Simulate failed save_index_to_disk()
        original_write = faiss.write_index
        def failing_write(*args, **kwargs):
            raise IOError("Simulated disk full")
        
        with patch.object(faiss, 'write_index', failing_write):
            try:
                store.vector.save_index_to_disk()
            except IOError:
                pass
        
        # Verify dirty flag is still set (save failed)
        dirty_after = store.db.get_meta("faiss_index_dirty", "0")
        assert dirty_after == "1", "Dirty flag should remain after failed save"
        
        # Now save successfully
        store.vector.save_index_to_disk()
        
        # Verify dirty flag is cleared
        dirty_cleared = store.db.get_meta("faiss_index_dirty", "0")
        assert dirty_cleared == "0", "Dirty flag should be cleared after successful save"


# ============================================================================
# Bug #4: Memory leak in FAISS _deleted_ids
# Severity: P3 (Low) - Performance degradation
# ============================================================================

class TestFAISSMemoryLeak:
    """Prove that _deleted_ids handling is correct."""

    def test_deleted_ids_cleared_after_rebuild(self, tmp_path, monkeypatch):
        """BUG REPRODUCTION:
        
        Scenario:
        1. Add facts
        2. Delete facts (if remove_ids() not supported, adds to _deleted_ids)
        3. _rebuild_index() should clear _deleted_ids
        
        Expected: _deleted_ids properly managed.
        """
        import companion.config as cfg
        monkeypatch.setattr(cfg, "DATA_DIR", str(tmp_path))
        db_path = str(tmp_path / "companion.db")
        monkeypatch.setattr(cfg, "SQLITE_PATH", db_path)
        
        store = MemoryStore()
        
        # Mock embeddings — each text gets a HASH-BASED vector. A constant
        # vector makes every fact look identical to the dedup gate (cosine
        # similarity 1.0), so 100 facts collapse into one. Hash-derived
        # vectors keep facts distinct and let the test verify rebuild/cleanup.
        from companion.config import EMBEDDING_DIM

        def _hash_vec(text: str) -> list[float]:
            import hashlib
            h = hashlib.sha256(text.encode("utf-8")).digest()
            return [((h[i % len(h)] % 200) - 100) / 100.0 for i in range(EMBEDDING_DIM)]

        monkeypatch.setattr(store.vector, 'embed_text_only', _hash_vec)
        monkeypatch.setattr(
            store.vector, 'compute_and_cache',
            lambda text, *args, **kwargs: (
                store.vector.upsert_embedding(text, _hash_vec(text), *args, **kwargs),
                _hash_vec(text),
            )[1],
        )

        # Add facts — DISTINCT texts. The dedup gate (text_overlap n-grams >= 0.88)
        # collapses near-identical strings like "Memory leak test fact N" or
        # any shared-phrasing variants into a single fact, so the test must use
        # fully unique strings to verify rebuild/cleanup behavior rather than
        # tripping over dedup.
        import uuid
        added_facts = []
        for i in range(100):
            fact = make_fact(f"memoryleaktestfact-{uuid.uuid4().hex}", importance=5)
            result = store.add_fact(fact)
            added_facts.append(result)

        with store.db._conn() as conn:
            cnt = conn.execute("SELECT count(*) FROM facts WHERE embedding IS NOT NULL").fetchone()[0]
            print("TOTAL NON-NULL AFTER ALL 100 FACTS ADDED:", cnt)

        # Check initial state
        assert len(store.vector._deleted_ids) == 0, "No deleted IDs initially"
        
        # Delete some facts
        for idx, fact in enumerate(added_facts[:50]):
            store.delete_fact(fact.id)
            if idx == 0:
                with store.db._conn() as c:
                    print("NON NULL AFTER DELETING 1 FACT:", c.execute("SELECT count(*) FROM facts WHERE embedding IS NOT NULL").fetchone()[0])
        
        # Verify deletion worked — check the index structures directly.
        # NOTE: with mocked hash-vectors, approximate HNSW search can return a
        # NEIGHBOR vector (random cosine > 0.3) for a deleted fact, so we must
        # assert on the exact content_hash instead of search-result emptiness.
        for fact in added_facts[:50]:
            h = store.vector._content_hash(fact.fact)
            assert h not in store.vector.hash_to_id, \
                f"Fact {fact.id} still mapped in index after deletion"
            assert fact.fact not in store.vector.id_to_content.values(), \
                f"Fact {fact.id} content still present after deletion"
            for r in store.vector.search(fact.fact, top_k=3):
                assert r["content_hash"] != h, \
                    f"Fact {fact.id} still returned by search after deletion"
        
        with store.db._conn() as conn:
            non_null_before = conn.execute("SELECT count(*) FROM facts WHERE embedding IS NOT NULL").fetchone()[0]
            print("NON NULL EMBEDDINGS BEFORE REBUILD:", non_null_before)

        # Rebuild index
        store.vector._rebuild_index()
        
        with store.db._conn() as conn:
            non_null_after = conn.execute("SELECT count(*) FROM facts WHERE embedding IS NOT NULL").fetchone()[0]
            print("NON NULL EMBEDDINGS AFTER REBUILD:", non_null_after)
            print("FAISS NTOTAL:", store.vector.index.ntotal)

        # Verify state is consistent
        assert len(store.vector._deleted_ids) == 0, \
            "Bug: _deleted_ids not cleared after rebuild"
        
        # Verify all remaining facts are still mapped in the index
        for fact in added_facts[50:]:
            h = store.vector._content_hash(fact.fact)
            assert h in store.vector.hash_to_id, \
                f"Fact {fact.id} lost from index after rebuild"
            assert any(r["content_hash"] == h for r in store.vector.search(fact.fact, top_k=3)), \
                f"Fact {fact.id} should be searchable"


# ============================================================================
# Bug #5: Partial transaction rollback inconsistency
# Severity: P1 (High) - Data inconsistency
# ============================================================================

class TestPartialRollback:
    """Prove that partial transaction rollback leaves inconsistent state."""

    def test_faiss_updated_but_sqlite_rolled_back(self, tmp_path, monkeypatch):
        """BUG REPRODUCTION:
        
        Scenario:
        1. add_fact() starts transaction
        2. Fact inserted into SQLite
        3. FAISS updated
        4. Transaction rolls back (error after FAISS update)
        5. Result: FAISS has vector, SQLite doesn't
        
        Expected bug: FAISS and SQLite become inconsistent.
        """
        import companion.config as cfg
        monkeypatch.setattr(cfg, "DATA_DIR", str(tmp_path))
        db_path = str(tmp_path / "companion.db")
        monkeypatch.setattr(cfg, "SQLITE_PATH", db_path)
        
        store = MemoryStore()
        
        # Mock embeddings
        from companion.config import EMBEDDING_DIM
        test_vec = np.zeros(EMBEDDING_DIM, dtype=np.float32)
        test_vec[0] = 1.0
        monkeypatch.setattr(store.vector, 'embed_text_only', lambda text: test_vec.tolist())
        
        # Track FAISS updates
        faiss_updated = False
        original_upsert = store.vector.upsert_embedding
        
        def tracking_upsert(text, vec, *args, **kwargs):
            nonlocal faiss_updated
            faiss_updated = True
            return original_upsert(text, vec, *args, **kwargs)
        
        # Simulate error after FAISS update
        fact = make_fact("Partial rollback test fact", importance=5)
        
        with patch.object(store.vector, 'upsert_embedding', tracking_upsert):
            try:
                with store.db.atomic_memory_transaction():
                    store.db._insert_fact(fact.to_dict())
                    store.vector.upsert_embedding(fact.fact, test_vec.tolist(), fact_id=fact.id)
                    raise Exception("Simulated error after FAISS update")
            except Exception:
                pass
        
        # BUG CHECK: Did FAISS update happen before rollback?
        # If upsert_embedding was called inside transaction, it should be rolled back
        # But FAISS doesn't support rollback!
        
        # Check if fact exists in SQLite
        saved_fact = store.db.get_fact(fact.id)
        
        # BUG: If FAISS was updated but SQLite rolled back, we have inconsistency
        if saved_fact is None and faiss_updated:
            # Search should not find it (FAISS has it but SQLite doesn't)
            search_results = store.vector.search(fact.fact, top_k=1)
            if len(search_results) > 0:
                pytest.fail("Bug: FAISS has vector but SQLite doesn't (inconsistent state)")


if __name__ == "__main__":
    pytest.main([__file__, "-v", "--tb=short"])
