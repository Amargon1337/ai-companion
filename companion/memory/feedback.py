"""Memory Feedback Loop module (Stage 3) — automated weight re-evaluation based on usage analytics."""
from __future__ import annotations

import logging
from typing import Any

from companion.memory.governor import (
    BoostRecommendation,
    DecayRecommendation,
    MemoryGovernor,
    MemoryRecommendation,
)
from companion.memory.immunity import is_immune
from companion.storage.sqlite_db import MemoryDatabase

logger = logging.getLogger(__name__)


class MemoryFeedbackLoop:
    """Analyzes retrieval effectiveness and generates weight adjustments for Governor."""

    def __init__(
        self,
        db: MemoryDatabase,
        governor: MemoryGovernor,
        *,
        min_retrieved: int = 10,
        low_precision_threshold: float = 0.10,
        high_usage_threshold: int = 5,
    ) -> None:
        self.db = db
        self.governor = governor
        self.min_retrieved = min_retrieved
        self.low_precision_threshold = low_precision_threshold
        self.high_usage_threshold = high_usage_threshold

    def analyze(self, facts: list[dict[str, Any]] | None = None) -> list[MemoryRecommendation]:
        """Inspect facts and generate Boost or Decay recommendations."""
        if facts is None:
            facts = self.db.list_facts(status="active")

        recs: list[MemoryRecommendation] = []
        for fact in facts:
            # Skip immune facts from feedback adjustments
            if is_immune(fact):
                continue

            fact_id = str(fact.get("id", ""))
            if not fact_id:
                continue

            retrieved = int(fact.get("facts_sent_count", fact.get("retrieved_count", 0)))
            used = int(fact.get("facts_used_count", fact.get("used_count", 0)))

            if retrieved >= self.min_retrieved:
                precision = float(used) / float(retrieved) if retrieved > 0 else 0.0
                if precision < self.low_precision_threshold:
                    recs.append(
                        DecayRecommendation(
                            fact_id=fact_id,
                            amount=1,
                            reason=f"low_precision:{precision:.2f}_retrieved:{retrieved}",
                            source="feedback_loop",
                        )
                    )
            if used >= self.high_usage_threshold:
                recs.append(
                    BoostRecommendation(
                        fact_id=fact_id,
                        amount=1,
                        reason=f"high_usage:{used}",
                        source="feedback_loop",
                    )
                )
        return recs

    def run_cycle(self) -> dict[str, int]:
        """Run one feedback loop cycle: analyze active facts and submit recommendations to Governor."""
        recs = self.analyze()
        logger.info("MemoryFeedbackLoop generated %d recommendations", len(recs))
        return self.governor.process_recommendations(recs)
