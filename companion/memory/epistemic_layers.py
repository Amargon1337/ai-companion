"""Epistemic Layers — separates what WAS SAID from what is INFERRED from how it's EXPLAINED.

The most dangerous confusion in a memory system is mixing:
  - "User said: 'I want to quit smoking'"    (evidence)
  - "User is trying to quit smoking"          (inference)
  - "User is health-conscious"                (narrative)

These are three DIFFERENT things with different reliability.
This module enforces the separation.

Architecture:

    Evidence Layer (GROUND TRUTH)
    ┌───────────────────────────────────────┐
    │ What was actually said/observed       │
    │                                       │
    │ - Direct user messages                │
    │ - Observed events                     │
    │ - Explicit statements                 │
    │                                       │
    │ Trust: HIGH (it happened)             │
    │ Epistemic class: DIRECT_FACT,         │
    │                   USER_STATED         │
    └──────────────────┬────────────────────┘
                       │
                       ▼
    Inference Layer (DERIVED KNOWLEDGE)
    ┌───────────────────────────────────────┐
    │ What the system derives from evidence │
    │                                       │
    │ - Patterns ("smokes when stressed")   │
    │ - Reflections ("loneliness is         │
    │   a recurring theme")                 │
    │ - Human model insights                │
    │ - Causal links                        │
    │                                       │
    │ Trust: MEDIUM (depends on evidence)   │
    │ Epistemic class: HYPOTHESIS,          │
    │                   LLM_INFERENCE,      │
    │                   PREDICTION          │
    └──────────────────┬────────────────────┘
                       │
                       ▼
    Narrative Layer (EXPLANATION)
    ┌───────────────────────────────────────┐
    │ How the system explains to the human  │
    │                                       │
    │ - Personality snapshot                │
    │ - Golden memory                       │
    │ - Life continuity summary             │
    │ - Explainability reports              │
    │                                       │
    │ Trust: LOW (it's a STORY, not truth)  │
    │ Always regenerable from layers below  │
    └───────────────────────────────────────┘

CRITICAL RULE: When evidence changes, inferences MUST be re-evaluated.
               When inferences change, narrative MUST be regenerated.
               Never the reverse.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, TYPE_CHECKING

if TYPE_CHECKING:
    from companion.memory.store import MemoryStore

logger = logging.getLogger(__name__)


class EpistemicLayer(Enum):
    """The three layers of knowledge.

    Evidence: what was directly observed or stated.
    Inference: what the system derives from evidence.
    Narrative: how the system explains it to the human.
    """
    EVIDENCE = "evidence"
    INFERENCE = "inference"
    NARRATIVE = "narrative"


# Map epistemic_class (from models.Fact) to layer
_EPISTEMIC_CLASS_TO_LAYER = {
    "DIRECT_FACT": EpistemicLayer.EVIDENCE,
    "USER_STATED": EpistemicLayer.EVIDENCE,
    "HYPOTHESIS": EpistemicLayer.INFERENCE,
    "LLM_INFERENCE": EpistemicLayer.INFERENCE,
    "PREDICTION": EpistemicLayer.INFERENCE,
}

# Memory types that belong to each layer by definition
_EVIDENCE_MEMORY_KINDS = {"event", "permanent"}
_INFERENCE_CATEGORIES = {"behavior", "coping", "mistake", "relationship", "trend"}


def classify_fact(fact: Any) -> EpistemicLayer:
    """Determine which epistemic layer a fact belongs to.

    Rules (in priority order):
    1. If meta contains epistemic_class → use it (most reliable, survives DB)
    2. If fact text looks like an inference → inference
    3. Default → evidence (conservative: assume ground truth)

    Note: The Fact dataclass default for epistemic_class is 'DIRECT_FACT',
    but this is NOT persisted to SQLite. The meta dict IS persisted, so we
    check meta first.
    """
    # Rule 1: Check meta dict (persisted across DB roundtrip)
    meta = getattr(fact, "meta", None) or {}
    if isinstance(meta, dict):
        meta_eclass = meta.get("epistemic_class", "")
        if meta_eclass in _EPISTEMIC_CLASS_TO_LAYER:
            return _EPISTEMIC_CLASS_TO_LAYER[meta_eclass]

    # Rule 2: Heuristic analysis of the fact text
    text = getattr(fact, "fact", "")
    if _looks_like_inference(text):
        return EpistemicLayer.INFERENCE

    return EpistemicLayer.EVIDENCE


def classify_pattern(pattern: Any) -> EpistemicLayer:
    """Patterns are always inferences — they're derived from facts."""
    return EpistemicLayer.INFERENCE


def classify_reflection(reflection: Any) -> EpistemicLayer:
    """Reflections are always inferences — they're generalizations."""
    return EpistemicLayer.INFERENCE


def classify_insight(insight: Any) -> EpistemicLayer:
    """HumanModel insights are inferences about the person."""
    return EpistemicLayer.INFERENCE


