"""Phase 5: Explainability — Generates traces for internal decisions."""
from __future__ import annotations

from typing import Any
from companion.memory.attention import AttentionScore


class ExplainabilityService:
    """Generates human-readable explanations for system decisions."""

    @classmethod
    def explain_retrieval(
        cls, item: dict[str, Any], score: AttentionScore, rank: int
    ) -> str:
        """Explains why a memory item was retrieved based on its attention score."""
        reasons = []
        
        # High semantic match
        if score.semantic > 0.75:
            reasons.append(f"high semantic match ({score.semantic:.2f})")
            
        # Recency
        if score.recency > 0.8:
            reasons.append("it occurred very recently")
        elif score.recency > 0.5:
            reasons.append("it occurred recently")
            
        # Importance
        if score.importance > 0.8:
            reasons.append(f"it has high historical importance ({score.importance:.2f})")
            
        # Conversation relevance
        if score.conversation_relevance > 0.5:
            reasons.append("it directly matches keywords in your query")
            
        # Relationship
        if score.relationship > 0.5:
            reasons.append("it involves an active entity in our conversation")
            
        # Goal / Prediction / Emotion
        if score.goal_relevance > 0.5:
            reasons.append("it relates to an active goal or task")
        if score.prediction_relevance > 0.5:
            reasons.append("it is relevant to a pending prediction")
        if score.emotion > 0.5:
            reasons.append("it matches your current emotional state")
            
        reason_str = ", and ".join(reasons) if reasons else "it had the highest baseline relevance"
        
        item_text = str(item.get("fact") or item.get("event") or item.get("name") or "memory item")
        # Truncate text for trace
        if len(item_text) > 40:
            item_text = item_text[:37] + "..."
            
        return f"[Trace Rank {rank}] Retrieved '{item_text}' because {reason_str} (Total Score: {score.total_score:.2f})."
