import pytest
import time
from companion.user_model import UserModel
from companion.proactive.engagement import evaluate_engagement, record_ping_sent, record_user_replied

@pytest.fixture
def clean_user_model():
    model = UserModel()
    # Reset lock for testing if needed
    return model

def test_engagement_user_is_active(clean_user_model):
    current_time = 1000000.0
    last_activity = current_time - (3 * 3600)  # User was active 3 hours ago
    
    decision = evaluate_engagement(clean_user_model, last_activity, current_time)
    assert decision.allowed is False
    assert "user_is_active" in decision.reason

def test_engagement_cooldown_active(clean_user_model):
    current_time = 1000000.0
    last_activity = current_time - (24 * 3600)  # Active a day ago, so this check passes
    
    # Simulate a ping sent 5 hours ago
    clean_user_model.data["proactivity"]["last_ping_time"] = current_time - (5 * 3600)
    
    decision = evaluate_engagement(clean_user_model, last_activity, current_time)
    assert decision.allowed is False
    assert "cooldown_active" in decision.reason

def test_engagement_too_many_ignored_pings(clean_user_model):
    current_time = 1000000.0
    last_activity = current_time - (48 * 3600)
    
    # Send 3 pings without reply, but space them out to pass cooldown
    clean_user_model.data["proactivity"]["last_ping_time"] = current_time - (24 * 3600) 
    clean_user_model.data["proactivity"]["consecutive_ignored_pings"] = 3
    
    decision = evaluate_engagement(clean_user_model, last_activity, current_time)
    assert decision.allowed is False
    assert decision.reason == "too_many_ignored_pings"

def test_engagement_allowed_and_boosted(clean_user_model):
    current_time = 1000000.0
    last_activity = current_time - (48 * 3600)  # Active 48 hours ago
    
    clean_user_model.data["emotional_timeline"]["baseline_state"] = "depressed"
    
    decision = evaluate_engagement(clean_user_model, last_activity, current_time)
    
    # Base 0.5 + depressed(0.2) + silence_modifier(should not apply until >48) = 0.7
    assert decision.allowed is True
    assert decision.reason == "engagement_ok"
    assert decision.score >= 0.7

def test_record_ping_and_reply(clean_user_model):
    current_time = 10000.0
    record_ping_sent(clean_user_model, current_time)
    
    assert clean_user_model.data["proactivity"]["consecutive_ignored_pings"] == 1
    assert clean_user_model.data["proactivity"]["last_ping_time"] == current_time
    
    record_user_replied(clean_user_model)
    assert clean_user_model.data["proactivity"]["consecutive_ignored_pings"] == 0
