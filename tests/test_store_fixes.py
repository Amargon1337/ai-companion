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
