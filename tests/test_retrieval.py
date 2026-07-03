"""Tests for RetrievalBudgetManager.select()."""
from __future__ import annotations

from companion.models import ContextBundle

from tests.conftest import make_fact


class TestRetrievalBudgetManagerSelect:
    def test_pinned_facts_always_included(self, retrieval_mgr, sample_facts):
        """Pinned, core_identity, anchor, and permanent facts must always appear."""
        bundle = retrieval_mgr.select(
            query="тестирование",
            facts=sample_facts,
            reflections=[],
        )
        fact_texts = {f.fact for f in bundle.facts}
        # These should always be present regardless of query relevance
        assert any("Иван" in f for f in fact_texts), "core_identity fact missing"
        assert any("Морзик" in f for f in fact_texts), "anchor fact missing"
        assert any("Python" in f for f in fact_texts), "pinned fact missing"
        assert any("4GB RAM" in f for f in fact_texts), "permanent fact missing"

    def test_high_importance_facts_included(self, retrieval_mgr, sample_facts):
        """Importance >= 9 facts are pinned even without special tags."""
        bundle = retrieval_mgr.select(
            query="погода",
            facts=sample_facts,
            reflections=[],
        )
        # F41.3 has importance 9 + core_identity tag — must be included
        fact_texts = {f.fact for f in bundle.facts}
        assert any("F41.3" in f for f in fact_texts), "high-importance fact missing"

    def test_char_budget_respected(self, retrieval_mgr):
        """Bundle to_prompt_block must not exceed char_budget."""
        many_facts = [make_fact(f"Длинный факт номер {i} с кучей текста для заполнения бюджета символов и проверки лимита" * 3, importance=5) for i in range(100)]
        bundle = retrieval_mgr.select(
            query="тест",
            facts=many_facts,
            reflections=[],
        )
        prompt = bundle.to_prompt_block()
        assert len(prompt) <= retrieval_mgr.char_budget, (
            f"Prompt length {len(prompt)} exceeds budget {retrieval_mgr.char_budget}"
        )

    def test_empty_facts_returns_empty_bundle(self, retrieval_mgr):
        bundle = retrieval_mgr.select(
            query="тест",
            facts=[],
            reflections=[],
        )
        assert isinstance(bundle, ContextBundle)
        assert len(bundle.facts) == 0

    def test_summary_tier_1_included(self, retrieval_mgr, sample_facts):
        """Most recent summary should always be in the bundle."""
        summaries = ["старый summary", "средний summary", "последний summary"]
        bundle = retrieval_mgr.select(
            query="тест", facts=sample_facts, reflections=[], summaries=summaries
        )
        assert "последний summary" in bundle.summaries, "latest summary missing"

    def test_reflections_limited(self, retrieval_mgr, sample_facts):
        from companion.models import Reflection
        reflections = [
            Reflection(insight=f"Reflection {i}", based_on=[], period="2026-06", importance=8, confidence=0.9)
            for i in range(10)
        ]
        bundle = retrieval_mgr.select(
            query="тест", facts=sample_facts, reflections=reflections
        )
        assert len(bundle.reflections) <= retrieval_mgr.max_reflections

    def test_external_queries_prune_unrelated_facts(self, retrieval_mgr, sample_facts):
        """Web search or pytest commands must filter out unrelated personal facts."""
        bundle = retrieval_mgr.select(
            query="посмотри в интернете последние новости о погоде",
            facts=sample_facts,
            reflections=[],
            faiss_scores={f.id: (0.9 if "погода" in f.fact else 0.0) for f in sample_facts}
        )
        fact_texts = {f.fact for f in bundle.facts}
        # Only weather-related fact should remain
        assert any("погода" in f for f in fact_texts), "weather fact missing"
        # Irrelevant core/anchor facts should be pruned
        assert not any("Иван" in f for f in fact_texts), "personal fact leaked"
        assert not any("Морзик" in f for f in fact_texts), "anchor fact leaked"
        assert not any("амитриптилин" in f for f in fact_texts), "medical fact leaked"
