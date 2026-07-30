"""Merge policy — rules for merging/superseding duplicate facts."""
from __future__ import annotations

from typing import Any

from companion.memory.policies.base import Policy, PolicyDecision
from companion.memory.policies.immunity_policy import ImmunityPolicy
from companion.models import Fact


class MergePolicy(Policy):
    """Evaluates MergeRecommendation to supersede an older fact by a newer target fact."""

    def __init__(self) -> None:
        self.immunity_policy = ImmunityPolicy()

    def evaluate(
        self,
        rec: Any,
        fact: dict[str, Any] | Fact,
        target_fact: dict[str, Any] | Fact | None = None,
    ) -> PolicyDecision:
        # 1. Check if source fact is immune
        imm_dec = self.immunity_policy.evaluate(rec, fact, target_fact)
        if not imm_dec.approved:
            return imm_dec

        target_id = getattr(rec, "target_fact_id", "")
        if not target_id or not target_fact:
            return PolicyDecision(
                approved=False,
                action="REJECT_NO_TARGET",
                updates={},
                reason="Merge recommendation missing valid target_fact",
                policy_name="MergePolicy",
            )

        if isinstance(fact, Fact):
            status = fact.status
        else:
            status = str(fact.get("status", "active"))

        if status == "superseded":
            return PolicyDecision(
                approved=False,
                action="REJECT_ALREADY_SUPERSEDED",
                updates={},
                reason="Fact is already superseded",
                policy_name="MergePolicy",
            )

        return PolicyDecision(
            approved=True,
            action="MERGE",
            updates={"status": "superseded", "superseded_by": target_id},
            reason=getattr(rec, "reason", "merge_duplicate"),
            policy_name="MergePolicy",
        )
