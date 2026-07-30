"""Phase 6.4: Agent Router — intent-based configuration profiles."""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass
class AgentProfile:
    """Configuration profile for a specialized agent mode."""
    name: str
    temperature: float
    memory_mode: str          # full, technical, minimal
    persona_enabled: bool
    reasoning_level: str = "medium"  # high, medium, low
    allowed_tools: list[str] = field(default_factory=list)
    prompt_emphasis: str = ""  # Extra instruction for this mode


# Pre-defined agent profiles
COMPANION_AGENT = AgentProfile(
    name="Companion",
    temperature=0.8,
    memory_mode="full",
    persona_enabled=True,
    reasoning_level="medium",
    prompt_emphasis="Focus on empathy, connection, and natural dialogue.",
)

CODING_AGENT = AgentProfile(
    name="Coding",
    temperature=0.3,
    memory_mode="technical",
    persona_enabled=False,
    reasoning_level="high",
    allowed_tools=["filesystem", "python"],
    prompt_emphasis="Focus on code quality, correctness, and technical accuracy.",
)

ANALYSIS_AGENT = AgentProfile(
    name="Analysis",
    temperature=0.4,
    memory_mode="full",
    persona_enabled=False,
    reasoning_level="high",
    prompt_emphasis="Focus on deep analysis, evidence-based conclusions, and structured reasoning.",
)

WRITING_AGENT = AgentProfile(
    name="Writing",
    temperature=0.7,
    memory_mode="minimal",
    persona_enabled=True,
    allowed_tools=["notes"],
    prompt_emphasis="Focus on creative expression, narrative quality, and stylistic fidelity.",
)

SEARCH_AGENT = AgentProfile(
    name="Search",
    temperature=0.5,
    memory_mode="minimal",
    persona_enabled=False,
    allowed_tools=["search"],
    reasoning_level="low",
    prompt_emphasis="Focus on factual accuracy and source reliability.",
)

AGENT_PROFILES = {
    "companion": COMPANION_AGENT,
    "coding": CODING_AGENT,
    "analysis": ANALYSIS_AGENT,
    "writing": WRITING_AGENT,
    "search": SEARCH_AGENT,
}


class AgentRouter:
    """Routes queries to the appropriate agent profile based on intent."""

    INTENT_TO_AGENT = {
        "relationship": "companion",
        "emotion": "companion",
        "small_talk": "companion",
        "general": "companion",
        "goal_tracking": "analysis",
        "prediction": "analysis",
        "temporal": "analysis",
        "coding": "coding",
        "programming": "coding",
        "debug": "coding",
        "writing": "writing",
        "creative": "writing",
        "search": "search",
        "factual": "search",
    }

    @classmethod
    def route(cls, intent: str, query: str = "") -> AgentProfile:
        """Selects the best agent profile for the given intent."""
        lowered = query.lower()

        # Keyword override for coding queries
        if any(kw in lowered for kw in ("код", "баг", "ошибк", "python", "функци", "класс")):
            return AGENT_PROFILES["coding"]
        # Keyword override for writing
        if any(kw in lowered for kw in ("напиш", "текст", "стих", "рассказ")):
            return AGENT_PROFILES["writing"]

        agent_key = cls.INTENT_TO_AGENT.get(intent, "companion")
        return AGENT_PROFILES[agent_key]
