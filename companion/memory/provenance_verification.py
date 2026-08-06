"""Provenance Verification — prevents 'memory hallucination'.

The most dangerous bug in a cognitive memory system is not a crash.
It's when the system confidently explains a WRONG story.

Example:
    Fact: "Иван работает QA инженером"
    Provenance: message_123
    But message_123 actually said: "Иван ХОЧЕТ работать QA инженером"

The provenance chain exists. The source message exists. But the fact
text doesn't match the source semantics. The system has "hallucinated"
a fact during extraction.

This module provides:

1. verify_fact_against_source(fact, source_text) → VerificationResult
   Checks if the fact text is semantically supported by the source.

2. verify_provenance_chain(store, fact_id) → ChainVerificationResult
   Walks the full evidence chain and checks each link.

3. detect_hallucinated_facts(store) → list[HallucinationReport]
   Scans active facts for potential hallucinations.

The verification is heuristic (not LLM-based) to avoid circular
dependency — we can't use LLM to verify LLM output.
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


@dataclass
class VerificationResult:
    """Result of verifying a fact against its source.

    Attributes:
        fact_id: The fact being verified.
        source_id: The source (message or fact) being checked.
        status: "verified" | "weak_match" | "mismatch" | "no_source"
        semantic_overlap: 0.0-1.0 overlap score between fact and source.
        key_phrases_in_source: Which key phrases from the fact appear in source.
        key_phrases_missing: Key phrases from the fact NOT found in source.
        hedging_detected: Whether the source uses hedging language.
        hedging_phrases: The specific hedging phrases found.
    """
    fact_id: str = ""
    source_id: str = ""
    status: str = "no_source"
    semantic_overlap: float = 0.0
    key_phrases_in_source: list[str] = field(default_factory=list)
    key_phrases_missing: list[str] = field(default_factory=list)
    hedging_detected: bool = False
    hedging_phrases: list[str] = field(default_factory=list)


@dataclass
class ChainVerificationResult:
    """Result of verifying a full provenance chain.

    Walks from a fact through its evidence to original messages.
    Each link is verified independently.
    """
    fact_id: str = ""
    fact_text: str = ""
    chain_links: list[VerificationResult] = field(default_factory=list)
    overall_status: str = "unknown"
    # "verified" — all links verified
    # "weak" — some links have weak matches
    # "suspicious" — significant mismatch detected
    # "broken" — source not found or chain incomplete
    issues: list[str] = field(default_factory=list)


@dataclass
class HallucinationReport:
    """Report of a potentially hallucinated fact.

    These are facts where the provenance exists but the semantic
    match to the source is weak, suggesting the LLM extraction
    may have distorted the meaning.
    """
    fact_id: str = ""
    fact_text: str = ""
    source_id: str = ""
    source_text: str = ""
    overlap: float = 0.0
    risk_level: str = "low"  # "low" | "medium" | "high"
    reason: str = ""


# ── Hedging language detection ──────────────────────────────────────────

# These phrases indicate the source is NOT stating a fact directly,
# but expressing a wish, possibility, or hypothetical.
_HEDGING_PHRASES = {
    # Russian
    "хочет", "хочу", "хотел", "хотела", "хотел бы", "хотела бы",
    "может быть", "возможно", "наверное", "кажется", "похоже",
    "планирует", "планирую", "собирается", "собираюсь",
    "мечтает", "мечтаю", "надеется", "надеюсь",
    "думает", "думаю", "полагаю", "предполагаю",
    "должен", "должна", "надо бы", "стоит",
    "иногда", "бывает", "случается",
    # English
    "wants to", "wants", "would like", "hoping", "hopes",
    "might", "maybe", "perhaps", "probably", "possibly",
    "planning to", "plans to", "thinking of", "considering",
    "dreams of", "dreams about", "wishes",
    "sometimes", "occasionally", "tends to",
}

# Intensifiers — these make a statement STRONGER, not weaker
_INTENSIFIERS = {
    "точно", "точно знаю", "уверен", "уверена", "факт",
    "definitely", "certainly", "absolutely", "for sure",
    "всегда", "никогда", "каждый день",
    "always", "never", "every day",
}


def verify_fact_against_source(
    fact_text: str,
    source_text: str,
    *,
    fact_id: str = "",
    source_id: str = "",
) -> VerificationResult:
    """Verify that a fact is semantically supported by its source.

    This is a HEURISTIC check, not an LLM call. We compare:
    1. Key phrases from the fact — do they appear in the source?
    2. Hedging language — does the source express certainty?
    3. Negation alignment — does the source agree on polarity?

    Args:
        fact_text: The extracted fact text.
        source_text: The original source text (message or parent fact).
        fact_id: Optional fact ID for tracking.
        source_id: Optional source ID for tracking.

    Returns:
        VerificationResult with overlap score and hedging detection.
    """
    result = VerificationResult(fact_id=fact_id, source_id=source_id)

    if not fact_text or not source_text:
        result.status = "no_source"
        return result

    fact_norm = _normalize(fact_text)
    source_norm = _normalize(source_text)
    fact_words = set(re.findall(r"\w+", fact_norm))
    source_words = set(re.findall(r"\w+", source_norm))

    # ── Check 1: Key phrase overlap ─────────────────────────────────────
    # Extract significant phrases from the fact (content words, not stop words)
    stop_words = _get_stop_words()
    key_phrases = {w for w in fact_words if len(w) >= 4 and w not in stop_words}

    if not key_phrases:
        # Fact has no significant content words → can't verify
        result.status = "verified"  # trivially true
        result.semantic_overlap = 1.0
        return result

    found = []
    missing = []
    for phrase in key_phrases:
        if phrase in source_words:
            found.append(phrase)
        elif phrase in source_norm:
            found.append(phrase)  # substring match
        else:
            missing.append(phrase)

    result.key_phrases_in_source = found
    result.key_phrases_missing = missing

    if key_phrases:
        result.semantic_overlap = len(found) / len(key_phrases)
    else:
        result.semantic_overlap = 1.0

    # ── Check 2: Hedging detection ──────────────────────────────────────
    source_lower = source_text.lower()
    hedging_found = [p for p in _HEDGING_PHRASES if p in source_lower]
    result.hedging_phrases = hedging_found
    result.hedging_detected = len(hedging_found) > 0

    # ── Check 3: Negation alignment ─────────────────────────────────────
    negation_words = {"не", "нет", "никогда", "not", "never", "no"}
    fact_has_neg = bool(fact_words & negation_words)
    source_has_neg = bool(source_words & negation_words)
    negation_mismatch = fact_has_neg != source_has_neg

    # ── Determine status ────────────────────────────────────────────────
    if result.semantic_overlap >= 0.6 and not negation_mismatch:
        if result.hedging_detected:
            # Source uses hedging language but fact is stated as truth
            result.status = "weak_match"
        else:
            result.status = "verified"
    elif result.semantic_overlap >= 0.3:
        result.status = "weak_match"
    else:
        result.status = "mismatch"

    if negation_mismatch:
        result.status = "mismatch"

    return result


def verify_provenance_chain(
    store: "MemoryStore",
    fact_id: str,
    *,
    max_depth: int = 3,
) -> ChainVerificationResult:
    """Walk the full provenance chain and verify each link.

    Starting from a fact, follows its evidence list back through
    intermediate facts to original messages. Each link is verified
    for semantic consistency.

    Args:
        store: MemoryStore for looking up entities.
        fact_id: Starting fact ID.
        max_depth: Maximum chain depth to follow.

    Returns:
        ChainVerificationResult with per-link verification and overall status.
    """
    fact = store.get_fact(fact_id)
    if fact is None:
        return ChainVerificationResult(
            fact_id=fact_id,
            overall_status="broken",
            issues=[f"Fact {fact_id} not found"],
        )

    result = ChainVerificationResult(fact_id=fact_id, fact_text=fact.fact)
    visited: set[str] = set()

    def _walk(current_fact_id: str, current_text: str, depth: int) -> None:
        if depth > max_depth or current_fact_id in visited:
            return
        visited.add(current_fact_id)

        current_fact = store.get_fact(current_fact_id)
        if current_fact is None:
            return

        # Check evidence list
        evidence_ids = current_fact.evidence or []
        if not evidence_ids:
            # No evidence → can't verify further
            result.issues.append(
                f"Fact {current_fact_id}: no evidence chain"
            )
            return

        for ev_id in evidence_ids:
            # Try as fact first
            ev_fact = store.get_fact(ev_id)
            if ev_fact:
                vr = verify_fact_against_source(
                    current_text, ev_fact.fact,
                    fact_id=current_fact_id, source_id=ev_id,
                )
                result.chain_links.append(vr)
                # Recurse deeper
                _walk(ev_id, ev_fact.fact, depth + 1)
                continue

            # Try as message — messages are stored differently
            # Look up in recent messages
            try:
                messages = store.recent_messages(min_importance=0, limit=500)
                for msg in messages:
                    if msg.id == ev_id:
                        vr = verify_fact_against_source(
                            current_text, msg.text,
                            fact_id=current_fact_id, source_id=ev_id,
                        )
                        result.chain_links.append(vr)
                        break
                else:
                    # Message not found in recent history
                    result.chain_links.append(VerificationResult(
                        fact_id=current_fact_id,
                        source_id=ev_id,
                        status="no_source",
                    ))
                    result.issues.append(
                        f"Source {ev_id} not found in message history"
                    )
            except Exception:
                pass

    _walk(fact_id, fact.fact, 0)

    # Determine overall status
    if not result.chain_links:
        result.overall_status = "broken"
        result.issues.append("Empty provenance chain")
    else:
        statuses = [link.status for link in result.chain_links]
        if all(s == "verified" for s in statuses):
            result.overall_status = "verified"
        elif all(s in ("verified", "weak_match") for s in statuses):
            result.overall_status = "weak"
        elif any(s == "mismatch" for s in statuses):
            result.overall_status = "suspicious"
        else:
            result.overall_status = "broken"

    return result


def detect_hallucinated_facts(
    store: "MemoryStore",
    *,
    max_facts: int = 100,
    min_risk: str = "medium",
) -> list[HallucinationReport]:
    """Scan active facts for potential hallucinations.

    Checks each fact against its evidence sources. Reports facts where
    the semantic match is weak, suggesting the LLM may have distorted
    the meaning during extraction.

    This is expensive — it walks the provenance chain for each fact.
    Use sparingly (e.g., nightly health check).

    Args:
        store: MemoryStore instance.
        max_facts: Maximum facts to scan (performance limit).
        min_risk: Minimum risk level to report ("low", "medium", "high").

    Returns:
        List of HallucinationReport for suspicious facts.
    """
    reports: list[HallucinationReport] = []
    risk_order = {"low": 0, "medium": 1, "high": 2}
    min_risk_level = risk_order.get(min_risk, 1)

    facts = store.list_facts("active")[:max_facts]

    for fact in facts:
        if not fact.evidence:
            continue

        # Check against each evidence source
        for ev_id in fact.evidence:
            ev_fact = store.get_fact(ev_id)
            if ev_fact:
                vr = verify_fact_against_source(
                    fact.fact, ev_fact.fact,
                    fact_id=fact.id, source_id=ev_id,
                )
                if vr.status in ("mismatch", "weak_match"):
                    risk = "high" if vr.status == "mismatch" else "medium"
                    if vr.hedging_detected:
                        risk = "high"  # hedging + mismatch = very suspicious

                    if risk_order.get(risk, 0) >= min_risk_level:
                        reports.append(HallucinationReport(
                            fact_id=fact.id,
                            fact_text=fact.fact[:200],
                            source_id=ev_id,
                            source_text=ev_fact.fact[:200],
                            overlap=vr.semantic_overlap,
                            risk_level=risk,
                            reason=(
                                f"Low overlap ({vr.semantic_overlap:.2f}) between fact "
                                f"and source. "
                                + (f"Hedging in source: {vr.hedging_phrases}"
                                   if vr.hedging_detected else "")
                                + (f"Missing phrases: {vr.key_phrases_missing}"
                                   if vr.key_phrases_missing else "")
                            ),
                        ))
            # Could also check against messages, but that's more expensive

    reports.sort(key=lambda r: risk_order.get(r.risk_level, 0), reverse=True)
    return reports


def _normalize(text: str) -> str:
    return re.sub(r"\s+", " ", text.lower().strip())


def _get_stop_words() -> set[str]:
    """Common stop words for Russian and English."""
    return {
        # Russian
        "это", "также", "другой", "который", "потому", "поэтому",
        "очень", "более", "менее", "самый", "каждый", "весь",
        "быть", "было", "была", "были", "будет", "является",
        "того", "этого", "того", "всего", "может",
        # English
        "this", "that", "also", "other", "which", "because",
        "therefore", "very", "more", "less", "most", "every",
        "each", "being", "been", "were", "will", "would",
        "should", "could", "have", "has", "had", "does", "did",
    }
