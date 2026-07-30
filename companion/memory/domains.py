"""World Model: классификация фактов по слоям знания (user | world | system).

Новые факты получают domain от LLM-экстрактора (FACT_EXTRACTION_PROMPT).
Этот модуль — консервативный heuristic backfill для фактов, созданных до
появления слоёв: по умолчанию всё остаётся "user", а "world"/"system"
выставляется только при явных маркерах, чтобы не исказить личную память.
"""
from __future__ import annotations

import logging
import re
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from companion.memory.store import MemoryStore

logger = logging.getLogger(__name__)

BACKFILL_META_KEY = "domain_backfill_v1"

# Маркеры объективного знания о мире (технологии, продукты, протоколы).
_WORLD_MARKERS = (
    "python", "питон", "asyncio", "aiogram", "sqlite", "faiss", "gemini",
    "google", "api", "json", "telegram", "docker", "linux", "windows",
    "javascript", "typescript", "rust", "golang", "regex", "http",
    "reaper", "dota", "fl studio", "obsidian",
)

# Маркеры знания об устройстве самого компаньона.
_SYSTEM_MARKERS = (
    "amargon", "компаньон", "companion", "ретрив", "retrieval", "rag",
    "промпт", "prompt", "пайплайн", "pipeline", "эмбеддинг", "embedding",
    "контекстное окно", "системная инструкция", "память бота", "memory store",
    " consolidat", "consolidation", "векторн", "faiss",
)

# Признаки личного события/состояния — сильнее любых world/system-маркеров.
_USER_GUARD = (
    "иван", "я ", " мне", " меня", " мой", " моя", " мои", " у меня",
    "морзик", "женя", "алина", "чувству", "настроени", "устал",
    "выпил", "бросил", "начал", "закончил", "купил", "сходил", "встретил",
)

_WS = re.compile(r"\s+")


def classify_domain(text: str) -> str:
    """Консервативная классификация: user — по умолчанию.

    world/system выставляется только если нет личных маркеров, а
    доменные маркеры присутствуют явно.
    """
    t = _WS.sub(" ", (text or "").lower()).strip()
    if not t:
        return "user"
    if any(g in t for g in _USER_GUARD):
        return "user"
    if any(m in t for m in _SYSTEM_MARKERS):
        return "system"
    if any(m in t for m in _WORLD_MARKERS):
        return "world"
    return "user"


def backfill_fact_domains(store: "MemoryStore", *, force: bool = False) -> dict[str, int]:
    """Одноразовая классификация существующих фактов (guard через meta key).

    Обновляет только факты, у которых domain отсутствует/'user' и heuristic
    уверенно даёт world/system. Никогда не понижает world/system обратно в user.
    """
    if not force and store.db.get_meta(BACKFILL_META_KEY, ""):
        return {"checked": 0, "world": 0, "system": 0, "skipped": 1}

    stats = {"checked": 0, "world": 0, "system": 0, "skipped": 0}
    for fact in store.list_all_facts():
        stats["checked"] += 1
        current = (getattr(fact, "domain", "user") or "user").lower()
        if current in ("world", "system"):
            continue  # не трогаем уже классифицированное (в т.ч. LLM'ом)
        domain = classify_domain(fact.fact)
        if domain == "user":
            continue
        store.db.update_fact_fields(fact.id, {"domain": domain})
        stats[domain] += 1

    store.db.set_meta(BACKFILL_META_KEY, "done")
    logger.info(
        "Domain backfill: checked=%d world=%d system=%d",
        stats["checked"], stats["world"], stats["system"],
    )
    return stats
