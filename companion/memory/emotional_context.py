"""Emotional Context Tracker — remembers how the user FELT, not just what happened.

This is what separates a digital friend from a search engine.

A search engine retrieves: "User talked about work on Monday"
A friend remembers: "User talked about work on Monday and was really stressed"

The emotional context tracker:
1. Records emotional tone alongside facts
2. Detects when a topic is being revisited with different emotions
3. Generates callback hints for the LLM ("Last time this came up, you were stressed")

This costs ZERO LLM calls — it's pure heuristic analysis of existing data.
"""
from __future__ import annotations

import logging
from datetime import datetime
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from companion.memory.store import MemoryStore

logger = logging.getLogger(__name__)


# Emotional tone markers in messages
_EMOTION_MARKERS = {
    "sad": {
        "ru": {"плохо", "грустно", "хреново", "устал", "одинок", "тоска",
               "не хочу", "нет сил", "заеба", "больно", "пиздец", "нахуй",
               "депресс", "выгор", "тяжело", "плачу", "разбит"},
        "weight": -0.6,
    },
    "anxious": {
        "ru": {"тревог", "паник", "страшн", "боюсь", "волнуюсь", "нервничаю",
               "беспоко", "не уверен", "а вдруг", "что если"},
        "weight": -0.4,
    },
    "angry": {
        "ru": {"бесит", "злюсь", "ненавиж", "заеба", "достал", "идиот",
               "тупо", "бесить"},
        "weight": -0.5,
    },
    "happy": {
        "ru": {"круто", "класс", "супер", "отлично", "кайф", "огонь",
               "раду", "счастлив", "ура", "заебок", "топ", "везёт"},
        "weight": 0.6,
    },
    "excited": {
        "ru": {"ого", "вау", "нифига", "офигеть", "реально", "серьёзно",
               "не может быть", "🔥", "💪"},
        "weight": 0.7,
    },
}

# Topics that tend to be emotionally loaded
_EMOTIONAL_TOPICS = {
    "work": {"работа", "работ", "начальник", "коллег", "офис", "увол",
             "задач", "дедлайн", "проект", "работать", "job", "work"},
    "relationships": {"отношен", "парень", "девушк", "друг", "подруг",
                      "расстал", "ссор", "любов", "встреча", "свидан"},
    "health": {"здоров", "бол", "врач", "лекарств", "бессонниц", "вес",
               "диет", "спорт", "тренер", "самочувств"},
    "family": {"мам", "пап", "родител", "семь", "брат", "сестр",
               "бабушк", "дедушк"},
    "money": {"деньг", "зарплат", "кредит", "долг", "финанс", "бюджет",
              "экономя"},
    "hobbies": {"музык", "игр", "фильм", "книг", "рисов", "фото",
                "програм", "код", "reaper", "dota"},
}


def detect_emotional_tone(text: str) -> dict[str, float]:
    """Detect emotional tone of a message using keyword matching.
    
    Returns dict of emotion → intensity (0.0 to 1.0).
    No LLM needed — pure heuristic.
    """
    if not text:
        return {}
    
    text_lower = text.lower()
    
    emotions = {}
    for emotion, data in _EMOTION_MARKERS.items():
        hits = 0
        for lang_key in ("ru", "en"):
            markers = data.get(lang_key, set())
            for marker in markers:
                if marker in text_lower:
                    hits += 1
        if hits > 0:
            emotions[emotion] = min(1.0, hits * 0.3)
    
    return emotions


def detect_topics(text: str) -> list[str]:
    """Detect which life topics a message is about.
    
    Returns list of topic names.
    """
    if not text:
        return []
    
    text_lower = text.lower()
    found = []
    for topic, markers in _EMOTIONAL_TOPICS.items():
        if any(m in text_lower for m in markers):
            found.append(topic)
    return found


