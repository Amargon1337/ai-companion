"""Тесты для перечисления MemoryActor и вспомогательных методов (Phase C2.1.5)."""

import pytest
from companion.models import MemoryActor


def test_memory_actor_values() -> None:
    """Проверка базовых значений перечисления MemoryActor."""
    assert MemoryActor.USER == "user"
    assert MemoryActor.LLM == "llm"
    assert MemoryActor.SYSTEM == "system"
    assert MemoryActor.ADMIN == "admin"


def test_memory_actor_helpers() -> None:
    """Проверка вспомогательных методов is_privileged, is_llm, is_system."""
    assert MemoryActor.USER.is_privileged() is True
    assert MemoryActor.ADMIN.is_privileged() is True
    assert MemoryActor.SYSTEM.is_privileged() is False
    assert MemoryActor.LLM.is_privileged() is False

    assert MemoryActor.LLM.is_llm() is True
    assert MemoryActor.USER.is_llm() is False

    assert MemoryActor.SYSTEM.is_system() is True
    assert MemoryActor.USER.is_system() is False


def test_from_string_mapping() -> None:
    """Проверка нормализации строк в MemoryActor."""
    assert MemoryActor.from_string("user") == MemoryActor.USER
    assert MemoryActor.from_string("USER") == MemoryActor.USER
    assert MemoryActor.from_string("user_statement") == MemoryActor.USER
    assert MemoryActor.from_string("user_explicit") == MemoryActor.USER

    assert MemoryActor.from_string("admin") == MemoryActor.ADMIN
    assert MemoryActor.from_string("administrator") == MemoryActor.ADMIN

    assert MemoryActor.from_string("llm") == MemoryActor.LLM
    assert MemoryActor.from_string("llm_extraction") == MemoryActor.LLM
    assert MemoryActor.from_string("assistant") == MemoryActor.LLM

    assert MemoryActor.from_string("system") == MemoryActor.SYSTEM
    assert MemoryActor.from_string("background_job") == MemoryActor.SYSTEM
    assert MemoryActor.from_string(None) == MemoryActor.SYSTEM
    assert MemoryActor.from_string(MemoryActor.USER) == MemoryActor.USER
