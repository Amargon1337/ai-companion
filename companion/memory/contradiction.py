"""Contradiction Engine v2 — distinguishes evolution from contradiction.

The first version treated all conflicts as contradictions. This was wrong.

    "I love coffee" -> "I no longer drink coffee"

This is NOT a contradiction. It's a temporal transition — the person
changed. A good memory system must recognize this.

Conflict classification::

    Detected conflict
         |
    Has change markers + time?
        YES /    \\ NO
       /          \\
    temporal_    logical_
    transition   contradiction
         |             |
    create LifeT.  supersede/quarantine

Additional classes:
    preference_change — "I liked X" -> "I don't like X anymore"
    uncertainty_update — same fact, confidence changed
    agreement — facts confirm each other

The engine now also checks temporal distance. Two facts 6 months apart
with change markers are almost certainly an evolution, not a bug.
"""
from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field
from typing import Any, TYPE_CHECKING

if TYPE_CHECKING:
    from companion.memory.store import MemoryStore
    from companion.models import Fact

logger = logging.getLogger(__name__)


# ── Lexicon ─────────────────────────────────────────────────────────────

# Negation markers — flip the truth value of a predicate
_NEGATION_MARKERS = {
    "не", "нет", "никогда", "ничего", "никак", "нигде", "никто",
    "not", "never", "nothing", "no", "none",
}

# Change markers — indicate the person/thing TRANSFORMED, not that
# the old fact was wrong. These signal temporal_transition, not contradiction.
_CHANGE_MARKERS = {
    # Russian
    "больше не", "перестал", "перестала", "перестали",
    "бросил", "бросила", "отказался", "отказалась",
    "теперь", "теперь не", "раньше", "раньше было",
    "начал", "начала", "начали",
    "стал", "стала", "стали",
    "прекратил", "прекратила",
    "вернулся", "вернулась",
    "переехал", "переехала",
    "уволился", "уволилась",
    "развёлся", "развелась",
    # English
    "no longer", "stopped", "quit", "started", "began",
    "now", "used to", "no more", "changed to",
    "moved to", "switched to", "became",
}

# Temporal distance markers — vague time references
_TEMPORAL_MARKERS = {
    "сейчас", "сегодня", "вчера", "завтра", "недавно",
    "раньше", "теперь", "уже", "ещё", "пока", "потом",
    "всегда", "иногда", "часто", "редко",
    "now", "today", "yesterday", "recently", "before",
    "always", "sometimes", "often", "rarely",
}

# Preference-domain words — changes in these are more likely preference
# evolution than logical contradiction
_PREFERENCE_DOMAINS = {
    "люб", "нрав", "хоч", "предпоч", "вку",
    "like", "love", "prefer", "enjoy", "want", "hate",
    "кофе", "чай", "еда", "музык", "фильм", "книг",
    "coffee", "tea", "food", "music", "movie", "book",
}


# ── Data structures ─────────────────────────────────────────────────────

@dataclass
class ContradictionResult:
    """Result of conflict analysis between a new fact and existing knowledge.

    Unlike v1, this distinguishes between different conflict classes.
    """
    new_fact_text: str = ""
    conflicts: list[ConflictRecord] = field(default_factory=list)
    transitions: list[TransitionRecord] = field(default_factory=list)
    agreements: list[dict[str, Any]] = field(default_factory=list)
    resolution: str | None = None


@dataclass
class ConflictRecord:
    """A logical contradiction — two facts that cannot both be true.

    Example: "Иван живёт в Минске" AND "Иван НЕ живёт в Минске"
    at the same time period.
    """
    existing_fact_id: str = ""
    existing_fact_text: str = ""
    conflict_class: str = "logical_contradiction"
    confidence: float = 0.5
    reason: str = ""
    # Backward compatibility: conflict_type maps to conflict_class
    conflict_type: str = "contradicts"


