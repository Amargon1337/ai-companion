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
