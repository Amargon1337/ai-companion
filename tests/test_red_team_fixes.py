"""Tests for Red Team Audit fixes:
- FactArchivedEvent publication on archive_fact
- Lifecycle guards on terminal states and restore_fact
- Russian negation awareness in deduplication
- FTS/token-overlap reflection search without FAISS self-contamination
"""
from __future__ import annotations

import pytest
from companion.memory.store import MemoryStore
from companion.models import Fact, Reflection
from companion.memory.events.base import FactArchivedEvent, FactUpdatedEvent


def make_fact(fact: str = "base fact", status: str = "active", **kwargs) -> Fact:
    return Fact(
        fact=fact,
        date="2026-07-07",
        importance=kwargs.get("importance", 5),
        confidence=kwargs.get("confidence", 0.8),
        source=kwargs.get("source", "test"),
        source_type=kwargs.get("source_type", "test"),
        memory_kind=kwargs.get("memory_kind", "event"),
        tags=kwargs.get("tags", []),
        evidence=kwargs.get("evidence", []),
        status=status,
    )


class TestFactArchivedEventFix:
    def test_archive_fact_publishes_archived_event(self, tmp_path, monkeypatch):
        import companion.config as cfg
        cfg.DATA_DIR = str(tmp_path)
        cfg.SQLITE_PATH = str(tmp_path / "companion.db")
        store = MemoryStore()

        events_published = []

        class MockEventBus:
            def publish(self, event):
                events_published.append(event)

        store.event_bus = MockEventBus()

        fact = make_fact(fact="Fact to archive")
        store.add_fact(fact)
        events_published.clear()

        archived = store.archive_fact(fact.id, reason="test_archive")
        assert archived is True

        # Ensure both FactUpdatedEvent and FactArchivedEvent were published
        updated_events = [e for e in events_published if isinstance(e, FactUpdatedEvent)]
        archived_events = [e for e in events_published if isinstance(e, FactArchivedEvent)]

        assert len(updated_events) == 1
        assert len(archived_events) == 1
        assert archived_events[0].fact_id == fact.id
        assert archived_events[0].fact_text == "Fact to archive"
        assert archived_events[0].reason == "test_archive"


class TestLifecycleGuardFix:
    def test_update_fact_blocks_archived_fact(self, tmp_path, monkeypatch):
        import companion.config as cfg
        cfg.DATA_DIR = str(tmp_path)
        cfg.SQLITE_PATH = str(tmp_path / "companion.db")
        store = MemoryStore()

        fact = make_fact(fact="Terminal fact")
        store.add_fact(fact)
        store.archive_fact(fact.id)

        assert store.update_fact(fact.id, fact="Trying to update archived fact") is False

        with pytest.raises(ValueError, match="Illegal lifecycle transition"):
            store.update_fact(fact.id, status="active")

    def test_restore_fact_reactivates_archived(self, tmp_path, monkeypatch):
        import companion.config as cfg
        cfg.DATA_DIR = str(tmp_path)
        cfg.SQLITE_PATH = str(tmp_path / "companion.db")
        store = MemoryStore()

        fact = make_fact(fact="Restorable fact")
        store.add_fact(fact)
        store.archive_fact(fact.id)

        archived_row = store.get_fact(fact.id)
        assert archived_row.status == "archived"

        restored = store.restore_fact(fact.id, reason="manual_restore")
        assert restored is True
        active_row = store.get_fact(fact.id)
        assert active_row.status == "active"


class TestNegationDedupFix:
    def test_negation_distinguished_in_dedup(self, tmp_path, monkeypatch):
        import companion.config as cfg
        cfg.DATA_DIR = str(tmp_path)
        cfg.SQLITE_PATH = str(tmp_path / "companion.db")
        store = MemoryStore()

        pos_fact = make_fact(fact="Иван любит чай")
        store.add_fact(pos_fact)

        # "Иван не любит чай" should NOT be seen as duplicate of "Иван любит чай"
        sim = store.find_similar_fact_any_status("Иван не любит чай")
        assert sim is None, f"Expected None, got {sim.fact if sim else None}"


class TestReflectionSearchFix:
    def test_reflection_search_uses_token_relevance(self, tmp_path, monkeypatch):
        import companion.config as cfg
        cfg.DATA_DIR = str(tmp_path)
        cfg.SQLITE_PATH = str(tmp_path / "companion.db")
        store = MemoryStore()

        ref1 = Reflection(
            id="ref_1",
            insight="Иван любит програмировать на Python в свободное время",
            based_on=[],
            period="2026-07",
            importance=5,
            confidence=0.9,
            created_at="2026-07-07T10:00:00",
            status="active",
        )
        ref2 = Reflection(
            id="ref_2",
            insight="Собаку зовут Морзик и она любит гулять",
            based_on=[],
            period="2026-07",
            importance=5,
            confidence=0.8,
            created_at="2026-07-07T10:01:00",
            status="active",
        )
        store.add_reflection(ref1)
        store.add_reflection(ref2)

        # Search without hitting FAISS
        results = store.search_reflections("програмировать Python", limit=5)
        assert len(results) >= 1
        assert results[0].id == "ref_1"