@dataclass
class TransitionRecord:
    """A temporal transition — person/thing changed over time.

    Example: "Иван курит" (Jan 2026) → "Иван бросил курить" (Jun 2026)
    This is NOT a contradiction. It's growth.
    """
    existing_fact_id: str = ""
    existing_fact_text: str = ""
    transition_type: str = "temporal_transition"
    # "temporal_transition" — explicit change markers + time gap
    # "preference_change" — preference domain, natural evolution
    # "uncertainty_update" — same fact, refined confidence
    confidence: float = 0.7
    reason: str = ""
    change_marker: str = ""  # The word/phrase that signals change
    time_gap_days: float = 0.0


# ── Main API ────────────────────────────────────────────────────────────

def check_contradictions(
    store: "MemoryStore",
    new_fact_text: str,
    *,
    threshold: float = 0.6,
    max_candidates: int = 10,
) -> ContradictionResult:
    """Check a new fact against existing knowledge.

    Classifies each detected conflict as either:
    - logical_contradiction: real conflict, needs resolution
    - temporal_transition: person changed, create LifeTransition
    - preference_change: preference evolved naturally
    - uncertainty_update: same fact, different confidence

    Returns ContradictionResult with separate lists for conflicts
    (real contradictions) and transitions (evolution).
    """
    result = ContradictionResult(new_fact_text=new_fact_text)

    if not new_fact_text or not new_fact_text.strip():
        return result

    new_norm = _normalize(new_fact_text)
    new_words = set(re.findall(r"\w+", new_norm))
    new_has_negation = bool(new_words & _NEGATION_MARKERS)
    new_has_change_marker = _has_change_marker(new_fact_text)
    new_is_preference = _is_preference_domain(new_fact_text)

    candidates = _get_candidates(store, new_fact_text, max_candidates)

    for candidate in candidates:
        cand_text = candidate.get("fact", "")
        cand_id = candidate.get("id", "")
        cand_date = candidate.get("date") or candidate.get("created_at", "")
        if not cand_text or not cand_id:
            continue

        cand_norm = _normalize(cand_text)
        cand_words = set(re.findall(r"\w+", cand_norm))
        cand_has_negation = bool(cand_words & _NEGATION_MARKERS)

        if cand_norm == new_norm:
            continue

        # ── Check 1: Negation flip ──────────────────────────────────────
        if new_has_negation != cand_has_negation:
            new_stripped = new_words - _NEGATION_MARKERS
            cand_stripped = cand_words - _NEGATION_MARKERS
            overlap = len(new_stripped & cand_stripped) / max(len(new_stripped | cand_stripped), 1)

            if overlap > 0.5:
                # KEY INSIGHT: If the new fact has a change marker AND there's
                # significant time between the facts, this is a TRANSITION,
                # not a contradiction.
                if new_has_change_marker:
                    from companion.memory.importance import days_since
                    time_gap = days_since(cand_date) if cand_date else 0

                    if time_gap > 7:  # At least a week apart
                        change_marker = _find_change_marker(new_fact_text)
                        result.transitions.append(TransitionRecord(
                            existing_fact_id=cand_id,
                            existing_fact_text=cand_text,
                            transition_type="temporal_transition",
                            confidence=min(1.0, overlap + 0.3),
                            reason=f"Person changed: '{change_marker}' detected. "
                                   f"Time gap: {time_gap:.0f} days. "
                                   f"This is evolution, not contradiction.",
                            change_marker=change_marker or "",
                            time_gap_days=time_gap,
                        ))
                        continue

                # No change marker → real contradiction
                result.conflicts.append(ConflictRecord(
                    existing_fact_id=cand_id,
                    existing_fact_text=cand_text,
                    conflict_class="logical_contradiction",
                    confidence=min(1.0, overlap + 0.2),
                    reason=f"Negation conflict: one states, other denies. "
                           f"Word overlap: {overlap:.2f}",
                    conflict_type="negation_opposite",
                ))
                continue

        # ── Check 2: Semantic similarity ─────────────────────────────────
        from companion.memory.text_sim import text_overlap
        similarity = text_overlap(new_norm, cand_norm)

        if similarity > threshold:
            if similarity < 0.85:
                # Similar but different. Is it evolution or contradiction?
                if new_has_change_marker or new_is_preference:
                    from companion.memory.importance import days_since
                    time_gap = days_since(cand_date) if cand_date else 0

                    if new_is_preference and time_gap > 14:
                        change_marker = _find_change_marker(new_fact_text)
                        result.transitions.append(TransitionRecord(
                            existing_fact_id=cand_id,
                            existing_fact_text=cand_text,
                            transition_type="preference_change",
                            confidence=similarity * 0.8,
                            reason=f"Preference evolved. Similarity: {similarity:.2f}. "
                                   f"Time gap: {time_gap:.0f} days.",
                            change_marker=change_marker or "",
                            time_gap_days=time_gap,
                        ))
                        continue

                    if new_has_change_marker and time_gap > 7:
                        change_marker = _find_change_marker(new_fact_text)
                        result.transitions.append(TransitionRecord(
                            existing_fact_id=cand_id,
                            existing_fact_text=cand_text,
                            transition_type="temporal_transition",
                            confidence=similarity * 0.9,
                            reason=f"Change detected. Similarity: {similarity:.2f}. "
                                   f"Marker: '{change_marker}'. Gap: {time_gap:.0f} days.",
                            change_marker=change_marker or "",
                            time_gap_days=time_gap,
                        ))
                        continue

                # No evolution signals → flag as potential contradiction
                result.conflicts.append(ConflictRecord(
                    existing_fact_id=cand_id,
                    existing_fact_text=cand_text,
                    conflict_class="logical_contradiction",
                    confidence=similarity * 0.7,
                    reason=f"High text overlap ({similarity:.2f}) but not identical. "
                           f"No change markers detected.",
                    conflict_type="contradicts",
                ))
            else:
                # Very similar → agreement/confirmation
                result.agreements.append({
                    "id": cand_id,
                    "text": cand_text[:200],
                    "similarity": similarity,
                })

        # ── Check 3: Entity value conflict ───────────────────────────────
        entity_conflict = _check_entity_conflict(new_words, cand_words)
        if entity_conflict:
            # Same subject, different values. Is this a move or a contradiction?
            if new_has_change_marker:
                from companion.memory.importance import days_since
                time_gap = days_since(cand_date) if cand_date else 0
                change_marker = _find_change_marker(new_fact_text)
                result.transitions.append(TransitionRecord(
                    existing_fact_id=cand_id,
                    existing_fact_text=cand_text,
                    transition_type="temporal_transition",
                    confidence=0.85,
                    reason=f"Entity value changed: {entity_conflict}. "
                           f"Marker: '{change_marker}'. Gap: {time_gap:.0f} days.",
                    change_marker=change_marker or "",
                    time_gap_days=time_gap,
                ))
            else:
                result.conflicts.append(ConflictRecord(
                    existing_fact_id=cand_id,
                    existing_fact_text=cand_text,
                    conflict_class="logical_contradiction",
                    confidence=0.8,
                    reason=f"Same entity, different values: {entity_conflict}. "
                           f"No change markers → possible contradiction.",
                    conflict_type="entity_value_conflict",
                ))

    return result


