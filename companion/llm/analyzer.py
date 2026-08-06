"""Unified LLM-based message analyzer — replaces mood_lite, importance heuristics, intent regex.

Optimization: deterministic fast-path for simple messages (saves ~30-40% LLM calls).
Only complex/ambiguous messages go through the LLM analyzer.
"""
from __future__ import annotations

import logging
import re
from typing import Any

from companion.llm.client import oneshot_structured, MessageAnalysis
from companion.config import MODEL_NAME
from companion.llm.telemetry import observe

logger = logging.getLogger(__name__)

ANALYSIS_PROMPT = """Проанализируй сообщение пользователя.
Оцени его настроение, состояние и интенцию.
Особое внимание обрати на пробелы (Gap-Filling): если пользователь упоминает интересную тему (хобби, работу, проект, человека), но деталей явно не хватает, сформируй скрытый уточняющий вопрос. Например: "На каком герое ты чаще всего играешь в хардлайне?" или "Как успехи с тем API?". Если пробелов нет, оставь needs_clarification пустым.

Сообщение: {text}"""


# ── Fast-path patterns (no LLM needed) ──────────────────────────────────

_SHORT_ACK = {"ок", "окей", "окей", "да", "нет", "ага", "угу", "ну", "ну да",
              "ладно", "пон", "понял", "ясно", "точно", "ого", "вау", "лол",
              "kek", "lol", "lmao", "yes", "no", "ok", "yeah", "nah", "haha",
              "хаха", "ахах", "ахаха", "ржу", "жиза", "база", "скилл", "имба",
              "кринж", "рофл", "🤣", "😂", "💀", "👍", "👎", "❤️", "😭", "🥲"}

_JOKE_MARKERS = {"рофл", "рофлишь", "шутк", "юмор", "мем", "лол", "хаха",
                 "ахах", "ржу", "kek", "lol", "lmao", "joke", "🤣", "😂",
                 "жиза", "крипово", "кринж", "панч", "байт"}

_SAD_MARKERS = {"плохо", "грустно", "хреново", "пиздец", "нахуй", "устал",
                "выгор", "депресс", "одинок", "не хочу", "нет сил", "заебал",
                "бесит", "ненавиж", "больно", "тоска", "тревог", "паник",
                "bad", "sad", "tired", "hate", "suck", "depress"}

_GOOD_MARKERS = {"круто", "класс", "супер", "отлично", "кайф", "огонь",
                 "ура", "раду", "счастлив", "везёт", "заебок", "топ",
                 "good", "great", "awesome", "happy", "love", "nice"}


