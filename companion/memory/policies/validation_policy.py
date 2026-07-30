"""FactValidationPolicy for checking confidence thresholds and quarantine conditions (Phase 1.6)."""
from __future__ import annotations

import logging
from typing import Any

from companion.memory.policies.base import Policy, PolicyDecision
from companion.models import Fact

logger = logging.getLogger(__name__)


class FactValidationPolicy(Policy):
    """Evaluates whether an ingested fact passes quality checks or should enter quarantine."""

    def __init__(self, min_confidence: float = 0.3) -> None:
        self.min_confidence = min_confidence

    def evaluate(
        self,
        rec: Any,
        fact: dict[str, Any] | Fact,
        target_fact: dict[str, Any] | Fact | None = None,
    ) -> PolicyDecision:
        conf = getattr(fact, "confidence", 1.0)
        if isinstance(fact, dict):
            conf = float(fact.get("confidence", 1.0))
        else:
            conf = float(getattr(fact, "confidence", 1.0))

        tags = getattr(fact, "tags", [])
        if isinstance(fact, dict):
            tags = fact.get("tags", [])
        tags_lower = [str(t).lower() for t in (tags or [])]

        is_hypo = False
        has_contra = False
        if isinstance(rec, dict):
            is_hypo = bool(rec.get("is_hypothetical", False))
            has_contra = bool(rec.get("has_contradiction", False))
        elif hasattr(rec, "is_hypothetical"):
            is_hypo = bool(getattr(rec, "is_hypothetical", False))
            has_contra = bool(getattr(rec, "has_contradiction", False))

        is_hypo = is_hypo or ("hypothetical" in tags_lower)
        has_contra = has_contra or ("contradiction" in tags_lower) or ("unverified" in tags_lower)

        if conf < self.min_confidence or is_hypo or has_contra:
            reasons = []
            if conf < self.min_confidence:
                reasons.append(f"confidence {conf} < threshold {self.min_confidence}")
            if is_hypo:
                reasons.append("hypothetical claim")
            if has_contra:
                reasons.append("contradiction or unverified claim")

            reason_str = "Quarantined: " + ", ".join(reasons)
            return PolicyDecision(
                approved=True,
                action="quarantine",
                updates={"status": "quarantine"},
                reason=reason_str,
                policy_name="FactValidationPolicy",
            )

        return PolicyDecision(
            approved=True,
            action="activate",
            updates={"status": "active"},
            reason="Validation passed",
            policy_name="FactValidationPolicy",
        )
