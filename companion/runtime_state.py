"""RuntimeState — state object for passing data between modules."""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass
class RuntimeState:
    user_message: str = ""
    message_importance: int = 5
    message_signals: list[str] = field(default_factory=list)
    policy_constraints: Any = None
    llm_response: str = ""
    mood_state: dict[str, float] | None = None
    reasoning_context: dict[str, Any] = field(default_factory=dict)
    critique_result: dict[str, Any] | None = None
    user_state: str = "NORMAL"
    intent: str = "chat_casual"
    intent_confidence: float = 0.5
    command: str = ""
