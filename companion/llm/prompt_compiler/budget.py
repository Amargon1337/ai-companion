"""Phase 6.2: Context Budget Manager — token allocation per section by scenario."""
from __future__ import annotations

from dataclasses import dataclass


@dataclass
class BudgetScenario:
    """Token budget allocation for a specific query scenario."""
    name: str
    total_budget: int
    section_budgets: dict[str, int]


# Pre-defined budget scenarios
SIMPLE_BUDGET = BudgetScenario(
    name="simple",
    total_budget=4000,
    section_budgets={
        "Identity": 800,
        "Persona": 500,
        "ConversationState": 200,
        "Memory": 800,
        "WorldModel": 300,
        "Reasoning": 300,
        "Goals": 200,
        "Predictions": 100,
        "Style": 200,
        "Safety": 400,
    }
)

COMPLEX_BUDGET = BudgetScenario(
    name="complex",
    total_budget=8000,
    section_budgets={
        "Identity": 600,
        "Persona": 300,
        "ConversationState": 200,
        "Memory": 3000,
        "WorldModel": 1500,
        "Reasoning": 1000,
        "Goals": 400,
        "Predictions": 300,
        "Style": 200,
        "Safety": 300,
    }
)

EMOTIONAL_BUDGET = BudgetScenario(
    name="emotional",
    total_budget=6000,
    section_budgets={
        "Identity": 800,
        "Persona": 500,
        "ConversationState": 200,
        "Memory": 1200,
        "WorldModel": 400,
        "Reasoning": 200,
        "Goals": 200,
        "Predictions": 100,
        "Style": 800,
        "Safety": 1200,
    }
)

SCENARIOS = {
    "simple": SIMPLE_BUDGET,
    "complex": COMPLEX_BUDGET,
    "emotional": EMOTIONAL_BUDGET,
}


class ContextBudgetManager:
    """Selects the appropriate budget scenario based on query characteristics."""

    @classmethod
    def select_scenario(
        cls,
        intent: str = "general",
        emotional_state: str = "neutral",
        query_complexity: str = "simple",
    ) -> BudgetScenario:
        """Determines budget scenario from intent, emotion, and complexity."""
        if emotional_state in ("depressed", "anxious", "angry"):
            return EMOTIONAL_BUDGET
        if query_complexity == "complex" or intent in ("goal_tracking", "prediction", "temporal"):
            return COMPLEX_BUDGET
        return SIMPLE_BUDGET

    @classmethod
    def get_budget_override(
        cls,
        intent: str = "general",
        emotional_state: str = "neutral",
        query_complexity: str = "simple",
    ) -> dict[str, int]:
        """Returns per-section budget map for the PromptCompiler."""
        scenario = cls.select_scenario(intent, emotional_state, query_complexity)
        return dict(scenario.section_budgets)
