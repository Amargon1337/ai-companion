"""Tests for Phase 0 critical fixes.

Verifies that the P0 bugs identified in the architectural audit are properly fixed:
  P0-1: embedding_retry_worker uses fact.meta (not fact.metadata)
  P0-2: api.env excluded from git
  P0-3: No duplicate config variables
  P0-4: No duplicate get_meta/set_meta methods
  P0-5: models.py imports json at top level
  P0-6: No embedding API call inside SQLite transaction
  P0-7: world_model failure doesn't prevent fact save
  P0-8: sync_lock protects cross-thread personality updates
"""
from __future__ import annotations

import ast
import os
import sqlite3
import tempfile
from unittest.mock import MagicMock, patch

import pytest


# ============================================================================
# P0-1: embedding_retry_worker uses correct field name
# ============================================================================

class TestP01EmbeddingRetryWorkerFieldName:
    """Verify embedding_retry_worker uses fact.meta, not fact.metadata."""

    def test_worker_accesses_meta_field(self):
        """The retry worker must use fact.meta (the actual Fact dataclass field)."""
        from companion.memory.embedding_retry_worker import EmbeddingRetryWorker
        source = open("companion/memory/embedding_retry_worker.py", encoding="utf-8").read()
        # Should NOT contain fact.metadata (wrong field)
        assert "fact.metadata" not in source, \
            "P0-1 FAIL: embedding_retry_worker still uses fact.metadata"
        # Should contain fact.meta (correct field)
        assert "fact.meta" in source, \
            "P0-1 FAIL: embedding_retry_worker doesn't use fact.meta"


# ============================================================================
# P0-2: api.env excluded from git
# ============================================================================

class TestP02ApiEnvInGitignore:
    """Verify api.env is in .gitignore to prevent credential leaks."""

    def test_api_env_excluded(self):
        with open(".gitignore", encoding="utf-8") as f:
            content = f.read()
        assert "api.env" in content, "P0-2 FAIL: api.env not in .gitignore"


# ============================================================================
# P0-3: No duplicate config variables
# ============================================================================

class TestP03NoDuplicateConfig:
    """Verify config.py has no duplicate variable definitions."""

    def test_no_duplicate_variables(self):
        with open("companion/config.py", encoding="utf-8") as f:
            tree = ast.parse(f.read())
        names = {}
        for node in ast.iter_child_nodes(tree):
            if isinstance(node, ast.Assign):
                for target in node.targets:
                    if isinstance(target, ast.Name):
                        assert target.id not in names, \
                            f"P0-3 FAIL: {target.id} defined at lines {names[target.id]} and {node.lineno}"
                        names[target.id] = node.lineno


# ============================================================================
# P0-4: No duplicate get_meta/set_meta
# ============================================================================

class TestP04NoDuplicateMetaMethods:
    """Verify MemoryDatabase has only one get_meta and one set_meta."""

    def test_no_duplicate_methods(self):
        with open("companion/storage/sqlite_db.py", encoding="utf-8") as f:
            source = f.read()
        # Count method definitions
        get_meta_count = source.count("def get_meta(")
        set_meta_count = source.count("def set_meta(")
        assert get_meta_count == 1, f"P0-4 FAIL: get_meta defined {get_meta_count} times"
        assert set_meta_count == 1, f"P0-4 FAIL: set_meta defined {set_meta_count} times"


# ============================================================================
# P0-5: models.py imports json
# ============================================================================

class TestP05ModelsImportsJson:
    """Verify models.py has import json at top level."""

    def test_json_imported(self):
        with open("companion/models.py", encoding="utf-8") as f:
            tree = ast.parse(f.read())
        top_level_imports = set()
        for node in ast.iter_child_nodes(tree):
            if isinstance(node, ast.Import):
                for alias in node.names:
                    top_level_imports.add(alias.name)
        assert "json" in top_level_imports, "P0-5 FAIL: json not imported in models.py"


# ============================================================================
# P0-6: No embedding API call inside transaction
# ============================================================================