def _looks_like_inference(text: str) -> bool:
    """Heuristic: does this fact text read like an inference?

    Inference indicators:
    - Generalizations ("always", "usually", "tends to")
    - Causal language ("because", "causes", "leads to")
    - Emotional interpretation ("feels", "seems", "appears")
    - Meta-cognitive ("prefers", "avoids", "struggles with")
    """
    import re
    text_lower = text.lower()

    inference_markers = {
        # Russian
        "всегда", "обычно", "часто", "как правило", "склон",
        "потому что", "из-за", "причина", "ведёт к",
        "чувствует", "кажется", "похоже",
        "предпочит", "избег", " struggle",
        "использует.*чтобы", "нужно.*чтобы",
        # English
        "always", "usually", "often", "tends to", "generally",
        "because", "causes", "leads to", "results in",
        "feels", "seems", "appears",
        "prefers", "avoids", "struggles with",
        "uses.*to", "needs.*to",
    }

    for marker in inference_markers:
        if ".*" in marker:
            if re.search(marker, text_lower):
                return True
        elif marker in text_lower:
            return True

    return False


# ── Cascade invalidation ────────────────────────────────────────────────

@dataclass
class CascadeResult:
    """Result of cascading invalidation when evidence changes.

    When an evidence-layer fact is invalidated (superseded, archived, etc.),
    all inference-layer entities that depend on it must be re-evaluated.
    If an inference loses all its evidence, it too becomes invalid, which
    may cascade further.
    """
    root_fact_id: str = ""
    root_reason: str = ""
    directly_affected: list[str] = field(default_factory=list)
    transitively_affected: list[str] = field(default_factory=list)
    narratives_to_regenerate: list[str] = field(default_factory=list)
    total_affected: int = 0


def cascade_invalidation(
    store: "MemoryStore",
    invalidated_fact_id: str,
    reason: str = "",
) -> CascadeResult:
    """When an evidence-layer fact is invalidated, cascade upward.

    1. Find all inference-layer entities that cite this fact as evidence
    2. For each affected inference:
       a. Count how many of its evidence sources are still valid
       b. If 0 valid sources → mark as refuted
       c. If some valid → reduce confidence proportionally
    3. Find all narrative-layer entities that depend on affected inferences
    4. Mark narratives for regeneration

    This prevents "immortal beliefs" — inferences that survive even when
    all their evidence is gone.
    """
    result = CascadeResult(
        root_fact_id=invalidated_fact_id,
        root_reason=reason,
    )

    # Step 1: Find facts that cite this as evidence
    directly_affected = _find_dependent_facts(store, invalidated_fact_id)
    result.directly_affected = [f.id for f in directly_affected]

    # Step 2: Find patterns that cite this fact
    dependent_patterns = _find_dependent_patterns(store, invalidated_fact_id)
    for pat in dependent_patterns:
        if pat.id not in result.directly_affected:
            result.directly_affected.append(pat.id)

    # Step 3: For each affected inference, re-evaluate
    for fact in directly_affected:
        _reevaluate_fact_evidence(store, fact)

    # Step 4: Find transitively affected (inferences that depend on inferences)
    for dep_id in list(result.directly_affected):
        transitive = _find_dependent_facts(store, dep_id)
        for t in transitive:
            if t.id not in result.directly_affected and t.id not in result.transitively_affected:
                result.transitively_affected.append(t.id)
                _reevaluate_fact_evidence(store, t)

    # Step 5: Identify narratives that need regeneration
    # Narratives are: personality snapshot, master summary, golden memory
    result.narratives_to_regenerate = [
        "personality_snapshot",
        "master_summary",
    ]
    if dependent_patterns:
        result.narratives_to_regenerate.append("golden_memory")

    result.total_affected = len(result.directly_affected) + len(result.transitively_affected)

    if result.total_affected > 0:
        logger.info(
            "Cascade invalidation from %s: %d directly affected, %d transitively, "
            "%d narratives to regenerate",
            invalidated_fact_id,
            len(result.directly_affected),
            len(result.transitively_affected),
            len(result.narratives_to_regenerate),
        )

    return result


def _find_dependent_facts(
    store: "MemoryStore", source_fact_id: str
) -> list:
    """Find facts that cite source_fact_id in their evidence list."""
    dependents = []
    for fact in store.list_all_facts():
        evidence = getattr(fact, "evidence", []) or []
        if source_fact_id in evidence:
            dependents.append(fact)
    return dependents


def _find_dependent_patterns(
    store: "MemoryStore", source_fact_id: str
) -> list:
    """Find patterns that cite source_fact_id in their evidence list."""
    dependents = []
    for pattern in store.list_patterns(status=None):
        evidence = getattr(pattern, "evidence", []) or []
        if source_fact_id in evidence:
            dependents.append(pattern)
    return dependents


