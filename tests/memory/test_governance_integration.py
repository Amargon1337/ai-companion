"""Тесты интеграции контроллера Governance в пути изменения статусов (Phase C2.2.1 Step 1)."""

import os
import pytest
from companion.memory.store import MemoryStore
from companion.models import Fact


@pytest.fixture
def store(tmp_path):
    old_db = os.environ.get("SQLITE_PATH")
    os.environ["SQLITE_PATH"] = str(tmp_path / "test_governance_integration.db")

    s = MemoryStore()
    yield s

    if old_db:
        os.environ["SQLITE_PATH"] = old_db
    else:
        os.environ.pop("SQLITE_PATH", None)


def test_revive_dormant_fact_denied_for_llm_core_value(store: MemoryStore) -> None:
    """Проверка запрета на восстановление dormant факта core_value непривилегированным LLM."""
    fact = Fact(
        fact="Core personality trait",
        date="2026-01-01",
        confidence=1.0,
        source="test",
        identity_layer="core_value",
        status="dormant",
        importance=10,
    )
    store.add_fact(fact, actor="SYSTEM")
    initial_events_len = len(store.events.get_history(fact.id))

    # LLM не имеет права переводить защищённые слои из dormant в active
    store.revive_dormant_fact(fact.id, actor="LLM")

    reloaded = store.get_fact(fact.id)
    assert reloaded is not None
    assert reloaded.status == "dormant"

    # Проверяем, что событие FACT_STATUS_CHANGED НЕ было записано в EventStore при DENY
    events_after = store.events.get_history(fact.id)
    assert len(events_after) == initial_events_len


def test_apply_importance_decay_denied_for_llm(store: MemoryStore) -> None:
    """LLM не имеет права запускать decay (отклоняется контроллером Governance)."""
    fact = Fact(
        fact="Some old detail",
        date="2020-01-01",
        confidence=1.0,
        source="test",
        status="active",
        importance=2,
    )
    store.add_fact(fact, actor="SYSTEM")
    initial_events_count = len(store.events.get_all_events())

    decayed_count = store.apply_importance_decay(actor="LLM")
    assert decayed_count == 0

    reloaded = store.get_fact(fact.id)
    assert reloaded is not None
    assert reloaded.status == "active"

    # При DENY никаких событий в EventStore не создаётся
    assert len(store.events.get_all_events()) == initial_events_count


def test_apply_importance_decay_allowed_for_system(store: MemoryStore) -> None:
    """SYSTEM с правом RUN_DECAY успешно переводит факты в dormant и пишет событие."""
    fact = Fact(
        fact="Ancient unimportant note",
        date="2020-01-01",
        confidence=1.0,
        source="test",
        status="active",
        importance=3,
    )
    store.add_fact(fact, actor="SYSTEM")

    decayed_count = store.apply_importance_decay(actor="SYSTEM")
    assert decayed_count == 1

    reloaded = store.get_fact(fact.id)
    assert reloaded is not None
    assert reloaded.status == "dormant"
