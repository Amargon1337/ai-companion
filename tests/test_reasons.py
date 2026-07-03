from companion.proactive.reasons import PingReason, ReasonDecision, select_reason

def test_reason_decision_priority():
    decision1 = ReasonDecision(reason=PingReason.LONG_SILENCE)
    decision2 = ReasonDecision(reason=PingReason.UNFINISHED_GOAL, source_id="goal_123")
    
    assert decision1.priority == 10
    assert decision2.priority == 100

def test_select_reason_empty():
    assert select_reason([]) is None

def test_select_reason_highest_priority():
    candidates = [
        ReasonDecision(reason=PingReason.LONG_SILENCE),
        ReasonDecision(reason=PingReason.EMOTIONAL_CHECKIN),
        ReasonDecision(reason=PingReason.MEMORY_CALLBACK, source_id="fact_42"),
        ReasonDecision(reason=PingReason.UNFINISHED_CONVERSATION, source_id="chat_99")
    ]
    
    selected = select_reason(candidates)
    
    assert selected is not None
    assert selected.reason == PingReason.UNFINISHED_CONVERSATION
    assert selected.source_id == "chat_99"
    assert selected.priority == 90
