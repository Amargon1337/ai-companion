"""Phase 5: Memory Attention — Multi-factor attention scoring for context injection."""
from __future__ import annotations

import math
from dataclasses import dataclass
from datetime import datetime
from typing import Any


@dataclass
class AttentionScore:
    """Detailed breakdown of the attention score for a memory item."""
    semantic: float = 0.0
    recency: float = 0.0
    importance: float = 0.0
    relationship: float = 0.0
    emotion: float = 0.0
    prediction_relevance: float = 0.0
    goal_relevance: float = 0.0
    conversation_relevance: float = 0.0
    
    @property
    def total_score(self) -> float:
        """Composite final attention score."""
        return (
            self.semantic
            + self.recency
            + self.importance
            + self.relationship
            + self.emotion
            + self.prediction_relevance
            + self.goal_relevance
            + self.conversation_relevance
        )


class MemoryAttentionService:
    """Calculates multi-factor attention scores to determine context relevance."""

    @classmethod
    def calculate_attention(
        cls,
        item: dict[str, Any],
        query: str,
        semantic_score: float,
        current_date: datetime | None = None,
        conversation_context: dict[str, Any] | None = None,
    ) -> AttentionScore:
        """
        Computes the full attention score for a memory item.
        `semantic_score` is provided by the vector similarity search (0.0 - 1.0).
        """
        now = current_date or datetime.now()
        ctx = conversation_context or {}

        # 1. Semantic (from vector search)
        semantic = float(semantic_score)

        # 2. Recency (exponential decay based on days old)
        recency = 0.0
        date_str = str(item.get("date") or item.get("created_at") or item.get("last_retrieved_at") or "")
        if date_str:
            try:
                dt = datetime.fromisoformat(date_str[:10])
                days_old = max(0, (now - dt).days)
                # Halves every 14 days
                recency = 1.0 * math.exp(-0.05 * days_old)
            except Exception:
                pass

        # 3. Importance (base importance or utility_importance)
        importance = float(item.get("utility_importance") or item.get("importance") or 0.5)

        # 4. Relationship (bonus if entity is actively mentioned in current context)
        relationship = 0.0
        active_entities = ctx.get("active_entities", [])
        item_text = str(item.get("fact") or item.get("event") or item.get("name") or "").lower()
        if any(e.lower() in item_text for e in active_entities):
            relationship = 0.8

        # 5. Emotion (bonus if item matches current conversation emotional state)
        emotion = 0.0
        current_emotion = str(ctx.get("emotion_state", "neutral")).lower()
        if current_emotion != "neutral" and current_emotion in item_text:
            emotion = 0.7

        # 6. Prediction Relevance (bonus if item is linked to a pending prediction)
        prediction_relevance = 0.0
        if "прогноз" in item_text or "ожида" in item_text:
            prediction_relevance = 0.5

        # 7. Goal Relevance (bonus if item contains goal keywords)
        goal_relevance = 0.0
        if any(kw in item_text for kw in ("цель", "задач", "план", "сдела")):
            goal_relevance = 0.6

        # 8. Conversation Relevance (bonus for direct mention in recent messages)
        conversation_relevance = 0.0
        q_words = set(w for w in query.lower().split() if len(w) > 3)
        item_words = set(w for w in item_text.split() if len(w) > 3)
        if len(q_words & item_words) >= 2:
            conversation_relevance = 0.9

        return AttentionScore(
            semantic=semantic,
            recency=round(recency, 2),
            importance=round(importance, 2),
            relationship=relationship,
            emotion=emotion,
            prediction_relevance=prediction_relevance,
            goal_relevance=goal_relevance,
            conversation_relevance=conversation_relevance,
        )

    @classmethod
    def sort_by_attention(
        cls,
        items: list[tuple[dict[str, Any], float]],
        query: str,
        conversation_context: dict[str, Any] | None = None,
        top_k: int = 10,
    ) -> list[tuple[dict[str, Any], AttentionScore]]:
        """Sorts a list of (item, semantic_score) tuples by their total attention score."""
        scored_items = []
        for item, sem_score in items:
            att = cls.calculate_attention(item, query, sem_score, conversation_context=conversation_context)
            scored_items.append((item, att))
            
        # Sort descending by total score
        scored_items.sort(key=lambda x: x[1].total_score, reverse=True)
        return scored_items[:top_k]