def _fast_analyze(text: str) -> dict | None:
    """Deterministic analysis for simple messages. Returns None if LLM needed.
    
    Saves ~30-40% of LLM calls by handling:
    - Short acknowledgments ("ок", "да", "ага")
    - Emoji-only messages
    - Pure joke/рофл messages
    - Clearly sad or clearly good messages
    """
    stripped = text.strip()
    if not stripped:
        return _default_analysis()

    lowered = stripped.lower()
    words = set(re.findall(r'\w+', lowered))

    # ── Short acknowledgment ────────────────────────────────────────────
    if lowered.strip() in _SHORT_ACK or (len(stripped) <= 3 and not any(c.isalpha() for c in stripped)):
        # Just an ack — low importance, neutral mood
        mood = {"anxiety": 0.0, "anger": 0.0, "sadness": 0.0, "energy": 0.4}
        if lowered in ("да", "ага", "угу", "ок", "окей"):
            mood["energy"] = 0.5
        return {
            "intent": "chat_casual",
            "confidence": 0.85,
            "user_mood": mood,
            "user_state": "NORMAL",
            "estimated_importance": 2,
            "command": "",
            "needs_clarification": "",
        }

    # ── Pure emoji ──────────────────────────────────────────────────────
    alpha_chars = [c for c in stripped if c.isalpha() or c.isdigit()]
    if len(alpha_chars) <= 2 and len(stripped) > 0:
        # Mostly emoji — detect mood from emoji
        mood = {"anxiety": 0.0, "anger": 0.0, "sadness": 0.0, "energy": 0.5}
        if any(e in stripped for e in ("😂", "🤣", "😄", "🔥", "💪")):
            mood["energy"] = 0.8
        elif any(e in stripped for e in ("😭", "💀", "🥲", "😔")):
            mood["sadness"] = 0.6
            mood["energy"] = 0.3
        return {
            "intent": "chat_casual",
            "confidence": 0.75,
            "user_mood": mood,
            "user_state": "NORMAL",
            "estimated_importance": 2,
            "command": "",
            "needs_clarification": "",
        }

    # ── Joke/рофл ───────────────────────────────────────────────────────
    joke_hits = words & _JOKE_MARKERS
    if len(joke_hits) >= 1 and len(words) <= 10:
        return {
            "intent": "chat_casual",
            "confidence": 0.8,
            "user_mood": {"anxiety": 0.0, "anger": 0.0, "sadness": 0.0, "energy": 0.7},
            "user_state": "ENERGIZED",
            "estimated_importance": 3,
            "command": "",
            "needs_clarification": "",
        }

    # ── Clearly sad ─────────────────────────────────────────────────────
    sad_hits = words & _SAD_MARKERS
    if len(sad_hits) >= 2:
        anxiety = 0.3 if any(w in words for w in ("тревог", "паник", "бесит")) else 0.1
        sadness = min(1.0, 0.4 + 0.2 * len(sad_hits))
        anger = 0.3 if any(w in words for w in ("бесит", "ненавиж", "заебал", "пиздец")) else 0.0
        return {
            "intent": "memory",
            "confidence": 0.75,
            "user_mood": {"anxiety": anxiety, "anger": anger, "sadness": sadness, "energy": 0.3},
            "user_state": "DEPRESSED" if sadness > 0.6 else "ANXIOUS",
            "estimated_importance": 7,
            "command": "",
            "needs_clarification": "Что случилось?",
        }

    # ── Clearly positive ────────────────────────────────────────────────
    good_hits = words & _GOOD_MARKERS
    if len(good_hits) >= 2:
        return {
            "intent": "memory",
            "confidence": 0.75,
            "user_mood": {"anxiety": 0.0, "anger": 0.0, "sadness": 0.0, "energy": 0.8},
            "user_state": "NORMAL",
            "estimated_importance": 5,
            "command": "",
            "needs_clarification": "",
        }

    # ── Very short but not in ack list → still too short for LLM ────────
    if len(words) <= 2 and len(stripped) < 30:
        return {
            "intent": "chat_casual",
            "confidence": 0.6,
            "user_mood": {"anxiety": 0.0, "anger": 0.0, "sadness": 0.0, "energy": 0.5},
            "user_state": "NORMAL",
            "estimated_importance": 3,
            "command": "",
            "needs_clarification": "",
        }

    # Everything else → needs LLM
    return None


@observe(name="analyze_message")
def analyze_message(text: str) -> dict:
    """
    Analyze user message using fast-path or Gemini structured output.

    Fast-path handles ~30-40% of messages without any LLM call:
    - Short acks ("ок", "да", "ага")
    - Emoji-only messages
    - Joke/рофл messages
    - Clearly sad or clearly good messages

    Complex messages still go through LLM analysis.

    Returns dict with validated keys:
        intent, confidence, user_mood, user_state, estimated_importance, command
    """
    if not text or not text.strip():
        return _default_analysis()

    # ── Fast path: deterministic analysis ───────────────────────────────
    fast_result = _fast_analyze(text)
    if fast_result is not None:
        logger.info(
            "[ANALYZER] Fast-path: intent=%s, state=%s, importance=%d (no LLM call)",
            fast_result["intent"], fast_result["user_state"],
            fast_result["estimated_importance"],
        )
        return fast_result

    # ── Slow path: LLM analysis ─────────────────────────────────────────
    prompt = ANALYSIS_PROMPT.format(text=text.strip()[:2000])

    try:
        logger.info("[ANALYZER] Запуск структурного анализа сообщения (LLM)...")
        validated = oneshot_structured(prompt, MessageAnalysis, MODEL_NAME)
        logger.info(f"[ANALYZER] Анализ завершён. Интент: {validated.intent}, Настроение: {validated.user_mood}, Состояние: {validated.user_state}, Важность: {validated.estimated_importance}")
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
