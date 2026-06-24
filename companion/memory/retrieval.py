"""Retrieval Budget Manager — ranked context within token budget."""
from __future__ import annotations

from companion.config import (
    RETRIEVAL_CHAR_BUDGET,
    RETRIEVAL_MAX_FACTS,
    RETRIEVAL_MAX_REFLECTIONS,
)
from companion.memory.importance import retrieval_score
from companion.models import ContextBundle, Fact, Reflection


def mood_to_retrieval_boost(mood: dict[str, float] | None, fact: str) -> float:
    """
    Преобразует mood в retrieval boost для факта.

    Args:
        mood: dict {anxiety, anger, sadness, energy} from analyzer
        fact: текст факта

    Returns:
        boost score (0.0 - 0.3)
    """
    if not isinstance(mood, dict):
        return 0.0

    boost = 0.0
    f_lower = fact.lower()

    if mood.get("anxiety", 0) > 0.5:
        if any(kw in f_lower for kw in ["морзик", "обещан", "якор", "помогает", "справляюсь"]):
            boost += 0.3
        elif any(kw in f_lower for kw in ["техник", "дыхан", "лекарства"]):
            boost += 0.2

    if mood.get("sadness", 0) > 0.5:
        if any(kw in f_lower for kw in ["достижен", "получилось", "горжусь", "ценю"]):
            boost += 0.25
        elif any(kw in f_lower for kw in ["друзья", "поддержк", "любят"]):
            boost += 0.2

    if mood.get("anger", 0) > 0.5:
        if any(kw in f_lower for kw in ["триггер", "причина", "что помогает"]):
            boost += 0.2

    if mood.get("energy", 0.5) < 0.3:
        if any(kw in f_lower for kw in ["сон", "отдых", "режим", "энергия"]):
            boost += 0.2

    return min(0.3, boost)


class RetrievalBudgetManager:
    def __init__(
        self,
        char_budget: int = RETRIEVAL_CHAR_BUDGET,
        max_facts: int = RETRIEVAL_MAX_FACTS,
        max_reflections: int = RETRIEVAL_MAX_REFLECTIONS,
    ) -> None:
        self.char_budget = char_budget
        self.max_facts = max_facts
        self.max_reflections = max_reflections

    def select(
        self,
        query: str,
        facts: list[Fact],
        reflections: list[Reflection],
        summaries: list[str] | None = None,
        permanent_notes: str = "",
        personality_snapshot: str = "",
        include_archived: bool = False,
        recent_messages: list | None = None,
        active_goals: list[str] | None = None,
        causal_links: list[str] | None = None,
        predictions: list[str] | None = None,
        world_model_context: str = "",
        mood: dict | None = None,
    ) -> ContextBundle:
        summaries = summaries or []
        active_goals = active_goals or []
        causal_links = causal_links or []
        predictions = predictions or []

        active_facts = [
            f for f in facts
            if f.status == "active" or (include_archived and f.status != "inactive")
        ]

        # БЛОК 2: PINNED FACTS GUARANTEE
        pinned_facts = self.extract_pinned_facts(active_facts)
        regular_facts = [f for f in active_facts if f.id not in {p.id for p in pinned_facts}]

        def _ranked_score(f: Fact) -> float:
            base = retrieval_score(f.to_dict(), query)
            mood_boost = mood_to_retrieval_boost(mood, f.fact) if mood else 0.0
            return base + mood_boost

        ranked_regular = sorted(
            regular_facts,
            key=_ranked_score,
            reverse=True,
        )

        available_slots = self.max_facts - len(pinned_facts)
        ranked_facts = pinned_facts + ranked_regular[:max(0, available_slots)]

        active_refl = [r for r in reflections if r.status == "active"]
        ranked_refl = sorted(
            active_refl,
            key=lambda r: (
                r.importance / 10.0
                + (0.5 if query and query.lower() in r.insight.lower() else 0)
            ),
            reverse=True,
        )[: self.max_reflections]

        # БЛОК 3: SUMMARY STACK (3-tier)
        picked_summaries: list[str] = []
        if summaries:
            if summaries:
                picked_summaries.append(summaries[-1][:1500])

            if query and len(summaries) > 1:
                q = query.lower()
                q_words = set(q.split())

                scored = []
                for s in summaries[:-1]:
                    s_lower = s.lower()
                    overlap = sum(1 for w in q_words if w in s_lower)
                    if overlap > 0:
                        scored.append((overlap, s))

                scored.sort(reverse=True)
                for _, s in scored[:3]:
                    picked_summaries.append(s[:1200])

        # NEW: Add recent high-importance messages to context
        picked_messages: list[str] = []
        if recent_messages:
            for msg in recent_messages[-7:]:
                if msg.importance >= 6 and msg.role == "user":
                    picked_messages.append(f"[{msg.ts[:16]}] {msg.text[:200]}")

        bundle = ContextBundle(
            facts=ranked_facts,
            reflections=ranked_refl,
            summaries=picked_summaries,
            permanent_notes=permanent_notes[:800] if permanent_notes else "",
            personality_snapshot=personality_snapshot,
            recent_messages=picked_messages,
            active_goals=active_goals[:5],
            causal_links=causal_links[:5],
            predictions=predictions[:5],
            world_model_context=world_model_context[:1200],
        )

        # Trim if over budget
        while len(bundle.to_prompt_block()) > self.char_budget and bundle.facts:
            bundle.facts.pop()
        while len(bundle.to_prompt_block()) > self.char_budget and bundle.summaries:
            bundle.summaries.pop()
        while len(bundle.to_prompt_block()) > self.char_budget and bundle.reflections:
            bundle.reflections.pop()

        return bundle

    def extract_pinned_facts(self, facts: list[Fact]) -> list[Fact]:
        """
        БЛОК 2: FACT RETRIEVAL GUARANTEE

        Извлекает pinned facts, которые должны ВСЕГДА попадать в prompt.
        """
        pinned = []

        for f in facts:
            tags_lower = [t.lower() for t in f.tags]

            if any(tag in tags_lower for tag in ["pinned", "core_identity", "anchor"]):
                pinned.append(f)
            elif f.memory_kind == "permanent":
                pinned.append(f)
            elif f.importance >= 9:
                pinned.append(f)

        return sorted(pinned, key=lambda f: f.importance, reverse=True)[:5]
