"""Tests for FAISS index performance and correctness (avoiding full rebuilds on search)."""
from __future__ import annotations

from unittest.mock import patch, MagicMock
import pytest

from companion.memory.store import MemoryStore
from companion.models import Fact, FactRelation


def test_faiss_rebuild_avoidance_on_search(tmp_path):
    """Test that multiple search calls do not rebuild the index from SQLite."""
    import companion.config as cfg
    original_data_dir = cfg.DATA_DIR
    original_sqlite = cfg.SQLITE_PATH
    cfg.DATA_DIR = str(tmp_path)
    cfg.SQLITE_PATH = str(tmp_path / "companion.db")
    
    try:
        # Create memory store
        store = MemoryStore()
        store.vector.embeddings_enabled = True
        
        # Now spy on VectorIndex._load_index
        with patch.object(store.vector, "_load_index") as spy_load_index, \
             patch("companion.memory.vector_index._embed_texts", return_value=[[0.1] * 768]):
            store.vector.embeddings_enabled = True
            # Add a dummy fact to sqlite/FAISS
            fact = Fact(
                id="test_perf_1",
                fact="Иван любит программировать на Python",
                date="2026-07-01",
                importance=8,
                confidence=0.9,
                source="test",
                source_type="test",
                memory_kind="permanent",
                tags=["coding"],
                status="active"
            )
            store.add_fact(fact)
            
            # Perform 5 search queries
            for _ in range(5):
                results = store.vector.search("Python", top_k=5)
            
            # Since the index was already initialized, it should NOT rebuild it during search queries!
            spy_load_index.assert_not_called()
            
    finally:
        cfg.DATA_DIR = original_data_dir
        cfg.SQLITE_PATH = original_sqlite


def test_faiss_correctness_after_add_and_delete(tmp_path):
    """Test that adding and deleting facts updates the in-memory index correctly."""
    import companion.config as cfg
    original_data_dir = cfg.DATA_DIR
    original_sqlite = cfg.SQLITE_PATH
    cfg.DATA_DIR = str(tmp_path)
    cfg.SQLITE_PATH = str(tmp_path / "companion.db")
    
    try:
        store = MemoryStore()
        store.vector.embeddings_enabled = True
        with patch("companion.memory.vector_index._embed_texts", lambda texts: [[0.1] * 768 for _ in texts]):
            store.vector.embeddings_enabled = True
            # 1. Add first fact
            fact1 = Fact(
                id="perf_test_fact1",
                fact="У Ивана есть собака Морзик",
                date="2026-07-01",
                importance=9,
                confidence=0.9,
                source="test",
                source_type="test",
                memory_kind="event",
                tags=["dog"],
                status="active"
            )
            store.add_fact(fact1)
            
            # Search should find it
            res = store.vector.search("Морзик", top_k=5)
            assert len(res) >= 1
            assert res[0]["content"] == "У Ивана есть собака Морзик"
            
            # 2. Add second fact (supersedes first)
            fact2 = Fact(
                id="perf_test_fact2",
                fact="У Ивана раньше была собака Морзик, но сейчас ее нет",
                date="2026-07-01",
                importance=9,
                confidence=0.9,
                source="test",
                source_type="test",
                memory_kind="event",
                tags=["dog"],
                status="active"
            )
            store.add_fact(fact2)
            
            # Add relation (supersedes)
            rel = FactRelation(
                from_id=fact2.id,
                to_id=fact1.id,
                relation="supersedes",
                reason="newer status",
                confidence=1.0
            )
            store.add_relation(rel)
            
            # The search for 'Морзик' should only find the active fact2, and fact1 should be deleted from the index!
            res_after = store.vector.search("Морзик", top_k=5)
            contents = [r["content"] for r in res_after]
            
            assert "У Ивана раньше была собака Морзик, но сейчас ее нет" in contents
            assert "У Ивана есть собака Морзик" not in contents
        
    finally:
        cfg.DATA_DIR = original_data_dir
        cfg.SQLITE_PATH = original_sqlite
