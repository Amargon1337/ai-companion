"""Memory Health Score — composite quality metric for each memory.

Every fact has a health score that determines whether it needs review.
This is the foundation of Self-Maintaining Cognitive Architecture.

Health Score = f(
    confidence,        — how sure are we?
    evidence_quality,  — how good are the sources?
    contradiction_cnt, — how many things conflict?
    age,               — how old is this?
    access_frequency,  — how often is it used?
    verification,      — has it been verified against source?
    source_reliability — how trustworthy is the origin?
)

Health ranges:
    0.8 - 1.0  HEALTHY    — no action needed
    0.5 - 0.8  DEGRADED   — monitor, may need review
    0.2 - 0.5  WEAK       — should be reviewed
    0.0 - 0.2  CRITICAL   — immediate review or archive

When health < threshold, fact enters the review queue:
    Review Queue
        ↓
    User confirmation / LLM recheck
        ↓
    Update belief state
"""
from __future__ import annotations

import logging
import math
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, TYPE_CHECKING

if TYPE_CHECKING:
    from companion.memory.store import MemoryStore
    from companion.models import Fact

logger = logging.getLogger(__name__)


# ── Health Score Weights ────────────────────────────────────────────────

# How much each factor contributes to the overall health score.
# These can be tuned per deployment.
DEFAULT_WEIGHTS = {
    "confidence": 0.25,        # Base certainty
    "evidence_quality": 0.20,  # How many sources, how reliable
    "contradiction": 0.15,     # How many conflicts exist
    "freshness": 0.10,         # Recency (prevents stale facts from dominating)
    "access_frequency": 0.10,  # Usage signal (unused facts may be irrelevant)
    "verification": 0.10,      # Has it been verified against source?
    "source_reliability": 0.10, # How trustworthy is the origin?
}


@dataclass
class HealthScore:
    """Composite health score for a single memory.

    Attributes:
        fact_id: The memory being scored.
        overall: 0.0-1.0 composite score.
        status: HEALTHY | DEGRADED | WEAK | CRITICAL
        components: Breakdown of each factor score.
        reasons: Human-readable explanations for low scores.
    """
    fact_id: str = ""
    overall: float = 0.0
    status: str = "healthy"
    components: dict[str, float] = field(default_factory=dict)
    reasons: list[str] = field(default_factory=list)

    def compute_status(self) -> None:
        """Set status based on overall score. Call after overall is set."""
        if self.overall >= 0.8:
            self.status = "healthy"
        elif self.overall >= 0.5:
            self.status = "degraded"
        elif self.overall >= 0.2:
            self.status = "weak"
        else:
            self.status = "critical"


@dataclass
class ReviewItem:
    """An item in the automatic review queue.

    When a fact's health drops below threshold, it enters the queue.
    The queue is processed by: user confirmation, LLM recheck, or
    automatic archival.
    """
    fact_id: str = ""
    fact_text: str = ""
    health_score: float = 0.0
    health_status: str = ""
    priority: str = "normal"  # "low" | "normal" | "high" | "urgent"
    reasons: list[str] = field(default_factory=list)
    suggested_action: str = "review"  # "review" | "reduce_confidence" | "archive" | "verify"
    created_at: str = ""


# ── Score computation ───────────────────────────────────────────────────

