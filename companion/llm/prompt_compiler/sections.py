"""Phase 6.1: Concrete prompt sections — 10 modular builders."""
from __future__ import annotations

from typing import Any

from companion.llm.prompt_compiler.compiler import PromptSection
from companion.llm.prompt_compiler import templates


class IdentitySection(PromptSection):
    """Core personality and identity rules. Always included, highest priority."""
    def __init__(self) -> None:
        super().__init__(name="Identity", priority=1, max_tokens=800)

    def build(self, context: dict[str, Any]) -> str:
        return templates.IDENTITY_TEMPLATE


class PersonaSection(PromptSection):
    """Dynamic persona state vectors (humor, empathy, directness, energy)."""
    def __init__(self) -> None:
        super().__init__(name="Persona", priority=2, max_tokens=500)

    def build(self, context: dict[str, Any]) -> str:
        persona = context.get("persona")
        if not persona:
            return ""
        return templates.PERSONA_TEMPLATE.format(
            humor=getattr(persona, "humor", 0.5),
            empathy=getattr(persona, "empathy", 0.7),
            directness=getattr(persona, "directness", 0.6),
            energy=getattr(persona, "energy", 0.7),
            persona_guidance=getattr(persona, "get_prompt_guidance", lambda: "")(),
        )


class ConversationStateSection(PromptSection):
    """Current conversation phase from the state machine."""
    def __init__(self) -> None:
        super().__init__(name="ConversationState", priority=3, max_tokens=300)

    def build(self, context: dict[str, Any]) -> str:
        state = context.get("conversation_state", "exploration")
        turn_count = context.get("turn_count", 0)
        strategy_hints = {
            "greeting": "Warm greeting; establish rapport.",
            "exploration": "Explore topics; ask thoughtful questions.",
            "problem": "Focus on the issue; provide support or analysis.",
            "planning": "Structure the plan; be concrete and actionable.",
            "execution": "Track progress; encourage momentum.",
            "reflection": "Summarize; draw conclusions; reinforce learnings.",
        }
        return templates.CONVERSATION_STATE_TEMPLATE.format(
            state=state,
            turn_count=turn_count,
            strategy_hint=strategy_hints.get(state, "Continue naturally."),
        )


class MemorySection(PromptSection):
    """RAG memory context — facts, reflections, summaries."""
    def __init__(self) -> None:
        super().__init__(name="Memory", priority=5, max_tokens=3000)

    def build(self, context: dict[str, Any]) -> str:
        mem_ctx = context.get("memory_context", "")
        if not mem_ctx:
            return ""
        return templates.MEMORY_TEMPLATE.format(memory_context=mem_ctx)


class WorldModelSection(PromptSection):
    """Entity graph context from World Model."""
    def __init__(self) -> None:
        super().__init__(name="WorldModel", priority=6, max_tokens=2000)

    def build(self, context: dict[str, Any]) -> str:
        wm = context.get("world_model_context", "")
        if not wm:
            return ""
        return templates.WORLD_MODEL_TEMPLATE.format(world_model_context=wm)


class ReasoningSection(PromptSection):
    """Reasoning engine output for the LLM to follow."""
    def __init__(self) -> None:
        super().__init__(name="Reasoning", priority=4, max_tokens=1500)

    def build(self, context: dict[str, Any]) -> str:
        reasoning = context.get("reasoning_context", "")
        if not reasoning:
            return ""
        return templates.REASONING_TEMPLATE.format(reasoning_context=reasoning)


class GoalsSection(PromptSection):
    """Active goals context."""
    def __init__(self) -> None:
        super().__init__(name="Goals", priority=7, max_tokens=800)

    def build(self, context: dict[str, Any]) -> str:
        goals = context.get("goals_context", "")
        if not goals:
            return ""
        return templates.GOALS_TEMPLATE.format(goals_context=goals)


class PredictionsSection(PromptSection):
    """Pending predictions context."""
    def __init__(self) -> None:
        super().__init__(name="Predictions", priority=8, max_tokens=500)

    def build(self, context: dict[str, Any]) -> str:
        preds = context.get("predictions_context", "")
        if not preds:
            return ""
        return templates.PREDICTIONS_TEMPLATE.format(predictions_context=preds)


class StyleSection(PromptSection):
    """Tone and strategy profiles based on emotional state."""
    def __init__(self) -> None:
        super().__init__(name="Style", priority=9, max_tokens=600)

    def build(self, context: dict[str, Any]) -> str:
        strategy = context.get("strategy", "")
        tone = context.get("tone", "")
        if not strategy and not tone:
            return ""
        return templates.STYLE_TEMPLATE.format(strategy=strategy, tone=tone)


class SafetySection(PromptSection):
    """Sensitivity guards, Zero-Advice protocol."""
    def __init__(self) -> None:
        super().__init__(name="Safety", priority=10, max_tokens=800)

    def build(self, context: dict[str, Any]) -> str:
        block = context.get("sensitivity_block", "")
        if not block:
            return ""
        return templates.SAFETY_TEMPLATE.format(sensitivity_block=block)


def create_default_sections() -> list[PromptSection]:
    """Creates the standard set of 10 prompt sections."""
    return [
        IdentitySection(),
        PersonaSection(),
        ConversationStateSection(),
        ReasoningSection(),
        MemorySection(),
        WorldModelSection(),
        GoalsSection(),
        PredictionsSection(),
        StyleSection(),
        SafetySection(),
    ]
