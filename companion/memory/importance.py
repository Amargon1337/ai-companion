"""Importance scoring and decay — never delete, only lower relevance."""
from __future__ import annotations

import math
import re
from datetime import datetime


def score_message_importance(text: str) -> tuple[int, list[str]]:
    """Heuristic importance 1-10 for a single message."""
    signals: list[str] = []
    score = 4
    t = text.lower()
    length = len(text)

    if length > 400:
        score += 1
        signals.append("long")
    if length > 800:
        score += 1
    if "?" in text and any(w in t for w in ("помнишь", "знаешь", "расскажи")):
        score += 1
        signals.append("memory_query")
    if any(w in t for w in ("важно", "никогда", "всегда", "клянусь", "решил")):
        score += 2
        signals.append("emphasis")
    if re.search(r"[А-ЯЁ][а-яё]+", text):
        score += 1
        signals.append("named_entity")

    return max(1, min(10, score)), signals


def decay_factor(days_old: float, memory_kind: str) -> float:
    """Returns multiplier 0..1. Permanent facts barely decay."""
    if memory_kind == "permanent":
        return 1.0
    if memory_kind == "state":
        half_life = 45.0
    else:
        half_life = 120.0
    if days_old <= 0:
        return 1.0
    return max(0.15, math.exp(-0.693 * days_old / half_life))


def days_since(iso_date: str) -> float:
    try:
        if "T" in iso_date:
            dt = datetime.fromisoformat(iso_date)
        else:
            dt = datetime.strptime(iso_date[:10], "%Y-%m-%d")
        return max(0.0, (datetime.now() - dt).total_seconds() / 86400)
    except (ValueError, TypeError):
        return 0.0


def retrieval_score(
    fact: dict,
    query: str = "",
    *,
    w_importance: float = 0.4,
    w_recency: float = 0.25,
    w_relevance: float = 0.35,
    faiss_score: float | None = None,
) -> float:
    importance = int(fact.get("importance", 5)) / 10.0
    kind = fact.get("memory_kind", "event")
    age = days_since(fact.get("date") or fact.get("created_at", ""))
    recency = decay_factor(age, kind)

    relevance = 0.3
    if faiss_score is not None and faiss_score > 0:
        relevance = faiss_score
    elif query:
        q = query.lower()
        ft = fact.get("fact", "").lower()
        tags = [t.lower() for t in fact.get("tags", [])]
        if q in ft:
            relevance = 1.0
        else:
            qw = set(q.split())
            fw = set(ft.split())
            overlap = len(qw & fw) / max(len(qw), 1)
            tag_hit = any(q in tag or tag in q for tag in tags)
            relevance = min(1.0, overlap * 0.8 + (0.3 if tag_hit else 0))
    elif kind == "permanent":
        relevance = 0.9
    elif kind == "state" and fact.get("status") == "active":
        relevance = 0.85

    from companion.memory.activation import fact_activation_score
    return fact_activation_score(
        fact,
        goal_relevance=relevance,
        w_importance=w_importance,
        w_recency=w_recency,
        w_goal=w_relevance,
    )



