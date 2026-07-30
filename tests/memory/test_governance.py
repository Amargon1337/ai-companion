"""Тесты для модели решений GovernanceDecision (Phase C2.2.0)."""

import pytest
from companion.memory.governance import (
    GovernanceAction,
    GovernanceDecision,
    GovernanceRule,
)
from companion.models import MemoryActor


def test_governance_decision_allow() -> None:
    """Проверка фабричного метода allow()."""
    dec = GovernanceDecision.allow(
        actor="USER",
        rule=GovernanceRule.ALLOWED,
        reason="Мутация разрешена",
        action=GovernanceAction.MUTATE_STATUS,
    )
    assert dec.allowed is True
    assert dec.actor == MemoryActor.USER
    assert dec.rule == "ALLOWED"
    assert dec.reason == "Мутация разрешена"
    assert dec.action == GovernanceAction.MUTATE_STATUS


def test_governance_decision_deny() -> None:
    """Проверка фабричного метода deny()."""
    dec = GovernanceDecision.deny(
        actor="llm",
        rule=GovernanceRule.CORE_MEMORY_PROTECTION_001,
        reason="Запрет изменения защищённого слоя личности",
        action=GovernanceAction.ARCHIVE_FACT,
    )
    assert dec.allowed is False
    assert dec.actor == MemoryActor.LLM
    assert dec.rule == "CORE_MEMORY_PROTECTION_001"
    assert dec.reason == "Запрет изменения защищённого слоя личности"
    assert dec.action == GovernanceAction.ARCHIVE_FACT


def test_raise_if_denied_behavior() -> None:
    """Метод raise_if_denied возбуждает ValueError для DENIED и не делает ничего для ALLOWED."""
    allowed_dec = GovernanceDecision.allow(actor="user")
    allowed_dec.raise_if_denied()  # не должно выбрасывать исключений

    denied_dec = GovernanceDecision.deny(
        actor="system",
        rule=GovernanceRule.PRIVILEGED_DELETE_001,
        reason="Удаление доступно только USER/ADMIN",
        action=GovernanceAction.DELETE_FACT,
    )
    with pytest.raises(
        ValueError,
        match=r"\[DENIED: PRIVILEGED_DELETE_001\] Удаление доступно только USER/ADMIN",
    ):
        denied_dec.raise_if_denied()


def test_to_dict_serialization() -> None:
    """Проверка преобразования решения в словарь для логирования."""
    dec = GovernanceDecision.deny(
        actor="llm",
        rule=GovernanceRule.LLM_RESTRICTION_001,
        reason="LLM не имеет права гасить факты",
        action=GovernanceAction.MUTATE_STATUS,
    )
    data = dec.to_dict()
    assert data == {
        "allowed": False,
        "reason": "LLM не имеет права гасить факты",
        "actor": "llm",
        "rule": "LLM_RESTRICTION_001",
        "action": "mutate_status",
    }


def test_str_representation() -> None:
    """Проверка строкового представления решения."""
    dec = GovernanceDecision.allow(actor="admin")
    assert "status=ALLOWED" in str(dec)
    assert "actor=admin" in str(dec)