def compute_health(
    store: "MemoryStore",
    fact: Any,
    *,
    weights: dict[str, float] | None = None,
) -> HealthScore:
    """Compute the health score for a single fact.

    Args:
        store: MemoryStore for querying related data.
        fact: The Fact object to score.
        weights: Optional weight overrides.

    Returns:
        HealthScore with overall score and component breakdown.
    """
    w = weights or DEFAULT_WEIGHTS
    score = HealthScore(fact_id=fact.id)

    # ── Component 1: Confidence ─────────────────────────────────────────
    confidence = float(getattr(fact, "confidence", 0.5))
    score.components["confidence"] = confidence
    if confidence < 0.5:
        score.reasons.append(f"Low confidence: {confidence:.2f}")

    # ── Component 2: Evidence Quality ───────────────────────────────────
    evidence_ids = getattr(fact, "evidence", []) or []
    if not evidence_ids:
        # No evidence chain — depends on fact type
        # Check meta first (persisted), then dataclass attribute (may be default)
        meta = getattr(fact, "meta", {}) or {}
        if isinstance(meta, dict):
            epistemic_class = meta.get("epistemic_class", "")
        else:
            epistemic_class = ""
        if not epistemic_class:
            epistemic_class = getattr(fact, "epistemic_class", "DIRECT_FACT")
        if epistemic_class in ("DIRECT_FACT", "USER_STATED"):
            evidence_score = 0.8  # Direct facts don't need evidence
        else:
            evidence_score = 0.2  # Inferences without evidence are weak
            score.reasons.append("Inference has no evidence chain")
    else:
        valid_sources = 0
        for ev_id in evidence_ids:
            ev = store.get_fact(ev_id)
            if ev and ev.status in ("active", "pending_review"):
                valid_sources += 1
        evidence_score = valid_sources / len(evidence_ids)
        if evidence_score < 0.5:
            score.reasons.append(
                f"Weak evidence: {valid_sources}/{len(evidence_ids)} sources valid"
            )
    score.components["evidence_quality"] = evidence_score

    # ── Component 3: Contradiction Count ────────────────────────────────
    contradiction_count = int(getattr(fact, "contradiction_count", 0))
    support_count = int(getattr(fact, "support_count", 0))
    if contradiction_count == 0:
        contradiction_score = 1.0
    else:
        # Each contradiction reduces health. Support counters it.
        net_conflict = max(0, contradiction_count - support_count)
        contradiction_score = max(0.0, 1.0 - net_conflict * 0.25)
        if contradiction_score < 0.5:
            score.reasons.append(
                f"Contradictions: {contradiction_count} (support: {support_count})"
            )
    score.components["contradiction"] = contradiction_score

    # ── Component 4: Freshness ──────────────────────────────────────────
    from companion.memory.importance import days_since
    ref_date = getattr(fact, "last_confirmed_at", "") or getattr(fact, "updated_at", "") or getattr(fact, "created_at", "")
    age_days = days_since(ref_date) if ref_date else 0

    # Half-life: 180 days for facts, 90 for state
    kind = getattr(fact, "memory_kind", "event")
    half_life = 90.0 if kind == "state" else 180.0
    freshness = math.exp(-0.693 * age_days / half_life)
    freshness = max(0.1, freshness)  # Floor at 0.1 — old doesn't mean dead
    if freshness < 0.3:
        score.reasons.append(f"Stale: {age_days:.0f} days since last update")
    score.components["freshness"] = freshness

    # ── Component 5: Access Frequency ───────────────────────────────────
    sent_count = int(getattr(fact, "facts_sent_count", 0))
    used_count = int(getattr(fact, "facts_used_count", 0))
    if sent_count == 0:
        access_score = 0.5  # Unknown — neutral
    else:
        precision = used_count / sent_count if sent_count > 0 else 0
        # High retrieval + high use = healthy
        # High retrieval + low use = might be irrelevant
        access_score = min(1.0, 0.3 + 0.7 * precision)
    if sent_count > 10 and used_count == 0:
        score.reasons.append(f"Never used despite {sent_count} retrievals")
    score.components["access_frequency"] = access_score

    # ── Component 6: Verification History ───────────────────────────────
    meta = getattr(fact, "meta", {}) or {}
    if not isinstance(meta, dict):
        meta = {}
    verification_status = meta.get("verification_status", "")
    if verification_status == "verified":
        verification_score = 1.0
    elif verification_status == "mismatch":
        verification_score = 0.1
        score.reasons.append("Provenance verification: MISMATCH")
    elif verification_status == "weak_match":
        verification_score = 0.4
        score.reasons.append("Provenance verification: weak match")
    else:
        verification_score = 0.5  # Never verified — neutral
    score.components["verification"] = verification_score

    # ── Component 7: Source Reliability ─────────────────────────────────
    source = getattr(fact, "source", "") or ""
    source_type = getattr(fact, "source_type", "") or ""

    # Reliability hierarchy:
    # - explicit_user_input > auto_extract > compress > migration > system
    reliability_map = {
        "explicit": 1.0,        # /remember command
        "user": 0.9,            # Direct user statement
        "diary_entry": 0.85,    # Explicit diary
        "compress": 0.6,        # LLM extraction (may hallucinate)
        "migration": 0.5,       # Migrated data
        "system": 0.4,          # System-generated
    }
    source_reliability = reliability_map.get(source_type, 0.5)
    if source_reliability < 0.5:
        score.reasons.append(f"Low source reliability: {source_type}")
    score.components["source_reliability"] = source_reliability

    # ── Composite Score ─────────────────────────────────────────────────
    overall = sum(
        w.get(key, 0) * value
        for key, value in score.components.items()
    )
    score.overall = max(0.0, min(1.0, overall))
    score.compute_status()

    return score


