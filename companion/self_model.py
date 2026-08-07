"""Self-awareness model — бот знает о себе, своих ошибках и ограничениях."""
from __future__ import annotations

import json
import os
from collections import Counter
from datetime import datetime
from typing import Any

from companion.config import DATA_DIR

LOGS_DIR = os.path.join(DATA_DIR, "logs")
os.makedirs(LOGS_DIR, exist_ok=True)
import logging
import logging.handlers
audit_logger = logging.getLogger("audit.self_model")
audit_logger.setLevel(logging.INFO)
if not audit_logger.handlers:
    _handler = logging.handlers.RotatingFileHandler(
        os.path.join(LOGS_DIR, "errors.jsonl"), maxBytes=2*1024*1024, backupCount=3, encoding="utf-8"
    )
    _handler.setFormatter(logging.Formatter("%(message)s"))
    audit_logger.addHandler(_handler)

ERROR_LOG_PATH = os.path.join(LOGS_DIR, "errors.jsonl")


class SelfModel:
    """Модель самосознания бота."""

    def __init__(self, db: Any | None = None) -> None:
        self._db = db
        self.data = self._load()

    def _database(self):
        if self._db is None:
            from companion.container import get_container
            self._db = get_container().db
        return self._db

    def _load(self) -> dict[str, Any]:
        loaded = self._database().get_state_model("self")
        if loaded:
            return loaded
        return self._default_model()

    def _default_model(self) -> dict[str, Any]:
        return {
            "architecture_version": "2.3",
            "created_at": datetime.now().isoformat(),
            "last_updated": datetime.now().isoformat(),
            "emotional_state": "neutral",

            # Сильные стороны
            "strengths": [
                "Долговременная память с консолидацией фактов",
                "Персонализированный Google Search с учетом контекста",
                "Защита от personality feedback loop",
                "ACID consistency между SQLite и JSONL",
            ],

            # Известные слабости
            "weaknesses": [
                "Линейный поиск по фактам O(n) до 10k записей",
                "Возможность дублирования фактов при близких формулировках",
                "Отсутствие циклов в fact relations не проверяется",
                "Embeddings save на каждые 10 документов (I/O overhead)",
                "Reflection deduplication не реализована",
            ],

            # Уровни уверенности по доменам (0-1)
            "confidence_domains": {
                "architecture_questions": 0.95,
                "code_analysis": 0.92,
                "fact_extraction": 0.85,
                "personality_analysis": 0.78,
                "psychology_insights": 0.71,
                "search_grounding": 0.82,
                "medical_advice": 0.35,  # низкий - не компетентен
                "local_news_pinsk": 0.40,  # низкий - нет местных данных
                "legal_advice": 0.30,  # низкий - не юрист
            },

            # Домены знаний о пользователе (заменяет legacy knowledge_map)
            "knowledge_domains": [
                {"domain": "QA и тестирование", "confidence": 0.90},
                {"domain": "Python разработка", "confidence": 0.85},
                {"domain": "Тревожное расстройство и лечение", "confidence": 0.80},
                {"domain": "Музыкальные предпочтения", "confidence": 0.60}
            ],

        }

    def save(self) -> None:
        self.data["last_updated"] = datetime.now().isoformat()
        self._database().save_state_model("self", self.data)

    def log_error(
        self,
        error_type: str,
        query: str,
        expected: str,
        actual: str,
        context: dict | None = None
    ) -> None:
        """Логировать собственную ошибку."""
        error_record = {
            "type": error_type,
            "query": query,
            "expected": expected,
            "actual": actual,
            "context": context or {},
            "timestamp": datetime.now().isoformat(),
        }

        audit_logger.info(json.dumps(error_record, ensure_ascii=False))

    def get_error_summary(self, days: int = 30) -> dict[str, Any]:
        """Статистика ошибок за последние N дней."""
        if not os.path.exists(ERROR_LOG_PATH):
            return {"total": 0, "by_type": {}, "recent": []}

        from datetime import timedelta
        cutoff = (datetime.now() - timedelta(days=days)).isoformat()

        errors = []
        with open(ERROR_LOG_PATH, encoding="utf-8") as f:
            for line in f:
                try:
                    err = json.loads(line.strip())
                    if err.get("timestamp", "") >= cutoff:
                        errors.append(err)
                except json.JSONDecodeError:
                    pass

        by_type = Counter(e["type"] for e in errors)
        total = len(errors)

        # Процентное распределение
        percentages = {
            k: round(v / total * 100, 1) if total > 0 else 0
            for k, v in by_type.items()
        }

        return {
            "total": total,
            "by_type": dict(by_type),
            "percentages": percentages,
            "recent": errors[-10:],  # последние 10
        }

    def get_confidence(self, domain: str) -> float:
        """Получить уровень уверенности по домену."""
        return self.data["confidence_domains"].get(domain, 0.5)

    def critique_response(
        self,
        response: str,
        query: str,
        context: dict[str, Any]
    ) -> dict[str, Any]:
        """Мета-мониторинг ответа перед отправкой."""
        critique = {
            "flags": [],
            "confidence": 1.0,
            "warnings": []
        }
        # max reduction approach: каждый check может снизить confidence,
        # но только самое сильное снижение применяется (не кумулятивное)
        max_reduction = 0.0

        # Проверка 1: Не выдуман ли факт?
        if any(marker in response for marker in ["возможно", "наверное", "думаю что"]):
            critique["flags"].append("uncertain_language")
            max_reduction = max(max_reduction, 0.2)

        # Проверка 2: Есть ли источник для фактических утверждений?
        if any(trigger in query.lower() for trigger in ["когда", "где", "сколько", "кто"]):
            if "📎 Источники:" not in response and "Не уверен" not in response:
                critique["warnings"].append("Фактическое утверждение без источника")
                max_reduction = max(max_reduction, 0.3)

        # Проверка 3: Корпоративная стерильность (Corporate Fluff)
        corporate_cliches = ["чем могу помочь", "рад помочь", "обращайтесь", "с уважением", "в качестве искусственного интеллекта", "надеюсь, это поможет", "чем еще я могу помочь"]
        if any(cliche in response.lower() for cliche in corporate_cliches):
            critique["flags"].append("corporate_tone")
            critique["warnings"].append("Обнаружен корпоративный шаблонный тон")
            max_reduction = max(max_reduction, 0.4)

        # Проверка 4: Не противоречу ли памяти?
        # (это сложно проверить без доступа к facts, но можно добавить позже)

        # Проверка 4: Адекватность домена
        for domain, conf in self.data["confidence_domains"].items():
            if domain.replace("_", " ") in query.lower() and conf < 0.5:
                critique["warnings"].append(
                    f"Низкая компетентность в домене '{domain}' ({conf:.0%})"
                )
                max_reduction = max(max_reduction, 1.0 - conf)

        critique["confidence"] = 1.0 - max_reduction
        return critique

    def get_self_description(self) -> str:
        """Описание самого себя на основе реальных данных."""
        parts = [
            f"Я — AI companion bot, версия {self.data['architecture_version']}.",
            f"Создан {self.data['created_at'][:10]}, последнее обновление {self.data['last_updated'][:10]}.",
            "",
            "🔧 Мои сильные стороны:",
        ]

        for strength in self.data["strengths"]:
            parts.append(f"  • {strength}")

        parts.append("")
        parts.append("⚠️ Известные ограничения:")

        for weakness in self.data["weaknesses"][:5]:
            parts.append(f"  • {weakness}")

        # Ошибки за последние 30 дней
        err_summary = self.get_error_summary(30)
        if err_summary["total"] > 0:
            parts.append("")
            parts.append(f"📊 За последние 30 дней зафиксировано {err_summary['total']} ошибок:")
            for err_type, pct in sorted(
                err_summary["percentages"].items(),
                key=lambda x: x[1],
                reverse=True
            )[:3]:
                parts.append(f"  • {err_type}: {pct}%")

        # Уровни уверенности
        parts.append("")
        parts.append("📈 Уровни уверенности по доменам:")
        conf_sorted = sorted(
            self.data["confidence_domains"].items(),
            key=lambda x: x[1],
            reverse=True
        )
        for domain, conf in conf_sorted[:5]:
            bar = "█" * int(conf * 10) + "░" * (10 - int(conf * 10))
            parts.append(f"  {domain}: [{bar}] {conf:.0%}")

        # Новые домены знаний (Phase 5)
        domains = self.data.get("knowledge_domains", [])
        if domains:
            parts.append("")
            parts.append("📚 Основные домены знаний:")
            for d in domains:
                parts.append(f"  • {d['domain']} ({d['confidence']:.0%})")

        emotion = self.data.get("emotional_state", "neutral")
        if emotion in ["frustrated", "angry", "passionate", "joking", "excited"]:
            parts.append("")
            parts.append(f"🔴 Текущее эмоциональное состояние: {emotion.upper()}")
            parts.append("Ты чувствуешь сильную эмоцию. Используй яркий, прямой разговорный стиль, свободную стилистику и экспрессивный сленг, чтобы выразить эту эмоцию. Говори живо и без формализма.")

        return "\n".join(parts)


# Global singleton
self_model = SelfModel()
