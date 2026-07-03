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

USER_MODEL_REFLECTION_PROMPT = """Ты анализируешь диалог Ивана и его AI-компаньона для обновления модели личности пользователя.

ТЕКУЩАЯ МОДЕЛЬ ПОЛЬЗОВАТЕЛЯ (JSON):
{current_model}

ПОСЛЕДНИЙ ДИАЛОГ:
Иван: "{user_message}"
Компаньон: "{bot_response}"

ВЫЯВЛЕННЫЕ ФАКТЫ:
{facts}

ТЕКУЩЕЕ НАСТРОЕНИЕ ИВАНА:
{mood}

ЗАДАЧА:
Проанализируй диалог и факты. Выдели новые черты личности, роли, ценности, страхи Ивана, его паттерны поведения или изменения в настроении/триггерах.
Верни строго JSON со следующей структурой:
{{
  "identity_updates": {{
    "who_they_are": "новое/обновленное краткое описание, кто такой Иван (если изменилось/дополнилось, иначе пустая строка)",
    "who_they_think_they_are": "как Иван видит себя (если изменилось)",
    "who_they_want_to_be": "стремления Ивана (если изменилось)",
    "who_they_fear_becoming": "страхи Ивана насчет будущего (если изменилось)",
    "core_traits_to_add": ["новые ключевые черты характера Ивана для добавления"],
    "values_to_add": ["новые ценности для добавления"],
    "roles_to_add": ["новые роли (например: QA-инженер, тульповод, хозяин пса Морзика) для добавления"]
  }},
  "patterns_updates": {{
    "actions_to_add": ["новые паттерны действий/привычки"],
    "mistakes_to_add": ["систематические ошибки Ивана"],
    "coping_mechanisms_to_add": ["выявленные копинг-стратегии/способы справляться"]
  }},
  "emotional_updates": {{
    "improvement_triggers_to_add": ["что улучшает состояние"],
    "deterioration_triggers_to_add": ["что ухудшает состояние/триггеры"],
    "baseline_state": "новое базовое состояние (если изменилось, например: anxious, depressed, neutral)"
  }},
  "reflection": {{
    "discoveries": ["описание новых открытий о личности"],
    "confirmations": ["подтверждения ранее известных фактов"],
    "falsifications": ["опровержения старых гипотез"],
    "belief_changes": ["изменения в убеждениях Ивана"],
    "pattern_observations": ["наблюдения за паттернами"],
    "emotional_notes": ["заметки об эмоциях"],
    "recurring_themes": ["повторяющиеся темы диалога"]
  }}
}}

Отвечай ТОЛЬКО чистым JSON без разметки ```json."""


