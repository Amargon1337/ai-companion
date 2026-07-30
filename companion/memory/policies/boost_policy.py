"""Boost and Decay policies — adjusts dynamic retrieval_bias without altering permanent importance."""
from __future__ import annotations

import json
from typing import Any

from companion.memory.policies.base import Policy, PolicyDecision
from companion.memory.policies.immunity_policy import ImmunityPolicy
from companion.models import Fact


def _get_meta_dict(fact: dict[str, Any] | Fact) -> dict[str, Any]:
    if isinstance(fact, Fact):
        return dict(fact.meta)
    meta_val = fact.get("meta", {})
    if isinstance(meta_val, str):
        try:
            return json.loads(meta_val)
        except Exception:
            return {}
    return dict(meta_val) if isinstance(meta_val, dict) else {}


class BoostPolicy(Policy):
    """Evaluates BoostRecommendation to increase dynamic retrieval_bias."""

    def evaluate(
        self,
        rec: Any,
        fact: dict[str, Any] | Fact,
        target_fact: dict[str, Any] | Fact | None = None,
    ) -> PolicyDecision:
        meta = _get_meta_dict(fact)
        old_bias = float(meta.get("retrieval_bias", 0.0))
        amount = getattr(rec, "amount", 1)
        new_bias = round(min(2.0, old_bias + 0.1 * amount), 3)

        meta["retrieval_bias"] = new_bias
        return PolicyDecision(
            approved=True,
            action="BOOST_BIAS",
            updates={"meta": meta},
            reason=getattr(rec, "reason", "boost_retrieval_bias"),
            policy_name="BoostPolicy",
        )


class DecayPolicy(Policy):
    """Evaluates DecayRecommendation to decrease dynamic retrieval_bias on non-immune facts."""

    def __init__(self) -> None:
        self.immunity_policy = ImmunityPolicy()

    def evaluate(
        self,
        rec: Any,
        fact: dict[str, Any] | Fact,
        target_fact: dict[str, Any] | Fact | None = None,
    ) -> PolicyDecision:
        # Structural immunity check: immune facts cannot be decayed
        imm_dec = self.immunity_policy.evaluate(rec, fact, target_fact)
        if not imm_dec.approved:
            return imm_dec

        meta = _get_meta_dict(fact)
        old_bias = float(meta.get("retrieval_bias", 0.0))
        amount = getattr(rec, "amount", 1)
        new_bias = round(max(-2.0, old_bias - 0.1 * amount), 3)

        meta["retrieval_bias"] = new_bias
        return PolicyDecision(
            approved=True,
            action="DECAY_BIAS",
            updates={"meta": meta},
            reason=getattr(rec, "reason", "decay_retrieval_bias"),
            policy_name="DecayPolicy",
        )
