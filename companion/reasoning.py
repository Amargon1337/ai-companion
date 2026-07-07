"""Reasoning Engine — активная модель мира, цели, причинно-следственные связи."""
from __future__ import annotations

import json
import os
import re
import time
from datetime import datetime
from typing import Any

from companion.config import DATA_DIR
from companion.memory.text_sim import text_overlap
from companion.storage.sqlite_db import MemoryDatabase

WORLD_MODEL_PATH = os.path.join(DATA_DIR, "world_model.json")


class Goal:
    """Долговременная цель пользователя."""

    def __init__(
        self,
        title: str,
        priority: int,  # 1-10
        status: str = "active",  # active, paused, completed, abandoned
        description: str = "",
        blockers: list[str] | None = None,
        next_actions: list[str] | None = None,
        resources: list[str] | None = None,
        obstacles: list[str] | None = None,
        progress_markers: list[dict] | None = None,
        created_at: str | None = None,
        updated_at: str | None = None,
        goal_id: str | None = None,
    ):
        self.title = title
        self.priority = priority
        self.status = status
        self.description = description
        self.blockers = blockers or []
        self.next_actions = next_actions or []
        self.resources = resources or []
        self.obstacles = obstacles or []
        self.progress_markers = progress_markers or []
        self.created_at = created_at or datetime.now().isoformat()
        self.updated_at = updated_at or datetime.now().isoformat()
        self.goal_id = goal_id or f"goal_{datetime.now().strftime('%Y%m%d')}_{os.urandom(4).hex()}"

    def to_dict(self) -> dict[str, Any]:
        return {
            "goal_id": self.goal_id,
            "title": self.title,
            "priority": self.priority,
            "status": self.status,
            "description": self.description,
            "blockers": self.blockers,
            "next_actions": self.next_actions,
            "resources": self.resources,
            "obstacles": self.obstacles,
            "progress_markers": self.progress_markers,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> Goal:
        return cls(**data)


class CausalLink:
    """Причинно-следственная связь между событиями."""

    def __init__(
        self,
        cause: str,
        effect: str,
        confidence: float,  # 0-1
        evidence: list[str] | None = None,
        mechanism: str = "",
        observed_count: int = 1,
        created_at: str | None = None,
        link_id: str | None = None,
    ):
        self.cause = cause
        self.effect = effect
        self.confidence = confidence
        self.evidence = evidence or []
        self.mechanism = mechanism
        self.observed_count = observed_count
        self.created_at = created_at or datetime.now().isoformat()
        self.link_id = link_id or f"link_{os.urandom(4).hex()}"

    def to_dict(self) -> dict[str, Any]:
        return {
            "link_id": self.link_id,
            "cause": self.cause,
            "effect": self.effect,
            "confidence": self.confidence,
            "evidence": self.evidence,
            "mechanism": self.mechanism,
            "observed_count": self.observed_count,
            "created_at": self.created_at,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> CausalLink:
        return cls(**data)


class Prediction:
    """Прогноз о будущем состоянии."""

    def __init__(
        self,
        hypothesis: str,
        confidence: float,
        timeframe: str,  # "1 week", "1 month", etc.
        conditions: list[str] | None = None,
        based_on: list[str] | None = None,
        outcome: str | None = None,  # verified, falsified, pending
        created_at: str | None = None,
        prediction_id: str | None = None,
    ):
        self.hypothesis = hypothesis
        self.confidence = confidence
        self.timeframe = timeframe
        self.conditions = conditions or []
        self.based_on = based_on or []
        self.outcome = outcome or "pending"
        self.created_at = created_at or datetime.now().isoformat()
        self.prediction_id = prediction_id or f"pred_{os.urandom(4).hex()}"

    def to_dict(self) -> dict[str, Any]:
        return {
            "prediction_id": self.prediction_id,
            "hypothesis": self.hypothesis,
            "confidence": self.confidence,
            "timeframe": self.timeframe,
            "conditions": self.conditions,
            "based_on": self.based_on,
            "outcome": self.outcome,
            "created_at": self.created_at,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> Prediction:
        return cls(**data)


class ReasoningEngine:
    """Движок разума — модель мира, цели, причинность, прогнозы."""

    def __init__(self):
        import threading
        self._lock = threading.RLock()
        self.db = MemoryDatabase()
        self.world_model = self._load_world_model()
        self._last_wm_save = 0.0

    def _load_world_model(self) -> dict[str, Any]:
        """Загрузить активную модель мира."""
        if os.path.exists(WORLD_MODEL_PATH):
            try:
                with open(WORLD_MODEL_PATH, encoding="utf-8") as f:
                    return json.load(f)
            except (OSError, json.JSONDecodeError):
                pass
        return {
            "current_state": {},
            "recent_patterns": {},
            "active_contexts": [],
            "last_updated": datetime.now().isoformat(),
        }

    def _save_world_model(self) -> None:
        with self._lock:
            now = time.time()
            if now - self._last_wm_save < 10.0:
                return
            self._last_wm_save = now
            os.makedirs(os.path.dirname(WORLD_MODEL_PATH) or ".", exist_ok=True)
            self.world_model["last_updated"] = datetime.now().isoformat()
            tmp = WORLD_MODEL_PATH + ".tmp"
            with open(tmp, "w", encoding="utf-8") as f:
                json.dump(self.world_model, f, ensure_ascii=False, indent=2)
            os.replace(tmp, WORLD_MODEL_PATH)

    def update_world_model_from_message(self, text: str, importance: int = 5) -> None:
        from companion.security.sanitizer import sanitize_markup
        clean = sanitize_markup(text).strip() if text else ""
        if len(clean) < 8:
            return
        with self._lock:
            contexts = self.world_model.setdefault("active_contexts", [])
            candidate = clean[:120]
            if any(candidate.lower() == str(existing).lower() for existing in contexts):
                return
            if importance >= 7 or self._is_reasoning_trigger(clean):
                contexts.append(candidate)
                self.world_model["active_contexts"] = contexts[-8:]
                self._save_world_model()

    def get_goal_snapshot(self, query: str = "", limit: int = 3) -> list[str]:
        goals = self.list_goals("active")
        if query:
            q_words = {w for w in query.lower().split() if len(w) > 3}
            ranked = []
            for goal in goals:
                haystack = f"{goal.title} {goal.description}".lower()
                score = sum(1 for word in q_words if word in haystack)
                ranked.append((score, goal))
            ranked.sort(key=lambda item: (item[0], item[1].priority), reverse=True)
            goals = [goal for score, goal in ranked if score > 0] or goals
        lines = []
        for goal in goals[:limit]:
            extra = f" — {goal.description[:80]}" if goal.description else ""
            lines.append(f"• [{goal.priority}/10] {goal.title}{extra}")
        return lines

    def get_relevant_causal_context(self, query: str, limit: int = 3) -> list[str]:
        links = self.list_causal_links(min_confidence=0.55)
        if not links:
            return []
        q = query.lower()
        scored = []
        for link in links:
            haystack = f"{link.cause} {link.effect} {link.mechanism}".lower()
            score = sum(1 for word in q.split() if len(word) > 3 and word in haystack)
            if score > 0 or self._is_causal_query(query):
                scored.append((score + link.confidence, link))
        scored.sort(key=lambda item: item[0], reverse=True)
        result = []
        for _, link in scored[:limit]:
            result.append(f"• {link.cause} -> {link.effect} ({link.confidence:.0%})")
        return result

    def get_prediction_context(self, query: str, limit: int = 3) -> list[str]:
        predictions = self.list_predictions("pending")
        if not predictions:
            return []
        q = query.lower()
        scored = []
        for pred in predictions:
            haystack = f"{pred.hypothesis} {' '.join(pred.conditions)} {' '.join(pred.based_on)}".lower()
            score = sum(1 for word in q.split() if len(word) > 3 and word in haystack)
            if score > 0 or self._is_future_query(query):
                scored.append((score + pred.confidence, pred))
        scored.sort(key=lambda item: item[0], reverse=True)
        result = []
        for _, pred in scored[:limit]:
            result.append(f"• {pred.hypothesis} [{pred.confidence:.0%}] | {pred.timeframe}")
        return result

    def get_world_model_context(self, query: str = "") -> str:
        active = self.world_model.get("active_contexts", [])
        if not active:
            return ""
        if not query:
            return "\n".join(f"• {ctx}" for ctx in active[-3:])
        q_words = {w for w in query.lower().split() if len(w) > 3}
        matched = [ctx for ctx in active if any(word in str(ctx).lower() for word in q_words)]
        selected = matched[:3] or active[-3:]
        return "\n".join(f"• {ctx}" for ctx in selected)

    def maybe_capture_goal(self, text: str) -> Goal | None:
        patterns = [
            r"моя цель\s*[-—:]?\s*(.+)",
            r"цель\s*[-—:]?\s*(.+)",
            r"хочу\s+(.+)",
        ]
        for pattern in patterns:
            match = re.search(pattern, text, re.IGNORECASE)
            if not match:
                continue
            from companion.security.sanitizer import sanitize_markup
            title = sanitize_markup(match.group(1)).strip(" .")[:160]
            if not title:
                continue
            for existing in self.list_goals("active"):
                if existing.title.lower() == title.lower():
                    return None
            goal = Goal(title=title, priority=5)
            self.add_goal(goal)
            return goal
        return None

    def auto_reasoning_context(self, query: str, importance: int = 5) -> dict[str, Any]:
        self.update_world_model_from_message(query, importance)
        maybe_goal = self.maybe_capture_goal(query) if importance >= 6 else None
        return {
            "active_goals": self.get_goal_snapshot(query),
            "causal_links": self.get_relevant_causal_context(query),
            "predictions": [],
            "world_model_context": self.get_world_model_context(query),
            "causal_trigger": self._is_causal_query(query),
            "future_trigger": self._is_future_query(query),
            "captured_goal": maybe_goal.title if maybe_goal else "",
            "captured_prediction": "",
        }

    @staticmethod
    def _is_causal_query(text: str) -> bool:
        lowered = text.lower()
        return any(marker in lowered for marker in ["почему", "из-за чего", "что привело", "причина"]) 

    @staticmethod
    def _is_future_query(text: str) -> bool:
        lowered = text.lower()
        return any(marker in lowered for marker in ["будет", "получится", "смож", "что дальше", "в будущем", "завтра"]) 

    @staticmethod
    def _is_reasoning_trigger(text: str) -> bool:
        lowered = text.lower()
        return any(marker in lowered for marker in ["цель", "почему", "будет", "план", "будущ", "причина"]) 

    # ═══ Goals ═══

    def add_goal(self, goal: Goal) -> None:
        """Добавить цель. Если похожая уже есть — обновить её (priority, status, updated_at)."""
        with self._lock:
            existing = self.list_goals("active")
            for ex in existing:
                if text_overlap(ex.title, goal.title) > 0.55:
                    updates = {}
                    if goal.priority != ex.priority:
                        updates["priority"] = goal.priority
                    if goal.description and goal.description != ex.description:
                        updates["description"] = goal.description
                    if updates:
                        updates["status"] = "active"
                        self.update_goal(ex.goal_id, updates)
                    return
            self.db.upsert_goal(goal.to_dict())

    def list_goals(self, status: str | None = None) -> list[Goal]:
        """Список целей."""
        goals = [Goal.from_dict(data) for data in self.db.list_goals(status)]

        # Сортировка: active первыми, потом по priority
        return sorted(
            goals,
            key=lambda g: (g.status != "active", -g.priority, g.created_at),
        )

    def update_goal(self, goal_id: str, updates: dict[str, Any]) -> bool:
        """Обновить цель (rewrite file)."""
        with self._lock:
            return self.db.update_goal(goal_id, updates)

    # ═══ Causal Links ═══

    def add_causal_link(self, link: CausalLink) -> None:
        """Добавить причинно-следственную связь."""
        self.db.upsert_causal_link(link.to_dict())

    def list_causal_links(self, min_confidence: float = 0.5) -> list[CausalLink]:
        """Список причинно-следственных связей."""
        links = [CausalLink.from_dict(data) for data in self.db.list_causal_links(min_confidence)]

        return sorted(links, key=lambda l: l.confidence, reverse=True)

    def get_causal_chain(self, start: str, max_depth: int = 3) -> list[tuple[str, str, float]]:
        """Построить цепочку причин-следствий."""
        links = self.list_causal_links()
        chain = []
        visited = set()

        def dfs(node: str, depth: int):
            if depth >= max_depth or node in visited:
                return
            visited.add(node)

            for link in links:
                if link.cause == node:
                    chain.append((link.cause, link.effect, link.confidence))
                    dfs(link.effect, depth + 1)

        dfs(start, 0)
        return chain

    # ═══ Predictions ═══

    def add_prediction(self, prediction: Prediction) -> None:
        """Добавить прогноз."""
        self.db.upsert_prediction(prediction.to_dict())

    def list_predictions(self, outcome: str | None = None) -> list[Prediction]:
        """Список прогнозов."""
        predictions = [Prediction.from_dict(data) for data in self.db.list_predictions(outcome)]

        return sorted(predictions, key=lambda p: p.created_at, reverse=True)

    # ═══ Reasoning ═══

    def build_situation_model(self, goal_id: str | None = None) -> str:
        """Построить активную модель ситуации для цели."""
        if goal_id:
            goals = [g for g in self.list_goals() if g.goal_id == goal_id]
            if not goals:
                return "Цель не найдена."
            goal = goals[0]
        else:
            goals = self.list_goals("active")
            if not goals:
                return "Нет активных целей."
            goal = goals[0]

        lines = [
            "📍 Моя текущая модель ситуации:",
            "",
            "🎯 Цель:",
            f"  {goal.title}",
        ]

        if goal.description:
            lines.append(f"  {goal.description}")

        lines.append("")

        if goal.resources:
            lines.append("💼 Ресурсы:")
            for r in goal.resources:
                lines.append(f"  ✓ {r}")
            lines.append("")

        if goal.obstacles:
            lines.append("⚠️ Препятствия:")
            for o in goal.obstacles:
                lines.append(f"  • {o}")
            lines.append("")

        if goal.progress_markers:
            lines.append("📊 Прогресс:")
            for pm in goal.progress_markers:
                status_emoji = "✅" if pm.get("done") else "⏳"
                lines.append(f"  {status_emoji} {pm.get('marker', '?')}")
            lines.append("")

        if goal.blockers:
            lines.append("🚧 Блокеры:")
            for b in goal.blockers:
                lines.append(f"  ! {b}")
            lines.append("")

        if goal.next_actions:
            lines.append("▶️ Следующие шаги:")
            for i, action in enumerate(goal.next_actions[:3], 1):
                lines.append(f"  {i}. {action}")

        return "\n".join(lines)

    def analyze_causality(self, event: str) -> str:
        """Анализ причинно-следственных связей для события."""
        chain = self.get_causal_chain(event, max_depth=3)

        if not chain:
            return f"Нет установленных причинно-следственных связей для '{event}'."

        lines = [
            f"🔗 Причинно-следственная цепочка от '{event}':",
            "",
        ]

        for cause, effect, conf in chain:
            conf_bar = "█" * int(conf * 10) + "░" * (10 - int(conf * 10))
            lines.append(f"{cause}")
            lines.append(f"  ↓ [{conf_bar}] {conf:.0%}")
            lines.append(f"{effect}")
            lines.append("")

        return "\n".join(lines)

    def get_predictions_summary(self) -> str:
        """Сводка по прогнозам."""
        pending = self.list_predictions("pending")
        verified = self.list_predictions("verified")
        falsified = self.list_predictions("falsified")

        total = len(pending) + len(verified) + len(falsified)
        accuracy = len(verified) / (len(verified) + len(falsified)) if (len(verified) + len(falsified)) > 0 else 0

        lines = [
            "🔮 Прогнозы:",
            "",
            f"Всего: {total}",
            f"Ожидают проверки: {len(pending)}",
            f"Подтвердились: {len(verified)}",
            f"Не подтвердились: {len(falsified)}",
            f"Точность: {accuracy:.0%}" if (len(verified) + len(falsified)) > 0 else "Точность: N/A",
            "",
        ]

        if pending:
            lines.append("Активные прогнозы:")
            for pred in pending[:3]:
                conf_bar = "█" * int(pred.confidence * 10) + "░" * (10 - int(pred.confidence * 10))
                lines.append(f"  • {pred.hypothesis}")
                lines.append(f"    [{conf_bar}] {pred.confidence:.0%} | {pred.timeframe}")

        return "\n".join(lines)

# Global singleton
reasoning_engine = ReasoningEngine()
