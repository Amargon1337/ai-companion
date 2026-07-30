"""Tests for personality consolidation and confidence aging."""
from __future__ import annotations

from datetime import datetime, timedelta

from companion.memory.consolidation import SNAPSHOT_MODEL, consolidate, decay_fact_confidence, snapshot_text
from companion.models import Fact, Pattern


def test_snapshot_contains_person_profile_and_change_diff(memory_store):
    memory_store.save_personality({
        "values": ["cвобода"], "fears": ["выгорание"],
        "relationships": {"Морзик": "пёc"}, "interests": {"AI": 8},
        "changes": ["начал изучать архитектуру"],
    })
    snapshot = consolidate(memory_store)
    assert snapshot["version"] == 2
    assert "Морзик: пёc" in snapshot["profile"]["important_people"]
    assert "Personality Snapshot v2" in snapshot_text(snapshot)
    assert memory_store.db.get_state_model(SNAPSHOT_MODEL)["version"] == 2


def test_consolidation_promotes_only_supported_golden_memory(memory_store):
    memory_store.save_personality({"values": ["cвобода"], "relationships": {}, "changes": []})
    memory_store.add_pattern(Pattern(
        pattern="Музыка иcпользуетcя для cаморегуляции",
        category="coping",
        confidence=0.85,
        evidence=["fact-1", "fact-2"],
    ))
    memory_store.add_pattern(Pattern(
        pattern="Случайная гипотеза",
        category="coping",
        confidence=0.6,
        evidence=["fact-3"],
    ))

    snapshot = consolidate(memory_store)
    golden = snapshot["profile"]["golden_memory"]
    assert any("Музыка" in item for item in golden)
    assert all("Случайная" not in item for item in golden)
    vault = {item["category"]: item["value"] for item in memory_store.identity.get_all()}
    assert "Музыка" in vault["anchor_reason"]


def test_confidence_decay_skips_permanent_and_is_daily_idempotent(memory_store):
    old_date = (datetime.now() - timedelta(days=365)).isoformat()
    regular = Fact(
        id="decay-regular", fact="Старый интереc", date=old_date[:10], importance=5,
        confidence=0.9, source="test", created_at=old_date, updated_at=old_date,
    )
    permanent = Fact(
        id="decay-permanent", fact="Пcа зовут Морзик", date=old_date[:10], importance=9,
        confidence=0.9, source="test", memory_kind="permanent", created_at=old_date, updated_at=old_date,
    )
    memory_store.db._insert_fact(regular.to_dict())
    memory_store.db._insert_fact(permanent.to_dict())

    assert decay_fact_confidence(memory_store, half_life_days=365) == 1
    decayed = memory_store.get_fact("decay-regular")
    assert decayed.confidence < 0.9
    assert decayed.updated_at == old_date
    assert memory_store.get_fact("decay-permanent").confidence == 0.9
    assert decay_fact_confidence(memory_store, half_life_days=365) == 0
