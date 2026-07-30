"""Тесты для машины состояний и политик управления жизненным циклом (Phase C2.1)."""

import pytest
from companion.memory.lifecycle import MemoryLifecycle
from companion.models import IdentityLayer


@pytest.fixture
def lifecycle() -> MemoryLifecycle:
    return MemoryLifecycle()


def test_active_to_dormant_allowed(lifecycle: MemoryLifecycle) -> None:
    """Проверка стандартного затухания важности: active -> dormant для SYSTEM."""
    assert lifecycle.can_transition("active", "dormant", actor="SYSTEM") is True
    assert lifecycle.can_transition("active", "quarantined", actor="SYSTEM") is True
    assert lifecycle.can_transition("active", "superseded", actor="SYSTEM") is True
    assert lifecycle.can_transition("active", "archived", actor="SYSTEM") is True


def test_core_value_cannot_archive(lifecycle: MemoryLifecycle) -> None:
    """Защищённый факт (core_value/core_belief/core_identity) не может быть архивирован или погашен системой."""
    assert lifecycle.can_transition(
        "active", "archived", actor="SYSTEM", identity_layer="core_value"
    ) is False
    assert lifecycle.can_transition(
        "active", "dormant", actor="SYSTEM", identity_layer="core_belief"
    ) is False
    assert lifecycle.can_transition(
        "active", "quarantined", actor="LLM", identity_layer="core_identity"
    ) is False


def test_core_value_can_be_changed_by_user(lifecycle: MemoryLifecycle) -> None:
    """Пользователь или администратор имеют право изменить статус защищённого факта."""
    assert lifecycle.can_transition(
        "active", "archived", actor="USER", identity_layer="core_value"
    ) is True
    assert lifecycle.can_transition(
        "active", "dormant", actor="ADMIN", identity_layer="core_belief"
    ) is True


def test_invalid_transition_rejected(lifecycle: MemoryLifecycle) -> None:
    """Проверка отклонения невалидных переходов по графу состояний."""
    # Из superseded или deleted переходы запрещены
    assert lifecycle.can_transition("superseded", "active", actor="USER") is False
    assert lifecycle.can_transition("deleted", "active", actor="USER") is False
    # Из dormant нельзя сразу в superseded (нужно сначала оживить или это не предусмотрено графом)
    assert lifecycle.can_transition("dormant", "superseded", actor="SYSTEM") is False


def test_llm_cannot_archive_or_delete(lifecycle: MemoryLifecycle) -> None:
    """LLM не имеет права архивировать, гасить или удалять память."""
    assert lifecycle.can_transition("active", "dormant", actor="LLM") is False
    assert lifecycle.can_transition("active", "archived", actor="LLM") is False
    assert lifecycle.can_transition("active", "quarantined", actor="LLM") is False
    assert lifecycle.can_transition("active", "deleted", actor="LLM") is False


def test_delete_only_by_privileged(lifecycle: MemoryLifecycle) -> None:
    """Переход в deleted разрешён только привилегированным субъектам."""
    assert lifecycle.can_transition("active", "deleted", actor="SYSTEM") is False
    assert lifecycle.can_transition("active", "deleted", actor="LLM") is False
    assert lifecycle.can_transition("active", "deleted", actor="USER") is True
    assert lifecycle.can_transition("active", "deleted", actor="ADMIN") is True


def test_same_status_is_noop_allowed(lifecycle: MemoryLifecycle) -> None:
    """Переход в текущий статус всегда разрешён."""
    assert lifecycle.can_transition("active", "active", actor="SYSTEM") is True
    assert lifecycle.can_transition("dormant", "dormant", actor="LLM") is True


def test_validate_transition_raises(lifecycle: MemoryLifecycle) -> None:
    """Метод validate_transition возбуждает ValueError при недопустимом переходе."""
    with pytest.raises(ValueError, match="Защищённый слой личности"):
        lifecycle.validate_transition(
            "active", "dormant", actor="SYSTEM", identity_layer="core_value"
        )

    with pytest.raises(ValueError, match="Переход 'superseded' -> 'active' запрещён"):
        lifecycle.validate_transition("superseded", "active", actor="USER")


def test_enum_compatibility(lifecycle: MemoryLifecycle) -> None:
    """Проверка работы с Enum IdentityLayer."""
    assert lifecycle.can_transition(
        "active", "dormant", actor="SYSTEM", identity_layer=IdentityLayer.CORE_VALUE
    ) is False
    assert lifecycle.can_transition(
        "active", "dormant", actor="SYSTEM", identity_layer=IdentityLayer.BIOGRAPHICAL
    ) is True
