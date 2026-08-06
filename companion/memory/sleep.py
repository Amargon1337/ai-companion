"""Memory Sleep Cycle (R4/R6) — forced consolidation when homeostasis breaches.

Cognitive function: when entropy trend exceeds tau, the organism must
CONSOLIDATE rather than accumulate: compress dormant episodes into summaries,
apply importance decay, and re-derive ToM/narrative read-models. This is the
"sleep" that keeps long-run memory stable (semantic-poisoning countermeasure).

All actions reuse existing, tested machinery (episodic compression, decay,
genome survival update). Nothing is deleted — Iron Law #5. The cycle is
idempotent and bounded (batch sizes), so it is safe to run nightly.
"""
from __future__ import annotations

import logging
from typing import Any

logger = logging.getLogger(__name__)


def run_sleep_cycle(store: Any, *, batch_size: int = 10,
                    min_facts: int = 3) -> dict[str, Any]:
    """One consolidation pass: compress -> decay -> refresh read-models.

    Returns a stats dict; any step failing is logged and does not abort the
    others (sleep must never crash the nightly worker).
    """
    stats: dict[str, Any] = {
        "compressed": 0, "decayed": 0, "genome_updated": 0,
        "tom_refreshed": 0, "narrative_arcs": 0,
    }

    # 1. Compression: dormant facts -> episodic summary facts.
    try:
        summaries = store.compress_dormant_episodes(batch_size=batch_size,
                                                    min_facts=min_facts)
        stats["compressed"] = len(summaries)
        logger.info("Sleep: compressed %d episodic summary fact(s)", len(summaries))
    except Exception as exc:
        logger.error("Sleep compression failed: %s", exc, exc_info=True)

    # 2. Soft forgetting: importance decay (already nightly-guarded).
    try:
        moved = store.apply_importance_decay()
        stats["decayed"] = moved
        logger.info("Sleep: %d fact(s) moved to dormant by decay", moved)
    except Exception as exc:
        logger.error("Sleep decay failed: %s", exc, exc_info=True)

    # 3. Genome survival re-evaluation: decay survival of stale, boost used.
    try:
        updated = _update_genome_survival(store)
        stats["genome_updated"] = updated
    except Exception as exc:
        logger.error("Sleep genome update failed: %s", exc, exc_info=True)

    # 4. Refresh derived read-models (ToM + narrative) so prompts reflect
    #    the consolidated state.
    try:
        ent_id = "ent_user"
        store.db.upsert_world_entity({"entity_id": ent_id, "name": "Иван",
                                      "type": "person", "importance": 1.0})
        tom_stats = store.tom.refresh(ent_id)
        stats["tom_refreshed"] = tom_stats.get("inserted", 0)
    except Exception as exc:
        logger.debug("Sleep ToM refresh skipped: %s", exc)
    try:
        arcs = store.narrative.build_arcs()
        stats["narrative_arcs"] = len(arcs)
    except Exception as exc:
        logger.debug("Sleep narrative refresh skipped: %s", exc)

    return stats


def _update_genome_survival(store: Any) -> int:
    """Recompute genome survival_score: used facts thrive, stale facts fade.

    survival_score is bounded [0,1]; a fact retrieved recently grows toward
    1.0, one untouched for >90 days drifts toward 0.2 (never 0 — kept).
    """
    updated = 0
    try:
        with store.db._conn() as conn:
            rows = conn.execute(
                "SELECT id, facts_sent_count, facts_used_count, last_retrieved_at, "
                "created_at FROM facts WHERE status IN ('active','dormant')"
            ).fetchall()
        from datetime import datetime
        from companion.memory.importance import days_since
        now = datetime.now().isoformat()
        for r in rows:
            fid = str(r[0])
            sent = int(r[1] or 0)
            used = int(r[2] or 0)
            last_ts = str(r[3] or r[4] or now)
            try:
                age_days = days_since(last_ts)
            except Exception:
                age_days = 0.0
            usage = min(1.0, (used / max(1, sent)) * 0.5 + (0.2 if sent else 0.0))
            recency_boost = max(0.0, 1.0 - age_days / 90.0) * 0.3
            new_survival = round(min(1.0, max(0.2, usage + recency_boost)), 4)
            store.db.upsert_memory_genome({
                "memory_id": fid,
                "origin": "survival_eval",
                "survival_score": new_survival,
                "last_evaluated_at": now,
            })
            updated += 1
    except Exception as exc:
        logger.error("Genome survival update failed: %s", exc)
    return updated
