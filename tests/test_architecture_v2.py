"""Tests for new architectural components:
  - Repository layer (FactRepository, EntityRepository, MessageRepository)
  - AppContainer (DI)
  - Memory Explainability API
  - Contradiction Engine
  - LLM Provider abstraction
"""
from __future__ import annotations

import os
import sqlite3
import tempfile

import pytest


# ============================================================================
# Repository Layer
# ============================================================================

class TestFactRepository:
    """Verify FactRepository works correctly as a decomposed layer."""

    def test_insert_and_get_fact(self, tmp_path, monkeypatch):
        """Insert a fact via repository, retrieve it back."""
        import companion.config as cfg
        monkeypatch.setattr(cfg, "DATA_DIR", str(tmp_path))
        monkeypatch.setattr(cfg, "SQLITE_PATH", str(tmp_path / "test.db"))

        from companion.storage.sqlite_db import MemoryDatabase
        from companion.storage.repositories.fact_repository import FactRepository
        from companion.models import Fact

        db = MemoryDatabase(str(tmp_path / "test.db"))
        repo = FactRepository(db)

        fact = Fact(
            id="f-repo-1",
            fact="Repository test fact",
            date="2026-08-06",
            importance=7,
            confidence=0.9,
            source="test",
        )
        repo.insert_fact(fact.to_dict())

        result = repo.get_fact("f-repo-1")
        assert result is not None
        assert result["fact"] == "Repository test fact"
        assert result["importance"] == 7
        assert result["status"] == "active"
        db.close()

    def test_list_facts_by_status(self, tmp_path, monkeypatch):
        """List facts filtered by status."""
        import companion.config as cfg
        monkeypatch.setattr(cfg, "DATA_DIR", str(tmp_path))
        monkeypatch.setattr(cfg, "SQLITE_PATH", str(tmp_path / "test.db"))

        from companion.storage.sqlite_db import MemoryDatabase
        from companion.storage.repositories.fact_repository import FactRepository

        db = MemoryDatabase(str(tmp_path / "test.db"))
        repo = FactRepository(db)

        # Insert two facts with different statuses
        repo.insert_fact({
            "id": "f-active", "fact": "Active fact", "status": "active",
            "importance": 5, "confidence": 0.8,
        })
        repo.insert_fact({
            "id": "f-dormant", "fact": "Dormant fact", "status": "dormant",
            "importance": 3, "confidence": 0.5,
        })

        active = repo.list_facts("active")
        assert len(active) == 1
        assert active[0]["id"] == "f-active"

        dormant = repo.list_facts("dormant")
        assert len(dormant) == 1
        assert dormant[0]["id"] == "f-dormant"

        all_facts = repo.list_facts(None)
        assert len(all_facts) == 2
        db.close()

    def test_update_fact_fields_with_occ(self, tmp_path, monkeypatch):
        """OCC: concurrent modification detected via version check."""
        import companion.config as cfg
        monkeypatch.setattr(cfg, "DATA_DIR", str(tmp_path))
        monkeypatch.setattr(cfg, "SQLITE_PATH", str(tmp_path / "test.db"))

        from companion.storage.sqlite_db import MemoryDatabase
        from companion.storage.repositories.fact_repository import FactRepository
        from companion.exceptions import ConcurrentModificationError

        db = MemoryDatabase(str(tmp_path / "test.db"))
        repo = FactRepository(db)

        repo.insert_fact({
            "id": "f-occ", "fact": "OCC test", "status": "active",
            "importance": 5, "confidence": 0.8, "version": 1,
        })

        # Update with correct version → succeeds
        repo.update_fact_fields("f-occ", {"importance": 8}, expected_version=1)
        updated = repo.get_fact("f-occ")
        assert updated["importance"] == 8
        assert updated["version"] == 2

        # Update with stale version → raises
        with pytest.raises(ConcurrentModificationError):
            repo.update_fact_fields("f-occ", {"importance": 10}, expected_version=1)

        # Fact unchanged
        unchanged = repo.get_fact("f-occ")
        assert unchanged["importance"] == 8
        db.close()

    def test_delete_fact_cleans_relations(self, tmp_path, monkeypatch):
        """Deleting a fact also removes its relations."""
        import companion.config as cfg
        monkeypatch.setattr(cfg, "DATA_DIR", str(tmp_path))
        monkeypatch.setattr(cfg, "SQLITE_PATH", str(tmp_path / "test.db"))

        from companion.storage.sqlite_db import MemoryDatabase
        from companion.storage.repositories.fact_repository import FactRepository

        db = MemoryDatabase(str(tmp_path / "test.db"))
        repo = FactRepository(db)

        repo.insert_fact({"id": "f1", "fact": "Fact 1", "status": "active", "importance": 5, "confidence": 0.8})
        repo.insert_fact({"id": "f2", "fact": "Fact 2", "status": "active", "importance": 5, "confidence": 0.8})
        repo.insert_relation({"id": "r1", "from_id": "f1", "to_id": "f2", "relation": "related_to"})

        assert len(repo.get_fact_relations("f1")) == 1

        repo.delete_fact("f1")
        assert repo.get_fact("f1") is None
        assert len(repo.get_fact_relations("f1")) == 0
        db.close()


