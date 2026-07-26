import pytest
from datetime import datetime
from companion.temporal import (
    get_day_name_ru,
    get_day_phase,
    generate_temporal_guidance,
    build_temporal_context_block,
    get_inactivity_gap,
)
from companion.memory.store import MemoryStore

def test_day_name_ru():
    # Wednesday 2026-07-22
    dt = datetime(2026, 7, 22, 14, 0, 0)
    assert get_day_name_ru(dt) == "Среда"


def test_day_phases():
    night_dt = datetime(2026, 7, 22, 3, 15, 0)
    phase, cat = get_day_phase(night_dt)
    assert phase == "Глубокая ночь"

    morning_dt = datetime(2026, 7, 22, 8, 30, 0)
    phase, cat = get_day_phase(morning_dt)
    assert phase == "Раннее утро"

    day_dt = datetime(2026, 7, 22, 14, 0, 0)
    phase, cat = get_day_phase(day_dt)
    assert phase == "Дневное время"

    evening_dt = datetime(2026, 7, 22, 20, 0, 0)
    phase, cat = get_day_phase(evening_dt)
    assert phase == "Вечер"


def test_temporal_guidance():
    # Night guidance
    guidance_night = generate_temporal_guidance("Глубокая ночь", "Среда", gap_hours=0.2)
    assert "ночь" in guidance_night.lower() or "ночной" in guidance_night.lower()

    # Long gap guidance
    guidance_gap = generate_temporal_guidance("Дневное время", "Среда", gap_hours=80.0)
    assert "несколько дней" in guidance_gap.lower()


def test_temporal_context_block():
    store = MemoryStore()
    block = build_temporal_context_block(store)
    assert "# TEMPORAL & CONTEXTUAL AWARENESS" in block
    assert "Фаза суток:" in block
    assert "Интервал с прошлого сообщения:" in block
