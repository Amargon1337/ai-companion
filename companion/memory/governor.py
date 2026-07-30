"""Memory Governor module (Stage 4) — pure decision engine for memory mutations.

Decoupled from SQLite storage; delegates rule evaluation to policies in companion.memory.policies.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
import logging
from typing import TYPE_CHECKING, Any

from companion.memory.policies.archive_policy import ArchivePolicy
from companion.memory.policies.base import PolicyDecision
from companion.memory.policies.boost_policy import BoostPolicy, DecayPolicy
from companion.memory.policies.immunity_policy import ImmunityPolicy
from companion.memory.policies.merge_policy import MergePolicy
from companion.memory.policies.validation_policy import FactValidationPolicy

if TYPE_CHECKING:
    from companion.models import Fact
    from companion.storage.sqlite_db import MemoryDatabase

logger = logging.getLogger(__name__)


@dataclass
class MemoryRecommendation:
    fact_id: str
    reason: str
    source: str
    timestamp: str = field(default_factory=lambda: datetime.now().isoformat())
    entity_type: str = "fact"


@dataclass
class ArchiveRecommendation(MemoryRecommendation):
    pass


@dataclass
class MergeRecommendation(MemoryRecommendation):
    target_fact_id: str = ""


@dataclass
class DecayRecommendation(MemoryRecommendation):
    amount: int = 1


@dataclass
class BoostRecommendation(MemoryRecommendation):
    amount: int = 1


class MemoryGovernor:
    """Pure decision engine for memory mutations.

    Evaluates memory recommendations using modular policies without directly executing SQL.
    """

    def __init__(self, db: MemoryDatabase | None = None) -> None:
        self.db = db
        self.archive_policy = ArchivePolicy()
        self.boost_policy = BoostPolicy()
        self.decay_policy = DecayPolicy()
        self.merge_policy = MergePolicy()
        self.immunity_policy = ImmunityPolicy()
        self.validation_policy = FactValidationPolicy()

    def validate_ingestion(self, fact: dict[str, Any] | Fact, **kwargs: Any) -> PolicyDecision:
        """Evaluate whether a new fact should be active, quarantined, or rejected."""
        return self.validation_policy.evaluate(kwargs, fact)

    def decide(
        self,
        rec: MemoryRecommendation,
        fact: dict[str, Any] | Fact,
        target_fact: dict[str, Any] | Fact | None = None,
    ) -> PolicyDecision:
        """Evaluate a recommendation against policies and return a PolicyDecision."""
        if isinstance(rec, ArchiveRecommendation):
            return self.archive_policy.evaluate(rec, fact, target_fact)
        if isinstance(rec, BoostRecommendation):
            return self.boost_policy.evaluate(rec, fact, target_fact)
        if isinstance(rec, DecayRecommendation):
            return self.decay_policy.evaluate(rec, fact, target_fact)
        if isinstance(rec, MergeRecommendation):
            return self.merge_policy.evaluate(rec, fact, target_fact)

        return PolicyDecision(
            approved=False,
            action="REJECT_UNKNOWN_REC",
            updates={},
            reason="Unknown recommendation type",
            policy_name="MemoryGovernor",
        )

    def propose(self, rec: MemoryRecommendation) -> bool:
        """Backward-compatible helper that delegates to MemoryPersistenceLayer when db is attached."""
        if not self.db:
            raise RuntimeError(
                "MemoryGovernor.propose() called without db; use MemoryPersistenceLayer or .decide() instead."
            )
        from companion.memory.persistence import MemoryPersistenceLayer

        return MemoryPersistenceLayer(self.db, self).propose_and_apply(rec)

    def process_recommendations(self, recommendations: list[MemoryRecommendation]) -> dict[str, int]:
        """Backward-compatible batch helper that delegates to MemoryPersistenceLayer when db is attached."""
        if not self.db:
            raise RuntimeError(
                "MemoryGovernor.process_recommendations() called without db; use MemoryPersistenceLayer instead."
            )
        from companion.memory.persistence import MemoryPersistenceLayer

        return MemoryPersistenceLayer(self.db, self).process_recommendations(recommendations)
