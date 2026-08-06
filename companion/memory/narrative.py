"""Narrative Identity Engine (R6) — the person as a story, derived read-model.

Cognitive function: instead of a flat list of facts, show the user's life as
Narrative Arcs — coherent threads (e.g. "музыка", "карьера QA", "тревожность")
built from episodes + life transitions + patterns. Arcs are DERIVED each time
they are read (no authoritative narrative table): the underlying memory stays
the source of truth, so the story can never drift from what memory holds.

Deliberately cheap: clustering is keyword-based over existing data, no LLM,
no new authoritative storage (Iron Law #1 / 8GB box).
"""
from __future__ import annotations

import logging
from collections import defaultdict
from typing import Any

logger = logging.getLogger(__name__)

# Keyword -> arc name for deterministic clustering.
_ARC_KEYWORDS: dict[str, tuple[str, ...]] = {
    "музыка": ("reaper", "трек", "бит", "микс", "музык", "синт", "звук", "дэб", "ambient"),
    "карьера": ("работа", "qa", "тестиров", "собесед", "проект", "задач", "код", "python"),
    "психика": ("тревог", "паник", "терапи", "лекарств", "депресс", "стресс", "амітриптилин", "амітрипт"),
    "отношения": ("женя", "аня", "мама", "папа", "друз", "подруг", "партн"),
    "здоровье": ("здоров", "бол", "врач", "анализ", "сон", "устал", "энерги"),
    "домашние": ("морзик", "пёс", "собак", "кот", "питом"),
}


class NarrativeEngine:
    def __init__(self, db: Any, store: Any = None) -> None:
        self.db = db
        self.store = store

    def _arc_of(self, text: str) -> str:
        lowered = (text or "").lower()
        for arc, keywords in _ARC_KEYWORDS.items():
            if any(k in lowered for k in keywords):
                return arc
        return "прочее"

    def build_arcs(self, limit_events: int = 30) -> list[dict[str, Any]]:
        """Cluster episodes + transitions + patterns into narrative arcs."""
        arcs: dict[str, dict[str, Any]] = defaultdict(lambda: {
            "events": [], "transitions": [], "patterns": [], "importance": 0,
        })

        # Episodes (recent, important).
        try:
            for ep in self.db.list_episodes(limit=limit_events):
                text = f"{ep.get('title', '')} {ep.get('narrative', '')}"
                arc = self._arc_of(text)
                arcs[arc]["events"].append({
                    "date": ep.get("date", ""),
                    "title": str(ep.get("title", ""))[:120],
                })
                arcs[arc]["importance"] = max(
                    arcs[arc]["importance"], int(ep.get("importance", 5)))
        except Exception as exc:
            logger.debug("Narrative episodes failed: %s", exc)

        # Life transitions (confirmed only).
        try:
            for t in self.db.list_life_transitions(status="active"):
                text = f"{t.get('from_state', '')} {t.get('to_state', '')} {t.get('explanation', '')}"
                arc = self._arc_of(text)
                arcs[arc]["transitions"].append(
                    f"{t.get('from_state', '')} → {t.get('to_state', '')}")
        except Exception as exc:
            logger.debug("Narrative transitions failed: %s", exc)

        # Patterns (active).
        try:
            if self.store:
                for p in self.store.list_patterns("active"):
                    arc = self._arc_of(p.pattern)
                    arcs[arc]["patterns"].append(str(p.pattern)[:120])
        except Exception as exc:
            logger.debug("Narrative patterns failed: %s", exc)

        result = []
        for arc, data in arcs.items():
            if not (data["events"] or data["transitions"] or data["patterns"]):
                continue
            data["arc"] = arc
            data["event_count"] = len(data["events"])
            result.append(data)
        result.sort(key=lambda a: (a["importance"], a["event_count"]), reverse=True)
        return result

    def to_prompt_block(self, limit_arcs: int = 4, limit_events: int = 3) -> str:
        """Compact prompt block: the user's life as stories, not just facts."""
        arcs = self.build_arcs()[:limit_arcs]
        if not arcs:
            return ""
        parts = ["[Нарративные арки — жизнь как история]"]
        for arc in arcs:
            lines = [f"• {arc['arc'].capitalize()} (важность {arc['importance']})"]
            for ev in arc["events"][:limit_events]:
                date = str(ev.get("date", ""))[:10]
                lines.append(f"  - [{date}] {ev['title']}")
            for tr in arc["transitions"][:2]:
                lines.append(f"  - переход: {tr}")
            for pat in arc["patterns"][:2]:
                lines.append(f"  - паттерн: {pat}")
            parts.append("\n".join(lines))
        return "\n\n".join(parts)
