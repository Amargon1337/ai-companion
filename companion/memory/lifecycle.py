"""Модуль управления жизненным циклом памяти (Phase C2.1 / C2.1.5).

Реализует формальный направленный граф переходов статусов (State Machine).
Проверка прав авторов и защиты ядра личности вынесена в GovernancePolicy.
"""

from __future__ import annotations

from enum import Enum
from typing import Any


class MemoryLifecycle:
    """Машина состояний жизненного цикла факта (Memory Lifecycle State Machine).

    Отвечает исключительно за проверку валидности переходов между статусами
    по направленному графу состояний:
    ACTIVE, DORMANT, ARCHIVED, QUARANTINED, SUPERSEDED, DELETED.
    """

    ALLOWED_TRANSITIONS: dict[str, list[str]] = {
        "active": [
            "dormant",
            "quarantined",
            "superseded",
            "archived",
            "deleted",
        ],
        "dormant": [
            "active",
            "archived",
            "quarantined",
            "deleted",
        ],
        "archived": [
            "active",
            "deleted",
        ],
        "quarantined": [
            "active",
            "deleted",
        ],
        "superseded": [],  # Терминальное состояние для замещённой памяти
        "deleted": [],     # Терминальное состояние для удалённой памяти
    }

    def _normalize_str(self, value: Any) -> str:
        """Приводит строковое значение или Enum к нижнему регистру."""
        if value is None:
            return ""
        if isinstance(value, Enum):
            return str(value.value).strip().lower()
        return str(value).strip().lower()

    def get_transition_error(
        self,
        old_status: Any,
        new_status: Any,
        *args: Any,
        **kwargs: Any,
    ) -> str | None:
        """Проверяет переход статуса по графу и возвращает причину отказа или None.

        Args:
            old_status: Текущий статус факта.
            new_status: Желаемый статус факта.

        Returns:
            str с описанием ошибки, если переход запрещён, или None, если разрешён.
        """
        old = self._normalize_str(old_status)
        new = self._normalize_str(new_status)

        # 0. Переход в тот же статус всегда разрешён (no-op)
        if old == new:
            return None

        # 1. Проверка существования статусов
        if old not in self.ALLOWED_TRANSITIONS:
            return f"Неизвестный исходный статус '{old}'."
        if new not in self.ALLOWED_TRANSITIONS:
            return f"Неизвестный целевой статус '{new}'."

        # 2. Проверка графа переходов
        if new not in self.ALLOWED_TRANSITIONS[old]:
            return f"Переход '{old}' -> '{new}' запрещён графом состояний."

        return None

    def can_transition(
        self,
        old_status: Any,
        new_status: Any,
        *args: Any,
        **kwargs: Any,
    ) -> bool:
        """Возвращает True, если переход разрешён графом состояний, иначе False."""
        return self.get_transition_error(old_status, new_status) is None

    def validate_transition(
        self,
        old_status: Any,
        new_status: Any,
        *args: Any,
        **kwargs: Any,
    ) -> None:
        """Проверяет переход и возбуждает ValueError, если переход запрещён графом."""
        error = self.get_transition_error(old_status, new_status)
        if error is not None:
            raise ValueError(error)
