from enum import Enum, auto
from dataclasses import dataclass
from typing import Optional

class PingReason(Enum):
    UNFINISHED_GOAL = auto()
    UNFINISHED_CONVERSATION = auto()
    ACHIEVEMENT_FOLLOWUP = auto()
    EMOTIONAL_CHECKIN = auto()
    MEMORY_CALLBACK = auto()  # Reserved for following up on past important events
    LONG_SILENCE = auto()

REASON_PRIORITIES = {
    PingReason.UNFINISHED_GOAL: 100,
    PingReason.UNFINISHED_CONVERSATION: 90,
    PingReason.ACHIEVEMENT_FOLLOWUP: 70,
    PingReason.MEMORY_CALLBACK: 60,
    PingReason.EMOTIONAL_CHECKIN: 50,
    PingReason.LONG_SILENCE: 10,
}

@dataclass
class ReasonDecision:
    reason: PingReason
    source_id: Optional[str] = None
    
    @property
    def priority(self) -> int:
        return REASON_PRIORITIES.get(self.reason, 0)

def select_reason(candidates: list[ReasonDecision]) -> Optional[ReasonDecision]:
    """Selects the highest priority reason from a list of candidates."""
    if not candidates:
        return None
        
    return max(candidates, key=lambda x: x.priority)
