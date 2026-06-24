"""Search Grounding Router — memory | world | mixed."""
from __future__ import annotations

import re

from companion.models import QueryIntent

WORLD_PATTERNS = [
    r"\bновост",
    r"\bчто нового\b",
    r"\bкурс\b",
    r"\bцена\b",
    r"\bкогда вышл",
    r"\bпоследн\w+ верс",
    r"\bактуальн",
    r"\bсегодня в мире\b",
    r"\bgemini\b",
    r"\bopenai\b",
    r"\bрелиз\b",
    r"\bчто происходит в\b",
    r"\bпогода\b",
    r"\bкурс доллара\b",
    r"\bкурс евро\b",
]

MEMORY_PATTERNS = [
    r"\bпомнишь\b",
    r"\bчто ты знаешь\b",
    r"\bчто ты помнишь\b",
    r"\bмы говорили\b",
    r"\bрассказывал\b",
    r"\bнапомни\b",
    r"\bпро меня\b",
    r"\bмоя жизнь\b",
    r"\bмоя жизнь\b",
    r"\bмои\b",
    r"\bиван\b",
    r"\bаня\b",
    r"\bморзик\b",
    r"\bполин",
    r"\bалин",
    r"\bзапомни\b",
]


def classify_intent(text: str) -> tuple[QueryIntent, float]:
    """Return intent and confidence 0..1."""
    t = text.lower().strip()
    if not t:
        return "memory", 0.5

    world_hits = sum(1 for p in WORLD_PATTERNS if re.search(p, t))
    memory_hits = sum(1 for p in MEMORY_PATTERNS if re.search(p, t))

    if world_hits > 0 and memory_hits > 0:
        return "mixed", min(0.9, 0.5 + (world_hits + memory_hits) * 0.1)
    if world_hits > 0:
        return "world", min(0.95, 0.55 + world_hits * 0.15)
    if memory_hits > 0:
        return "memory", min(0.95, 0.55 + memory_hits * 0.1)

    # Questions about external topics often start with "что" without personal refs
    if t.startswith(("что такое ", "кто такой ", "когда ", "где ", "сколько стоит")):
        if memory_hits == 0:
            return "world", 0.65

    return "memory", 0.6
