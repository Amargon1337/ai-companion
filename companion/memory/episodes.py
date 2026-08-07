"""Episodic Memory Engine — извлечение структурированных эпизодов из недавних фактов.

Episode = дата + что произошло + кто участвовал + эмоции + чему научило + связанные факты.
Создаёт: запись в таблице episodes + retrieval-факет с тегом "episode" + связи fact_relations.
"""
from __future__ import annotations

import logging
import uuid
from collections import defaultdict
from datetime import datetime, timedelta
from typing import TYPE_CHECKING, Any, List

from companion.llm.client import aio_oneshot, parse_json_object
from companion.llm.prompts import SUMMARY_PROMPT
from companion.models import Episode, Fact, FactRelation
from companion.config import MODEL_NAME

if TYPE_CHECKING:
    from companion.memory.store import MemoryStore

logger = logging.getLogger(__name__)

EPISODE_EXTRACTION_PROMPT = """Ты — аналитик эпизодической памяти. Дан список фактов за один день (или близкий период).
Твоя задача — объединить их в ОДИН связный эпизод (историю).

Верни СТРОГО JSON:
{
  "title": "Короткий заголовок: что произошло (макс. 80 симв.)",
  "narrative": "2-4 предложения — история события/состояния. Пиши от третьего лица: \"Иван...\". Без воды.",
  "participants": ["имена/роли участников"],
  "emotions": {"joy": 0.0, "sadness": 0.0, "anger": 0.0, "fear": 0.0, "hope": 0.0},
  "lesson": "Чему это научило / какой вывод (пустая строка, если нет)",
  "importance": 7
}

Факты:
{facts}

Сообщения контекста:
{messages}
"""


class EpisodeEngine:
    """Извлекает эпизоды из недавних высоко-важных фактов."""

    def __init__(self, store: "MemoryStore", lookback_days: int = 2, min_importance: int = 7, min_facts: int = 2) -> None:
        self.store = store
        self.lookback_days = lookback_days
        self.min_importance = min_importance
        self.min_facts = min_facts

    def _candidate_facts(self) -> List[Fact]:
        """Факты за lookback_days с importance >= min_importance, без тега episode, без связи к эпизоду."""
        cutoff = (datetime.now() - timedelta(days=self.lookback_days)).strftime("%Y-%m-%d")
        facts = self.store.list_facts("active")
        res = []
        for f in facts:
            if f.importance < self.min_importance:
                continue
            if (f.date or f.created_at or "") < cutoff:
                continue
            if any(t.lower() == "episode" for t in f.tags):
                continue
            # Проверяем, не связан ли уже с эпизодом
            rels = self.store.db.get_fact_relations(f.id)
            if any(r.get("relation") == "related_to" and "episode:" in (r.get("reason") or "") for r in rels):
                continue
            res.append(f)
        return res

    def _group_by_day(self, facts: List[Fact]) -> dict[str, List[Fact]]:
        groups = defaultdict(list)
        for f in facts:
            day = (f.date or f.created_at or "")[:10]
            if len(day) == 10:
                groups[day].append(f)
        return {d: fs for d, fs in groups.items() if len(fs) >= self.min_facts}

    async def _extract_episode(self, day: str, facts: List[Fact]) -> Episode | None:
        # Берём сообщения за этот день для контекста
        msgs = self.store.recent_messages(min_importance=2, limit=100)
        day_msgs = [m for m in msgs if m.ts.startswith(day)]
        msg_text = "\n".join(f"[{m.role}] {m.text[:200]}" for m in day_msgs[-15:]) or "нет сообщений"

        fact_lines = "\n".join(f"- {f.fact} (imp={f.importance})" for f in facts)
        prompt = EPISODE_EXTRACTION_PROMPT.format(facts=fact_lines, messages=msg_text)

        try:
            raw = await aio_oneshot(prompt, MODEL_NAME)
            data = parse_json_object(raw)
        except Exception as e:
            logger.warning("Episode LLM extraction failed for %s: %s", day, e)
            data = None

        if not data or not data.get("narrative"):
            # Детерминированный фоллбэк
            top = facts[0].fact
            data = {
                "title": f"События {day}",
                "narrative": f"В этот день: {top}.",
                "participants": [],
                "emotions": {},
                "lesson": "",
                "importance": max(f.importance for f in facts),
            }

        # Создаём retrieval-факет для эпизода
        narrative = str(data.get("narrative", "")).strip()
        title = str(data.get("title", "")).strip() or f"Эпизод {day}"
        ep_fact = Fact(
            fact=f"[Эпизод] {title}: {narrative}",
            date=day,
            importance=max(6, min(10, int(data.get("importance", 7)))),
            confidence=0.85,
            source="episode_engine",
            source_type="system",
            memory_kind="event",
            tags=["episode", day],
            status="active",
        )
        persisted_episode_fact = self.store.add_fact(ep_fact)

        # A deduplicated episode must reference the canonical persisted fact.
        # Otherwise the Episode row and its graph edges point at a transient ID.
        ep_fact = persisted_episode_fact

        # Episode запись
        episode = Episode(
            title=title,
            narrative=narrative,
            date=day,
            participants=[str(p) for p in data.get("participants", [])],
            emotions={k: float(v) for k, v in (data.get("emotions", {}) or {}).items() if float(v) > 0},
            lesson=str(data.get("lesson", "")).strip(),
            fact_ids=[f.id for f in facts],
            fact_id=ep_fact.id,
            importance=ep_fact.importance,
            confidence=0.85,
        )
        self.store.db.upsert_episode(episode.to_dict())

        # Связи episode_fact -> source facts
        for f in facts:
            rel = FactRelation(
                from_id=ep_fact.id,
                to_id=f.id,
                relation="related_to",
                reason=f"episode:{episode.id}",
                confidence=0.9,
            )
            self.store.add_relation(rel)

        logger.info("Created episode %s for %d facts on %s", episode.id, len(facts), day)
        return episode

    async def extract_recent(self) -> List[Episode]:
        """Основной метод: найти кандидатов, сгруппировать по дням, извлечь эпизоды."""
        candidates = self._candidate_facts()
        if not candidates:
            return []
        groups = self._group_by_day(candidates)
        episodes: List[Episode] = []
        for day, facts in groups.items():
            ep = await self._extract_episode(day, facts)
            if ep:
                episodes.append(ep)
        return episodes


async def run_episode_engine(store: "MemoryStore") -> None:
    """Точка входа для вызова из compress pipeline."""
    engine = EpisodeEngine(store)
    try:
        await engine.extract_recent()
    except Exception as e:
        logger.error("EpisodeEngine failed: %s", e, exc_info=True)