"""Tests for Stage 2: Activation Score."""
import pytest
from companion.models import Fact
from companion.memory.activation import (
    calculate_activation_score,
    confirmation_strength,
    fact_activation_score,
)
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


class TestConfirmationStrength:
    """Time, not the model's opinion, is what turns a mood into a trait."""

    def test_single_observation_is_never_confirmed(self) -> None:
        # One LLM assertion, however confident, earns nothing.
        assert confirmation_strength(1, "2026-01-01", "2026-07-01") == 0.0
        assert confirmation_strength(0, "", "") == 0.0

    def test_one_evening_does_not_make_a_trait(self) -> None:
        """Three mentions in one evening describe a state, not a person."""
        one_evening = confirmation_strength(3, "2026-07-30T20:00", "2026-07-30T23:00")
        three_months = confirmation_strength(3, "2026-05-01T12:00", "2026-07-30T12:00")

        assert one_evening < 0.05, "a single evening must stay near zero"
        assert three_months > 0.4, "a months-long pattern must score high"
        # Same count — only the time span differs.
        assert three_months > one_evening * 10

    def test_span_saturates_at_threshold(self) -> None:
        short = confirmation_strength(5, "2026-07-24", "2026-07-30")   # 6 days
        full = confirmation_strength(5, "2026-06-01", "2026-07-30")    # ~60 days
        assert full > short
        assert 0.0 <= full <= 1.0

    def test_missing_dates_do_not_crash(self) -> None:
        assert confirmation_strength(5, "", "") == 0.0
        assert 0.0 <= confirmation_strength(5, "2026-07-01", "") <= 1.0


class TestConfirmationOutranksRecency:
    """The core inversion: earned memory must survive recency decay."""

    def test_confirmed_old_beats_fresh_unproven(self) -> None:
        fresh = calculate_activation_score(importance=6, recency=1.0, usage=0.0, confirmation=0.0)
        earned = calculate_activation_score(importance=6, recency=0.3, usage=0.5, confirmation=0.8)
        assert earned > fresh, "a confirmed pattern must outrank a fresh one-off"

    def test_confirmation_outweighs_raw_emotion(self) -> None:
        """importance != emotion: a calm, repeated fact beats a dramatic one-off."""
        emotional = calculate_activation_score(
            importance=5, recency=0.5, usage=0.0, emotional_weight=1.0, confirmation=0.0
        )
        confirmed = calculate_activation_score(
            importance=5, recency=0.5, usage=0.0, emotional_weight=0.0, confirmation=0.9
        )
        assert confirmed > emotional

    def test_signature_is_backwards_compatible(self) -> None:
        # Existing callers omit `confirmation` entirely.
        score = calculate_activation_score(7, 0.8, 0.3)
        assert 0.0 <= score <= 1.0

