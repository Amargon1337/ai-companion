"""Модуль управления жизненным циклом памяти (Phase C2.1).

Реализует формальный направленный граф переходов статусов (State Machine)
и проверку прав авторов и защиты ядра личности (Governance Policy Engine).
"""

from __future__ import annotations

from enum import Enum
from typing import Any


class MemoryLifecycle:
    """Машина состояний жизненного цикла факта (Memory Lifecycle State Machine).
    
    Отвечает за проверку валидности переходов между статусами:
    ACTIVE, DORMANT, ARCHIVED, QUARANTINED, SUPERSEDED, DELETED
    и за применение правил защиты ядра личности (Core Memory Protection).
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

    PROTECTED_LAYERS: set[str] = {
        "core_value",
        "core_belief",
        "core_identity",
    }

    PRIVILEGED_ACTORS: set[str] = {
        "user",
        "admin",
        "user_statement",
        "user_explicit",
    }

    LLM_ACTORS: set[str] = {
        "llm",
        "llm_extraction",
        "llm_inference",
        "assistant",
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
        actor: Any = "system",
        identity_layer: Any = None,
        reason: str | None = None,
    ) -> str | None:
        """Проверяет переход статуса и возвращает причину отказа или None, если переход разрешён.

        Args:
            old_status: Текущий статус факта.
            new_status: Желаемый статус факта.
            actor: Инициатор изменения (USER, SYSTEM, LLM, ADMIN и др.).
            identity_layer: Слой идентичности (core_value, biographical и др.).
            reason: Опциональная причина перехода.

        Returns:
            str с описанием ошибки, если переход запрещён, или None, если разрешён.
        """
        old = self._normalize_str(old_status)
        new = self._normalize_str(new_status)
        act = self._normalize_str(actor)
        layer = self._normalize_str(identity_layer)

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

        # 3. Проверка защиты ядра личности (Core Memory Protection)
        if layer in self.PROTECTED_LAYERS:
            # Автоматические процессы (SYSTEM, LLM) не имеют права менять статус CORE_VALUE/BELIEF/IDENTITY
            if act not in self.PRIVILEGED_ACTORS:
                return (
                    f"Защищённый слой личности ('{layer}') не может быть изменён "
                    f"автоматическим субъектом '{act}' ('{old}' -> '{new}')."
                )

        # 4. Проверка ограничений прав LLM
        if act in self.LLM_ACTORS:
            if new in {"dormant", "archived", "quarantined", "deleted"}:
                return f"Субъект LLM ('{act}') не имеет права переводить память в статус '{new}'."

        # 5. Проверка прав на удаление (DELETED - только привилегированный автор)
        if new == "deleted":
            if act not in self.PRIVILEGED_ACTORS:
                return f"Удаление ('deleted') разрешено только привилегированным авторам, но вызвано '{act}'."

        return None

    def can_transition(
        self,
        old_status: Any,
        new_status: Any,
        actor: Any = "system",
        identity_layer: Any = None,
        reason: str | None = None,
    ) -> bool:
        """Возвращает True, если переход разрешён, иначе False.

        Args:
            old_status: Текущий статус факта.
            new_status: Желаемый статус факта.
            actor: Инициатор изменения (USER, SYSTEM, LLM, ADMIN и др.).
            identity_layer: Слой идентичности (core_value, biographical и др.).
            reason: Опциональная причина перехода.

        Returns:
            bool: True если операция допустима, False если отклонена.
        """
        return self.get_transition_error(old_status, new_status, actor, identity_layer, reason) is None

    def validate_transition(
        self,
        old_status: Any,
        new_status: Any,
        actor: Any = "system",
        identity_layer: Any = None,
        reason: str | None = None,
    ) -> None:
        """Проверяет переход и возбуждает ValueError, если переход запрещён."""
        error = self.get_transition_error(old_status, new_status, actor, identity_layer, reason)
        if error is not None:
            raise ValueError(error)
