"""Контроллер управления памятью (Memory Governance Controller — Phase C2.2.1.0).

Оркеструет проверки жизненного цикла (MemoryLifecycle) и политик субъектов (GovernancePolicy),
выступая арбитром без побочных эффектов. Не интегрируется напрямую в MemoryStore на шаге C2.2.1.0,
предоставляя строгие методы авторизации:
- authorize_status_transition(...)
- authorize_fact_creation(...)
- authorize_fact_update(...)
- authorize_relation_creation(...)
- authorize_deletion(...)
"""

from __future__ import annotations

from companion.memory.governance import (
    GovernanceAction,
    GovernanceContext,
    GovernanceDecision,
    GovernanceRule,
    MemoryCapability,
)
from companion.memory.governance_policy import GovernancePolicy
from companion.memory.lifecycle import MemoryLifecycle


class MemoryGovernanceController:
    """Судья (арбитр) операций над памятью.

    Проверяет направленный граф переходов MemoryLifecycle и доменные ограничения
    GovernancePolicy на основе лёгкого контекста запроса GovernanceContext.
    """

    def __init__(
        self,
        lifecycle: MemoryLifecycle | None = None,
        policy: GovernancePolicy | None = None,
    ) -> None:
        self.lifecycle = lifecycle or MemoryLifecycle()
        self.policy = policy or GovernancePolicy()

    def authorize_status_transition(
        self,
        old_status: str,
        new_status: str,
        context: GovernanceContext,
    ) -> GovernanceDecision:
        """Проверка разрешения на перевод факта из old_status в new_status."""
        allowed_caps = {
            MemoryCapability.CHANGE_STATUS,
            MemoryCapability.RUN_DECAY,
            MemoryCapability.DELETE_FACT,
            MemoryCapability.MODIFY_FACT,
        }
        if not any(cap in allowed_caps for cap in context.permissions):
            return GovernanceDecision.deny(
                actor=context.actor,
                rule=GovernanceRule.CAPABILITY_RESTRICTION_001,
                reason=f"У субъекта '{context.actor.value}' нет способности на изменение статуса",
                action=GovernanceAction.MUTATE_STATUS,
            )

        # 1. Проверка направленного графа MemoryLifecycle
        if not self.lifecycle.can_transition(old_status, new_status):
            err = self.lifecycle.get_transition_error(old_status, new_status) or "Недопустимый переход в графе состояний"
            return GovernanceDecision.deny(
                actor=context.actor,
                rule=GovernanceRule.LIFECYCLE_GRAPH_001,
                reason=err,
                action=GovernanceAction.MUTATE_STATUS,
            )

        # 2. Проверка ограничений GovernancePolicy (права субъекта и защита ядра)
        if not self.policy.can_modify_status(
            old_status=old_status,
            new_status=new_status,
            actor=context.actor,
            identity_layer=context.identity_layer,
        ):
            err = self.policy.get_policy_error(
                old_status=old_status,
                new_status=new_status,
                actor=context.actor,
                identity_layer=context.identity_layer,
            ) or "Операция отклонена политикой GovernancePolicy"
            rule = (
                GovernanceRule.CORE_MEMORY_PROTECTION_001
                if context.identity_layer in self.policy.PROTECTED_LAYERS
                else GovernanceRule.LLM_RESTRICTION_001
            )
            return GovernanceDecision.deny(
                actor=context.actor,
                rule=rule,
                reason=err,
                action=GovernanceAction.MUTATE_STATUS,
            )

        return GovernanceDecision.allow(
            actor=context.actor,
            rule=GovernanceRule.ALLOWED,
            reason="Переход статуса разрешён",
            action=GovernanceAction.MUTATE_STATUS,
        )

    def authorize_fact_creation(
        self,
        context: GovernanceContext,
    ) -> GovernanceDecision:
        """Проверка разрешения на создание нового факта в памяти."""
        if not (
            context.has_capability(MemoryCapability.CREATE_FACT)
            or context.has_capability(MemoryCapability.MODIFY_FACT)
        ):
            return GovernanceDecision.deny(
                actor=context.actor,
                rule=GovernanceRule.CAPABILITY_RESTRICTION_001,
                reason=f"У субъекта '{context.actor.value}' нет способности '{MemoryCapability.CREATE_FACT.value}'",
                action=GovernanceAction.MODIFY_FACT,
            )
        return GovernanceDecision.allow(
            actor=context.actor,
            rule=GovernanceRule.ALLOWED,
            reason="Создание факта разрешено",
            action=GovernanceAction.MODIFY_FACT,
        )

    def authorize_fact_update(
        self,
        context: GovernanceContext,
    ) -> GovernanceDecision:
        """Проверка разрешения на изменение содержимого существующего факта."""
        if not (
            context.has_capability(MemoryCapability.MODIFY_FACT)
            or context.has_capability(MemoryCapability.CHANGE_STATUS)
        ):
            return GovernanceDecision.deny(
                actor=context.actor,
                rule=GovernanceRule.CAPABILITY_RESTRICTION_001,
                reason=f"У субъекта '{context.actor.value}' нет способности '{MemoryCapability.MODIFY_FACT.value}'",
                action=GovernanceAction.MODIFY_FACT,
            )

        # Проверка защиты CORE_* слоёв
        if (
            context.identity_layer in self.policy.PROTECTED_LAYERS
            and not context.actor.is_privileged()
        ):
            return GovernanceDecision.deny(
                actor=context.actor,
                rule=GovernanceRule.CORE_MEMORY_PROTECTION_001,
                reason="Изменение защищённого слоя идентичности разрешено только USER или ADMIN",
                action=GovernanceAction.MODIFY_FACT,
            )

        return GovernanceDecision.allow(
            actor=context.actor,
            rule=GovernanceRule.ALLOWED,
            reason="Изменение факта разрешено",
            action=GovernanceAction.MODIFY_FACT,
        )

    def authorize_relation_creation(
        self,
        context: GovernanceContext,
    ) -> GovernanceDecision:
        """Проверка разрешения на создание связи между фактами."""
        if not (
            context.has_capability(MemoryCapability.CREATE_FACT)
            or context.has_capability(MemoryCapability.MODIFY_FACT)
        ):
            return GovernanceDecision.deny(
                actor=context.actor,
                rule=GovernanceRule.CAPABILITY_RESTRICTION_001,
                reason=f"У субъекта '{context.actor.value}' нет способности на создание связей",
                action=GovernanceAction.MODIFY_FACT,
            )
        return GovernanceDecision.allow(
            actor=context.actor,
            rule=GovernanceRule.ALLOWED,
            reason="Создание связи разрешено",
            action=GovernanceAction.MODIFY_FACT,
        )

    def authorize_deletion(
        self,
        context: GovernanceContext,
    ) -> GovernanceDecision:
        """Проверка разрешения на удаление (перевод в статус deleted)."""
        if not context.has_capability(MemoryCapability.DELETE_FACT):
            return GovernanceDecision.deny(
                actor=context.actor,
                rule=GovernanceRule.CAPABILITY_RESTRICTION_001,
                reason=f"У субъекта '{context.actor.value}' нет способности '{MemoryCapability.DELETE_FACT.value}'",
                action=GovernanceAction.DELETE_FACT,
            )

        if not context.actor.is_privileged():
            return GovernanceDecision.deny(
                actor=context.actor,
                rule=GovernanceRule.PRIVILEGED_DELETE_001,
                reason="Удаление фактов разрешено только привилегированным субъектам (USER/ADMIN)",
                action=GovernanceAction.DELETE_FACT,
            )

        if (
            context.identity_layer in self.policy.PROTECTED_LAYERS
            and not context.actor.is_privileged()
        ):
            return GovernanceDecision.deny(
                actor=context.actor,
                rule=GovernanceRule.CORE_MEMORY_PROTECTION_001,
                reason="Удаление защищённого слоя идентичности разрешено только USER или ADMIN",
                action=GovernanceAction.DELETE_FACT,
            )

        return GovernanceDecision.allow(
            actor=context.actor,
            rule=GovernanceRule.ALLOWED,
            reason="Удаление разрешено",
            action=GovernanceAction.DELETE_FACT,
        )
