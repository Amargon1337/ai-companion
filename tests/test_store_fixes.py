"""Tests for the targeted memory-store fixes only."""
from __future__ import annotations

from datetime import datetime

from companion.memory.store import MemoryStore
from companion.models import Fact, FactRelation


def make_fact(*, fact: str = "base fact", status: str = "active", **kwargs) -> Fact:
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


class TestContradictionFix:
    def test_contradiction_marks_old_fact_pending_review(self, tmp_path, monkeypatch):
        import companion.config as cfg
        from companion.storage.sqlite_db import MemoryDatabase

        cfg.DATA_DIR = str(tmp_path)
        cfg.SQLITE_PATH = str(tmp_path / "companion.db")
        store = MemoryStore()

        old = make_fact(fact="работает тестировщиком в X", confidence=0.9)
        new = make_fact(fact="уволился из X", confidence=0.95)
        store.add_fact(old)
        store.add_fact(new)
        store.add_relation(
            FactRelation(
                from_id=new.id,
                to_id=old.id,
                relation="contradicts",
                confidence=0.95,
            )
        )

        refreshed = store.get_fact(old.id)
        assert refreshed is not None
        assert refreshed.status == "superseded"
        assert refreshed.confidence == old.confidence

    def test_contradiction_respects_protected_tags(self, tmp_path, monkeypatch):
        import companion.config as cfg
        from companion.storage.sqlite_db import MemoryDatabase

        cfg.DATA_DIR = str(tmp_path)
        cfg.SQLITE_PATH = str(tmp_path / "companion.db")
        store = MemoryStore()

        old = make_fact(
            fact="звут Иван",
            memory_kind="permanent",
            tags=["core_identity", "anchor"],
        )
        new = make_fact(fact="зовут не Иван")
        store.add_fact(old)
        store.add_fact(new)
        store.add_relation(
            FactRelation(
                from_id=new.id,
                to_id=old.id,
                relation="contradicts",
                confidence=0.95,
            )
        )

        refreshed = store.get_fact(old.id)
        assert refreshed is not None
        assert refreshed.status == "active"
        assert refreshed.confidence == old.confidence


class TestDuplicateInsertionFix:
    def test_add_fact_blocks_duplicate_from_different_sources(self, tmp_path, monkeypatch):
        import companion.config as cfg
        from companion.storage.sqlite_db import MemoryDatabase

        cfg.DATA_DIR = str(tmp_path)
        cfg.SQLITE_PATH = str(tmp_path / "companion.db")
        store = MemoryStore()

        fact1 = make_fact(fact="моя цель - собеседование в пятницу", source="user", source_type="user")
        returned1 = store.add_fact(fact1)
        assert returned1.id == fact1.id

        fact2 = make_fact(fact="собеседование в пятницу - моя цель", source="diary_entry", source_type="user")
        returned2 = store.add_fact(fact2)
        assert returned2.id == fact1.id

        facts = store.list_facts("active")
        assert len(facts) == 1

    def test_add_fact_allows_near_duplicate_against_superseded(self, tmp_path, monkeypatch):
        import companion.config as cfg
        from companion.storage.sqlite_db import MemoryDatabase

        cfg.DATA_DIR = str(tmp_path)
        cfg.SQLITE_PATH = str(tmp_path / "companion.db")
        store = MemoryStore()

        old = make_fact(fact="работает тестировщиком", source="compress", source_type="compress")
        store.add_fact(old)
        store.db.update_fact_status(old.id, "superseded")

        new = make_fact(fact="работает тестировщиком", source="user", source_type="user")
        returned = store.add_fact(new)
        assert returned.id == new.id

        active = store.list_facts("active")
        ids = [f.id for f in active]
        assert new.id in ids
        assert old.id not in ids


class TestGetRandomFactFix:
    """get_random_fact queried a non-existent `superseded` column and bypassed
    _row_fact, so it raised OperationalError on every call. These tests use a
    real SQLite store (no mocking) — the previous suite mocked the store whole,
    which is why the breakage survived."""

    def _store(self, tmp_path):
        import companion.config as cfg

        cfg.DATA_DIR = str(tmp_path)
        cfg.SQLITE_PATH = str(tmp_path / "companion.db")
        return MemoryStore()

    def test_empty_db_returns_none(self, tmp_path):
        store = self._store(tmp_path)
        assert store.get_random_fact() is None

    def test_returns_added_fact(self, tmp_path):
        store = self._store(tmp_path)
        fact = make_fact(fact="Пса зовут Морзик")
        store.add_fact(fact)

        result = store.get_random_fact()
        assert result is not None
        assert result.id == fact.id
        assert result.fact == "Пса зовут Морзик"

    def test_json_fields_are_decoded(self, tmp_path):
        """Regression: _row_fact must decode JSON columns into Python types."""
        store = self._store(tmp_path)
        store.add_fact(make_fact(fact="Иван любит Python", tags=["anchor", "tech"]))

        result = store.get_random_fact()
        assert result is not None
        assert isinstance(result.tags, list)
        assert "anchor" in result.tags
        assert isinstance(result.evidence, list)
        assert isinstance(result.meta, dict)

    def test_skips_superseded_fact(self, tmp_path):
        """Regression: the filter must exclude superseded rows via superseded_by."""
        store = self._store(tmp_path)
        old = make_fact(fact="работал в старой компании")
        store.add_fact(old)
        store.db.update_fact_status(old.id, "superseded")
        store.db.update_fact_fields(old.id, {"superseded_by": "fact_newer"})

        assert store.get_random_fact() is None


class TestPatternConfirmation:
    """A repeated observation must CONFIRM the existing pattern, not silently
    vanish. Time and repetition — not a single LLM verdict — are what turn an
    observation into a trait, so the confirmation must be recorded."""

    def _store(self, tmp_path):
        import companion.config as cfg

        cfg.DATA_DIR = str(tmp_path)
        cfg.SQLITE_PATH = str(tmp_path / "companion.db")
        return MemoryStore()

    def test_repeat_observation_bumps_freshness(self, tmp_path):
        import time

        from companion.models import Pattern

        store = self._store(tmp_path)
        text = "использует музыку для регуляции состояния"

        first = store.add_pattern(Pattern(pattern=text, category="coping"))
        before = store.get_pattern(first.id).last_confirmed_at
        time.sleep(0.01)
        second = store.add_pattern(Pattern(pattern=text, category="coping"))

        assert second.id == first.id, "must return the existing pattern, not a duplicate"
        assert len(store.list_patterns("active")) == 1
        assert store.get_pattern(first.id).last_confirmed_at > before, (
            "repeat observation must be recorded as confirmation"
        )

    def test_dedup_works_without_embeddings(self, tmp_path):
        """Confirmation must not depend on the embedding provider — otherwise
        swapping the embedding model turns every repeat into a fresh trait."""
        from companion.models import Pattern

        store = self._store(tmp_path)
        store.vector.embeddings_enabled = False
        text = "предпочитает глубокую архитектуру вместо быстрых решений"

        first = store.add_pattern(Pattern(pattern=text, category="behavior"))
        second = store.add_pattern(Pattern(pattern=text, category="behavior"))

        assert second.id == first.id
        assert len(store.list_patterns("active")) == 1

    def test_genuinely_new_pattern_is_still_added(self, tmp_path):
        from companion.models import Pattern

        store = self._store(tmp_path)
        store.add_pattern(Pattern(pattern="использует музыку для регуляции", category="coping"))
        store.add_pattern(Pattern(pattern="предпочитает работать ночью", category="behavior"))

        assert len(store.list_patterns("active")) == 2
