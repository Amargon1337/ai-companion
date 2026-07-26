"""Retrieval Budget Manager — ranked context within token budget."""
from __future__ import annotations
from typing import Any

from companion.config import (
    RETRIEVAL_CHAR_BUDGET,
    RETRIEVAL_MAX_FACTS,
    RETRIEVAL_MAX_REFLECTIONS,
)
from companion.memory.importance import retrieval_score
from companion.models import ContextBundle, Fact, Reflection


def _is_explicit_search_query(query: str) -> bool:
    lowered = query.lower()
    search_keywords = ["интернет", "google", "гугл", "погугли", "поищи"]
    if any(kw in lowered for kw in search_keywords):
        action_verbs = ["посмотри", "найди", "поищи", "поиск", "проверь", "узнай", "загугли", "погугли"]
        return any(verb in lowered for verb in action_verbs) or any(
            lowered.startswith(verb) for verb in ["найди ", "поищи ", "погугли ", "загугли "]
        )
    return False


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
        store: Any = None,
    ) -> None:
        self.char_budget = char_budget
        self.max_facts = max_facts
        self.max_reflections = max_reflections
        self.store = store

    def select(
        self,
        query: str,
        facts: list[Fact],
        reflections: list[Reflection],
        patterns: list = None,
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
        runtime_context_block: str = "",
        comm_prefs: "Any" = None,
        human_model: "Any" = None,
        life_transitions: "Any" = None,
        store: "Any" = None,
    ) -> ContextBundle:
        summaries = summaries or []
        active_goals = active_goals or []
        causal_links = causal_links or []
        predictions = predictions or []
        # Disabled until the prediction system has a producer+verifier loop.
        predictions = []
        faiss_scores = faiss_scores or {}

        active_facts = [
            f for f in facts
            if f.status == "active" or (include_archived and f.status == "archived")
        ]

        # БЛОК 2: PINNED FACTS GUARANTEE
        explicit_search = bool(query and _is_explicit_search_query(query))
        if explicit_search:
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
            kind_boost = {"state": 0.15, "event": 0.1, "belief": 0.0}.get(f.memory_kind, 0.0)
            
            final_score = semantic + importance + recency_val + mood_boost + kind_boost
            
            import logging
            logger = logging.getLogger(__name__)
            logger.debug("Fact %s retrieval: FAISS=%.3f Final=%.3f", f.id, semantic_score, final_score)
            return final_score

        # Rank regular facts with initial scores
        ranked_regular = []
        for f in regular_facts:
            semantic = faiss_scores.get(f.id, 0.0)
            score = _ranked_score(f, semantic)
            if explicit_search:
                if semantic < 0.2:
                    continue
            ranked_regular.append((f, score))
            
        ranked_regular = sorted(ranked_regular, key=lambda item: item[1], reverse=True)
        
        # Apply MMR (Maximum Marginal Relevance) to increase diversity
        mmr_ranked = []
        selected_texts = []
        
        for f, score in ranked_regular:
            penalty = 0.0
            ft_lower = f.fact.lower()
            f_words = set(ft_lower.split())
            f_tags = set(t.lower() for t in f.tags)
            
            for sw, stags in selected_texts:
                word_overlap = len(f_words & sw) / max(len(f_words), 1)
                tag_overlap = len(f_tags & stags) / max(len(f_tags), 1) if f_tags else 0.0
                
                if word_overlap > 0.4:
                    penalty += 0.2
                if tag_overlap > 0.5:
                    penalty += 0.15
                    
            final_mmr_score = score - penalty
            f.retrieval_score = final_mmr_score  # Save for logging
            mmr_ranked.append((f, final_mmr_score))
            selected_texts.append((f_words, f_tags))
            
        mmr_ranked = sorted(mmr_ranked, key=lambda item: item[1], reverse=True)
        
        store_ref = store or getattr(self, "store", None)
        if store_ref is not None:
            try:
                anchor_ids = [f.id for f in pinned_facts] + [f.id for f, _ in mmr_ranked[:5]]
                if anchor_ids:
                    conn_tuples = store_ref.get_connected_facts(
                        fact_ids=anchor_ids,
                        max_hops=2,
                        max_facts=6,
                        min_confidence=0.6,
                        exclude_relations={"summarizes", "summarized_by", "inverse_summarizes", "inverse_summarized_by"},
                    )
                    existing_ids = {f.id for f in (pinned_facts + [f for f, _ in mmr_ranked])}
                    for conn_fact, hop_dist, rel_desc in conn_tuples:
                        if conn_fact.id not in existing_ids:
                            conn_fact.retrieval_score = max(4.0 - hop_dist * 1.0, 1.0)
                            mmr_ranked.append((conn_fact, conn_fact.retrieval_score))
                            existing_ids.add(conn_fact.id)
                    mmr_ranked = sorted(mmr_ranked, key=lambda item: item[1], reverse=True)
            except Exception as e:
                import logging
                logging.getLogger(__name__).warning("GraphRAG multi-hop retrieval failed: %s", e)

            try:
                # Parent-Child Unpacking (Phase 3+)
                existing_ids = {f.id for f in pinned_facts} | {f.id for f, _ in mmr_ranked}
                parent_candidates = list(pinned_facts) + [f for f, _ in mmr_ranked]
                unpacked_children: list[tuple[Fact, float]] = []
                for parent_fact in parent_candidates:
                    if self._is_parent_summary(parent_fact, store_ref):
                        child_ids = self._get_summary_child_ids(parent_fact.id, store_ref)
                        for child_id in child_ids:
                            if child_id in existing_ids:
                                continue
                            child_fact = store_ref.get_fact(child_id)
                            if not child_fact or child_fact.status not in {"active", "dormant"}:
                                continue
                            rel_score = self._compute_child_relevance(query, child_fact, faiss_scores)
                            if rel_score >= 0.75:
                                parent_score = 10.0 if parent_fact.id in {p.id for p in pinned_facts} else getattr(parent_fact, "retrieval_score", 5.0)
                                child_fact.retrieval_score = max(parent_score - 0.05, rel_score * 5.0)
                                unpacked_children.append((child_fact, child_fact.retrieval_score))
                                existing_ids.add(child_id)
                if unpacked_children:
                    mmr_ranked.extend(unpacked_children)
                    mmr_ranked = sorted(mmr_ranked, key=lambda item: item[1], reverse=True)
            except Exception as e:
                import logging
                logging.getLogger(__name__).warning("GraphRAG parent-child unpacking failed: %s", e)

        for f in pinned_facts:
            f.retrieval_score = 99.0
            
        ranked_facts = pinned_facts + [f for f, s in mmr_ranked]

        # Rank reflections (already filtered by FAISS in bot_core)
        active_refl = [r for r in reflections if r.status == "active"]
        ranked_refl = sorted(active_refl, key=lambda r: r.importance, reverse=True)

        picked_messages: list[str] = []
        if recent_messages:
            for msg in reversed(recent_messages[:7]):
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

        # T4.5: Behavior patterns (Уровень 2) — inferences over facts.
        # Normalize: search_patterns returns (Pattern, score) tuples; list_patterns
        # returns Pattern objects. Keep only Pattern instances.
        raw_patterns = patterns or []
        norm_patterns: list = []
        for p in raw_patterns:
            if isinstance(p, tuple):
                p = p[0]
            if hasattr(p, "pattern"):
                norm_patterns.append(p)
        ranked_patterns = sorted(
            norm_patterns, key=lambda p: getattr(p, "importance", 5), reverse=True
        )[: self.max_reflections]

        # T5: Historical summaries (2000 tokens = 8000 chars)
        summaries = limit_list(summaries, 8000, lambda s: s)

        bundle = ContextBundle(
            facts=ranked_facts,
            reflections=ranked_refl,
            patterns=ranked_patterns,
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
            runtime_context_block=runtime_context_block,
            comm_prefs=comm_prefs,
            human_model=human_model,
            life_transitions=life_transitions,
        )

        # Global Overflow Eviction: T5 -> T4 -> T3
        pinned_ids = {f.id for f in pinned_facts}
        while len(bundle.to_prompt_block()) > self.char_budget:
            if bundle.summaries:
                bundle.summaries.pop()
            elif bundle.reflections or bundle.causal_links:
                if bundle.causal_links: bundle.causal_links.pop()
                elif bundle.reflections: bundle.reflections.pop()
            elif bundle.patterns:
                # T4.5: паттерны урезаются ДО фактов — интерпретация не
                # вытесняет сырые факты (тем более защищённые anchors).
                bundle.patterns.pop()
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
            elif f.memory_kind == "permanent" and "profile_fact" in tags_lower:
                pinned.append(f)

        # Phase 2.4: Pinned facts are identity and must be fully preserved
        pinned = sorted(pinned, key=lambda f: f.importance, reverse=True)
        return pinned

    def _is_parent_summary(self, fact: Fact, store: Any = None) -> bool:
        if fact.memory_kind == "summary":
            return True
        if any("summary" in str(t).lower() for t in fact.tags):
            return True
        if fact.source == "episodic_compression":
            return True
        if store and hasattr(store, "db") and hasattr(store.db, "get_fact_relations"):
            for r in store.db.get_fact_relations(fact.id):
                if r.get("from_id") == fact.id and r.get("relation") == "summarizes":
                    return True
                if r.get("to_id") == fact.id and r.get("relation") == "summarized_by":
                    return True
        return False

    def _get_summary_child_ids(self, parent_id: str, store: Any) -> list[str]:
        if not store or not hasattr(store, "db") or not hasattr(store.db, "get_fact_relations"):
            return []
        child_ids: list[str] = []
        for rel in store.db.get_fact_relations(parent_id):
            if rel.get("from_id") == parent_id and rel.get("relation") == "summarizes":
                child_ids.append(str(rel.get("to_id")))
            elif rel.get("to_id") == parent_id and rel.get("relation") == "summarized_by":
                child_ids.append(str(rel.get("from_id")))
        seen = set()
        unique_ids = []
        for cid in child_ids:
            if cid not in seen and cid != parent_id:
                seen.add(cid)
                unique_ids.append(cid)
        return unique_ids

    def _compute_child_relevance(self, query: str, child_fact: Fact, faiss_scores: dict[str, float]) -> float:
        if not query or not query.strip():
            return 0.0
        score = faiss_scores.get(child_fact.id, 0.0)
        if score > 0.0:
            return score

        from companion.memory.text_sim import text_overlap
        overlap_score = text_overlap(query, child_fact.fact)

        q_lower = query.lower().strip()
        f_lower = child_fact.fact.lower()
        tags_lower = [str(t).lower() for t in child_fact.tags]

        if q_lower in f_lower:
            return 1.0

        qw = set(q_lower.split())
        fw = set(f_lower.split())
        word_overlap = len(qw & fw) / max(len(qw), 1)
        tag_hit = any(q_lower in t or t in q_lower for t in tags_lower)
        bm25_sim = min(1.0, word_overlap * 0.85 + (0.25 if tag_hit else 0.0))

        return max(overlap_score, bm25_sim)