class UserModel:
    """Целостная модель пользователя как системы."""

    def __init__(self):
        import threading
        self._lock = threading.RLock()
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

    def to_prompt_block(self) -> str:
        """Сборка профиля пользователя для промпта."""
        with self._lock:
            identity = self.data.get("identity", {})
            patterns = self.data.get("patterns", {})
            timeline = self.data.get("emotional_timeline", {})

        parts = []

        # Identity
        who = identity.get("who_they_are", "")
        think = identity.get("who_they_think_they_are", "")
        want = identity.get("who_they_want_to_be", "")
        fear = identity.get("who_they_fear_becoming", "")
        traits = identity.get("core_traits", [])
        values = identity.get("values", [])
        roles = identity.get("roles", [])

        identity_lines = []
        if who:
            identity_lines.append(f"- Описание: {who}")
        if think:
            identity_lines.append(f"- Самовосприятие: {think}")
        if want:
            identity_lines.append(f"- Стремления: {want}")
        if fear:
            identity_lines.append(f"- Страхи: {fear}")
        if traits:
            identity_lines.append(f"- Ключевые черты: {', '.join(traits)}")
        if values:
            identity_lines.append(f"- Ценности: {', '.join(values)}")
        if roles:
            identity_lines.append(f"- Роли: {', '.join(roles)}")

        if identity_lines:
            parts.append("Идентичность:\n" + "\n".join(identity_lines))

        # Patterns
        actions = patterns.get("actions", [])
        mistakes = patterns.get("mistakes", [])
        coping = patterns.get("coping_mechanisms", [])

        patterns_lines = []
        if actions:
            patterns_lines.append(f"- Паттерны поведения: {', '.join(actions)}")
        if mistakes:
            patterns_lines.append(f"- Систематические ошибки: {', '.join(mistakes)}")
        if coping:
            patterns_lines.append(f"- Копинг-стратегии: {', '.join(coping)}")

        if patterns_lines:
            parts.append("Паттерны поведения:\n" + "\n".join(patterns_lines))

        # Emotional baseline
        baseline = timeline.get("baseline_state", "")
        if baseline and baseline != "neutral":
            parts.append(f"Базовое эмоциональное состояние: {baseline}")

        if parts:
            return "[Модель пользователя]\n" + "\n\n".join(parts)
        return ""

    async def reflect_after_interaction(
        self,
        user_message: str,
        bot_response: str,
        facts_extracted: list[Any],
        mood_state: Any | None = None,
    ) -> dict[str, Any]:
        """Self-reflection после значимого диалога через Gemini."""
        from companion.llm.client import aio_oneshot, parse_json_object
        from companion.config import MODEL_NAME

        current_model_json = json.dumps(self.data, ensure_ascii=False, indent=2)
        facts_str = "\n".join(f"- {f.fact if hasattr(f, 'fact') else str(f)}" for f in facts_extracted)

        prompt = USER_MODEL_REFLECTION_PROMPT.format(
            current_model=current_model_json,
            user_message=user_message,
            bot_response=bot_response,
            facts=facts_str or "нет новых фактов",
            mood=json.dumps(mood_state, ensure_ascii=False) if mood_state else "неизвестно"
        )

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

        try:
            raw = await aio_oneshot(prompt, MODEL_NAME)
            res = parse_json_object(raw)

            # --- SHADOW EVALUATION (Drift Control) ---
            from companion.llm.shadow_eval import evaluate_identity_change
            identity_updates = res.get("identity_updates", {})
            who_new = identity_updates.get("who_they_are")
            if who_new:
                with self._lock:
                    who_old = self.data.get("identity", {}).get("who_they_are", "")
                is_valid = await evaluate_identity_change("core_identity", who_old, who_new)
                if not is_valid:
                    identity_updates["who_they_are"] = "" # Block drift
                    res.setdefault("reflection", {}).setdefault("discoveries", []).append("ShadowEvaluator blocked core_identity drift.")
            # -----------------------------------------

            with self._lock:
                # identity_updates already resolved (with shadow eval applied)
                patterns_updates = res.get("patterns_updates", {})
                emotional_updates = res.get("emotional_updates", {})

                # 1. Identity
                identity = self.data.setdefault("identity", {})
                if identity_updates.get("who_they_are"):
                    identity["who_they_are"] = identity_updates["who_they_are"]
                if identity_updates.get("who_they_think_they_are"):
                    identity["who_they_think_they_are"] = identity_updates["who_they_think_they_are"]
                if identity_updates.get("who_they_want_to_be"):
                    identity["who_they_want_to_be"] = identity_updates["who_they_want_to_be"]
                if identity_updates.get("who_they_fear_becoming"):
                    identity["who_they_fear_becoming"] = identity_updates["who_they_fear_becoming"]

                # Lists to append (with deduplication)
                for key, updates_key in [("core_traits", "core_traits_to_add"),
                                         ("values", "values_to_add"),
                                         ("roles", "roles_to_add")]:
                    lst = identity.setdefault(key, [])
                    for item in identity_updates.get(updates_key, []):
                        if item and item not in lst:
                            lst.append(item)

                # 2. Patterns
                patterns = self.data.setdefault("patterns", {})
                for key, updates_key in [("actions", "actions_to_add"),
                                         ("mistakes", "mistakes_to_add"),
                                         ("coping_mechanisms", "coping_mechanisms_to_add")]:
                    lst = patterns.setdefault(key, [])
                    for item in patterns_updates.get(updates_key, []):
                        if item and item not in lst:
                            lst.append(item)

                # 3. Emotional Timeline
                timeline = self.data.setdefault("emotional_timeline", {})
                for key, updates_key in [("improvement_triggers", "improvement_triggers_to_add"),
                                         ("deterioration_triggers", "deterioration_triggers_to_add")]:
                    lst = timeline.setdefault(key, [])
                    for item in emotional_updates.get(updates_key, []):
                        if item and item not in lst:
                            lst.append(item)
                if emotional_updates.get("baseline_state"):
                    timeline["baseline_state"] = emotional_updates["baseline_state"]

                # Populate reflection log
                res_reflection = res.get("reflection", {})
                for k in ["discoveries", "confirmations", "falsifications", "belief_changes",
                          "pattern_observations", "emotional_notes", "recurring_themes"]:
                    reflection[k] = res_reflection.get(k, [])

        except Exception as e:
            logger.error("Failed to perform user model reflection via LLM: %s", e)
            reflection["discoveries"].append(f"Reflection LLM failed: {e}")

        with self._lock:
            # Update metadata
            self.data["total_interactions"] += 1
            self.data["last_updated"] = datetime.now().isoformat()
            self._save_model()
            self._log_reflection(reflection)

            # Sync to IdentityVault
            from companion.bot_core import memory_store
            ident = self.data.get("identity", {})
            if ident.get("who_they_are"):
                memory_store.identity.update_identity("core_identity", ident["who_they_are"], explicit_overwrite=True)
            if ident.get("who_they_want_to_be"):
                memory_store.identity.update_identity("ambitions", ident["who_they_want_to_be"], explicit_overwrite=True)
            if ident.get("who_they_fear_becoming"):
                memory_store.identity.update_identity("fears", ident["who_they_fear_becoming"], explicit_overwrite=True)
            if ident.get("core_traits"):
                memory_store.identity.update_identity("core_traits", ", ".join(ident["core_traits"]), explicit_overwrite=True)
            if ident.get("values"):
                memory_store.identity.update_identity("values", ", ".join(ident["values"]), explicit_overwrite=True)
            if ident.get("roles"):
                memory_store.identity.update_identity("roles", ", ".join(ident["roles"]), explicit_overwrite=True)

        return reflection

    def _save_model(self):
        from companion.storage.sqlite_db import MemoryDatabase
        db = MemoryDatabase()
        db.set_meta("user_model", json.dumps(self.data, ensure_ascii=False))

    def _load_model(self):
        from companion.storage.sqlite_db import MemoryDatabase
        db = MemoryDatabase()
        val = db.get_meta("user_model", "")
        if val:
            try:
                self.data = json.loads(val)
                return
            except json.JSONDecodeError as e:
                logger.error(f"Failed to load user model from DB: {e}")
        
        # Migrate from legacy file
        if os.path.exists(USER_MODEL_PATH):
            try:
                with open(USER_MODEL_PATH, encoding="utf-8") as f:
                    self.data = json.load(f)
                self._save_model()
                try:
                    os.remove(USER_MODEL_PATH)
                except OSError:
                    pass
            except (OSError, json.JSONDecodeError) as e:
                logger.error(f"Failed to migrate user model: {e}")

    def _log_reflection(self, reflection: dict[str, Any]):
        append_jsonl(MODEL_UPDATES_LOG, reflection)
        rotate_jsonl(MODEL_UPDATES_LOG)


# Global singleton
user_model = UserModel()
