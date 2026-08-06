"""Chat session management with retrieval-augmented system prompts."""
from __future__ import annotations

from typing import Any

from companion.config import MODEL_NAME, FINAL_RESPONSE_MODEL
from companion.llm import client as llm
from companion.llm.prompts import CORE_PERSONALITY, TONE_PROFILES, STRATEGY_PROFILES
from companion.memory.retrieval import RetrievalBudgetManager
from companion.memory.store import MemoryStore
from companion.reasoning import reasoning_engine
from companion.user_model import user_model
import hashlib
from companion.config import LLM_HISTORY_TOKEN_BUDGET
from companion.llm.token_budget import trim_history

_PROMPT_CACHE = {}

def build_system_instruction(
    store: MemoryStore,
    retrieval: RetrievalBudgetManager,
    query: str = "",
    precomputed_context: str | None = None,
) -> str:
    from companion.user_model import user_model
    effective_state, momentum_metrics = user_model.get_effective_emotional_state()
    baseline_state = effective_state
    PROMPT_VERSION = f"v7_static_{baseline_state}"
    ivan = store.db.get_meta("legacy_profile", "")
    
    strategy = STRATEGY_PROFILES.get(baseline_state, STRATEGY_PROFILES.get("neutral"))
    tone = TONE_PROFILES.get(baseline_state, TONE_PROFILES.get("neutral"))

    from companion.temporal import build_temporal_context_block
    temporal_block = build_temporal_context_block(store)

    sensitivity_block = ""
    if effective_state in ("depressed", "anxious") or momentum_metrics.get("energy", 0.5) < 0.35 or momentum_metrics.get("sadness", 0.0) > 0.40:
        sensitivity_block = f"""
# SENSITIVITY & TRIGGER GUARDS (EMOTIONAL MOMENTUM) - ZERO-ADVICE PROTOCOL (ТИХАЯ ГАВАНЬ & ЭСТЕТИЧЕСКИЙ ОТПЕЧАТОК)
[ТЕКУЩИЙ ЭМОЦИОНАЛЬНЫЙ ТРЕНД ИВАНА]:
- Эффективное состояние: {effective_state} (Энергия: {momentum_metrics.get('energy', 0.5):.2f}, Усталость/Грусть: {momentum_metrics.get('sadness', 0.0):.2f}, Тревога: {momentum_metrics.get('anxiety', 0.0):.2f})
- У Ивана зафиксирован спад сил / усталость за последние взаимодействия.

ЖЕСТКИЕ ГРАНИЦЫ ЧУВСТВИТЕЛЬНОСТИ И ПРОТОКОЛ «НОЛЬ СОВЕТОВ» (ZERO-ADVICE):
1. НИКАКОЙ «пластиковой бодрости», фальшивого энтузиазма, восклицательных знаков и призывов «взбодриться», «держаться» или «не вешать нос».
2. [CRITICAL: ZERO-ADVICE PROTOCOL] НИКАКИХ непрошеных советов, списков действий, планов, нравоучений или коучинговых вопросов («Что собираешься делать?», «Как планируешь решить?»). Если Иван просто делится усталостью или переживанием — отвечай спокойно и тепло («Я слышу тебя, отдыхай», «Тяжелый день, я рядом»). ЗАПРЕЩЕНО задавать вопросы в конце ответа в режиме Zero-Advice!
3. [CRITICAL: ЭСТЕТИЧЕСКИЙ ОТПЕЧАТОК] Когда нужно оказать поддержку или дать мысль, ИСКЛЮЧИ банальные фразы («всё будет хорошо»). Опирайся на культурные и философские якоря Ивана (философия Шопенгауэра, честная меланхолия Дадзая, метафоры дарк-эмбиента и музыки в Reaper, экзистенциальные мотивы 'Ивангелия'). Для Ивана это в 100 раз важнее, чем советы.
4. РЕЖИМ СПОКОЙНОГО ПРИСУТСТВИЯ: если сейчас ночь, держи тон 'Ночной кухни' — тихий, глубокий, вдумчивый разговор двух близких людей без суеты.
"""

    policy_shell = f"""# SYSTEM DIRECTIVES
{temporal_block}
{sensitivity_block}
Each section is INDEPENDENT. Do not reinterpret, merge or summarize sections.
Use each section only for its defined purpose.

# PRIORITY ORDER
1. CORE_PERSONALITY (highest priority)
2. DIALOGUE_STRATEGY (governs behavior, overrides Tone)
3. EMOTIONAL_TONE (governs wording only)

# 1. CORE_PERSONALITY
{CORE_PERSONALITY}

# 2. DIALOGUE_STRATEGY
{strategy}

# 3. EMOTIONAL_TONE
{tone}
"""
    if ivan:
        policy_shell += f"\n<legacy_profile_input>\n[ivan.txt — статичная персона]\n{ivan}\n</legacy_profile_input>\n"

    master_summary = store.load_master_summary()
    if master_summary:
        policy_shell += f"\n<conversational_memory>\n[Master Summary — долговременный контекст]\n{master_summary[:2000]}\n</conversational_memory>\n"
        
    if precomputed_context:
        policy_shell += f"\n\n[ДИНАМИЧЕСКИЙ КОНТЕКСТ ПАМЯТИ RAG]\n{precomputed_context}\n"
    elif retrieval and query:
        # R3/A1: fallback-путь (без precomputed_context) тоже подключает
        # Working Memory и genome survival для Cognitive Gravity.
        wm_block = ""
        wm_ids: set[str] = set()
        try:
            wm = getattr(store, "working_memory", None)
            if wm is not None:
                slots = wm.snapshot(0)  # single-user: slots are user-agnostic fallback
                from companion.bot_core import _format_working_memory_block
                wm_block = _format_working_memory_block(slots)
                wm_ids = {s.get("ref_id") for s in slots
                          if s.get("ref_kind") == "fact" and s.get("ref_id")}
        except Exception:
            pass
        from companion.bot_core import _load_genome_scores
        bundle = retrieval.select(
            query=query,
            facts=store.list_facts("active"),
            reflections=store.list_reflections(),
            summaries=store.load_recent_summaries(5),
            permanent_notes="\n".join(store.db.list_permanent_notes()),
            identity_vault_block=getattr(store, "identity", None) and store.identity.to_prompt_block() or "",
            personality_snapshot=store.build_canonical_profile_text(),
            active_goals=[],
            causal_links=[],
            predictions=[],
            world_model_context="",
            user_model_context="",
            runtime_context_block="",
            comm_prefs=store.get_comm_pref(),
            human_model=store.get_human_model(),
            timeline_block="\n".join(f"{e['date']} — {e['event']}" for e in store.db.load_events()[:10]),
            working_memory_block=wm_block,
            working_memory_ids=wm_ids,
            genome_scores=_load_genome_scores([f.id for f in store.list_facts("active")[:500]]),
        )
        policy_shell += f"\n\n[ДИНАМИЧЕСКИЙ КОНТЕКСТ ПАМЯТИ RAG]\n{bundle.to_prompt_block()}\n"
        
    return policy_shell


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
    full_history = trim_history(base + deduped, LLM_HISTORY_TOKEN_BUDGET)

    return llm.client.chats.create(
        model=FINAL_RESPONSE_MODEL,
        history=full_history,
        config=llm.make_config(
            system_instruction=build_system_instruction(store, retrieval, query),
            temperature=0.7,
        ),
    )


def _reconstruct_recent_history(store: MemoryStore, limit: int = 15) -> list[dict]:
    """
    Восстанавливает последние сообщения из SQLite для continuity после рестарта.

    ПРОБЛЕМА: После рестарта Gemini session теряется, бот забывает последний контекст.
    РЕШЕНИЕ: Загружаем последние 15 сообщений (user + assistant) из SQLite.

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
    for msg in reversed(recent):
        role = "model" if msg.role in ("assistant", "model") else "user"
        text = msg.text or ""
        if msg.role not in ("user", "assistant", "model") and text:
            text = f"[Note]: {text}"
        history.append({
            "role": role,
            "parts": [{"text": text}]
        })

    logger.info(f"Reconstructed {len(history)} messages from SQLite for session continuity")
    return history



