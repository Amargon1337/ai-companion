"""Immunity policy — enforces structural protection of core facts against archival and decay."""
from __future__ import annotations

from typing import Any

from companion.memory.immunity import is_immune
from companion.memory.policies.base import Policy, PolicyDecision
from companion.models import Fact


class ImmunityPolicy(Policy):
    """Checks whether a fact is protected by structural immunity rules."""

    def evaluate(
        self,
        rec: Any,
        fact: dict[str, Any] | Fact,
        target_fact: dict[str, Any] | Fact | None = None,
    ) -> PolicyDecision:
        fact_id = fact.id if isinstance(fact, Fact) else str(fact.get("id", ""))
        if is_immune(fact):
            return PolicyDecision(
                approved=False,
                action="REJECT_IMMUNE",
                updates={},
                reason=f"fact_id={fact_id}_protected_by_structural_immunity",
                policy_name="ImmunityPolicy",
            )
        return PolicyDecision(
            approved=True,
            action="PASS_IMMUNITY",
            updates={},
            reason=f"fact_id={fact_id}_not_immune",
            policy_name="ImmunityPolicy",
        )
