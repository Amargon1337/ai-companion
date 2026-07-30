"""Тесты для машины состояний жизненного цикла памяти (Phase C2.1)."""

import pytest
from companion.memory.lifecycle import MemoryLifecycle


@pytest.fixture
def lifecycle() -> MemoryLifecycle:
    return MemoryLifecycle()


def test_valid_transitions_allowed(lifecycle: MemoryLifecycle) -> None:
    """Проверка разрешённых переходов из статусов active и dormant."""
    assert lifecycle.can_transition("active", "dormant") is True
    assert lifecycle.can_transition("active", "quarantined") is True
    assert lifecycle.can_transition("active", "superseded") is True
    assert lifecycle.can_transition("active", "archived") is True
    assert lifecycle.can_transition("active", "deleted") is True

    assert lifecycle.can_transition("dormant", "active") is True
    assert lifecycle.can_transition("dormant", "archived") is True
    assert lifecycle.can_transition("dormant", "quarantined") is True
    assert lifecycle.can_transition("dormant", "deleted") is True


def test_invalid_transitions_rejected(lifecycle: MemoryLifecycle) -> None:
    """Проверка отклонения невалидных переходов по графу состояний."""
    assert lifecycle.can_transition("superseded", "active") is False
    assert lifecycle.can_transition("deleted", "active") is False
    assert lifecycle.can_transition("dormant", "superseded") is False
    assert lifecycle.can_transition("archived", "quarantined") is False


def test_same_status_is_noop_allowed(lifecycle: MemoryLifecycle) -> None:
    """Переход в текущий статус всегда разрешён."""
    assert lifecycle.can_transition("active", "active") is True
    assert lifecycle.can_transition("dormant", "dormant") is True


def test_unknown_status_rejected(lifecycle: MemoryLifecycle) -> None:
    """Проверка отклонения неизвестных статусов."""
    assert lifecycle.can_transition("active", "unknown_status") is False
    assert lifecycle.can_transition("invalid_status", "dormant") is False


def test_validate_transition_raises(lifecycle: MemoryLifecycle) -> None:
    """Метод validate_transition возбуждает ValueError при недопустимом переходе графа."""
    with pytest.raises(ValueError, match="Переход 'superseded' -> 'active' запрещён"):
        lifecycle.validate_transition("superseded", "active")

    with pytest.raises(ValueError, match="Неизвестный целевой статус 'foo'"):
        lifecycle.validate_transition("active", "foo")
