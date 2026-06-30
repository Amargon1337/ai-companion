"""Chat session management with retrieval-augmented system prompts."""
from __future__ import annotations

from typing import Any

from companion.config import MODEL_NAME
from companion.llm import client as llm
from companion.llm.prompts import SYSTEM_INSTRUCTION
from companion.memory.retrieval import RetrievalBudgetManager
from companion.memory.store import MemoryStore
from companion.reasoning import reasoning_engine
from companion.storage.legacy import LegacyStorage


def build_system_instruction(
    store: MemoryStore,
    retrieval: RetrievalBudgetManager,
    query: str = "",
) -> str:
    notes = LegacyStorage.load_permanent_notes()
    pers_snapshot = store.build_personality_snapshot_text()
    ivan = LegacyStorage.load_memory()

    # БЛОК 3: SUMMARY STACK - включаем Tier 3 (master summary)
    master_summary = LegacyStorage.load_master_summary()
    active_goals = reasoning_engine.get_goal_snapshot(query)
    causal_links = reasoning_engine.get_relevant_causal_context(query)
    predictions = reasoning_engine.get_prediction_context(query)
    world_model_context = reasoning_engine.get_world_model_context(query)

    from companion.user_model import user_model
    bundle = retrieval.select(
        query=query,
        facts=store.list_facts("active"),
        reflections=store.list_reflections(),
        summaries=LegacyStorage.load_all_summaries()[-5:],
        permanent_notes=notes,
        personality_snapshot=pers_snapshot,
        active_goals=active_goals,
        causal_links=causal_links,
        predictions=predictions,
        world_model_context=world_model_context,
        user_model_context=user_model.to_prompt_block(),
    )
    ctx = bundle.to_prompt_block()

    # БЛОК 3: Добавляем master summary в system instruction
    result = SYSTEM_INSTRUCTION + "\n\n"

    if master_summary:
        result += f"[Master Summary — долговременный контекст]\n{master_summary[:2000]}\n\n"

    result += (ctx if ctx else pers_snapshot)
    result += f"\n\n[ivan.txt — статичная персона]\n{ivan}"

    return result


def create_default_session(
    store: MemoryStore,
    retrieval: RetrievalBudgetManager,
    history: list[dict] | None = None,
    query: str = "",
) -> Any:
    # БЛОК 1: RESTART MEMORY RECOVERY
    # Если history пустая, восстанавливаем последние сообщения из SQLite
    if not history:
        history = _reconstruct_recent_history(store)

    return llm.client.chats.create(
        model=MODEL_NAME,
        history=history,
        config=llm.make_config(
            system_instruction=build_system_instruction(store, retrieval, query),
            temperature=0.7,
        ),
    )


def _reconstruct_recent_history(store: MemoryStore, limit: int = 30) -> list[dict]:
    """
    Восстанавливает последние сообщения из SQLite для continuity после рестарта.

    ПРОБЛЕМА: После рестарта Gemini session теряется, бот забывает последний контекст.
    РЕШЕНИЕ: Загружаем последние 30 сообщений (user + assistant) из SQLite.

    ВЛИЯНИЕ НА КАЧЕСТВО:
    - После рестарта бот помнит недавний диалог
    - Continuity сохраняется даже до compress
    - Важные сообщения (importance < 6) не теряются

    Args:
        store: MemoryStore instance
        limit: максимум сообщений для восстановления (default 30)

    Returns:
        List[dict]: история в формате Gemini [{role, parts: [{text}]}]
    """
    import logging
    logger = logging.getLogger(__name__)

    # Загружаем последние сообщения (важность >= 5 — фильтруем casual-флуд)
    recent = store.recent_messages(min_importance=5, limit=limit)

    if not recent:
        logger.info("No recent messages to reconstruct")
        return []

    # Конвертируем в формат Gemini history
    history = []
    for msg in recent:
        role = "model" if msg.role == "assistant" else msg.role
        history.append({
            "role": role,
            "parts": [{"text": msg.text}]
        })

    logger.info(f"Reconstructed {len(history)} messages from SQLite for session continuity")
    return history



