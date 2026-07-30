"""Archive policy — rules for archiving non-immune facts."""
from __future__ import annotations

from typing import Any

from companion.memory.policies.base import Policy, PolicyDecision
from companion.memory.policies.immunity_policy import ImmunityPolicy
from companion.models import Fact


class ArchivePolicy(Policy):
    """Evaluates ArchiveRecommendation against immunity and current state."""

    def __init__(self) -> None:
        self.immunity_policy = ImmunityPolicy()

    def evaluate(
        self,
        rec: Any,
        fact: dict[str, Any] | Fact,
        target_fact: dict[str, Any] | Fact | None = None,
    ) -> PolicyDecision:
        # 1. Structural immunity check
        imm_dec = self.immunity_policy.evaluate(rec, fact, target_fact)
        if not imm_dec.approved:
            return imm_dec

        # 2. Check if already archived
        if isinstance(fact, Fact):
            status = fact.status
            archived = getattr(fact, "archived", 0)
        else:
            status = str(fact.get("status", "active"))
            archived = int(fact.get("archived", 0))

        if status == "archived" or archived == 1:
            return PolicyDecision(
                approved=False,
                action="REJECT_ALREADY_ARCHIVED",
                updates={},
                reason="Fact is already archived",
                policy_name="ArchivePolicy",
            )

        return PolicyDecision(
            approved=True,
            action="ARCHIVE",
            updates={"status": "archived", "archived": 1},
            reason=getattr(rec, "reason", "archive_policy_approved"),
            policy_name="ArchivePolicy",
        )
