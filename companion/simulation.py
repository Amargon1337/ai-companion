"""Phase 5: Simulation Engine — Heuristic pre-response strategy simulation."""
from __future__ import annotations

from dataclasses import dataclass
from companion.persona import DynamicPersona


@dataclass
class SimulationOption:
    """A simulated response strategy."""
    strategy_name: str
    expected_user_reaction: str
    utility_score: float  # 0.0 .. 1.0


class SimulationEngine:
    """Simulates response strategies heuristically without extra LLM calls."""

    @classmethod
    def simulate(
        cls,
        query: str,
        persona: DynamicPersona,
        conversation_state: str,
        uncertainty_level: str,
    ) -> list[SimulationOption]:
        """Generates and scores potential response strategies."""
        options = []
        lowered = query.lower()
        
        # Option A: Direct Answer
        direct_score = 0.5
        if uncertainty_level == "High":
            direct_score += 0.3
        if persona.directness > 0.7:
            direct_score += 0.1
        if conversation_state in ("execution", "planning"):
            direct_score += 0.2
            
        options.append(SimulationOption(
            strategy_name="Direct Answer",
            expected_user_reaction="User accepts information and moves forward.",
            utility_score=min(1.0, direct_score)
        ))
        
        # Option B: Empathic Support
        empathy_score = 0.3
        if persona.empathy >= 0.7:
            empathy_score += 0.4
        if any(w in lowered for w in ("stress", "тяжело", "устал", "плохо", "грустно")):
            empathy_score += 0.4
        if conversation_state == "problem":
            empathy_score += 0.2
            
        options.append(SimulationOption(
            strategy_name="Empathic Support",
            expected_user_reaction="User feels heard and de-escalates stress.",
            utility_score=min(1.0, empathy_score)
        ))
        
        # Option C: Clarification / Question
        quest_score = 0.4
        if uncertainty_level == "Low":
            quest_score += 0.4
        if conversation_state == "exploration":
            quest_score += 0.2
            
        options.append(SimulationOption(
            strategy_name="Ask Question",
            expected_user_reaction="User provides missing context or details.",
            utility_score=min(1.0, quest_score)
        ))
        
        # Sort by utility
        options.sort(key=lambda x: x.utility_score, reverse=True)
        return options