def _reevaluate_fact_evidence(store: "MemoryStore", fact) -> None:
    """Re-evaluate a fact's confidence based on its evidence sources.

    If evidence sources are invalidated, the fact's confidence drops.
    If ALL sources are invalidated, the fact is marked as refuted.
    """
    evidence_ids = getattr(fact, "evidence", []) or []
    if not evidence_ids:
        return  # No evidence to check

    # Only re-evaluate inference-layer facts
    from companion.memory.epistemic_layers import classify_fact, EpistemicLayer
    if classify_fact(fact) != EpistemicLayer.INFERENCE:
        return

    valid_sources = 0
    total_sources = len(evidence_ids)

    for ev_id in evidence_ids:
        ev_fact = store.get_fact(ev_id)
        if ev_fact and ev_fact.status in ("active", "pending_review"):
            valid_sources += 1

    if valid_sources == 0:
        # ALL evidence gone → this inference is baseless
        logger.info(
            "Fact %s lost all evidence (%d sources). Marking as pending_review.",
            fact.id, total_sources,
        )
        try:
            meta = fact.meta if isinstance(fact.meta, dict) else {}
            meta["invalidation_reason"] = "all_evidence_lost"
            store.db.update_fact_fields(
                fact.id,
                {"status": "pending_review", "confidence": 0.1, "meta": meta},
            )
        except Exception as exc:
            logger.warning("Failed to mark %s as pending_review: %s", fact.id, exc)
    elif valid_sources < total_sources:
        # Some evidence lost → reduce confidence proportionally
        survival_ratio = valid_sources / total_sources
        new_confidence = max(0.1, fact.confidence * survival_ratio)
        if abs(new_confidence - fact.confidence) > 0.05:
            logger.info(
                "Fact %s lost evidence (%d/%d sources remain). "
                "Confidence: %.2f -> %.2f",
                fact.id, valid_sources, total_sources,
                fact.confidence, new_confidence,
            )
            try:
                store.db.update_fact_fields(
                    fact.id,
                    {"confidence": new_confidence},
                )
            except Exception as exc:
                logger.warning("Failed to update confidence for %s: %s", fact.id, exc)


# ── Layer audit ─────────────────────────────────────────────────────────

@dataclass
class LayerAuditResult:
    """Audit of epistemic layer separation.

    Checks that the system maintains clean separation between
    evidence, inference, and narrative layers.
    """
    evidence_count: int = 0
    inference_count: int = 0
    narrative_count: int = 0
    # Anomalies:
    evidence_citing_inference: list[str] = field(default_factory=list)
    inference_without_evidence: list[str] = field(default_factory=list)
    orphaned_inferences: list[str] = field(default_factory=list)


def audit_epistemic_layers(store: "MemoryStore") -> LayerAuditResult:
    """Audit the separation of epistemic layers.

    Checks:
    1. No evidence-layer fact cites an inference-layer fact as evidence
       (ground truth shouldn't depend on speculation)
    2. Every inference-layer fact has at least one evidence-layer source
       (inferences need grounding)
    3. No inference with ALL sources invalidated is still 'active'
    """
    result = LayerAuditResult()

    all_facts = store.list_all_facts()
    fact_layers = {}
    for f in all_facts:
        layer = classify_fact(f)
        fact_layers[f.id] = layer
        if layer == EpistemicLayer.EVIDENCE:
            result.evidence_count += 1
        elif layer == EpistemicLayer.INFERENCE:
            result.inference_count += 1

    # Check 1: evidence citing inference
    for f in all_facts:
        if fact_layers.get(f.id) != EpistemicLayer.EVIDENCE:
            continue
        evidence_ids = getattr(f, "evidence", []) or []
        for ev_id in evidence_ids:
            if fact_layers.get(ev_id) == EpistemicLayer.INFERENCE:
                result.evidence_citing_inference.append(f.id)

    # Check 2: inference without any evidence
    for f in all_facts:
        if fact_layers.get(f.id) != EpistemicLayer.INFERENCE:
            continue
        evidence_ids = getattr(f, "evidence", []) or []
        if not evidence_ids:
            result.inference_without_evidence.append(f.id)

    # Check 3: orphaned inferences (all evidence invalidated)
    for f in all_facts:
        if fact_layers.get(f.id) != EpistemicLayer.INFERENCE:
            continue
        if f.status != "active":
            continue
        evidence_ids = getattr(f, "evidence", []) or []
        if not evidence_ids:
            continue
        all_invalid = True
        for ev_id in evidence_ids:
            ev = store.get_fact(ev_id)
            if ev and ev.status in ("active", "pending_review"):
                all_invalid = False
                break
        if all_invalid:
            result.orphaned_inferences.append(f.id)

    # Narratives are: personality_snapshot, master_summary in state_models
    result.narrative_count = 2  # personality + master_summary

    if result.evidence_citing_inference:
        logger.warning(
            "Layer audit: %d evidence facts cite inference (wrong direction)",
            len(result.evidence_citing_inference),
        )
    if result.orphaned_inferences:
        logger.warning(
            "Layer audit: %d active inferences have ALL evidence invalidated",
            len(result.orphaned_inferences),
        )

    return result
