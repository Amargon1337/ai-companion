"""Background task scheduler — circuit breaker, reflection, personality micro-update."""
from __future__ import annotations

import asyncio
import logging
import time
from datetime import datetime
from typing import Any

from companion.user_model import user_model

logger = logging.getLogger(__name__)

# Rate limiter for user_model reflection (prevent burst overload)
_last_reflection_time: dict[int, float] = {}
_REFLECTION_COOLDOWN_SECONDS = 60  # 1 minute between reflections

# Circuit breaker for background tasks (prevent log spam on persistent failures)
_background_task_failures: dict[str, int] = {}
_background_task_cooldown_until: dict[str, float] = {}
_MAX_CONSECUTIVE_FAILURES = 5
_CIRCUIT_BREAKER_COOLDOWN_SECONDS = 600  # 10 minutes

# Semaphore for background tasks (prevent OOM from task pileup)
_background_semaphore = asyncio.Semaphore(5)

# Set to track currently running background tasks
_active_tasks: set[asyncio.Task] = set()


def safe_task(coro, task_name: str = "background") -> asyncio.Task:
    """Fire-and-forget with semaphore, exception logging to logger + self_errors.jsonl."""
    async def _wrapped():
        async with _background_semaphore:
            try:
                await coro
            except Exception as e:
                logger.exception("Background task '%s' failed: %s", task_name, e)
                try:
                    from companion.self_model import self_model
                    self_model.log_error(
                        error_type=f"background_task.{task_name}",
                        query=task_name,
                        expected="success",
                        actual=str(e),
                    )
                except Exception:
                    pass
    task = asyncio.create_task(_wrapped())
    _active_tasks.add(task)
    task.add_done_callback(_active_tasks.discard)
    return task


async def cancel_all_tasks() -> None:
    """Cancel all active background tasks and wait for them to finish."""
    if not _active_tasks:
        return
    logger.info("Cancelling %d active background tasks...", len(_active_tasks))
    tasks_to_cancel = list(_active_tasks)
    for task in tasks_to_cancel:
        task.cancel()
    await asyncio.gather(*tasks_to_cancel, return_exceptions=True)
    _active_tasks.clear()


def _check_circuit_breaker(task_name: str) -> bool:
    now = time.time()
    cooldown = _background_task_cooldown_until.get(task_name)
    if cooldown and now < cooldown:
        logger.debug(f"Circuit breaker active for {task_name}, skipping")
        return False
    return True


def _record_success(task_name: str) -> None:
    _background_task_failures[task_name] = 0


def _record_failure(task_name: str) -> None:
    _background_task_failures[task_name] = _background_task_failures.get(task_name, 0) + 1
    if _background_task_failures[task_name] >= _MAX_CONSECUTIVE_FAILURES:
        cooldown_until = time.time() + _CIRCUIT_BREAKER_COOLDOWN_SECONDS
        _background_task_cooldown_until[task_name] = cooldown_until
        logger.warning(
            f"Circuit breaker triggered for {task_name} after {_MAX_CONSECUTIVE_FAILURES} failures. "
            f"Cooling down for {_CIRCUIT_BREAKER_COOLDOWN_SECONDS}s"
        )

async def background_user_model_reflection(state, store) -> None:
    """Фоновое обновление user model через reflection."""
    task_name = "user_model_reflection"

    if not _check_circuit_breaker(task_name):
        return

    try:
        logger.info("[REFLECTION] Запуск теневого анализа изменений личности пользователя...")
        recent_facts = store.recent_facts(10)

        reflection = await user_model.reflect_after_interaction(
            user_message=state.user_message,
            bot_response=state.llm_response,
            facts_extracted=recent_facts,
            mood_state=state.mood_state,
        )

        if reflection:
            discoveries = len(reflection.get("discoveries", []))
            confirmations = len(reflection.get("confirmations", []))
            falsifications = len(reflection.get("falsifications", []))

            if discoveries > 0 or confirmations > 0 or falsifications > 0:
                logger.info(
                    f"[REFLECTION] User model updated: {discoveries} discoveries, "
                    f"{confirmations} confirmations, {falsifications} falsifications. "
                    f"Детали: {reflection}"
                )

        _record_success(task_name)

    except Exception as e:
        logger.error(f"User model reflection error: {e}")
        _record_failure(task_name)


async def background_personality_micro_update(state, store) -> None:
    task_name = "personality_micro_update"

    if not _check_circuit_breaker(task_name):
        return

    try:
        # Сбор сырых данных — вне локa (I/O к SQLite).
        recent = await asyncio.to_thread(store.recent_messages, 0, 10)
        user_messages = [m.text for m in recent if m.role == "user"]

        if not user_messages:
            return

        all_text = " ".join(user_messages).lower()

        topics = [
            "qa", "тестирование", "работа", "код", "python",
            "аня", "морзик", "семья", "друзья",
            "тревога", "паника", "лекарства", "терапия",
            "музыка", "игры", "фильмы", "книги",
            "спорт", "тренировки", "здоровье"
        ]

        # Считаем дельту до входа в критическую секцию.
        interests_delta: dict[str, int] = {}
        for topic in topics:
            count = all_text.count(topic)
            if count > 0:
                interests_delta[topic] = count

        new_change = None
        if state.message_importance >= 7:
            from companion.security.sanitizer import sanitize_markup
            observation = sanitize_markup(state.user_message or "")[:150]
            new_change = {
                "date": datetime.now().strftime("%Y-%m-%d"),
                "observation": observation,
            }

        if not interests_delta and not new_change:
            return

        def _sync_micro_update(store_ref, delta, change) -> None:
            curr = store_ref.load_personality()
            inter = dict(curr.get("interests", {}))
            for topic, count in delta.items():
                inter[topic] = inter.get(topic, 0) + count
            curr["interests"] = inter

            if change:
                ch_list = list(curr.get("changes", []))
                ch_list.append(change)
                curr["changes"] = ch_list[-20:]

            curr["last_updated"] = datetime.now().isoformat()
            store_ref.save_personality(curr)

        # Критическая секция: read-modify-write под тем же локом, что и
        # generate_personality_snapshot, иначе перетирают изменения друг друга.
        async with store.lock:
            await asyncio.to_thread(_sync_micro_update, store, interests_delta, new_change)

        logger.info("Personality micro-update completed")
        _record_success(task_name)

    except Exception as e:
        logger.error(f"Personality micro-update error: {e}")
        _record_failure(task_name)


def run_background_tasks(uid: int, state, store, user_message_counts: dict[int, int]) -> None:
    if user_message_counts.get(uid, 0) % 10 == 0:
        safe_task(background_personality_micro_update(state, store), "personality_micro_update")
