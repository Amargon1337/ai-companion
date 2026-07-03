import pytest
from companion.proactive.reasons import PingReason
from companion.proactive.collector import ContextPayload
from companion.proactive.formatter import assemble_prompt, format_ping
from unittest.mock import patch

def test_assemble_prompt_high_urgency():
    payload = ContextPayload(
        reason=PingReason.EMOTIONAL_CHECKIN,
        source_id=None,
        facts=["baseline_state=depressed"],
        urgency=90
    )
    prompt = assemble_prompt(payload, "Test Strategy", "Test Tone")
    
    assert "Keep message under 80 words." in prompt
    assert "baseline_state=depressed" in prompt
    assert "Test Strategy" in prompt
    assert "Test Tone" in prompt

def test_assemble_prompt_low_urgency():
    payload = ContextPayload(
        reason=PingReason.LONG_SILENCE,
        source_id=None,
        facts=[],
        urgency=10
    )
    prompt = assemble_prompt(payload, "Strategy", "Tone")
    
    assert "Keep message under 25 words." in prompt
    assert "No specific facts. Use generic reason." in prompt

@pytest.mark.anyio
@patch("companion.proactive.formatter.aio_oneshot", return_value='"This is a ping message"')
async def test_format_ping_production_mode(mock_oneshot):
    payload = ContextPayload(reason=PingReason.LONG_SILENCE, source_id=None, facts=[], urgency=10)
    result = await format_ping(payload, "Strategy", "Tone", debug=False)
    
    assert result == "This is a ping message"
    assert isinstance(result, str)

@pytest.mark.anyio
@patch("companion.proactive.formatter.aio_oneshot", return_value="Debug ping")
async def test_format_ping_debug_mode(mock_oneshot):
    payload = ContextPayload(reason=PingReason.UNFINISHED_GOAL, source_id="123", facts=["Goal text"], urgency=70)
    result = await format_ping(payload, "Strategy", "Tone", debug=True)
    
    assert isinstance(result, dict)
    assert result["reason"] == "UNFINISHED_GOAL"
    assert result["urgency"] == 70
    assert result["message"] == "Debug ping"
    assert "Keep message under 50 words" in result["prompt"]
