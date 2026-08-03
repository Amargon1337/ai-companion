"""Activation Score module (Stage 2) — multi-factor ranking for memory retrieval."""
from __future__ import annotations

from typing import Any

from companion.config import (
    ACTIVATION_WEIGHT_CONFIRMATION,
    ACTIVATION_WEIGHT_EMOTION,
    ACTIVATION_WEIGHT_GOAL,
    ACTIVATION_WEIGHT_IMPORTANCE,
    ACTIVATION_WEIGHT_RECENCY,
    ACTIVATION_WEIGHT_USAGE,
)
from companion.memory.importance import days_since, decay_factor
from companion.models import Fact


def confirmation_strength(
    evidence_count: int | float,
    first_seen: str = "",
    last_seen: str = "",
    *,
    min_span_days: float = 14.0,
) -> float:
    """How *earned* a memory is: repeated observation spread over real time.

    This is the counterweight to recency. Three mentions in one evening
    describe a mood; three mentions across three months describe the person.
    Only the time span separates them, so the span — not the count alone —
    carries the weight.

    Returns 0..1. A single observation scores 0 no matter how confident the
    model was when it wrote it.
    """
    count = max(0.0, float(evidence_count or 0))
    if count <= 1.0:
        return 0.0

    # Repetition saturates fast: 2 -> .33, 3 -> .5, 5 -> .67, 9 -> .8
    repetition = (count - 1.0) / (count + 1.0)

    span_days = 0.0
    if first_seen and last_seen:
        span_days = max(0.0, days_since(first_seen) - days_since(last_seen))
    # Full credit once observations span min_span_days; partial below that.
    span = min(1.0, span_days / min_span_days) if min_span_days > 0 else 1.0

    # Multiplicative on purpose: repetition without time span stays weak.
    return max(0.0, min(1.0, repetition * span))


def calculate_activation_score(
    importance: float | int,
    recency: float,
    usage: float | int,
    emotional_weight: float = 0.0,
    goal_relevance: float = 0.0,
    confirmation: float = 0.0,
    *,
    w_importance: float | None = None,
    w_recency: float | None = None,
    w_usage: float | None = None,
    w_emotion: float | None = None,
    w_goal: float | None = None,
    w_confirmation: float | None = None,
    retrieval_bias: float = 0.0,
) -> float:
    """Calculate composite Activation Score for memory ranking using an additive-multiplicative model.

    Additive base: intrinsic memory strength (importance & recency).
    Multiplicative context: usage, goal relevance, emotional weight, and dynamic retrieval bias.
    """
    w_imp = w_importance if w_importance is not None else ACTIVATION_WEIGHT_IMPORTANCE
    w_rec = w_recency if w_recency is not None else ACTIVATION_WEIGHT_RECENCY
    w_use = w_usage if w_usage is not None else ACTIVATION_WEIGHT_USAGE
    w_emo = w_emotion if w_emotion is not None else ACTIVATION_WEIGHT_EMOTION
    w_goal_rel = w_goal if w_goal is not None else ACTIVATION_WEIGHT_GOAL
    w_conf = w_confirmation if w_confirmation is not None else ACTIVATION_WEIGHT_CONFIRMATION

    norm_imp = max(0.0, min(1.0, float(importance) / 10.0 if importance > 1.0 else float(importance)))
    norm_rec = max(0.0, min(1.0, float(recency)))
    norm_use = max(0.0, min(1.0, float(usage)))
    norm_emo = max(0.0, min(1.0, float(emotional_weight)))
    norm_goal = max(0.0, min(1.0, float(goal_relevance)))
    norm_conf = max(0.0, min(1.0, float(confirmation)))

    # Confirmation is intrinsic memory strength, not context — it belongs in
    # the additive base next to importance/recency. As a mere multiplier it
    # could never outweigh a fresh-but-unproven memory, which is the whole
    # point of tracking it.
    base_w = w_imp + w_rec + w_conf
    base = (
        (w_imp * norm_imp + w_rec * norm_rec + w_conf * norm_conf) / base_w
        if base_w > 0
        else norm_imp
    )
    multiplier = (
        (1.0 + w_use * norm_use)
        * (1.0 + w_goal_rel * norm_goal)
        * (1.0 + w_emo * norm_emo)
        * (1.0 + retrieval_bias)
    )
    score = base * multiplier
    return max(0.0, min(1.0, score))


def fact_activation_score(
    fact: Fact | dict[str, Any],
    goal_relevance: float = 0.0,
    *,
    w_importance: float | None = None,
    w_recency: float | None = None,
    w_usage: float | None = None,
    w_emotion: float | None = None,
    w_goal: float | None = None,
) -> float:
    """Helper to compute activation score directly from a Fact object or dict."""
    if isinstance(fact, Fact):
        imp = fact.importance
        kind = fact.memory_kind
        date_str = fact.date or fact.created_at
        tags = [str(t).lower() for t in fact.tags]
        meta = fact.meta
        used_count = fact.used_count
        precision = fact.precision
        first_seen = fact.created_at or fact.date
        last_seen = fact.last_used_at or fact.last_retrieved_at or ""
    else:
        imp = int(fact.get("importance", 5))
        kind = str(fact.get("memory_kind", "event"))
        date_str = str(fact.get("date") or fact.get("created_at", ""))
        tags = [str(t).lower() for t in fact.get("tags", [])]
        meta = fact.get("meta") or {}
        used_count = int(fact.get("facts_used_count", fact.get("used_count", 0)))
        retrieved_count = int(fact.get("facts_sent_count", fact.get("retrieved_count", 0)))
        precision = min(1.0, float(used_count) / float(retrieved_count)) if retrieved_count > 0 else 0.0
        first_seen = str(fact.get("created_at") or fact.get("date") or "")
        last_seen = str(fact.get("last_used_at") or fact.get("last_retrieved_at") or "")

    age = days_since(date_str)
    recency = decay_factor(age, kind)

    # Calculate usage score from count and precision
    if used_count > 0:
        usage_score = min(1.0, 0.3 + 0.05 * used_count + 0.3 * precision)
    else:
        usage_score = 0.0

    # Calculate emotional weight from tags or meta
    emotional_tags = {"emotion", "feeling", "эмоци", "радость", "грусть", "страх", "гнев", "любовь", "тревога"}
    if any(et in t for t in tags for et in emotional_tags):
        emo_weight = 0.8
    else:
        emo_weight = float(meta.get("emotional_weight", 0.0))

    retrieval_bias = float(meta.get("retrieval_bias", 0.0))

    # A fact re-used across a real time span is earned knowledge, not a mood.
    confirmation = confirmation_strength(used_count, first_seen, last_seen)

    return calculate_activation_score(
        importance=imp,
        recency=recency,
        usage=usage_score,
        emotional_weight=emo_weight,
        goal_relevance=goal_relevance,
        confirmation=confirmation,
        w_importance=w_importance,
        w_recency=w_recency,
        w_usage=w_usage,
        w_emotion=w_emotion,
        w_goal=w_goal,
        retrieval_bias=retrieval_bias,
    )