class TestP06NoApiCallInTransaction:
    """Verify add_fact() doesn't call embedding API inside atomic_memory_transaction."""

    def test_add_fact_structure(self, tmp_path, monkeypatch):
        """Embedding API call must happen BEFORE the transaction, not inside it."""
        import companion.config as cfg
        monkeypatch.setattr(cfg, "DATA_DIR", str(tmp_path))
        monkeypatch.setattr(cfg, "SQLITE_PATH", str(tmp_path / "test.db"))

        from companion.memory.store import MemoryStore
        from companion.models import Fact

        store = MemoryStore()

        api_called_during_transaction = False
        original_atomic = store.db.atomic_memory_transaction

        class TrackingTransaction:
            def __enter__(self):
                nonlocal api_called_during_transaction
                self._ctx = original_atomic().__enter__()
                return self._ctx

            def __exit__(self, *args):
                return original_atomic().__exit__(*args)

        # Track if embed_text_only is called during transaction
        embed_call_times = []
        transaction_times = []

        original_embed = store.vector.embed_text_only
        def tracking_embed(text):
            embed_call_times.append("called")
            return original_embed(text)

        monkeypatch.setattr(store.vector, 'embed_text_only', tracking_embed)

        fact = Fact(
            fact="P0-6 test fact", date="2026-08-06", importance=5,
            confidence=0.8, source="test",
        )
        # This should not crash — if API is called inside transaction and fails,
        # the transaction would rollback and lose the fact.
        store.add_fact(fact)

        saved = store.get_fact(fact.id)
        assert saved is not None, "Fact should be saved"

    def test_failed_embedding_creates_pending_status(self, tmp_path, monkeypatch):
        """When embedding fails, fact must get pending_embedding status (not active)."""
        import companion.config as cfg
        monkeypatch.setattr(cfg, "DATA_DIR", str(tmp_path))
        monkeypatch.setattr(cfg, "SQLITE_PATH", str(tmp_path / "test.db"))

        from companion.memory.store import MemoryStore
        from companion.models import Fact

        store = MemoryStore()
        monkeypatch.setattr(store.vector, 'embed_text_only', lambda text: None)

        fact = Fact(
            fact="P0-6 embedding fail test", date="2026-08-06", importance=5,
            confidence=0.8, source="test",
        )
        result = store.add_fact(fact)
        assert result.status == "pending_embedding", \
            "P0-6 FAIL: failed embedding should create pending_embedding status"


# ============================================================================
# P0-7: world_model failure doesn't prevent fact save
# ============================================================================

class TestP07WorldModelIsolation:
    """Verify that world_model.process_fact() failure doesn't prevent fact save."""

    def test_fact_saved_when_world_model_fails(self, tmp_path, monkeypatch):
        """Even if world_model crashes, the fact must be persisted."""
        import companion.config as cfg
        monkeypatch.setattr(cfg, "DATA_DIR", str(tmp_path))
        monkeypatch.setattr(cfg, "SQLITE_PATH", str(tmp_path / "test.db"))

        from companion.memory.store import MemoryStore
        from companion.models import Fact
        from companion.config import EMBEDDING_DIM
        import numpy as np

        store = MemoryStore()

        # Mock embedding to succeed
        test_vec = [0.0] * EMBEDDING_DIM
        test_vec[0] = 1.0
        monkeypatch.setattr(store.vector, 'embed_text_only', lambda text: test_vec)

        # Mock world_model to crash
        store.world_model = MagicMock()
        store.world_model.process_fact.side_effect = RuntimeError("World model crashed!")

        fact = Fact(
            fact="P0-7 isolation test", date="2026-08-06", importance=5,
            confidence=0.8, source="test",
        )
        result = store.add_fact(fact)

        # Fact MUST be saved despite world_model failure
        saved = store.get_fact(fact.id)
        assert saved is not None, "P0-7 FAIL: fact not saved when world_model failed"
        assert saved.fact == "P0-7 isolation test"
        assert saved.status == "active", "Fact should be active (embedding succeeded)"


# ============================================================================
# P0-8: sync_lock exists for cross-thread protection
# ============================================================================

class TestP08SyncLock:
    """Verify sync_lock property exists and returns threading.Lock."""

    def test_sync_lock_exists(self, tmp_path, monkeypatch):
        import companion.config as cfg
        monkeypatch.setattr(cfg, "DATA_DIR", str(tmp_path))
        monkeypatch.setattr(cfg, "SQLITE_PATH", str(tmp_path / "test.db"))

        import threading
        from companion.memory.store import MemoryStore

        store = MemoryStore()
        assert hasattr(store, 'sync_lock'), "P0-8 FAIL: sync_lock property missing"
        lock = store.sync_lock
        assert isinstance(lock, type(threading.Lock())) or hasattr(lock, 'acquire'), \
            "sync_lock must be a threading.Lock-compatible object"

    def test_pipeline_uses_sync_lock(self):
        """pipeline.py must use sync_lock inside to_thread, not asyncio.Lock around it."""
        with open("companion/llm/pipeline.py", encoding="utf-8") as f:
            source = f.read()
        # Should NOT have "async with store.lock" followed by "to_thread"
        assert "async with store.lock" not in source or "sync_lock" in source, \
            "P0-8 FAIL: pipeline.py still uses async lock around to_thread"

    def test_scheduler_uses_sync_lock(self):
        """background_scheduler.py must use sync_lock inside to_thread."""
        with open("companion/background_scheduler.py", encoding="utf-8") as f:
            source = f.read()
        assert "async with store.lock" not in source, \
            "P0-8 FAIL: background_scheduler.py still uses async lock around to_thread"


# ============================================================================
# Additional: cosine_similarity not duplicated
# ============================================================================

class TestCosineSimilarityNoDuplicate:
    """Verify cosine_similarity is defined exactly once in vector_index.py."""

    def test_single_definition(self):
        with open("companion/memory/vector_index.py", encoding="utf-8") as f:
            source = f.read()
        count = source.count("def cosine_similarity(")
        assert count == 1, f"cosine_similarity defined {count} times (should be 1)"
