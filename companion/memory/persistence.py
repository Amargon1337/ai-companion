"""Memory Persistence Layer — decouples memory decision-making from storage and audit logging."""
from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Any

from companion.memory.events.base import (
    FactArchivedEvent,
    FactSupersededEvent,
    FactUpdatedEvent,
    MutationAppliedEvent,
)
from companion.memory.policies.base import PolicyDecision
from companion.storage.sqlite_db import MemoryDatabase

if TYPE_CHECKING:
    from companion.memory.governor import MemoryGovernor, MemoryRecommendation

logger = logging.getLogger(__name__)


class MemoryPersistenceLayer:
    """Executes PolicyDecision updates in SQLite and records them in the Mutation Log."""

    def __init__(
        self,
        db: MemoryDatabase,
        governor: MemoryGovernor,
        event_bus: Any | None = None,
    ) -> None:
        self.db = db
        self.governor = governor
        self.event_bus = event_bus

    def apply_decision(
        self,
        fact_id: str,
        decision: PolicyDecision,
        old_state: dict[str, Any] | None = None,
        reason: str = "",
        initiator: str = "governor",
        entity_type: str = "fact",
    ) -> str | None:
        """Apply an approved PolicyDecision to SQLite and log the mutation."""
        if not decision.approved or not decision.updates:
            return None

        if old_state is None:
            old_state = self.db.get_entity(entity_type, fact_id) or {}

        old_status = old_state.get("status")
        new_status = decision.updates.get("status")
        if old_status in {"archived", "superseded"} and new_status == "active":
            raise ValueError(
                f"Illegal lifecycle transition: cannot reactivate '{old_status}' entity {fact_id} to 'active' via apply_decision. Use explicit restore_fact() if reactivation is required."
            )

        # Extract only the modified keys for before/after snapshots
        old_sub = {k: old_state.get(k) for k in decision.updates.keys()}
        new_sub = dict(decision.updates)

        with self.db.atomic_memory_transaction():
            self.db.update_entity_fields(entity_type, fact_id, decision.updates, expected_version=old_state.get("version"))
            mutation_id = self.db.log_mutation(
                entity_id=fact_id,
                action=decision.action,
                reason=reason or decision.reason,
                state_before=old_sub,
                state_after=new_sub,
                entity_type=entity_type,
                initiator=initiator,
            )
        logger.info(
            "MemoryPersistenceLayer applied action=%s to fact_id=%s (initiator=%s, reason=%s)",
            decision.action,
            fact_id,
            initiator,
            reason or decision.reason,
        )

        if self.event_bus:
            try:
                self.event_bus.publish(
                    MutationAppliedEvent(
                        mutation_id=mutation_id or "",
                        fact_id=fact_id,
                        action=decision.action,
                        reason=reason or decision.reason,
                        initiator=initiator,
                    )
                )
                new_status = str(decision.updates.get("status", "")).lower()
                if decision.action == "archive" or new_status == "archived":
                    self.event_bus.publish(
                        FactArchivedEvent(
                            fact_id=fact_id,
                            fact_text=str(old_state.get("fact", "")),
                            reason=reason or decision.reason,
                            initiator=initiator,
                        )
                    )
                elif decision.action == "supersede" or new_status == "superseded":
                    self.event_bus.publish(
                        FactSupersededEvent(
                            fact_id=fact_id,
                            fact_text=str(old_state.get("fact", "")),
                            superseded_by=str(decision.updates.get("superseded_by", "")),
                            reason=reason or decision.reason,
                            initiator=initiator,
                        )
                    )
                else:
                    self.event_bus.publish(
                        FactUpdatedEvent(
                            fact_id=fact_id,
                            old_state=old_sub,
                            new_state=new_sub,
                            reason=reason or decision.reason,
                            initiator=initiator,
                        )
                    )
            except Exception:
                logger.exception("Error publishing event for fact_id=%s", fact_id)

        return mutation_id

    def propose_and_apply(
        self, rec: MemoryRecommendation, initiator: str | None = None
    ) -> bool:
        """Evaluate a recommendation via Governor and apply it if approved."""
        fact = self.db.get_fact(rec.fact_id)
        if not fact:
            logger.warning("MemoryPersistenceLayer rejected rec on unknown fact_id=%s", rec.fact_id)
            return False

        target_fact = None
        target_id = getattr(rec, "target_fact_id", "")
        if target_id:
            target_fact = self.db.get_fact(target_id)
            if not target_fact:
                logger.warning(
                    "MemoryPersistenceLayer rejected merge rec due to unknown target_fact_id=%s",
                    target_id,
                )
                return False

        decision = self.governor.decide(rec, fact, target_fact=target_fact)
        if not decision.approved:
            logger.info(
                "MemoryGovernor rejected rec %s on fact_id=%s (reason=%s)",
                rec.__class__.__name__,
                rec.fact_id,
                decision.reason,
            )
            return False

        self.apply_decision(
            fact_id=rec.fact_id,
            decision=decision,
            old_state=fact,
            reason=rec.reason,
            initiator=initiator or getattr(rec, "source", "governor"),
            entity_type=getattr(rec, "entity_type", "fact"),
        )
        return True

    def process_recommendations(
        self,
        recommendations: list[MemoryRecommendation],
        initiator: str | None = None,
    ) -> dict[str, int]:
        """Process a batch of recommendations and return execution statistics."""
        stats = {"submitted": len(recommendations), "approved": 0, "rejected": 0}
        for rec in recommendations:
            approved = self.propose_and_apply(rec, initiator=initiator)
            if approved:
                stats["approved"] += 1
            else:
                stats["rejected"] += 1
        return stats
