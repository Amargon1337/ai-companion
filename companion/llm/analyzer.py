"""Unified LLM-based message analyzer — replaces mood_lite, importance heuristics, intent regex."""
from __future__ import annotations

import logging

from companion.llm.client import oneshot, parse_json_object
from companion.config import MODEL_NAME

logger = logging.getLogger(__name__)

ANALYSIS_PROMPT = """Проанализируй сообщение пользователя. Верни ТОЛЬКО JSON, без лишнего текста.

Поля JSON:
- intent: тип намерения (world — факты/новости/внешний мир, memory — личные данные/воспоминания, mixed — смешанный, command — команда боту, chat_casual — обычная беседа)
- confidence: уверенность в интенте (0.0-1.0)
- user_mood: объект {anxiety, anger, sadness, energy} каждое 0.0-1.0
- user_state: ANXIOUS | DEPRESSED | CURIOUS | OVERWHELMED | NORMAL
- estimated_importance: 1-10
- command: если intent=command, укажи точное название команды (reset_context, show_facts, show_notes, export_diary, show_timeline, show_year, show_context, week_digest, retrospective, monthbook, selfie, show_goals, add_goal, show_reasoning, self_description, knowledge_map, show_todos, add_todo, complete_todo, delete_todo, clear_done, diary_entry). Если не команда — пустая строка.

Сообщение: {text}
JSON:"""


def analyze_message(text: str) -> dict:
    """
    Analyze user message using Gemini.

    Returns dict with validated keys:
        intent, confidence, user_mood, user_state, estimated_importance, command
    """
    if not text or not text.strip():
        return _default_analysis()

    prompt = ANALYSIS_PROMPT.format(text=text.strip()[:2000])

    try:
        raw = oneshot(prompt, MODEL_NAME)
        result = parse_json_object(raw)
        return _validate_analysis(result)
    except Exception as e:
        logger.error(f"LLM analysis failed: {e}")
        return _default_analysis()


def _validate_analysis(result: dict) -> dict:
    VALID_INTENTS = {"world", "memory", "mixed", "command", "chat_casual"}
    VALID_STATES = {"ANXIOUS", "DEPRESSED", "CURIOUS", "OVERWHELMED", "NORMAL"}

    user_mood_raw = result.get("user_mood", {})
    if not isinstance(user_mood_raw, dict):
        user_mood_raw = {}

    user_state = result.get("user_state", "NORMAL")
    if user_state not in VALID_STATES:
        user_state = "NORMAL"

    intent = result.get("intent", "chat_casual")
    if intent not in VALID_INTENTS:
        intent = "chat_casual"

    return {
        "intent": intent,
        "confidence": max(0.0, min(1.0, float(result.get("confidence", 0.5)))),
        "user_mood": {
            "anxiety": max(0.0, min(1.0, float(user_mood_raw.get("anxiety", 0.0)))),
            "anger": max(0.0, min(1.0, float(user_mood_raw.get("anger", 0.0)))),
            "sadness": max(0.0, min(1.0, float(user_mood_raw.get("sadness", 0.0)))),
            "energy": max(0.0, min(1.0, float(user_mood_raw.get("energy", 0.5)))),
        },
        "user_state": user_state,
        "estimated_importance": max(1, min(10, int(result.get("estimated_importance", 5)))),
        "command": result.get("command", ""),
    }


def _default_analysis() -> dict:
    return {
        "intent": "chat_casual",
        "confidence": 0.5,
        "user_mood": None,
        "user_state": "NORMAL",
        "estimated_importance": 5,
        "command": "",
    }
