"""Master Summary Management — БЛОК 3: Tier 3 долговременный контекст."""
from __future__ import annotations

import logging

from companion.llm import client as llm

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
        from companion.bot_core import memory_store
        current_master = memory_store.load_master_summary()

        # Первый compress — master summary = первый summary
        if not current_master:
            memory_store.save_master_summary(new_summary[:2000])
            logger.info("Created initial master summary")
            return

        # Backup current master summary before update
        import os
        from companion.config import BASE_DIR
        backup_path = os.path.join(BASE_DIR, "master_summary.txt.bak")
        try:
            with open(backup_path, "w", encoding="utf-8") as f:
                f.write(current_master)
        except Exception as backup_err:
            logger.warning("Failed to backup master summary: %s", backup_err)

        # Обновление через LLM
        prompt = MASTER_SUMMARY_UPDATE_PROMPT.format(
            current_master=current_master,
            new_summary=new_summary,
        )

        updated = llm.oneshot(prompt)

        if not _validate_master_summary(updated):
            logger.warning("summary_validation_status: Failed validation, retrying...")
            updated = llm.oneshot(prompt + "\n\nВАЖНО: Обязательно включи секции [Личность], [История], [Отношения], [Паттерны], [Актуальное состояние].")
            if not _validate_master_summary(updated):
                logger.error("summary_validation_status: CRITICAL: Master summary update failed validation twice. Keeping old summary.")
                return
        
        logger.info("summary_validation_status: Passed validation.")

        memory_store.save_master_summary(updated[:2000])
        logger.info("Updated master summary (%d chars)", len(updated))

    except Exception as e:
        logger.error(f"Master summary update failed: {e}")
        # Не падаем — master summary не критичен для работы


def _validate_master_summary(text: str | None) -> bool:
    """Validate that master summary contains expected structure."""
    if not text or len(text) < 100:
        return False
    expected_sections = ["[Личность]", "[История]", "[Отношения]", "[Паттерны]", "[Актуальное"]
    matches = sum(1 for section in expected_sections if section in text)
    return matches >= 2
