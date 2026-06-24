"""User Model — целостная модель пользователя как системы."""
from __future__ import annotations

import json
import logging
import os
from datetime import datetime
from typing import Any

from companion.config import DATA_DIR
from companion.storage.jsonl import append_jsonl, rotate_jsonl

logger = logging.getLogger(__name__)

USER_MODEL_PATH = os.path.join(DATA_DIR, "user_model.json")
MODEL_UPDATES_LOG = os.path.join(DATA_DIR, "user_model_updates.jsonl")


class UserModel:
    """Целостная модель пользователя как системы."""

    def __init__(self):
        self.data: dict[str, Any] = {
            "identity": {
                "who_they_are": "",
                "who_they_think_they_are": "",
                "who_they_want_to_be": "",
                "who_they_fear_becoming": "",
                "core_traits": [],
                "values": [],
                "roles": [],
                "self_perception_confidence": 0.5,
            },
            "beliefs": {},
            "patterns": {
                "actions": [],
                "mistakes": [],
                "coping_mechanisms": [],
                "life_cycles": [],
            },
            "emotional_timeline": {
                "improvement_triggers": [],
                "deterioration_triggers": [],
                "frequent_triggers": [],
                "baseline_state": "neutral",
                "state_variance": 0.5,
            },
            "last_updated": datetime.now().isoformat(),
            "total_interactions": 0,
            "model_confidence": 0.3,
        }
        self._load_model()

    async def reflect_after_interaction(
        self,
        user_message: str,
        bot_response: str,
        facts_extracted: list[Any],
        mood_state: Any | None = None,
    ) -> dict[str, Any]:
        """Self-reflection после значимого диалога."""
        reflection = {
            "timestamp": datetime.now().isoformat(),
            "discoveries": [],
            "confirmations": [],
            "falsifications": [],
            "belief_changes": [],
            "pattern_observations": [],
            "emotional_notes": [],
            "recurring_themes": [],
        }

        # Simplified keyword-based heuristics (from original class methods)
        message_lower = user_message.lower()

        # 1. Identity discovery
        identity_markers = ["я есть", "я такой", "я всегда", "я хочу", "боюсь"]
        for marker in identity_markers:
            if marker in message_lower:
                reflection["discoveries"].append(f"Identity marker found: {marker}")

        # 2. Confirmations/Falsifications
        if any(w in message_lower for w in ["точно", "именно", "да"]):
             reflection["confirmations"].append("Confirmation detected")
        if any(w in message_lower for w in ["нет", "неправда"]):
             reflection["falsifications"].append("Falsification detected")

        # Обновить модель
        self.data["total_interactions"] += 1
        self.data["last_updated"] = datetime.now().isoformat()
        self._save_model()
        self._log_reflection(reflection)

        return reflection

    def _save_model(self):
        os.makedirs(os.path.dirname(USER_MODEL_PATH) or ".", exist_ok=True)
        with open(USER_MODEL_PATH, "w", encoding="utf-8") as f:
            json.dump(self.data, f, ensure_ascii=False, indent=2)

    def _load_model(self):
        if os.path.exists(USER_MODEL_PATH):
            try:
                with open(USER_MODEL_PATH, encoding="utf-8") as f:
                    self.data = json.load(f)
            except (OSError, json.JSONDecodeError) as e:
                logger.error(f"Failed to load user model: {e}")

    def _log_reflection(self, reflection: dict[str, Any]):
        append_jsonl(MODEL_UPDATES_LOG, reflection)
        rotate_jsonl(MODEL_UPDATES_LOG)


# Global singleton
user_model = UserModel()
