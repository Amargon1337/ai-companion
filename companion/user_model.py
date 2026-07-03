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
  }},
  "shared_lore_candidates": [
    {{
      "candidate_phrase": "потенциальный локальный мем/фраза из диалога (если есть)",
      "candidate_context": "почему это смешно или значимо",
      "confidence": 0.9
    }}
  ]
}}

Отвечай ТОЛЬКО чистым JSON без разметки ```json."""


class UserModel:
    """Целостная модель пользователя как системы."""
    CORE_STATES = {"neutral", "depressed", "anxious", "energized", "angry"}

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
                "signals": [],
                "state_variance": 0.5,
            },
            "proactivity": {
                "last_ping_time": 0.0,
                "consecutive_ignored_pings": 0,
                "total_pings_sent": 0,
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
            
        signals = timeline.get("signals", [])
        if signals:
            parts.append(f"Эмоциональные маркеры (сигналы): {', '.join(signals)}")

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

            # --- SHARED LORE PHASE 0: Dry-Run Logging ---
            lore_candidates = res.get("shared_lore_candidates", [])
            if lore_candidates:
                import os
                from companion.config import DATA_DIR
                lore_log_path = os.path.join(DATA_DIR, "shared_lore_candidates.jsonl")
                now_str = datetime.now().isoformat()
                with open(lore_log_path, "a", encoding="utf-8") as f:
                    for cand in lore_candidates:
                        # Ensure it's not a hallucinated placeholder
                        if cand.get("candidate_phrase") and cand.get("candidate_phrase") != "потенциальный локальный мем/фраза из диалога (если есть)":
                            cand["timestamp"] = now_str
                            f.write(json.dumps(cand, ensure_ascii=False) + "\n")

            # --- SHADOW EVALUATION (Drift Control) ---
            from companion.llm.shadow_eval import evaluate_identity_change
            identity_updates = res.get("identity_updates", {})

            with self._lock:
                current_ident = self.data.get("identity", {})

            # 1. Scalar fields mapping: (update_key -> vault_category)
            scalar_map = {
                "who_they_are": "core_identity",
                "who_they_want_to_be": "ambitions",
                "who_they_fear_becoming": "fears",
            }

            for key, category in scalar_map.items():
                new_val = identity_updates.get(key)
                if new_val:
                    old_val = current_ident.get(key, "")
                    is_valid = await evaluate_identity_change(category, old_val, new_val)
                    if not is_valid:
                        identity_updates[key] = ""  # Block drift
                        res.setdefault("reflection", {}).setdefault("discoveries", []).append(f"ShadowEvaluator blocked {category} drift.")

            # 2. List fields mapping: (update_key -> (ident_key, vault_category))
            list_map = {
                "core_traits_to_add": ("core_traits", "core_traits"),
                "values_to_add": ("values", "values"),
                "roles_to_add": ("roles", "roles"),
            }

            for update_key, (ident_key, category) in list_map.items():
                items_to_add = identity_updates.get(update_key, [])
                if items_to_add:
                    old_list = current_ident.get(ident_key, [])
                    new_list = old_list.copy()
                    for item in items_to_add:
                        if item and item not in new_list:
                            new_list.append(item)
                    
                    old_val = ", ".join(old_list)
                    new_val = ", ".join(new_list)
                    
                    is_valid = await evaluate_identity_change(category, old_val, new_val)
                    if not is_valid:
                        identity_updates[update_key] = []  # Block drift
                        res.setdefault("reflection", {}).setdefault("discoveries", []).append(f"ShadowEvaluator blocked {category} drift.")
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
                    raw_state = emotional_updates["baseline_state"].lower()
                    if raw_state in UserModel.CORE_STATES:
                        timeline["baseline_state"] = raw_state
                    else:
                        logger.warning(f"Invalid CORE state '{raw_state}'. Falling back to 'neutral'. Sub-state logged to signals.")
                        timeline["baseline_state"] = "neutral"
                        signals = timeline.setdefault("signals", [])
                        if raw_state not in signals:
                            signals.append(raw_state)
                            if len(signals) > 20:
                                signals.pop(0)

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
            import copy
            ident_snapshot = copy.deepcopy(self.data.get("identity", {}))
            
        def _sync_io(snapshot, ref_data):
            self._save_model()
            self._log_reflection(ref_data)
            
            # Sync to IdentityVault
            from companion.bot_core import memory_store
            if snapshot.get("who_they_are"):
                memory_store.identity.update_identity("core_identity", snapshot["who_they_are"], explicit_overwrite=True)
            if snapshot.get("who_they_want_to_be"):
                memory_store.identity.update_identity("ambitions", snapshot["who_they_want_to_be"], explicit_overwrite=True)
            if snapshot.get("who_they_fear_becoming"):
                memory_store.identity.update_identity("fears", snapshot["who_they_fear_becoming"], explicit_overwrite=True)
            if snapshot.get("core_traits"):
                memory_store.identity.update_identity("core_traits", ", ".join(snapshot["core_traits"]), explicit_overwrite=True)
            if snapshot.get("values"):
                memory_store.identity.update_identity("values", ", ".join(snapshot["values"]), explicit_overwrite=True)
            if snapshot.get("roles"):
                memory_store.identity.update_identity("roles", ", ".join(snapshot["roles"]), explicit_overwrite=True)

        await asyncio.to_thread(_sync_io, ident_snapshot, reflection)

        return reflection

    def _save_model(self):
        from companion.storage.sqlite_db import MemoryDatabase
        with self._lock:
            data_json = json.dumps(self.data, ensure_ascii=False)
        db = MemoryDatabase()
        db.set_meta("user_model", data_json)

    def _load_model(self):
        from companion.storage.sqlite_db import MemoryDatabase
        db = MemoryDatabase()
        val = db.get_meta("user_model", "")
        if val:
            try:
                loaded_data = json.loads(val)
                # Preserve defaults if keys are missing from loaded data
                for k, v in loaded_data.items():
                    if isinstance(v, dict) and k in self.data:
                        self.data[k].update(v)
                    else:
                        self.data[k] = v
                return
            except json.JSONDecodeError as e:
                logger.error(f"Failed to load user model from DB: {e}")
        
        # Migrate from legacy file
        if os.path.exists(USER_MODEL_PATH):
            try:
                with open(USER_MODEL_PATH, encoding="utf-8") as f:
                    loaded_data = json.load(f)
                    for k, v in loaded_data.items():
                        if isinstance(v, dict) and k in self.data:
                            self.data[k].update(v)
                        else:
                            self.data[k] = v
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
