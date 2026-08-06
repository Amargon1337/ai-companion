"""Модель решений управления памятью (Governance Decision Model — Phase C2.2.0).

Определяет строгие типы и структуры для результатов проверок политик (GovernanceDecision,
GovernanceAction, GovernanceRule), обеспечивая аудит и отслеживаемость отказов (DENIED)
и разрешений (ALLOWED).
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from enum import Enum
from typing import Any
from companion.models import MemoryActor


class MemoryCapability(str, Enum):
    """Возможности (capabilities), необходимые для выполнения операций над памятью."""
    CREATE_FACT = "create_fact"
    MODIFY_FACT = "modify_fact"
    CHANGE_STATUS = "change_status"
    RUN_DECAY = "run_decay"
    VERIFY_MEMORY = "verify_memory"
    DELETE_FACT = "delete_fact"


class GovernanceAction(str, Enum):
    """Типы действий над памятью, регулируемые контроллером Governance."""
    MUTATE_STATUS = "mutate_status"         # Изменение статуса (active -> archived и др.)
    MODIFY_FACT = "modify_fact"             # Изменение содержимого/значения факта
    ARCHIVE_FACT = "archive_fact"           # Архивация факта
    DELETE_FACT = "delete_fact"             # Полное удаление факта
    REVIVE_FACT = "revive_fact"             # Восстановление (revive) погасшего факта
    DECAY_IMPORTANCE = "decay_importance"   # Снижение важности (decay)


class GovernanceRule(str, Enum):
    """Идентификаторы стандартных правил Governance для аудита."""
    ALLOWED = "ALLOWED"                                       # Операция разрешена
    CORE_MEMORY_PROTECTION_001 = "CORE_MEMORY_PROTECTION_001" # Защита CORE_VALUE / CORE_BELIEF / CORE_IDENTITY
    LLM_RESTRICTION_001 = "LLM_RESTRICTION_001"               # Запрет для LLM архивировать/удалять/гасить
    PRIVILEGED_DELETE_001 = "PRIVILEGED_DELETE_001"           # Запрет на удаление непривилегированным субъектам
    CAPABILITY_RESTRICTION_001 = "CAPABILITY_RESTRICTION_001" # У субъекта отсутствует требуемая способность (capability)
    LIFECYCLE_GRAPH_001 = "LIFECYCLE_GRAPH_001"               # Нарушение графа переходов MemoryLifecycle
    UNKNOWN_STATUS_001 = "UNKNOWN_STATUS_001"                 # Неизвестный статус памяти
    CUSTOM_POLICY_001 = "CUSTOM_POLICY_001"                   # Пользовательское ограничение


@dataclass(frozen=True)
class GovernanceContext:
    """Контекст выполнения операции Governance.

    Не содержит всю базу или служебные тяжёлые объекты — только лёгкие метаданные
    запроса для принятия решения арбитром безопасности.
    Анти-блоат правило: запрещено добавлять сюда тяжелые DTO, эмбеддинги, токены,
    историю диалога и всю базу памяти. Только метаданные для проверки прав.
    """
    actor: MemoryActor
    permissions: frozenset[MemoryCapability] = field(
        default_factory=lambda: frozenset({MemoryCapability.CHANGE_STATUS})
    )
    reason: str = ""
    source: str | None = None
    identity_layer: str | None = None
    confidence: float | None = None

    def has_capability(self, cap: MemoryCapability) -> bool:
        """Проверяет наличие заданной способности у субъекта."""
        return cap in self.permissions

    @property
    def capability(self) -> MemoryCapability:
        """Обратная совместимость для получения первой способности из набора."""
        for cap in self.permissions:
            return cap
        return MemoryCapability.CHANGE_STATUS

    @classmethod
    def create(
        cls,
        actor: Any = "system",
        capability: MemoryCapability | str | None = None,
        capabilities: Any = None,
        reason: str = "",
        source: str | None = None,
        identity_layer: str | None = None,
        confidence: float | None = None,
    ) -> GovernanceContext:
        act = MemoryActor.from_string(actor)
        if capabilities is not None:
            caps_set = frozenset(
                c if isinstance(c, MemoryCapability) else MemoryCapability(str(c))
                for c in capabilities
            )
        elif capability is not None:
            cap_enum = (
                capability
                if isinstance(capability, MemoryCapability)
                else MemoryCapability(str(capability))
            )
            caps_set = frozenset({cap_enum})
        else:
            caps_set = frozenset({MemoryCapability.CHANGE_STATUS})

        return cls(
            actor=act,
            permissions=caps_set,
            reason=reason,
            source=source,
            identity_layer=identity_layer,
            confidence=confidence,
        )


@dataclass(frozen=True)
class GovernanceDecision:
    """Результат проверки правил управления памятью (Governance Decision).

    Содержит вердикт, субъект, действие, правило и понятную причину.
    Используется для принятия решения контроллером и записи в аудит-лог.
    """
    allowed: bool
    reason: str
    actor: MemoryActor
    rule: str
    action: GovernanceAction = GovernanceAction.MUTATE_STATUS

    @classmethod
    def allow(
        cls,
        actor: Any = "system",
        rule: str | GovernanceRule = GovernanceRule.ALLOWED,
        reason: str = "Операция разрешена",
        action: GovernanceAction | str = GovernanceAction.MUTATE_STATUS,
    ) -> GovernanceDecision:
        """Создаёт положительное решение (ALLOWED)."""
        act = MemoryActor.from_string(actor)
        rule_str = rule.value if isinstance(rule, Enum) else str(rule)
        act_enum = action if isinstance(action, GovernanceAction) else GovernanceAction(str(action))
        return cls(
            allowed=True,
            reason=reason,
            actor=act,
            rule=rule_str,
            action=act_enum,
        )

    @classmethod
    def deny(
        cls,
        actor: Any,
        rule: str | GovernanceRule,
        reason: str,
        action: GovernanceAction | str = GovernanceAction.MUTATE_STATUS,
    ) -> GovernanceDecision:
        """Создаёт отрицательное решение (DENIED)."""
        act = MemoryActor.from_string(actor)
        rule_str = rule.value if isinstance(rule, Enum) else str(rule)
        act_enum = action if isinstance(action, GovernanceAction) else GovernanceAction(str(action))
        return cls(
            allowed=False,
            reason=reason,
            actor=act,
            rule=rule_str,
            action=act_enum,
        )

    def raise_if_denied(self) -> None:
        """Возбуждает ValueError с отформатированным сообщением, если операция запрещена."""
        if not self.allowed:
            raise ValueError(
                f"[DENIED: {self.rule}] {self.reason} "
                f"(actor={self.actor.value}, action={self.action.value})"
            )

    def to_dict(self) -> dict[str, Any]:
        """Возвращает словарь для сериализации в JSON и записи в лог."""
        data = asdict(self)
        data["actor"] = self.actor.value
        data["action"] = self.action.value
        return data

    def __str__(self) -> str:
        status = "ALLOWED" if self.allowed else "DENIED"
        return (
            f"GovernanceDecision(status={status}, rule={self.rule}, "
            f"actor={self.actor.value}, action={self.action.value}, reason='{self.reason}')"
        )
