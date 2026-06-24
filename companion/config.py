"""Configuration, paths, constants."""
from __future__ import annotations

import os
from typing import List

from dotenv import load_dotenv

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

_dotenv = os.path.join(BASE_DIR, "api.env")
if not os.path.exists(_dotenv):
    _alt = os.path.join(BASE_DIR, "api.env.txt")
    if os.path.exists(_alt):
        _dotenv = _alt
load_dotenv(_dotenv)

API_TOKEN = os.getenv("API_TOKEN")
GOOGLE_API_KEY = os.getenv("GOOGLE_API_KEY")
if not API_TOKEN:
    raise ValueError("API_TOKEN не найден в api.env!")
if not GOOGLE_API_KEY:
    raise ValueError("GOOGLE_API_KEY не найден в api.env!")

_raw_admins = os.getenv("ADMIN_IDS")
if not _raw_admins:
    raise ValueError(
        "ADMIN_IDS не найден в api.env! Укажи ID администратора через запятую.\n"
        "Пример: ADMIN_IDS=8390550519 или ADMIN_IDS=111111,222222 для нескольких"
    )
def _parse_admin_ids(raw_admins: str) -> list[int]:
    admin_ids: list[int] = []
    for item in raw_admins.split(","):
        value = item.strip()
        if not value:
            continue
        if not value.isdigit():
            continue
        admin_ids.append(int(value))
    if not admin_ids:
        raise ValueError(
            "ADMIN_IDS содержит только placeholder или некорректные значения. "
            "Укажи числовой Telegram ID, например ADMIN_IDS=8390550519"
        )
    return admin_ids


ADMIN_IDS: List[int] = _parse_admin_ids(_raw_admins)

EMPTY_PERSONALITY: dict = {
    "interests": {}, "beliefs": [], "values": [], "fears": [],
    "motivation": [], "relationships": {}, "habits": {}, "addictions": {},
    "strengths": [], "weaknesses": [], "last_updated": None, "changes": [],
}

# Legacy paths (unchanged for compatibility)
TODO_PATH = os.path.join(BASE_DIR, "todo.json")
PERSONALITY_PATH = os.path.join(BASE_DIR, "personality.json")
TIMELINE_PATH = os.path.join(BASE_DIR, "timeline.jsonl")
MOOD_PATH = os.path.join(BASE_DIR, "mood.jsonl")
DIARY_PATH = os.path.join(BASE_DIR, "diary.txt")
SUMMARIES_PATH = os.path.join(BASE_DIR, "summaries.txt")
IVAN_PATH = os.path.join(BASE_DIR, "ivan.txt")
PERMANENT_NOTES_PATH = os.path.join(BASE_DIR, "permanent_notes.txt")
MONTHBOOK_DIR = os.path.join(BASE_DIR, "monthbook")

# Memory architecture paths
DATA_DIR = os.path.join(BASE_DIR, "data")
FACTS_PATH = os.path.join(DATA_DIR, "facts.jsonl")
FACT_RELATIONS_PATH = os.path.join(DATA_DIR, "fact_relations.jsonl")
MESSAGES_PATH = os.path.join(DATA_DIR, "messages.jsonl")
REFLECTIONS_PATH = os.path.join(DATA_DIR, "reflections.jsonl")
BELIEFS_PATH = os.path.join(DATA_DIR, "beliefs.jsonl")

SQLITE_PATH = os.path.join(DATA_DIR, "companion.db")

# Logging
LOG_PATH = os.getenv("LOG_PATH", os.path.join(BASE_DIR, "bot.log"))
LOG_LEVEL = os.getenv("LOG_LEVEL", "INFO").upper()

os.makedirs(MONTHBOOK_DIR, exist_ok=True)
os.makedirs(DATA_DIR, exist_ok=True)

# Bootstrap data files
_BOOTSTRAP_LOGGER = None


def _bootstrap_log(msg: str) -> None:
    global _BOOTSTRAP_LOGGER
    if _BOOTSTRAP_LOGGER is None:
        import logging
        _BOOTSTRAP_LOGGER = logging.getLogger("companion.config.bootstrap")
    _BOOTSTRAP_LOGGER.info(msg)


if not os.path.exists(PERSONALITY_PATH):
    import json
    with open(PERSONALITY_PATH, "w", encoding="utf-8") as f:
        json.dump(EMPTY_PERSONALITY, f, ensure_ascii=False, indent=2)
    _bootstrap_log(f"Created {PERSONALITY_PATH} with empty personality")

if not os.path.exists(IVAN_PATH):
    with open(IVAN_PATH, "w", encoding="utf-8") as f:
        f.write("")  # empty template
    _bootstrap_log(f"Created {IVAN_PATH} (empty)")

MODEL_NAME = "gemini-3.1-flash-lite"
# Для поиска с Google Search grounding используем Gemini 2.5 Flash
# Поддерживает grounding и работает на бесплатном тарифе (июнь 2026)
SEARCH_MODEL = "gemini-2.5-flash"

SUMMARY_THRESHOLD = 50
REFLECTION_EVERY_N = 4
MAX_DOCUMENT_CHARS = 120_000
RETRIEVAL_CHAR_BUDGET = 50_000
RETRIEVAL_MAX_FACTS = 25  # Увеличено с 15 до 25
RETRIEVAL_MAX_REFLECTIONS = 5

# LLM timeouts and retry
LLM_TIMEOUT = int(os.getenv("LLM_TIMEOUT", "120"))
LLM_RETRIES = int(os.getenv("LLM_RETRIES", "3"))
LLM_RETRY_DELAY = int(os.getenv("LLM_RETRY_DELAY", "4"))

# Safety settings — пороги блокировки контента Gemini.
# По умолчанию BLOCK_NONE — фильтрация отключена.
# Можно переопределить через api.env отдельные категории:
#   SAFETY_HARASSMENT=BLOCK_ONLY_HIGH
#   SAFETY_HATE_SPEECH=BLOCK_NONE
#   SAFETY_SEXUAL=BLOCK_NONE
#   SAFETY_DANGEROUS=BLOCK_NONE
_SAFETY_DEFAULTS = {
    "HARASSMENT": "BLOCK_NONE",
    "HATE_SPEECH": "BLOCK_NONE",
    "SEXUAL": "BLOCK_NONE",
    "DANGEROUS": "BLOCK_NONE",
}

# Собираем настройки безопасности: дефолты + env-переопределения
# Формат в api.env: SAFETY_HARASSMENT=BLOCK_ONLY_HIGH (категория = значение)
SAFETY_SETTINGS_CONFIG: dict = {}
for category, default in _SAFETY_DEFAULTS.items():
    env_key = f"SAFETY_{category}"
    SAFETY_SETTINGS_CONFIG[category] = os.getenv(env_key, default)

TEXT_EXTENSIONS = {
    ".txt", ".md", ".json", ".jsonl", ".csv", ".tsv", ".log",
    ".py", ".js", ".ts", ".html", ".htm", ".xml", ".yaml", ".yml",
    ".rst", ".ini", ".cfg", ".env", ".sql", ".sh", ".bat",
}

MONTH_NAMES = [
    "Январь", "Февраль", "Март", "Апрель", "Май", "Июнь",
    "Июль", "Август", "Сентябрь", "Октябрь", "Ноябрь", "Декабрь",
]



