"""Tests for Stage 2: Activation Score."""
import pytest
from companion.models import Fact
from companion.memory.activation import calculate_activation_score, fact_activation_score
from companion.memory.importance import retrieval_score


def test_calculate_activation_score_basic() -> None:
    score = calculate_activation_score(
        importance=8,
        recency=1.0,
        usage=0.5,
        emotional_weight=0.2,
        goal_relevance=0.8,
    )
    assert 0.0 <= score <= 1.0
    assert score > 0.5  # High importance, recency, goal relevance


def test_fact_activation_score_with_fact_object() -> None:
    f = Fact(
        fact="У пользователя радость от сдачи проекта",
        date="2026-07-28",
        importance=9,
        confidence=0.95,
        source="test",
        tags=["emotion", "work"],
        facts_sent_count=10,
        facts_used_count=5,
    )
    score = fact_activation_score(f, goal_relevance=0.9)
    assert 0.0 <= score <= 1.0
    assert score > 0.7  # High emotional weight, importance, and precision


def test_retrieval_score_backward_compatibility() -> None:
    d = {
        "fact": "Обычный факт о погоде",
        "importance": 5,
        "date": "2026-07-20",
        "memory_kind": "event",
    }
    score = retrieval_score(d, query="погода")
    assert 0.0 <= score <= 1.0


def test_retrieval_bias_affects_activation_score() -> None:
    f_neutral = Fact(
        fact="Факт без смещения",
        date="2026-07-28",
        importance=5,
        confidence=0.9,
        source="test",
        meta={"retrieval_bias": 0.0},
    )
    f_boosted = Fact(
        fact="Факт с положительным смещением",
        date="2026-07-28",
        importance=5,
        confidence=0.9,
        source="test",
        meta={"retrieval_bias": 0.5},
    )
    score_neutral = fact_activation_score(f_neutral)
    score_boosted = fact_activation_score(f_boosted)
    assert score_boosted > score_neutral