# ── Review Queue ────────────────────────────────────────────────────────

def scan_review_queue(
    store: "MemoryStore",
    *,
    threshold: float = 0.5,
    max_items: int = 50,
    statuses: tuple[str, ...] = ("active",),
) -> list[ReviewItem]:
    """Scan all active facts and return those below health threshold.

    This is the main entry point for the self-maintaining system.
    Run periodically (nightly, or on-demand via /memory_health).

    Args:
        store: MemoryStore instance.
        threshold: Facts below this health score enter the queue.
        max_items: Maximum items to return.
        statuses: Which fact statuses to scan.

    Returns:
        List of ReviewItem, sorted by health (worst first).
    """
    items: list[ReviewItem] = []

    for status in statuses:
        for fact in store.list_facts(status):
            # Skip immune facts
            from companion.memory.immunity import is_immune
            if is_immune(fact):
                continue

            health = compute_health(store, fact)
            if health.overall < threshold:
                priority = _compute_priority(health)
                action = _suggest_action(health, fact)
                items.append(ReviewItem(
                    fact_id=fact.id,
                    fact_text=fact.fact[:200],
                    health_score=health.overall,
                    health_status=health.status,
                    priority=priority,
                    reasons=health.reasons,
                    suggested_action=action,
                    created_at=datetime.now().isoformat(),
                ))

    items.sort(key=lambda x: x.health_score)
    return items[:max_items]


def _compute_priority(health: HealthScore) -> str:
    """Determine review priority from health score."""
    if health.overall < 0.2:
        return "urgent"
    elif health.overall < 0.35:
        return "high"
    elif health.overall < 0.5:
        return "normal"
    return "low"


def _suggest_action(health: HealthScore, fact: Any) -> str:
    """Suggest what to do with a low-health fact."""
    components = health.components

    # If verification failed → verify
    if components.get("verification", 1.0) < 0.3:
        return "verify"

    # If no evidence and it's an inference → archive
    if components.get("evidence_quality", 1.0) < 0.2:
        epistemic = getattr(fact, "epistemic_class", "DIRECT_FACT")
        if epistemic not in ("DIRECT_FACT", "USER_STATED"):
            return "archive"

    # If contradictions dominate → reduce confidence
    if components.get("contradiction", 1.0) < 0.3:
        return "reduce_confidence"

    # If just stale → verify and update
    if components.get("freshness", 1.0) < 0.3:
        return "verify"

    # Default: review
    return "review"


# ── Bulk Health Report ──────────────────────────────────────────────────

def memory_health_report(store: "MemoryStore") -> dict[str, Any]:
    """Generate a full health report for the memory system.

    Returns aggregate statistics and top items needing attention.
    """
    all_facts = store.list_facts("active")
    scores = []
    status_counts = {"healthy": 0, "degraded": 0, "weak": 0, "critical": 0}

    for fact in all_facts:
        health = compute_health(store, fact)
        scores.append(health)
        status_counts[health.status] += 1

    avg_health = sum(s.overall for s in scores) / max(1, len(scores))
    review_items = scan_review_queue(store, threshold=0.5, max_items=10)

    # Layer audit
    from companion.memory.epistemic_layers import audit_epistemic_layers
    layer_audit = audit_epistemic_layers(store)

    return {
        "total_active_facts": len(all_facts),
        "average_health": round(avg_health, 3),
        "health_distribution": status_counts,
        "review_queue_size": len(review_items),
        "top_review_items": [
            {
                "id": item.fact_id,
                "text": item.fact_text[:100],
                "health": round(item.health_score, 3),
                "priority": item.priority,
                "action": item.suggested_action,
            }
            for item in review_items[:5]
        ],
        "epistemic_layers": {
            "evidence": layer_audit.evidence_count,
            "inference": layer_audit.inference_count,
            "narrative": layer_audit.narrative_count,
            "anomalies": {
                "evidence_citing_inference": len(layer_audit.evidence_citing_inference),
                "inference_without_evidence": len(layer_audit.inference_without_evidence),
                "orphaned_inferences": len(layer_audit.orphaned_inferences),
            },
        },
    }
