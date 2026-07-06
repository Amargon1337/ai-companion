"""Chat session management with retrieval-augmented system prompts."""
from __future__ import annotations

from typing import Any

from companion.config import MODEL_NAME, FINAL_RESPONSE_MODEL
from companion.llm import client as llm
from companion.llm.prompts import CORE_PERSONALITY, TONE_PROFILES, STRATEGY_PROFILES
from companion.memory.retrieval import RetrievalBudgetManager
from companion.memory.store import MemoryStore
from companion.reasoning import reasoning_engine
from companion.storage.legacy import LegacyStorage
from companion.user_model import user_model
import hashlib

_PROMPT_CACHE = {}

def build_system_instruction(
    store: MemoryStore,
    retrieval: RetrievalBudgetManager,
    query: str = "",
) -> str:
    PROMPT_VERSION = "v6"
    
    notes = LegacyStorage.load_permanent_notes()
    pers_snapshot = store.build_personality_snapshot_text()
    ivan = LegacyStorage.load_memory()

    # БЛОК 3: SUMMARY STACK - включаем Tier 3 (master summary)
    master_summary = store.load_master_summary()
    active_goals = reasoning_engine.get_goal_snapshot(query)
    causal_links = reasoning_engine.get_relevant_causal_context(query)
    predictions = reasoning_engine.get_prediction_context(query)
    world_model_context = reasoning_engine.get_world_model_context(query)

    bundle = retrieval.select(
        query=query,
        facts=store.list_facts("active"),
        reflections=store.list_reflections(),
        summaries=store.load_recent_summaries(5),
        permanent_notes=notes,
        identity_vault_block=store.identity.to_prompt_block(),
        personality_snapshot=pers_snapshot,
        active_goals=active_goals,
        causal_links=causal_links,
        predictions=predictions,
        world_model_context=world_model_context,
        user_model_context=user_model.to_prompt_block(),
    )
    ctx = bundle.to_prompt_block()

    # --- Policy Engine (Dynamic Tone V6) ---
    raw_state = user_model.data.get("emotional_timeline", {}).get("baseline_state", "neutral").lower()
    from companion.user_model import UserModel
    if raw_state not in UserModel.CORE_STATES:
        current_state = "neutral"
    else:
        current_state = raw_state

    mood_intensity = 1 

    strategy = STRATEGY_PROFILES.get(current_state, STRATEGY_PROFILES["neutral"])
    tone = TONE_PROFILES.get(current_state, TONE_PROFILES["neutral"])

    prompt_hash_source = f"{PROMPT_VERSION}_{current_state}_{mood_intensity}_{strategy}_{tone}"
    cache_key = hashlib.sha256(prompt_hash_source.encode('utf-8')).hexdigest()

    if "default" not in _PROMPT_CACHE or _PROMPT_CACHE["default"].get("key") != cache_key:
        policy_shell = f"""# SYSTEM DIRECTIVES
Each section is INDEPENDENT. Do not reinterpret, merge or summarize sections.
Use each section only for its defined purpose.

# PRIORITY ORDER
1. CORE_PERSONALITY (highest priority)
2. DIALOGUE_STRATEGY (governs behavior, overrides Tone)
3. EMOTIONAL_TONE (governs wording only)
4. CONTEXT

# 1. CORE_PERSONALITY
{CORE_PERSONALITY}

# 2. DIALOGUE_STRATEGY
{strategy}

# 3. EMOTIONAL_TONE
{tone}
"""
        _PROMPT_CACHE["default"] = {"key": cache_key, "compiled_prompt": policy_shell}
        
    # Context assembly (not cached because it changes every request)
    memory_block = ""
    if ivan:
        memory_block += f"<system_identity>\n[ivan.txt — статичная персона]\n{ivan}\n</system_identity>\n\n"
    
    if ctx:
        memory_block += ctx
    elif pers_snapshot:
        memory_block += f"\n<user_profile>\n{pers_snapshot}\n</user_profile>\n\n"
        
    if master_summary:
        memory_block += f"\n<conversational_memory>\n[Master Summary — долговременный контекст]\n{master_summary[:2000]}\n</conversational_memory>\n"

    final_system_prompt = _PROMPT_CACHE["default"]["compiled_prompt"] + f"\n# 4. CONTEXT\n{memory_block}\n"
    
    return final_system_prompt


def create_default_session(
    store: MemoryStore,
    retrieval: RetrievalBudgetManager,
    history: list[dict] | None = None,
    query: str = "",
) -> Any:
    # БЛОК 1: RESTART MEMORY RECOVERY
    # Всегда реконструируем последние сообщения из SQLite для continuity.
    # Раньше guard "if not history" пропускал реконструкцию, если передавали
    # summary-контекст → бот забывал несжатое окно после рестарта.
    reconstructed = _reconstruct_recent_history(store)
    base = list(history) if history else []
    # Deduplicate: reconstructed may overlap with base summary context.
    # Use text content as key to avoid sending the same message twice.
    seen_texts = {(m.get("parts", [{}])[0].get("text", "")).strip()
                  for m in base if m.get("parts")}
    deduped = [m for m in reconstructed
               if (m.get("parts", [{}])[0].get("text", "")).strip() not in seen_texts]
    # summary-контекст первым, затем уникальные недавние реплики.
    full_history = base + deduped

    return llm.client.chats.create(
        model=FINAL_RESPONSE_MODEL,
        history=full_history,
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



