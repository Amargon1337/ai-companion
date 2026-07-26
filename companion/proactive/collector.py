from dataclasses import dataclass, field
from typing import Any
from companion.proactive.reasons import PingReason, ReasonDecision
from companion.user_model import UserModel

@dataclass
class ContextPayload:
    reason: PingReason
    source_id: str | None
    facts: list[str] = field(default_factory=list)
    metadata: dict[str, Any] = field(default_factory=dict)
    urgency: int = 50  # 1-100 scale
    recent_pings: list[str] = field(default_factory=list)
    random_anchor: str = ""
    dream_insight: str = ""
    dream_id: str | None = None


def collect_goal_context(decision: ReasonDecision, user_model: UserModel) -> ContextPayload:
    from companion.reasoning import reasoning_engine
    # Fallback for V1
    active_goals = reasoning_engine.get_goal_snapshot()
    facts = []
    if active_goals:
        facts.append("Найдены активные цели:\n" + "\n".join(active_goals))
        
    identity = user_model.data.get("identity", {})
    want = identity.get("who_they_want_to_be")
    fear = identity.get("who_they_fear_becoming")
    if want:
        facts.append(f"[ИДЕНТИЧНОСТЬ] Глобальное стремление пользователя: {want}")
    if fear:
        facts.append(f"[ИДЕНТИЧНОСТЬ] Глубинный страх пользователя: {fear}")
    
    return ContextPayload(
        reason=decision.reason,
        source_id=decision.source_id,
        facts=facts,
        urgency=80
    )

def collect_conversation_context(decision: ReasonDecision, user_model: UserModel) -> ContextPayload:
    from companion.memory.store import MemoryStore
    from companion.bot_core import memory_store
    
    # Ideally we fetch open discussions. For V1 we fetch last summary or facts.
    recent_summaries = memory_store.load_recent_summaries(1)
    facts = []
    if recent_summaries:
        facts.append("Недавний контекст:\n" + recent_summaries[0])
        
    return ContextPayload(
        reason=decision.reason,
        source_id=decision.source_id,
        facts=facts,
        urgency=70
    )

def collect_emotional_context(decision: ReasonDecision, user_model: UserModel) -> ContextPayload:
    baseline = user_model.data.get("emotional_timeline", {}).get("baseline_state", "neutral")
    signals = user_model.data.get("emotional_timeline", {}).get("signals", [])
    
    facts = [f"baseline_state={baseline}"]
    if signals:
        facts.append(f"signals={', '.join(signals)}")
        
    return ContextPayload(
        reason=decision.reason,
        source_id=decision.source_id,
        facts=facts,
        urgency=90
    )

def collect_achievement_context(decision: ReasonDecision, user_model: UserModel) -> ContextPayload:
    return ContextPayload(
        reason=decision.reason,
        source_id=decision.source_id,
        facts=["Цель/задача была недавно закрыта"],
        urgency=60
    )

def collect_silence_context(decision: ReasonDecision, user_model: UserModel) -> ContextPayload:
    return ContextPayload(
        reason=decision.reason,
        source_id=decision.source_id,
        facts=[],
        urgency=10
    )

def collect_memory_callback_context(decision: ReasonDecision, user_model: UserModel) -> ContextPayload:
    return ContextPayload(
        reason=decision.reason,
        source_id=decision.source_id,
        facts=[],
        metadata={"implemented": False},
        urgency=50
    )


_REASON_HANDLERS = {
    PingReason.UNFINISHED_GOAL: collect_goal_context,
    PingReason.UNFINISHED_CONVERSATION: collect_conversation_context,
    PingReason.EMOTIONAL_CHECKIN: collect_emotional_context,
    PingReason.ACHIEVEMENT_FOLLOWUP: collect_achievement_context,
    PingReason.LONG_SILENCE: collect_silence_context,
    PingReason.MEMORY_CALLBACK: collect_memory_callback_context,
}

def collect_context(decision: ReasonDecision, user_model: UserModel) -> ContextPayload:
    """Сборщик контекста на основе выбранной причины."""
    handler = _REASON_HANDLERS.get(decision.reason)
    if not handler:
        # Fallback if unknown reason
        payload = ContextPayload(reason=decision.reason, source_id=decision.source_id)
    else:
        payload = handler(decision, user_model)

    from companion.proactive.telemetry import get_recent_pings
    from companion.proactive.inner_monologue import get_latest_unused_dream
    try:
        from companion.bot_core import memory_store
        random_fact = memory_store.get_random_fact()
        if random_fact:
            payload.random_anchor = random_fact.fact
    except Exception:
        pass

    payload.recent_pings = get_recent_pings(limit=3)
    dream_doc = get_latest_unused_dream(user_model)
    if dream_doc and dream_doc.get("insight"):
        payload.dream_insight = dream_doc["insight"]
        payload.dream_id = dream_doc.get("id")

    return payload
