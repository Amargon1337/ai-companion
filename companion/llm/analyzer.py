"""Unified LLM-based message analyzer — replaces mood_lite, importance heuristics, intent regex."""
from __future__ import annotations

import logging

from companion.llm.client import oneshot_structured, MessageAnalysis
from companion.config import MODEL_NAME

logger = logging.getLogger(__name__)

ANALYSIS_PROMPT = """Проанализируй сообщение пользователя.

Сообщение: {text}"""


def analyze_message(text: str) -> dict:
    """
    Analyze user message using Gemini structured output.

    Returns dict with validated keys:
        intent, confidence, user_mood, user_state, estimated_importance, command
    """
    if not text or not text.strip():
        return _default_analysis()

    prompt = ANALYSIS_PROMPT.format(text=text.strip()[:2000])

    try:
        validated = oneshot_structured(prompt, MessageAnalysis, MODEL_NAME)
        # Convert to dict format expected by callers
        return {
            "intent": validated.intent,
            "confidence": validated.confidence,
            "user_mood": {
                "anxiety": validated.user_mood.anxiety,
                "anger": validated.user_mood.anger,
                "sadness": validated.user_mood.sadness,
                "energy": validated.user_mood.energy,
            },
            "user_state": validated.user_state,
            "estimated_importance": validated.estimated_importance,
            "command": validated.command,
        }
    except Exception as e:
        logger.error(f"LLM structured analysis failed: {e}")
        return _default_analysis()


def _default_analysis() -> dict:
    return {
        "intent": "chat_casual",
        "confidence": 0.5,
        "user_mood": {"anxiety": 0.0, "anger": 0.0, "sadness": 0.0, "energy": 0.5},
        "user_state": "NORMAL",
        "estimated_importance": 5,
        "command": "",
    }
