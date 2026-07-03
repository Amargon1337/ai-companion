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
        identity_vault_block: str = "",
        personality_snapshot: str = "",
        include_archived: bool = False,
        recent_messages: list | None = None,
        active_goals: list[str] | None = None,
        causal_links: list[str] | None = None,
        predictions: list[str] | None = None,
        world_model_context: str = "",
        user_model_context: str = "",
        unified_profile_block: str = "",
        mood: dict | None = None,
        faiss_scores: dict[str, float] | None = None,
    ) -> ContextBundle:
        summaries = summaries or []
        active_goals = active_goals or []
        causal_links = causal_links or []
        predictions = predictions or []
        faiss_scores = faiss_scores or {}

        active_facts = [
            f for f in facts
            if f.status == "active" or (include_archived and f.status != "inactive")
        ]

        # БЛОК 2: PINNED FACTS GUARANTEE
        from companion.bot_core import is_explicit_search_request
        if query and is_explicit_search_request(query):
            pinned_facts_all = []
        else:
            pinned_facts_all = self.extract_pinned_facts(active_facts)
            
        pinned_facts = []
        pinned_chars = 0
        for f in pinned_facts_all:
            if pinned_chars + len(f.fact) <= 1500:
                pinned_facts.append(f)
                pinned_chars += len(f.fact)

        regular_facts = [f for f in active_facts if f.id not in {p.id for p in pinned_facts}]

        def _ranked_score(f: Fact, semantic_score: float = 0.0) -> float:
            from companion.memory.importance import decay_factor, days_since
            age = days_since(f.date or f.created_at)
            recency = decay_factor(age, f.memory_kind)
            
            if semantic_score == 0.0 and query:
                q = query.lower()
                ft = f.fact.lower()
                tags = [t.lower() for t in f.tags]
                if q in ft:
                    semantic_score = 1.0
                else:
                    qw = set(q.split())
                    fw = set(ft.split())
                    overlap = len(qw & fw) / max(len(qw), 1)
                    tag_hit = any(q in tag or tag in q for tag in tags)
                    semantic_score = min(1.0, overlap * 0.8 + (0.3 if tag_hit else 0))

            semantic = semantic_score * 0.50
            importance = (f.importance / 10) * 0.30
            recency_val = recency * 0.20
            mood_boost = mood_to_retrieval_boost(mood, f.fact) if mood else 0.0
            
            final_score = semantic + importance + recency_val + mood_boost
            
            import logging
            logger = logging.getLogger(__name__)
            logger.info("Fact %s retrieval: FAISS=%.3f Final=%.3f", f.id, semantic_score, final_score)
            return final_score

        # Rank regular facts
        ranked_regular = []
        for f in regular_facts:
            semantic = faiss_scores.get(f.id, 0.0)
            score = _ranked_score(f, semantic)
            if query and is_explicit_search_request(query):
                # For explicit searches, only include facts strictly matching the topic (semantic > 0.2)
                # Ignore importance/recency boosts if semantic match is poor.
                if semantic < 0.2:
                    continue
            ranked_regular.append((f, score))
            
        ranked_regular = sorted(
            ranked_regular,
            key=lambda item: item[1],
            reverse=True,
        )
        ranked_facts = pinned_facts + [f for f, s in ranked_regular]

        # Rank reflections (already filtered by FAISS in bot_core)
        active_refl = [r for r in reflections if r.status == "active"]
        ranked_refl = sorted(active_refl, key=lambda r: r.importance, reverse=True)

        picked_messages: list[str] = []
        if recent_messages:
            for msg in recent_messages[-7:]:
                if msg.importance >= 6 and msg.role == "user":
                    picked_messages.append(f"[{msg.ts[:16]}] {msg.text[:200]}")

        # TIER ENFORCEMENT (Tokens -> Chars: 1 token = 4 chars)
        def limit_list(items, max_chars, get_text):
            res, cur = [], 0
            for x in items:
                t = get_text(x)
                if cur + len(t) > max_chars: break
                res.append(x)
                cur += len(t)
            return res

        # T0: IdentityVault (500 tokens = 2000 chars)
        identity_vault_block = identity_vault_block[:2000]
        
        # T1: Personality snapshot (1500 tokens = 6000 chars)
        personality_snapshot = personality_snapshot[:6000]
        
        # T2: Master summary (permanent_notes) + recent messages (3000 tokens = 12000 chars)
        t2_budget = 12000
        permanent_notes = permanent_notes[:8000]
        t2_budget -= len(permanent_notes)
        picked_messages = limit_list(picked_messages, t2_budget, lambda m: m)

        # T3: FAISS-ranked facts (5000 tokens = 20000 chars)
        ranked_facts = limit_list(ranked_facts, 20000, lambda f: f.fact)

        # T4: Reflections + causal links (2000 tokens = 8000 chars)
        t4_budget = 8000
        causal_links = limit_list(causal_links, 4000, lambda c: str(c))
        t4_budget -= sum(len(str(c)) for c in causal_links)
        
        ranked_refl = ranked_refl[:self.max_reflections]
        ranked_refl = limit_list(ranked_refl, t4_budget, lambda r: r.insight)

        # T5: Historical summaries (2000 tokens = 8000 chars)
        summaries = limit_list(summaries, 8000, lambda s: s)

        bundle = ContextBundle(
            facts=ranked_facts,
            reflections=ranked_refl,
            summaries=summaries,
            permanent_notes=permanent_notes,
            identity_vault_block=identity_vault_block,
            personality_snapshot=personality_snapshot,
            recent_messages=picked_messages,
            active_goals=active_goals[:5],
            causal_links=causal_links,
            predictions=predictions[:5],
            world_model_context=world_model_context[:1200],
            user_model_context=user_model_context,
            unified_profile_block=unified_profile_block,
        )

        # Global Overflow Eviction: T5 -> T4 -> T3
        pinned_ids = {f.id for f in pinned_facts}
        while len(bundle.to_prompt_block()) > self.char_budget:
            if bundle.summaries:
                bundle.summaries.pop()
            elif bundle.reflections or bundle.causal_links:
                if bundle.causal_links: bundle.causal_links.pop()
                elif bundle.reflections: bundle.reflections.pop()
            elif any(f.id not in pinned_ids for f in bundle.facts):
                for i in range(len(bundle.facts) - 1, -1, -1):
                    if bundle.facts[i].id not in pinned_ids:
                        bundle.facts.pop(i)
                        break
            else:
                # If only pinned facts remain in T3, we stop evicting facts and accept overflow (or break)
                break

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

        # Phase 2.4: Pinned facts are identity and must be fully preserved
        pinned = sorted(pinned, key=lambda f: f.importance, reverse=True)
        return pinned
