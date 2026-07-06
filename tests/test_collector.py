import pytest
from companion.proactive.reasons import PingReason, ReasonDecision
from companion.proactive.collector import collect_context, ContextPayload
from companion.user_model import UserModel

@pytest.fixture
def clean_user_model():
    model = UserModel()
    return model

def test_collect_emotional_context(clean_user_model):
    decision = ReasonDecision(reason=PingReason.EMOTIONAL_CHECKIN)
    clean_user_model.data["emotional_timeline"]["baseline_state"] = "anxious"
    clean_user_model.data["emotional_timeline"]["signals"] = ["scared", "burnt_out"]
    
    payload = collect_context(decision, clean_user_model)
    
    assert payload.reason == PingReason.EMOTIONAL_CHECKIN
    assert payload.urgency == 90
    assert "baseline_state=anxious" in payload.facts[0]
    assert "signals=scared, burnt_out" in payload.facts[1]

def test_collect_silence_context(clean_user_model):
    decision = ReasonDecision(reason=PingReason.LONG_SILENCE)
    
    payload = collect_context(decision, clean_user_model)
    
    assert payload.reason == PingReason.LONG_SILENCE
    assert payload.urgency == 10
    assert len(payload.facts) == 0

def test_collect_memory_callback_context(clean_user_model):
    decision = ReasonDecision(reason=PingReason.MEMORY_CALLBACK)
    
    payload = collect_context(decision, clean_user_model)
    
    assert payload.reason == PingReason.MEMORY_CALLBACK
    assert payload.urgency == 50
    assert payload.metadata.get("implemented") is False


def test_collect_goal_context(clean_user_model):
    from unittest.mock import patch
    decision = ReasonDecision(reason=PingReason.UNFINISHED_GOAL)
    
    with patch("companion.reasoning.reasoning_engine.get_goal_snapshot") as mock_goals:
        mock_goals.return_value = ["Цель 1: Закончить отчет", "Цель 2: Купить продукты"]
        payload = collect_context(decision, clean_user_model)
        
        assert payload.reason == PingReason.UNFINISHED_GOAL
        assert payload.urgency == 80
        assert "Цель 1: Закончить отчет" in payload.facts[0]
        assert "Цель 2: Купить продукты" in payload.facts[0]


def test_collect_conversation_context(clean_user_model):
    from unittest.mock import patch
    decision = ReasonDecision(reason=PingReason.LONG_SILENCE)
    from companion.proactive.collector import collect_conversation_context
    
    with patch("companion.bot_core.memory_store.load_recent_summaries") as mock_summaries:
        mock_summaries.return_value = ["Саммари: Пользователь учил Python."]
        payload = collect_conversation_context(decision, clean_user_model)
        
        assert payload.reason == PingReason.LONG_SILENCE
        assert payload.urgency == 70
        assert "Саммари: Пользователь учил Python." in payload.facts[0]