class TestEntityRepository:
    """Verify EntityRepository works correctly."""

    def test_create_and_get_entity(self, tmp_path, monkeypatch):
        import companion.config as cfg
        monkeypatch.setattr(cfg, "DATA_DIR", str(tmp_path))
        monkeypatch.setattr(cfg, "SQLITE_PATH", str(tmp_path / "test.db"))

        from companion.storage.sqlite_db import MemoryDatabase
        from companion.storage.repositories.entity_repository import EntityRepository

        db = MemoryDatabase(str(tmp_path / "test.db"))
        repo = EntityRepository(db)

        entity_id = repo.upsert_entity({
            "entity_id": "ent-test-1",
            "name": "TestEntity",
            "type": "person",
            "importance": 0.9,
        })
        assert entity_id == "ent-test-1"

        entity = repo.get_entity("ent-test-1")
        assert entity is not None
        assert entity["name"] == "TestEntity"
        assert entity["type"] == "person"
        db.close()

    def test_add_mention_and_retrieve(self, tmp_path, monkeypatch):
        import companion.config as cfg
        monkeypatch.setattr(cfg, "DATA_DIR", str(tmp_path))
        monkeypatch.setattr(cfg, "SQLITE_PATH", str(tmp_path / "test.db"))

        from companion.storage.sqlite_db import MemoryDatabase
        from companion.storage.repositories.entity_repository import EntityRepository

        db = MemoryDatabase(str(tmp_path / "test.db"))
        repo = EntityRepository(db)

        repo.upsert_entity({
            "entity_id": "ent-mention",
            "name": "MentionEntity",
            "type": "concept",
            "importance": 0.5,
        })

        # Insert a fact to link to
        db._conn().__enter__().execute(
            "INSERT INTO facts (id, fact, status, importance, confidence) VALUES (?, ?, ?, ?, ?)",
            ("f-mention-link", "Fact mentioning entity", "active", 5, 0.8),
        )
        db._conn().__enter__().commit()

        mention_id = repo.add_mention({
            "entity_id": "ent-mention",
            "fact_id": "f-mention-link",
            "context_snippet": "Test context",
        })
        assert mention_id > 0

        mentions = repo.get_mentions_for_entity("ent-mention")
        assert len(mentions) == 1
        assert mentions[0]["fact_id"] == "f-mention-link"
        db.close()


