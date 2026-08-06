"""Contradiction Engine — detects and manages conflicts between memories.

When new information enters the system, it may contradict existing knowledge.
The Contradiction Engine:

1. Detects potential contradictions via semantic + lexical analysis
2. Classifies the conflict (agreement / contradiction / uncertainty)
3. Creates contradiction records that link conflicting facts
4. Guides resolution: protected facts win, newer facts supersede older ones

This is NOT about deleting old information. It's about making conflicts
visible and auditable. The system should be able to say:

    "I have two conflicting beliefs:
     A) 'Иван живёт в Минске' (confidence 0.8, stated 2025-01)
     B) 'Иван живёт в Берлине' (confidence 0.9, stated 2026-06)
     These contradict. B is newer and more confident.
     I believe B, but A is preserved as superseded."

Architecture:
    New fact → check_contradictions() → list of (existing_fact, conflict_type)
                                         │
                                         ├─ agreement     → confirm (bump support)
                                         ├─ contradiction → create relation, resolve
                                         └─ uncertainty   → mark pending_review
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


# Negation markers — facts with negation are checked more carefully
_NEGATION_MARKERS = {
    "не", "нет", "никогда", "ничего", "никак", "нигде", "никто",
    "перестал", "бросил", "отказался", "запрещено",
    "not", "never", "nothing", "no", "stopped",
}

# Temporal markers — indicate time-bounded facts
_TEMPORAL_MARKERS = {
    "сейчас", "сегодня", "вчера", "завтра", "недавно", "раньше",
    "потом", "сейчас", "всегда", "иногда", "часто", "редко",
    "раньше", "теперь", "уже", "ещё", "пока",
}


@dataclass
class ContradictionResult:
    """Result of contradiction check between a new fact and existing knowledge.

    Attributes:
        new_fact_text: Text of the incoming fact.
        conflicts: List of detected conflicts with existing facts.
        agreements: List of existing facts that confirm/support the new fact.
        resolution: How the conflict was resolved (or None if no conflict).
    """
    new_fact_text: str = ""
    conflicts: list[ConflictRecord] = field(default_factory=list)
    agreements: list[dict[str, Any]] = field(default_factory=list)
    resolution: str | None = None  # "supersede", "quarantine", "none"


@dataclass
class ConflictRecord:
    """A single conflict between two facts.

    Attributes:
        existing_fact_id: ID of the existing fact.
        existing_fact_text: Text of the existing fact.
        conflict_type: "contradicts" | "negation_opposite" | "temporal_supersede"
        confidence: How confident we are this is a real contradiction (0-1).
        reason: Human-readable explanation.
    """
    existing_fact_id: str = ""
    existing_fact_text: str = ""
    conflict_type: str = "contradicts"
    confidence: float = 0.5
    reason: str = ""


def check_contradictions(
    store: MemoryStore,
    new_fact_text: str,
    *,
    threshold: float = 0.6,
    max_candidates: int = 10,
) -> ContradictionResult:
    """Check a new fact against existing knowledge for contradictions.

    This is called BEFORE inserting a new fact. If contradictions are found,
    the caller can:
    - Create a 'contradicts' relation and let the resolution logic handle it
    - Send the fact to 'pending_review' if confidence is low
    - Proceed normally if no contradictions found

    Args:
        store: MemoryStore instance for querying existing facts.
        new_fact_text: The text of the incoming fact.
        threshold: Minimum similarity to consider as potential conflict.
        max_candidates: Maximum candidates to check (performance).

    Returns:
        ContradictionResult with conflicts and agreements.
    """
    result = ContradictionResult(new_fact_text=new_fact_text)

    if not new_fact_text or not new_fact_text.strip():
        return result

    new_norm = _normalize(new_fact_text)
    new_words = set(re.findall(r"\w+", new_norm))
    new_has_negation = bool(new_words & _NEGATION_MARKERS)

    # Get candidate facts to check against
    candidates = _get_candidates(store, new_fact_text, max_candidates)

    for candidate in candidates:
        cand_text = candidate.get("fact", "")
        cand_id = candidate.get("id", "")
        if not cand_text or not cand_id:
            continue

        cand_norm = _normalize(cand_text)
        cand_words = set(re.findall(r"\w+", cand_norm))
        cand_has_negation = bool(cand_words & _NEGATION_MARKERS)

        # Skip if same fact
        if cand_norm == new_norm:
            continue

        # ── Check 1: Negation flip ──────────────────────────────────────
        # "Иван курит" vs "Иван не курит"
        # Same words except one has negation → contradiction
        if new_has_negation != cand_has_negation:
            # Remove negation words and compare
            new_stripped = new_words - _NEGATION_MARKERS
            cand_stripped = cand_words - _NEGATION_MARKERS
            overlap = len(new_stripped & cand_stripped) / max(len(new_stripped | cand_stripped), 1)
            if overlap > 0.5:
                result.conflicts.append(ConflictRecord(
                    existing_fact_id=cand_id,
                    existing_fact_text=cand_text,
                    conflict_type="negation_opposite",
                    confidence=min(1.0, overlap + 0.2),
                    reason=f"Negation conflict: one states, other denies. "
                           f"Word overlap (without negation): {overlap:.2f}",
                ))
                continue

        # ── Check 2: Semantic similarity + different meaning ─────────────
        # Use text_overlap for fuzzy matching
        from companion.memory.text_sim import text_overlap
        similarity = text_overlap(new_norm, cand_norm)
        if similarity > threshold:
            # High similarity but not identical → potential contradiction
            # or confirmation depending on context
            if similarity < 0.85:
                # Similar but different → uncertain, flag it
                result.conflicts.append(ConflictRecord(
                    existing_fact_id=cand_id,
                    existing_fact_text=cand_text,
                    conflict_type="contradicts",
                    confidence=similarity * 0.7,  # Lower confidence for fuzzy
                    reason=f"High text overlap ({similarity:.2f}) but not identical. "
                           f"May be contradiction or update.",
                ))
            else:
                # Very similar → likely agreement/confirmation
                result.agreements.append({
                    "id": cand_id,
                    "text": cand_text[:200],
                    "similarity": similarity,
                })

        # ── Check 3: Entity-based contradiction ──────────────────────────
        # "Иван живёт в Минске" vs "Иван живёт в Берлине"
        # Same subject + same predicate structure + different object
        entity_conflict = _check_entity_conflict(new_words, cand_words)
        if entity_conflict:
            result.conflicts.append(ConflictRecord(
                existing_fact_id=cand_id,
                existing_fact_text=cand_text,
                conflict_type="entity_value_conflict",
                confidence=0.8,
                reason=f"Same subject, same attribute, different value: {entity_conflict}",
            ))

    return result


def resolve_contradiction(
    store: MemoryStore,
    new_fact: Fact,
    conflict: ConflictRecord,
) -> str:
    """Resolve a contradiction between a new fact and an existing fact.

    Resolution rules:
    1. If existing fact is permanent/anchored → existing wins, new fact quarantined
    2. If new fact has higher confidence → new supersedes old
    3. If existing fact has higher confidence → old kept, new marked pending_review
    4. If equal confidence → newer fact wins (time-based resolution)

    Returns: "supersede", "quarantine_new", "quarantine_old", "pending_review"
    """
    existing = store.get_fact(conflict.existing_fact_id)
    if existing is None:
        return "pending_review"

    # Rule 1: Protected facts always win
    existing_protected = _is_protected(existing)
    new_protected = _is_protected(new_fact)

    if existing_protected and not new_protected:
        logger.info(
            "Contradiction resolved: existing fact %s is protected. "
            "New fact %s → pending_review.",
            existing.id, new_fact.id,
        )
        return "quarantine_new"

    if new_protected and not existing_protected:
        logger.info(
            "Contradiction resolved: new fact %s is protected. "
            "Existing fact %s → superseded.",
            new_fact.id, existing.id,
        )
        return "supersede"

    # Rule 2: Higher confidence wins
    if new_fact.confidence > existing.confidence + 0.1:
        return "supersede"
    if existing.confidence > new_fact.confidence + 0.1:
        return "quarantine_new"

    # Rule 3: Equal confidence → newer fact wins
    from companion.memory.importance import days_since
    new_age = days_since(new_fact.date or new_fact.created_at)
    old_age = days_since(existing.date or existing.created_at)
    if new_age < old_age:
        return "supersede"
    else:
        return "quarantine_old"


def _get_candidates(
    store: MemoryStore, new_fact_text: str, max_candidates: int
) -> list[dict[str, Any]]:
    """Get candidate facts to check for contradictions.

    Strategy:
    1. Vector search for semantically similar facts
    2. Keyword search for overlapping content
    3. Recent facts (temporal proximity)
    """
    candidates: dict[str, dict[str, Any]] = {}

    # Vector search
    try:
        search_results = store.vector.search(
            new_fact_text, top_k=max_candidates, content_type="fact"
        )
        for r in search_results:
            content_hash = r.get("content_hash", "")
            # Find the fact with this content hash
            for f in store.list_facts("active"):
                if store.vector._content_hash(f.fact) == content_hash:
                    candidates[f.id] = f.to_dict()
                    break
    except Exception as exc:
        logger.debug("Vector search for contradictions failed: %s", exc)

    # Keyword fallback if vector search didn't find enough
    if len(candidates) < max_candidates:
        new_words = set(re.findall(r"\w+", _normalize(new_fact_text)))
        # Only check words with length >= 4 (skip stop words)
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
    """Detect if two fact texts describe the same entity with different values.

    Pattern: same subject words + same verb/attribute words + different object.
    E.g., "Иван живёт в Минске" vs "Иван живёт в Берлине"
    """
    # Common words (subject + predicate)
    common = words_a & words_b
    # Unique words (objects/values)
    unique_a = words_a - words_b
    unique_b = words_b - words_a

    # If there are significant common words AND both have unique content words
    if len(common) >= 2 and unique_a and unique_b:
        # Check if common words form >50% of either set
        overlap_ratio = len(common) / max(len(words_a), len(words_b))
        if overlap_ratio > 0.4:
            return f"different values: {{{', '.join(unique_a)}}} vs {{{', '.join(unique_b)}}}"
    return None


def _is_protected(fact) -> bool:
    """Check if a fact is protected from being superseded."""
    if hasattr(fact, "memory_kind") and fact.memory_kind == "permanent":
        return True
    tags = [str(t).lower() for t in (getattr(fact, "tags", []) or [])]
    protected_tags = {"anchor", "core_identity", "pinned"}
    if protected_tags & set(tags):
        return True
    if getattr(fact, "importance", 0) >= 9:
        return True
    return False


def _normalize(text: str) -> str:
    """Normalize text for comparison."""
    return re.sub(r"\s+", " ", text.lower().strip())
