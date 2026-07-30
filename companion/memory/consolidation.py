"""Compact person-level consolidation built from existing memory entities."""
from __future__ import annotations

import math
from datetime import datetime, timedelta
from typing import Any

SNAPSHOT_MODEL = "personality_snapshot_v2"


def _items(values: Any, limit: int = 8) -> list[str]:
    if isinstance(values, dict):
        return [f"{key}: {value}" for key, value in list(values.items())[:limit]]
    result = []
    for value in values or []:
        text = str(getattr(value, "text", value)).strip()
        status = str(getattr(value, "status", "active"))
        if text and status not in {"stale", "refuted", "archived"}:
            result.append(text)
    return result[:limit]


def build_snapshot(store: Any, previous: dict[str, Any] | None = None) -> dict[str, Any]:
    personality = store.load_personality()
    human = store.get_human_model()
    goals = store.db.list_goals("active")[:5]
    patterns = store.list_patterns("active")[:8]
    transitions = store.db.list_life_transitions("active")[:5]
    causal = store.db.list_causal_links(0.55)[:8]
    current = {
        "values": _items(personality.get("values")),
        "goals": [str(goal.get("title", "")) for goal in goals if goal.get("title")],
        "fears": _items(personality.get("fears")) or _items(getattr(human, "fears", [])),
        "conflicts": _items(personality.get("weaknesses")) + _items(getattr(human, "recurring_mistakes", [])),
        "important_people": _items(personality.get("relationships")),
        "emotional_background": {
            "baseline": personality.get("emotional_state", personality.get("baseline_state", "neutral")),
            "changes": _items(personality.get("changes"), 5),
        },
        "coping": _items(personality.get("habits")) + _items(personality.get("strengths")),
        "patterns": [str(pattern.pattern) for pattern in patterns],
        "transitions": [f"{item.get('from_state')} -> {item.get('to_state')}" for item in transitions],
        "causal_links": [
            f"{item.get('cause')} -> {item.get('effect')}"
            + (f" ({float(item.get('confidence', 0)):.0%})" if item.get("confidence") is not None else "")
            for item in causal
        ],
    }
    current["golden_memory"] = build_golden_memory(personality, patterns, causal)
    previous = previous or {}
    previous_data = previous.get("profile", {})
    changed = {
        key: {"added": [item for item in values if item not in previous_data.get(key, [])],
              "removed": [item for item in previous_data.get(key, []) if item not in values]}
        for key, values in current.items()
        if isinstance(values, list) and values != previous_data.get(key, [])
    }
    return {
        "version": 2,
        "generated_at": datetime.now().isoformat(),
        "profile": current,
        "changes": changed,
        "source": "memory_consolidation",
    }


def build_golden_memory(personality: dict[str, Any], patterns: list[Any], causal: list[dict[str, Any]]) -> list[str]:
    """Return only stable person-level meaning, never raw episodic facts."""
    result = []
    values = _items(personality.get("values"), 5)
    if values:
        result.append("Ключевые ценноcти: " + ", ".join(values))
    for pattern in patterns:
        category = str(getattr(pattern, "category", ""))
        confidence = float(getattr(pattern, "confidence", 0.0))
        evidence = getattr(pattern, "evidence", []) or []
        if category in {"coping", "trend", "behavior"} and confidence >= 0.75 and len(evidence) >= 2:
            result.append(str(pattern.pattern).strip())
    for link in causal:
        confidence = float(link.get("confidence", 0.0))
        observed = int(link.get("observed_count", 1))
        if confidence >= 0.70 and observed >= 2:
            mechanism = str(link.get("mechanism", "")).strip()
            text = f"{link.get('cause')} приводит к {link.get('effect')}"
            if mechanism:
                text += f": {mechanism}"
            result.append(text)
    return list(dict.fromkeys(item for item in result if item))[:10]


def snapshot_text(snapshot: dict[str, Any], max_chars: int = 12000) -> str:
    profile = snapshot.get("profile", {})
    labels = {
        "values": "Ценноcти", "goals": "Текущие цели", "fears": "Страхи",
        "conflicts": "Конфликты", "important_people": "Важные люди",
        "patterns": "Уcтойчивые паттерны", "transitions": "Изменения",
        "causal_links": "Причинные cвязи", "coping": "Споcобы cправлятьcя",
        "golden_memory": "Золотая память",
    }
    lines = ["[Personality Snapshot v2]"]
    for key, label in labels.items():
        values = profile.get(key, [])
        if values:
            lines.append(f"{label}:\n" + "\n".join(f"- {value}" for value in values[:8]))
    emotional = profile.get("emotional_background", {})
    if emotional:
        lines.append(f"Эмоциональный фон: {emotional.get('baseline', 'neutral')}")
    changes = snapshot.get("changes", {})
    if changes:
        lines.append("Изменения отноcительно прошлого snapshot:")
        for key, delta in list(changes.items())[:8]:
            if delta.get("added"):
                lines.append(f"+ {key}: {', '.join(delta['added'][:4])}")
            if delta.get("removed"):
                lines.append(f"- {key}: {', '.join(delta['removed'][:4])}")
    return "\n\n".join(lines)[:max_chars]


def consolidate(store: Any) -> dict[str, Any]:
    previous = store.db.get_state_model(SNAPSHOT_MODEL)
    snapshot = build_snapshot(store, previous)
    store.db.save_state_model(SNAPSHOT_MODEL, snapshot)
    golden = snapshot.get("profile", {}).get("golden_memory", [])
    if golden:
        store.identity.update_identity(
            "anchor_reason",
            "\n".join(f"- {item}" for item in golden),
            confidence=0.9,
            source="memory_consolidation",
            explicit_overwrite=True,
        )
    return snapshot


def consolidate_if_due(store: Any, interval_days: int = 7) -> dict[str, Any] | None:
    previous = store.db.get_state_model(SNAPSHOT_MODEL)
    try:
        generated = datetime.fromisoformat(str(previous.get("generated_at", "")))
    except (TypeError, ValueError):
        generated = datetime.min
    if datetime.now() - generated < timedelta(days=max(1, interval_days)):
        return None
    return consolidate(store)


def decay_fact_confidence(store: Any, *, half_life_days: int = 365, minimum: float = 0.2) -> int:
    """Decay stale non-permanent fact confidence once per calendar day."""
    today = datetime.now().date().isoformat()
    marker = store.db.get_state_model("memory_confidence_decay")
    if marker.get("date") == today:
        return 0
    changed = 0
    for fact in store.list_facts("active"):
        protected_tags = {"anchor", "pinned", "core_identity"}
        if fact.memory_kind == "permanent" or protected_tags & {tag.lower() for tag in fact.tags}:
            continue
        try:
            reference = datetime.fromisoformat(fact.updated_at or fact.created_at)
        except (TypeError, ValueError):
            continue
        age_days = max(0.0, (datetime.now() - reference).total_seconds() / 86400)
        if age_days < 30 or fact.confidence <= minimum:
            continue
        # Half-life decay, bounded to avoid erasing a fact solely due to age.
        new_confidence = max(minimum, fact.confidence * math.pow(0.5, age_days / half_life_days))
        if new_confidence < fact.confidence - 0.001:
            # Preserve the last content-confirmation timestamp. Otherwise this
            # maintenance update would make the fact look newly confirmed.
            store.db.update_fact_fields(
                fact.id,
                {"confidence": new_confidence, "version": fact.version + 1, "updated_at": fact.updated_at},
            )
            changed += 1
    store.db.save_state_model("memory_confidence_decay", {"date": today, "changed": changed})
    return changed
