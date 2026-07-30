"""Тесты для контроллера MemoryGovernanceController (Phase C2.2.1.0)."""

import pytest
from companion.memory.controller import MemoryGovernanceController
from companion.memory.governance import (
    GovernanceAction,
    GovernanceContext,
    GovernanceRule,
    MemoryCapability,
)
from companion.models import MemoryActor


@pytest.fixture
def controller() -> MemoryGovernanceController:
    return MemoryGovernanceController()


def test_authorize_status_transition_success(controller: MemoryGovernanceController) -> None:
    """Успешная проверка разрешения на перевод статуса active -> dormant."""
    ctx = GovernanceContext.create(
        actor="SYSTEM",
        capability=MemoryCapability.RUN_DECAY,
        reason="Автоматическое затухание памяти",
    )
    decision = controller.authorize_status_transition("active", "dormant", ctx)
    assert decision.allowed is True
    assert decision.rule == "ALLOWED"


def test_authorize_status_transition_capability_denied(
    controller: MemoryGovernanceController,
) -> None:
    """Отказ в переводе статуса из-за отсутствия нужной способности (capability)."""
    ctx = GovernanceContext.create(
        actor="SYSTEM",
        capability=MemoryCapability.CREATE_FACT,  # способность не подходит для смены статуса
    )
    decision = controller.authorize_status_transition("active", "dormant", ctx)
    assert decision.allowed is False
    assert decision.rule == "CAPABILITY_RESTRICTION_001"
    assert "нет способности" in decision.reason


def test_authorize_status_transition_lifecycle_denied(
    controller: MemoryGovernanceController,
) -> None:
    """Отказ в невалидном переходе в графе состояний (dormant -> superseded)."""
    ctx = GovernanceContext.create(
        actor="USER",
        capability=MemoryCapability.CHANGE_STATUS,
    )
    decision = controller.authorize_status_transition("dormant", "superseded", ctx)
    assert decision.allowed is False
    assert decision.rule == "LIFECYCLE_GRAPH_001"


def test_authorize_status_transition_llm_policy_denied(
    controller: MemoryGovernanceController,
) -> None:
    """Отказ LLM в переводе факта в архив (LLM_RESTRICTION_001)."""
    ctx = GovernanceContext.create(
        actor="LLM",
        capability=MemoryCapability.CHANGE_STATUS,
    )
    decision = controller.authorize_status_transition("active", "archived", ctx)
    assert decision.allowed is False
    assert decision.rule == "LLM_RESTRICTION_001"


def test_authorize_fact_creation(controller: MemoryGovernanceController) -> None:
    """Проверка прав на создание фактов."""
    ok_ctx = GovernanceContext.create(actor="LLM", capability=MemoryCapability.CREATE_FACT)
    assert controller.authorize_fact_creation(ok_ctx).allowed is True

    denied_ctx = GovernanceContext.create(actor="LLM", capability=MemoryCapability.RUN_DECAY)
    assert controller.authorize_fact_creation(denied_ctx).allowed is False
    assert controller.authorize_fact_creation(denied_ctx).rule == "CAPABILITY_RESTRICTION_001"


def test_authorize_fact_update_core_protection(controller: MemoryGovernanceController) -> None:
    """Проверка защиты ядра (CORE_VALUE/CORE_BELIEF/CORE_IDENTITY) от изменения LLM."""
    llm_ctx = GovernanceContext.create(
        actor="LLM",
        capability=MemoryCapability.MODIFY_FACT,
        identity_layer="core_value",
    )
    decision = controller.authorize_fact_update(llm_ctx)
    assert decision.allowed is False
    assert decision.rule == "CORE_MEMORY_PROTECTION_001"

    user_ctx = GovernanceContext.create(
        actor="USER",
        capability=MemoryCapability.MODIFY_FACT,
        identity_layer="core_value",
    )
    assert controller.authorize_fact_update(user_ctx).allowed is True


def test_authorize_deletion(controller: MemoryGovernanceController) -> None:
    """Удаление доступно только привилегированным субъектам."""
    user_ctx = GovernanceContext.create(
        actor="USER",
        capability=MemoryCapability.DELETE_FACT,
    )
    assert controller.authorize_deletion(user_ctx).allowed is True

    llm_ctx = GovernanceContext.create(
        actor="LLM",
        capability=MemoryCapability.DELETE_FACT,
    )
    decision = controller.authorize_deletion(llm_ctx)
    assert decision.allowed is False
    assert decision.rule == "PRIVILEGED_DELETE_001"