class TestMessageRepository:
    """Verify MessageRepository works correctly."""

    def test_insert_and_list(self, tmp_path, monkeypatch):
        import companion.config as cfg
        monkeypatch.setattr(cfg, "DATA_DIR", str(tmp_path))
        monkeypatch.setattr(cfg, "SQLITE_PATH", str(tmp_path / "test.db"))

        from companion.storage.sqlite_db import MemoryDatabase
        from companion.storage.repositories.message_repository import MessageRepository

        db = MemoryDatabase(str(tmp_path / "test.db"))
        repo = MessageRepository(db)

        repo.insert({
            "id": "msg-1", "ts": "2026-08-06T10:00:00",
            "role": "user", "text": "Hello", "importance": 5,
        })
        repo.insert({
            "id": "msg-2", "ts": "2026-08-06T10:01:00",
            "role": "model", "text": "Hi there", "importance": 4,
        })

        msgs = repo.list_messages(min_importance=0)
        assert len(msgs) == 2

        important = repo.list_messages(min_importance=5)
        assert len(important) == 1
        assert important[0]["text"] == "Hello"
        db.close()


# ============================================================================
# DI Container
# ============================================================================

class TestAppContainer:
    """Verify AppContainer lazy initialization."""

    def test_container_creates_db(self, tmp_path, monkeypatch):
        import companion.config as cfg
        monkeypatch.setattr(cfg, "DATA_DIR", str(tmp_path))
        monkeypatch.setattr(cfg, "SQLITE_PATH", str(tmp_path / "test.db"))

        from companion.container import AppContainer, AppConfig

        config = AppConfig(
            sqlite_path=str(tmp_path / "test.db"),
            data_dir=str(tmp_path),
        )
        container = AppContainer(config=config)

        # Lazy init: db created on first access
        assert container._db is None
        db = container.db
        assert db is not None
        assert container._db is db  # cached

        container.close()

    def test_container_creates_repositories(self, tmp_path, monkeypatch):
        import companion.config as cfg
        monkeypatch.setattr(cfg, "DATA_DIR", str(tmp_path))
        monkeypatch.setattr(cfg, "SQLITE_PATH", str(tmp_path / "test.db"))

        from companion.container import AppContainer, AppConfig

        config = AppConfig(
            sqlite_path=str(tmp_path / "test.db"),
            data_dir=str(tmp_path),
        )
        container = AppContainer(config=config)

        facts = container.facts
        entities = container.entities
        messages = container.messages

        from companion.storage.repositories import FactRepository, EntityRepository, MessageRepository
        assert isinstance(facts, FactRepository)
        assert isinstance(entities, EntityRepository)
        assert isinstance(messages, MessageRepository)

        container.close()


# ============================================================================
# Contradiction Engine
# ============================================================================

class TestContradictionEngine:
    """Verify contradiction detection between facts."""

    def test_negation_conflict_detected(self, tmp_path, monkeypatch):
        """'Иван курит' vs 'Иван не курит' should be detected as contradiction."""
        import companion.config as cfg
        monkeypatch.setattr(cfg, "DATA_DIR", str(tmp_path))
        monkeypatch.setattr(cfg, "SQLITE_PATH", str(tmp_path / "test.db"))

        from companion.memory.store import MemoryStore
        from companion.memory.contradiction import check_contradictions
        from companion.models import Fact
        from companion.config import EMBEDDING_DIM
        import numpy as np

        store = MemoryStore()
        # Mock embedding to succeed so facts get 'active' status
        test_vec = [0.0] * EMBEDDING_DIM
        test_vec[0] = 1.0
        monkeypatch.setattr(store.vector, 'embed_text_only', lambda text: test_vec)

        # Insert existing fact
        existing = Fact(
            id="f-smokes",
            fact="Иван курит",
            date="2026-01-01",
            importance=7,
            confidence=0.8,
            source="test",
        )
        store.add_fact(existing)

        # Verify fact is active
        saved = store.get_fact("f-smokes")
        assert saved is not None and saved.status == "active"

        # Check contradiction with negated version
        result = check_contradictions(store, "Иван не курит")
        assert len(result.conflicts) > 0, "Should detect negation conflict"
        assert result.conflicts[0].conflict_type == "negation_opposite"
        store.close()

    def test_no_conflict_for_unrelated(self, tmp_path, monkeypatch):
        """Completely different facts should not conflict."""
        import companion.config as cfg
        monkeypatch.setattr(cfg, "DATA_DIR", str(tmp_path))
        monkeypatch.setattr(cfg, "SQLITE_PATH", str(tmp_path / "test.db"))

        from companion.memory.store import MemoryStore
        from companion.memory.contradiction import check_contradictions
        from companion.models import Fact

        store = MemoryStore()
        monkeypatch.setattr(store.vector, 'embed_text_only', lambda text: None)

        existing = Fact(
            id="f-python",
            fact="Иван любит Python",
            date="2026-01-01",
            importance=5,
            confidence=0.8,
            source="test",
        )
        store.add_fact(existing)

        result = check_contradictions(store, "Сегодня хорошая погода на улице")
        assert len(result.conflicts) == 0, "Unrelated facts should not conflict"
        store.close()


