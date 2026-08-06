"""Модуль политик управления памятью (Governance Policy — Phase C2.1.5).

Отвечает за проверку ограничений субъектов (MemoryActor) и защиту
ядра личности (Core Memory Protection). Работает совместно с MemoryLifecycle,
который проверяет направленный граф переходов.
"""

from __future__ import annotations

from enum import Enum
from typing import Any
from companion.models import MemoryActor


class GovernancePolicy:
    """Политика безопасности и прав доступа в системе памяти (Phase C2.1.5).
    
    Проверяет:
    - Права субъектов (MemoryActor) на выполнение операций (LLM не может гасить/удалять память).
    - Защиту ядра личности (CORE_VALUE / CORE_BELIEF / CORE_IDENTITY не могут меняться автоматикой).
    - Привилегии на удаление (DELETED доступно только USER / ADMIN).
    """

    PROTECTED_LAYERS: set[str] = {
        "core_value",
        "core_belief",
        "core_identity",
    }

    def _normalize_layer(self, identity_layer: Any) -> str:
        if identity_layer is None:
            return ""
        if isinstance(identity_layer, Enum):
            return str(identity_layer.value).strip().lower()
        return str(identity_layer).strip().lower()

    def get_policy_error(
        self,
        old_status: str,
        new_status: str,
        actor: Any = "system",
        identity_layer: Any = None,
        reason: str | None = None,
    ) -> str | None:
        """Проверяет соблюдение политик Governance при изменении статуса факта.

        Args:
            old_status: Текущий статус факта.
            new_status: Целевой статус факта.
            actor: Субъект изменения (MemoryActor или строка).
            identity_layer: Слой идентичности (core_value, biographical и др.).
            reason: Опциональная причина мутации.

        Returns:
            str с описанием ошибки, если политика нарушена, или None, если операция разрешена.
        """
        act = MemoryActor.from_string(actor)
        layer = self._normalize_layer(identity_layer)

        if old_status == new_status:
            return None

        # 1. Защита ядра личности (Core Memory Protection)
        if layer in self.PROTECTED_LAYERS:
            if not act.is_privileged():
                return (
                    f"Защищённый слой личности ('{layer}') не может быть изменён "
                    f"непривилегированным субъектом '{act.value}' ('{old_status}' -> '{new_status}')."
                )

        # 2. Ограничения субъекта LLM
        if act.is_llm():
            if new_status in {"dormant", "archived", "quarantined", "deleted"}:
                return f"Субъект LLM ('{act.value}') не имеет права переводить память в статус '{new_status}'."

        # 3. Ограничения на удаление (DELETED - только привилегированные авторы)
        if new_status == "deleted":
            if not act.is_privileged():
                return (
                    f"Удаление ('deleted') разрешено только привилегированным авторам, "
                    f"но вызвано '{act.value}'."
                )

        return None

    def can_modify_status(
        self,
        old_status: str,
        new_status: str,
        actor: Any = "system",
        identity_layer: Any = None,
        reason: str | None = None,
    ) -> bool:
        """Возвращает True, если политика разрешает мутацию статуса, иначе False."""
        return self.get_policy_error(old_status, new_status, actor, identity_layer, reason) is None

    def validate_modification(
        self,
        old_status: str,
        new_status: str,
        actor: Any = "system",
        identity_layer: Any = None,
        reason: str | None = None,
    ) -> None:
        """Проверяет политику мутации и возбуждает ValueError при нарушении."""
        error = self.get_policy_error(old_status, new_status, actor, identity_layer, reason)
        if error is not None:
            raise ValueError(error)
