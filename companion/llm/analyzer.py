"""Unified LLM-based message analyzer — replaces mood_lite, importance heuristics, intent regex."""
from __future__ import annotations

import logging

from companion.llm.client import oneshot_structured, MessageAnalysis
from companion.config import MODEL_NAME

logger = logging.getLogger(__name__)

ANALYSIS_PROMPT = """Проанализируй сообщение пользователя.
Оцени его настроение, состояние и интенцию.
Особое внимание обрати на пробелы (Gap-Filling): если пользователь упоминает интересную тему (хобби, работу, проект, человека), но деталей явно не хватает, сформируй скрытый уточняющий вопрос. Например: "На каком герое ты чаще всего играешь в хардлайне?" или "Как успехи с тем API?". Если пробелов нет, оставь needs_clarification пустым.

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
        logger.info("[ANALYZER] Запуск структурного анализа сообщения: определение интента и эмоций...")
        validated = oneshot_structured(prompt, MessageAnalysis, MODEL_NAME)
        logger.info(f"[ANALYZER] Анализ завершен. Интент: {validated.intent}, Важность: {validated.estimated_importance}")
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
            "needs_clarification": getattr(validated, "needs_clarification", ""),
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
        "needs_clarification": "",
    }