def resolve_contradiction(
    store: "MemoryStore",
    new_fact: "Fact",
    conflict: ConflictRecord,
) -> str:
    """Resolve a logical contradiction (not a transition).

    Resolution rules:
    1. Protected facts always win
    2. Higher confidence wins
    3. Equal confidence → newer fact wins

    Returns: "supersede", "quarantine_new", "quarantine_old", "pending_review"
    """
    existing = store.get_fact(conflict.existing_fact_id)
    if existing is None:
        return "pending_review"

    existing_protected = _is_protected(existing)
    new_protected = _is_protected(new_fact)

    if existing_protected and not new_protected:
        return "quarantine_new"
    if new_protected and not existing_protected:
        return "supersede"

    if new_fact.confidence > existing.confidence + 0.1:
        return "supersede"
    if existing.confidence > new_fact.confidence + 0.1:
        return "quarantine_new"

    from companion.memory.importance import days_since
    new_age = days_since(new_fact.date or new_fact.created_at)
    old_age = days_since(existing.date or existing.created_at)
    if new_age < old_age:
        return "supersede"
    return "quarantine_old"


# ── Helpers ─────────────────────────────────────────────────────────────

def _has_change_marker(text: str) -> bool:
    """Check if text contains markers indicating change/evolution."""
    text_lower = text.lower()
    # Check multi-word markers first
    for marker in _CHANGE_MARKERS:
        if " " in marker:
            if marker in text_lower:
                return True
    # Then single-word markers
    words = set(re.findall(r"\w+", text_lower))
    single_markers = {m for m in _CHANGE_MARKERS if " " not in m}
    return bool(words & single_markers)


