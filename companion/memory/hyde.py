"""HyDE (Hypothetical Document Embeddings) module for abstract and emotional queries."""
from __future__ import annotations

import logging

logger = logging.getLogger(__name__)

HYDE_PROMPT = """Пользователь задал вопрос: "{query}"

Напиши ОДНО предложение — гипотетический факт из его дневника или памяти, который бы идеально ответил на этот вопрос. Пиши от третьего лица ("Иван ..."). Не добавляй никаких пояснений."""


def should_use_hyde(query: str) -> bool:
    """Determine whether query is short or emotional and should trigger HyDE."""
    if not query or not query.strip():
        return False
    words = query.strip().split()
    if len(words) < 5:
        return True
    lowered = query.lower()
    emotion_markers = [
        "почему", "зачем", "как справиться", "как быть", "устал",
        "устала", "бесит", "раздражает", "грустно", "плохо",
        "тревож", "сил нет", "нет сил", "выгоран", "одинок", "боюсь",
        "страшн", "не знаю что делать", "чувствую",
    ]
    return any(marker in lowered for marker in emotion_markers)


def generate_hypothetical_fact(query: str) -> str:
    """Generate a Hypothetical Document Embedding (HyDE) fact for abstract/emotional queries."""
    if not query or not query.strip():
        return query

    from companion.llm import client as llm

    prompt = HYDE_PROMPT.format(query=query.strip())
    try:
        res = llm.oneshot(prompt, temperature=0.3)
        text = (res or "").strip().strip('"').strip("'")
        if text:
            text = text.splitlines()[0].strip()
        if text and len(text) >= 5:
            return text
    except Exception as e:
        logger.debug("LLM HyDE generation failed, falling back to query: %s", e)

    return query