# ============================================================================
# Memory Explainability
# ============================================================================

class TestExplainability:
    """Verify Memory Explainability API."""

    def test_explain_fact(self, tmp_path, monkeypatch):
        """explain_memory() returns full provenance for a fact."""
        import companion.config as cfg
        monkeypatch.setattr(cfg, "DATA_DIR", str(tmp_path))
        monkeypatch.setattr(cfg, "SQLITE_PATH", str(tmp_path / "test.db"))

        from companion.memory.store import MemoryStore
        from companion.memory.explainability import explain_memory
        from companion.models import Fact
        from companion.config import EMBEDDING_DIM

        store = MemoryStore()
        # Mock embedding to succeed so fact gets 'active' status
        test_vec = [0.0] * EMBEDDING_DIM
        test_vec[0] = 1.0
        monkeypatch.setattr(store.vector, 'embed_text_only', lambda text: test_vec)

        fact = Fact(
            id="f-explain-1",
            fact="Иван работает QA инженером",
            date="2026-01-15",
            importance=8,
            confidence=0.9,
            source="user_stated",
            tags=["core_identity"],
        )
        store.add_fact(fact)

        result = explain_memory(store, "f-explain-1")
        assert result is not None
        assert result["entity_type"] == "fact"
        assert result["text"] == "Иван работает QA инженером"
        assert result["status"] == "active"
        assert result["confidence"] == 0.9
        assert result["importance"] == 8
        assert "core_identity" in result["tags"]
        store.close()

    def test_explain_nonexistent(self, tmp_path, monkeypatch):
        """explain_memory() returns error for unknown entity."""
        import companion.config as cfg
        monkeypatch.setattr(cfg, "DATA_DIR", str(tmp_path))
        monkeypatch.setattr(cfg, "SQLITE_PATH", str(tmp_path / "test.db"))

        from companion.memory.store import MemoryStore
        from companion.memory.explainability import explain_memory

        store = MemoryStore()
        result = explain_memory(store, "f-nonexistent-xyz")
        assert "error" in result
        store.close()


# ============================================================================
# LLM Provider
# ============================================================================

class TestLLMProviderProtocol:
    """Verify LLM provider protocol is correctly defined."""

    def test_protocol_defined(self):
        """LLMProvider protocol should be importable."""
        from companion.llm.provider import LLMProvider, EmbeddingProvider
        assert LLMProvider is not None
        assert EmbeddingProvider is not None

    def test_llm_config(self):
        """LLMConfig should have sensible defaults."""
        from companion.llm.provider import LLMConfig, EmbeddingConfig
        config = LLMConfig()
        assert config.model == "gemini-3.5-flash-lite"
        assert config.temperature == 0.7
        assert config.retries == 3

        emb_config = EmbeddingConfig()
        assert emb_config.dimension == 768

    def test_factory_unknown_provider(self):
        """Factory should raise for unknown provider type."""
        from companion.llm.provider import create_llm_provider
        with pytest.raises(ValueError, match="Unknown"):
            create_llm_provider("unknown_provider")