def _find_change_marker(text: str) -> str:
    """Find the specific change marker in text."""
    text_lower = text.lower()
    for marker in sorted(_CHANGE_MARKERS, key=len, reverse=True):
        if marker in text_lower:
            return marker
    return ""


def _is_preference_domain(text: str) -> bool:
    """Check if the text is about preferences/tastes."""
    text_lower = text.lower()
    return any(word in text_lower for word in _PREFERENCE_DOMAINS)


def _get_candidates(
    store: "MemoryStore", new_fact_text: str, max_candidates: int
) -> list[dict[str, Any]]:
    """Get candidate facts for comparison."""
    candidates: dict[str, dict[str, Any]] = {}

    try:
        search_results = store.vector.search(
            new_fact_text, top_k=max_candidates, content_type="fact"
        )
        for r in search_results:
            content_hash = r.get("content_hash", "")
            for f in store.list_facts("active"):
                if store.vector._content_hash(f.fact) == content_hash:
                    candidates[f.id] = f.to_dict()
                    break
    except Exception as exc:
        logger.debug("Vector search for contradictions failed: %s", exc)

    if len(candidates) < max_candidates:
        new_words = set(re.findall(r"\w+", _normalize(new_fact_text)))
        significant_words = {w for w in new_words if len(w) >= 4}
        if significant_words:
            for f in store.list_facts("active"):
                if f.id in candidates:
                    continue
                f_words = set(re.findall(r"\w+", _normalize(f.fact)))
                overlap = len(significant_words & f_words)
                if overlap >= 2:
                    candidates[f.id] = f.to_dict()
                    if len(candidates) >= max_candidates:
                        break

    return list(candidates.values())[:max_candidates]


def _check_entity_conflict(words_a: set[str], words_b: set[str]) -> str | None:
    """Detect same entity with different attribute values."""
    common = words_a & words_b
    unique_a = words_a - words_b
    unique_b = words_b - words_a

    if len(common) >= 2 and unique_a and unique_b:
        overlap_ratio = len(common) / max(len(words_a), len(words_b))
        if overlap_ratio > 0.4:
            return f"different values: {{{', '.join(unique_a)}}} vs {{{', '.join(unique_b)}}}"
    return None


def _is_protected(fact) -> bool:
    """Check if a fact is protected from being superseded."""
    if hasattr(fact, "memory_kind") and fact.memory_kind == "permanent":
        return True
    tags = [str(t).lower() for t in (getattr(fact, "tags", []) or [])]
    if {"anchor", "core_identity", "pinned"} & set(tags):
        return True
    if getattr(fact, "importance", 0) >= 9:
        return True
    return False


def _normalize(text: str) -> str:
    return re.sub(r"\s+", " ", text.lower().strip())
