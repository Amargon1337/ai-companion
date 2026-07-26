"""Tests for Emotional Momentum and Sensitivity Guards."""
from __future__ import annotations

from companion.user_model import user_model
from companion.policy_layer import policy_layer
from companion.temporal import build_temporal_context_block
from companion.llm.sessions import build_system_instruction
from companion.memory.store import MemoryStore


def test_record_emotional_state_calculates_momentum():
    with user_model._lock:
        user_model.data.setdefault("emotional_timeline", {})["state_history"] = []

    for _ in range(3):
        user_model.record_emotional_state(
            {"energy": 0.2, "sadness": 0.6, "anxiety": 0.1, "anger": 0.0},
            "depressed",
        )

    effective_state, metrics = user_model.get_effective_emotional_state()
    assert effective_state == "depressed"
    assert metrics["energy"] < 0.35
    assert metrics["sadness"] > 0.40


def test_temporal_block_adapts_morning_greeting_to_low_energy():
    with user_model._lock:
        user_model.data.setdefault("emotional_timeline", {})["state_history"] = []
    user_model.record_emotional_state(
        {"energy": 0.2, "sadness": 0.5, "anxiety": 0.0, "anger": 0.0},
        "depressed",
    )

    block = build_temporal_context_block()
    assert "учитывай зафиксированный спад сил Ивана после тяжелых дней" in block
    assert "НЕ ИСПОЛЬЗУЙ бодрые утренние приветствия" in block


def test_system_instruction_injects_sensitivity_guards():
    store = MemoryStore()
    with user_model._lock:
        user_model.data.setdefault("emotional_timeline", {})["state_history"] = []
    user_model.record_emotional_state(
        {"energy": 0.2, "sadness": 0.6, "anxiety": 0.1, "anger": 0.0},
        "depressed",
    )

    instruction = build_system_instruction(store, None, query="")
    assert "SENSITIVITY & TRIGGER GUARDS (EMOTIONAL MOMENTUM)" in instruction
    assert "НИКАКОЙ «пластиковой бодрости»" in instruction
    assert "РЕЖИМ СПОКОЙНОГО ПРИСУТСТВИЯ" in instruction


def test_policy_layer_filters_toxic_positivity():
    raw_text = "Привет!! Не вешай нос! Всё будет отлично! Главное — позитивный настрой! Как твои дела?"
    filtered = policy_layer.enforce_sensitivity_guards(raw_text, effective_state="depressed")

    assert "Не вешай нос" not in filtered
    assert "Всё будет отлично" not in filtered
    assert "Главное — позитивный настрой" not in filtered
    assert "!!" not in filtered
    assert "Как твои дела?" in filtered
