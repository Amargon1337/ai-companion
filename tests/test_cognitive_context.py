from __future__ import annotations

import sqlite3
from datetime import date, datetime

from companion.context import ContextAggregator, TemporalContextProvider, TemporalDeltaEngine, VibeResolver
from companion.memory.semantic_ranker import SemanticImportanceRanker
from companion.models import Fact
from companion.storage.sqlite_db import MemoryDatabase


def test_temporal_context_xml_and_vibe():
    provider = TemporalContextProvider(
        timezone_name="UTC",
        clock=lambda: datetime(2026, 7, 3, 3, 15),
        vibe_resolver=VibeResolver(),
    )
    xml = provider.get_context().to_prompt_xml()
    assert xml.count("<runtime_context>") == 1
    assert 'time="03:15"' in xml
    assert 'weekday="friday"' in xml
    assert 'vibe="nocturnal_nihilism"' in xml


def test_context_aggregator_injects_once(memory_store):
    block = ContextAggregator(memory_store.db).build_prompt_block()
    assert block.count("<runtime_context>") == 1


def test_temporal_delta_pause_resume(memory_store):
    engine = TemporalDeltaEngine(memory_store.db)
    engine.create_counter("training", "Fitness project", date(2026, 1, 1), timezone="UTC")
    engine.pause_counter("training", date(2026, 1, 10), "illness")
    engine.resume_counter("training", date(2026, 1, 15))

    rows = memory_store.db.list_temporal_counters()
    assert rows[0]["counter_name"] == "training"
    pauses = memory_store.db.list_temporal_counter_pauses(rows[0]["id"])
    assert pauses[0]["pause_start_date"] == "2026-01-10"
    assert pauses[0]["pause_end_date"] == "2026-01-15"
    assert '<counter name="training"' in engine.to_prompt_xml()


def test_temporal_counter_archive_and_soft_delete(memory_store):
    engine = TemporalDeltaEngine(memory_store.db)
    engine.create_counter("reading", "Reading streak", date(2026, 1, 1), timezone="UTC")
    engine.archive_counter("reading", True)
    assert memory_store.db.list_temporal_counters() == []
    engine.delete_counter("reading")
    assert memory_store.db.list_temporal_counters() == []


def test_schema_migration_adds_context_tables_and_fact_metadata(tmp_path):
    db = MemoryDatabase(str(tmp_path / "companion.db"))
    with sqlite3.connect(db.path) as conn:
        fact_cols = {row[1] for row in conn.execute("PRAGMA table_info(facts)").fetchall()}
        tables = {row[0] for row in conn.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()}
    assert "anchor_flag" in fact_cols
    assert "access_count" in fact_cols
    assert "temporal_counters" in tables
    assert "temporal_counter_pauses" in tables
    assert "memory_access_log" in tables


def test_semantic_ranker_archive_filter_anchor_and_access(memory_store):
    anchor = Fact(
        id="fact_anchor",
        fact="Пса зовут Морзик",
        date="2026-01-01",
        importance=9,
        confidence=1.0,
        source="test",
        tags=["anchor"],
    )
    regular = Fact(
        id="fact_regular",
        fact="Купил хлеб",
        date="2026-01-01",
        importance=5,
        confidence=1.0,
        source="test",
    )
    archived = Fact(
        id="fact_archived",
        fact="Старый факт",
        date="2026-01-01",
        importance=10,
        confidence=1.0,
        source="test",
        status="archived",
    )
    memory_store.db._insert_fact(anchor.to_dict())
    memory_store.db._insert_fact(regular.to_dict())
    memory_store.db._insert_fact(archived.to_dict())

    ranker = SemanticImportanceRanker(memory_store.db)
    ranked = ranker.rerank([(regular, 0.9), (anchor, 0.82), (archived, 1.0)], query_text="морзик", update_access=True)
    ids = [fact.id for fact, _ in ranked]
    assert "fact_archived" not in ids
    assert ids[0] == "fact_anchor"
    meta = memory_store.db.hydrate_fact_metadata(["fact_anchor"])["fact_anchor"]
    assert meta["access_count"] == 1
