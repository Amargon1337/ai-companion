"""Phase 5: Dynamic Persona & Conversation State Machine."""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any


@dataclass
class DynamicPersona:
    """Represents the dynamic state of the bot's personality."""
    humor: float = 0.50
    empathy: float = 0.70
    directness: float = 0.60
    energy: float = 0.70

    def adapt_to_emotion(self, emotion: str) -> None:
        """Shifts persona vectors based on user's emotional state."""
        emotion = emotion.lower()
        if "stress" in emotion or "bad" in emotion or "sad" in emotion:
            self.energy = max(0.1, self.energy - 0.20)
            self.empathy = min(1.0, self.empathy + 0.20)
            self.humor = max(0.0, self.humor - 0.30)
            self.directness = max(0.2, self.directness - 0.20)
        elif "happy" in emotion or "good" in emotion or "celebrat" in emotion:
            self.energy = min(1.0, self.energy + 0.20)
            self.humor = min(1.0, self.humor + 0.20)
            self.empathy = max(0.4, self.empathy - 0.10)
        else:
            # Decay toward baseline
            self.energy += (0.70 - self.energy) * 0.1
            self.empathy += (0.70 - self.empathy) * 0.1
            self.humor += (0.50 - self.humor) * 0.1
            self.directness += (0.60 - self.directness) * 0.1

    def get_prompt_guidance(self) -> str:
        """Returns LLM guidance based on current persona state."""
        guidance = []
        if self.empathy > 0.8:
            guidance.append("Be highly empathetic, supportive, and warm.")
        if self.humor > 0.7:
            guidance.append("Use light humor or playful tone.")
        elif self.humor < 0.3:
            guidance.append("Keep the tone serious and respectful. No jokes.")
        if self.directness > 0.8:
            guidance.append("Be extremely direct and concise. Avoid fluff.")
        if self.energy < 0.4:
            guidance.append("Keep responses calm, grounded, and low-energy.")
        return " ".join(guidance)


class ConversationStateMachine:
    """Tracks the phase of the current conversation."""
    
    VALID_STATES = ["greeting", "exploration", "problem", "planning", "execution", "reflection"]

    def __init__(self) -> None:
        self.current_state = "greeting"
        self.turn_count = 0

    def transition(self, query: str, intent: str) -> str:
        """Transitions to the next logical state based on heuristics."""
        lowered = query.lower()
        self.turn_count += 1
        
        # Hard resets or explicit states
        if any(w in lowered for w in ("привет", "доброе утро")):
            self.current_state = "greeting"
            self.turn_count = 1
            return self.current_state
            
        if self.current_state == "greeting":
            if intent in ("goal_tracking", "problem"):
                self.current_state = "problem"
            else:
                self.current_state = "exploration"
                
        elif self.current_state == "exploration":
            if any(w in lowered for w in ("проблема", "ошибка", "застрял", "не работает")):
                self.current_state = "problem"
            elif any(w in lowered for w in ("план", "сделаем", "как быть")):
                self.current_state = "planning"
                
        elif self.current_state == "problem":
            if any(w in lowered for w in ("решение", "понял", "план", "давай")):
                self.current_state = "planning"
                
        elif self.current_state == "planning":
            if any(w in lowered for w in ("сделал", "готово", "запустил")):
                self.current_state = "execution"
                
        elif self.current_state == "execution":
            if any(w in lowered for w in ("итог", "результат", "выводы")):
                self.current_state = "reflection"
                
        # After 10 turns of exploration, naturally shift to reflection if not planning
        if self.turn_count > 10 and self.current_state == "exploration":
            self.current_state = "reflection"
            
        return self.current_state
