"""Tests for deterministic history and retrieval token budgets."""
from __future__ import annotations

from unittest.mock import MagicMock

from companion.llm.token_budget import estimate_tokens, trim_history
from companion.memory.retrieval import RetrievalBudgetManager
from tests.conftest import make_fact


def test_trim_history_keeps_newest_complete_turns():
    history = [
        {"role": "user", "parts": [{"text": "old " * 60}]},
        {"role": "model", "parts": [{"text": "old answer " * 60}]},
        {"role": "user", "parts": [{"text": "recent question"}]},
        {"role": "model", "parts": [{"text": "recent answer"}]},
    ]
    trimmed = trim_history(history, token_budget=20)
    assert [m["parts"][0]["text"] for m in trimmed] == ["recent question", "recent answer"]
    assert sum(estimate_tokens(m["parts"][0]["text"]) + 4 for m in trimmed) <= 20


def test_retrieval_manager_reuses_injected_reranker():
    reranker = MagicMock()
    reranker.rerank.side_effect = lambda _query, facts, **_kwargs: facts
    manager = RetrievalBudgetManager(reranker=reranker)

    for query in ("first", "second"):
        manager.select(query=query, facts=[make_fact(query)], reflections=[])

    assert reranker.rerank.call_count == 2
    assert manager.reranker is reranker


def test_retrieval_token_budget_is_enforced():
    reranker = MagicMock()
    reranker.rerank.side_effect = lambda _query, facts, **_kwargs: facts
    manager = RetrievalBudgetManager(char_budget=100_000, token_budget=120, reranker=reranker)
    facts = [make_fact(f"fact {i} " * 50) for i in range(12)]

    bundle = manager.select(query="fact", facts=facts, reflections=[])

    assert estimate_tokens(bundle.to_prompt_block()) <= manager.token_budget
