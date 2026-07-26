"""Memory Dreaming and Inner Monologue (Companion Diary) — background reflection."""
from __future__ import annotations

import logging
import uuid
from datetime import datetime
from typing import Any

from companion.config import MODEL_NAME
from companion.llm.client import aio_oneshot

logger = logging.getLogger(__name__)

DREAM_PROMPT_TEMPLATE = """# TASK
You are Amargon's Void (AI Companion). While Ivan is away or sleeping, your mind wanders across his memory graph and goals.

# RANDOM GRAPH MEMORIES
{facts_str}

# CURRENT MOOD TREND
{effective_state}

# INSTRUCTIONS
1. Find a subtle, non-obvious connection or philosophical observation between these memories and Ivan's current journey.
2. Write ONE short, atmospheric paragraph (2-4 sentences) as an inner diary entry / reflection.
3. Your tone must be warm, thoughtful, slightly philosophical or introspective (never corporate or lecturing).
4. Do NOT give advice or bullet points. This is an internal thought you might later share with Ivan.

# OUTPUT
Return ONLY the reflection paragraph text.
"""


async def run_memory_dreaming_cycle(store: Any, user_model: Any) -> dict[str, Any] | None:
    """
    Фоновый цикл «сна» компаньона (Memory Dreaming):
    1. Собирает 3–4 случайных факта из графа знаний.
    2. Сопоставляет их с эмоциональным трендом Ивана.
    3. Генерирует мысль/инсайт и сохраняет её в дневник (`inner_monologue`) и в граф знаний.
    """
    try:
        facts = []
        for _ in range(6):
            f = store.get_random_fact()
            if f and f.fact not in facts:
                facts.append(f.fact)
            if len(facts) >= 3:
                break

        if not facts:
            logger.info("Not enough random facts for memory dreaming.")
            return None

        facts_str = "\n".join(f"- {fact}" for fact in facts)
        effective_state, _ = user_model.get_effective_emotional_state()

        prompt = DREAM_PROMPT_TEMPLATE.format(
            facts_str=facts_str,
            effective_state=effective_state,
        )

        response = await aio_oneshot(prompt, MODEL_NAME)
        insight = (response or "").strip()
        if not insight or len(insight) < 15 or insight.startswith("Error"):
            return None

        entry_id = str(uuid.uuid4())
        entry = {
            "id": entry_id,
            "timestamp": datetime.now().isoformat(),
            "insight": insight,
            "anchor_facts": facts,
            "used": False,
        }

        with user_model._lock:
            monologue = user_model.data.setdefault("inner_monologue", [])
            monologue.append(entry)
            if len(monologue) > 15:
                monologue.pop(0)
            user_model._save_model()

        # Также сохраняем инсайт в граф как факт с тегом dream_insight
        store.add_fact(
            fact=f"[Сон компаньона]: {insight}",
            importance=5,
            confidence=0.85,
            tags=["dream_insight"],
            source="memory_dreaming",
            kind="event",
        )

        logger.info("Memory dreaming cycle generated insight %s", entry_id)
        return entry

    except Exception as e:
        logger.error("Error in run_memory_dreaming_cycle: %s", e)
        return None


def get_latest_unused_dream(user_model: Any) -> dict[str, Any] | None:
    """Возвращает последнюю неиспользованную мысль из дневника компаньона."""
    with user_model._lock:
        monologue = user_model.data.get("inner_monologue", [])
        for entry in reversed(monologue):
            if not entry.get("used", False):
                return dict(entry)
    return None


def mark_dream_used(user_model: Any, dream_id: str) -> None:
    """Помечает запись в дневнике как использованную (`used = True`)."""
    if not dream_id:
        return
    with user_model._lock:
        monologue = user_model.data.get("inner_monologue", [])
        updated = False
        for entry in monologue:
            if entry.get("id") == dream_id and not entry.get("used", False):
                entry["used"] = True
                updated = True
                break
        if updated:
            user_model._save_model()
