"""Master Summary Management — БЛОК 3: Tier 3 долговременный контекст."""
from __future__ import annotations

import logging

from companion.llm import client as llm
from companion.storage.legacy import LegacyStorage

logger = logging.getLogger(__name__)

MASTER_SUMMARY_UPDATE_PROMPT = """Ты обновляешь master summary — долговременный контекст о пользователе.

ТЕКУЩИЙ MASTER SUMMARY:
{current_master}

НОВЫЙ COMPRESS SUMMARY:
{new_summary}

ЗАДАЧА:
1. Интегрируй новую информацию в master summary
2. Сохрани ключевые долгосрочные факты
3. Обнови изменившуюся информацию
4. Удали устаревшее
5. Максимум 2000 символов

Формат master summary:
[Личность]
- Имя, возраст, работа, ключевые характеристики

[История]
- Важные события и изменения

[Отношения]
- Ключевые люди и связи

[Паттерны]
- Повторяющееся поведение, привычки, проблемы

[Актуальное состояние]
- Текущие цели, проблемы, фокус

Пиши кратко, только суть. Без воды."""


def update_master_summary(new_summary: str) -> None:
    """
    БЛОК 3: AUTO-UPDATE MASTER SUMMARY

    Обновляет master summary после каждого compress.

    ПРОБЛЕМА: Старые summaries не используются, долговременный контекст теряется.
    РЕШЕНИЕ: Master summary аккумулирует ключевую информацию.

    ВЛИЯНИЕ НА КАЧЕСТВО:
    - Бот помнит пользователя через месяцы общения
    - Долгосрочные изменения отслеживаются
    - После рестарта доступен долговременный контекст

    Args:
        new_summary: свежий summary из текущего compress
    """
    try:
        current_master = LegacyStorage.load_master_summary()

        # Первый compress — master summary = первый summary
        if not current_master:
            LegacyStorage.save_master_summary(new_summary[:2000])
            logger.info("Created initial master summary")
            return

        # Обновление через LLM
        prompt = MASTER_SUMMARY_UPDATE_PROMPT.format(
            current_master=current_master,
            new_summary=new_summary,
        )

        updated = llm.oneshot(prompt)

        if updated and len(updated) > 100:  # Sanity check
            LegacyStorage.save_master_summary(updated[:2000])
            logger.info(f"Updated master summary ({len(updated)} chars)")
        else:
            logger.warning("Master summary update returned invalid result, keeping old")

    except Exception as e:
        logger.error(f"Master summary update failed: {e}")
        # Не падаем — master summary не критичен для работы