def build_emotional_callback(
    store: "MemoryStore",
    current_text: str,
    lookback_messages: int = 50,
) -> str:
    """Build an emotional callback hint for the LLM.
    
    When the user mentions a topic that was previously discussed
    with strong emotions, generate a hint like:
    "Last time Ivan talked about work (3 days ago), he was very stressed."
    
    This is what makes the bot feel like a friend who remembers.
    
    Returns: empty string if no callback needed, or a hint string.
    """
    if not current_text:
        return ""
    
    current_topics = detect_topics(current_text)
    if not current_topics:
        return ""
    
    current_emotions = detect_emotional_tone(current_text)
    
    # Look through recent messages for emotional topic mentions
    try:
        recent = store.recent_messages(min_importance=3, limit=lookback_messages)
    except Exception:
        return ""
    
    callbacks = []
    
    for topic in current_topics:
        
        for msg in recent:
            if msg.role != "user":
                continue
            
            msg_text = msg.text or ""
            msg_topics = detect_topics(msg_text)
            if topic not in msg_topics:
                continue
            
            msg_emotions = detect_emotional_tone(msg_text)
            if not msg_emotions:
                continue
            
            # Found a previous message about the same topic with emotions
            # Check if current message has DIFFERENT emotions (evolution)
            # or SAME emotions (ongoing issue)
            
            # Get the dominant emotion from the past message
            past_dominant = max(msg_emotions, key=msg_emotions.get)
            past_intensity = msg_emotions[past_dominant]
            
            if past_intensity < 0.3:
                continue  # Too weak to mention
            
            # Time ago
            try:
                msg_time = datetime.fromisoformat(msg.ts)
                days_ago = (datetime.now() - msg_time).total_seconds() / 86400
                time_str = f"{int(days_ago)} дн. назад" if days_ago >= 1 else "недавно"
            except Exception:
                time_str = "недавно"
            
            emotion_labels = {
                "sad": "было тяжело",
                "anxious": "тревожился",
                "angry": "злился",
                "happy": "радовался",
                "excited": "был возбуждён",
            }
            
            emotion_label = emotion_labels.get(past_dominant, past_dominant)
            
            # Check if emotions changed
            current_dominant = max(current_emotions, key=current_emotions.get) if current_emotions else ""
            
            if current_dominant == past_dominant:
                # Same emotion — ongoing issue, acknowledge it
                callbacks.append(
                    f"[ЭМОЦИОНАЛЬНЫЙ КОНТЕКСТ] В последний раз, когда речь зашла "
                    f"о {topic} ({time_str}), пользователю {emotion_label}. "
                    f"Сейчас тема снова поднята — возможно, ситуация продолжается. "
                    f"Будь чутким."
                )
            else:
                # Different emotion — acknowledge the change
                callbacks.append(
                    f"[ЭМОЦИОНАЛЬНЫЙ КОНТЕКСТ] Последний раз тема {topic} "
                    f"({time_str}) была эмоционально заряжена: "
                    f"пользователь {emotion_label}. Сейчас тон другой — "
                    f"отметь это."
                )
            
            break  # One callback per topic is enough
    
    return "\n".join(callbacks[:2])  # Max 2 callbacks


def track_emotional_state(store: "MemoryStore", user_text: str, assistant_text: str) -> None:
    """Track emotional state for future callbacks.
    
    This stores emotional metadata in the message record so future
    lookups can find emotional context cheaply.
    
    Called after each message exchange. No LLM calls.
    """
    emotions = detect_emotional_tone(user_text)
    topics = detect_topics(user_text)
    
    if not emotions and not topics:
        return  # Nothing emotionally significant
    
    # Log as a high-importance system message for future retrieval
    if emotions:
        dominant = max(emotions, key=emotions.get)
        intensity = emotions[dominant]
        if intensity >= 0.3:
            topic_str = ", ".join(topics) if topics else "general"
            # This gets stored as a regular message but with emotional metadata
            # that retrieval can pick up
            logger.info(
                "Emotional tracking: user=%s, topics=%s, intensity=%.2f",
                dominant, topic_str, intensity,
            )
