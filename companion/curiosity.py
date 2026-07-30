"""Phase 5: Curiosity Planner & Episodic Recall."""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any


@dataclass
class CuriosityQuestion:
    """A question designed to maximize information gain."""
    topic: str
    question: str
    expected_gain: float  # 0.0 .. 1.0


class CuriosityPlanner:
    """Determines the highest Information Gain question to ask."""

    @classmethod
    def plan(cls, query: str, context_density: float) -> CuriosityQuestion | None:
        """
        If context density is low, plans a curiosity question to expand the world model.
        """
        if context_density > 0.6:
            return None  # We have enough context, no need to pry
            
        lowered = query.lower()
        if "работа" in lowered or "проект" in lowered:
            return CuriosityQuestion(
                topic="Work Goals",
                question="А какие у вас главные цели по этому проекту на ближайший месяц?",
                expected_gain=0.8
            )
        elif "семья" in lowered or "отношения" in lowered:
            return CuriosityQuestion(
                topic="Relationships",
                question="Как это обычно влияет на ваши отношения в целом?",
                expected_gain=0.75
            )
        else:
            return CuriosityQuestion(
                topic="Personal Values",
                question="Что для вас самое важное в этой ситуации?",
                expected_gain=0.7
            )


@dataclass
class AnalogicalRecall:
    """Analogical comparison between a past episode and current context."""
    episode_id: str
    similarities: str
    differences: str
    learned_lesson: str


class EpisodicRecallService:
    """Performs analogical reasoning on retrieved episodes."""

    @classmethod
    def analyze(cls, query: str, episode: dict[str, Any]) -> AnalogicalRecall:
        """Analyzes why an episode is relevant to the current query."""
        ep_text = str(episode.get("event") or episode.get("title") or "")
        
        sim = "Involves similar entities or emotional context."
        diff = "Occurred in the past under different temporal constraints."
        lesson = "Past outcomes suggest caution or a specific approach."
        
        if "ошибка" in ep_text.lower():
            lesson = "We should avoid repeating the previous mistake."
        elif "успех" in ep_text.lower() or "получилось" in ep_text.lower():
            lesson = "We should replicate the successful strategy used here."
            
        return AnalogicalRecall(
            episode_id=str(episode.get("id", "unknown")),
            similarities=sim,
            differences=diff,
            learned_lesson=lesson
        )
