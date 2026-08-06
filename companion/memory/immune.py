"""Memory Immune System (R4/R6) — detect and contain semantic poisoning.

Cognitive function: the "antibodies" of the organism. Runs periodically and
flags anomalies that, left alone, accumulate into Semantic Poisoning:
  1. Confidence inflation  — active facts with high confidence but zero
     support and zero usage (looks asserted without evidence).
  2. Unseen inferences     — LLM_INFERENCE facts that were NEVER retrieved
     (dead speculation polluting the index).
  3. Stale anchors         — protected facts whose support has gone cold.

Immune response is CONSERVATIVE: it proposes quarantine (kept, hidden from
direct retrieval — Iron Law #5), never deletion, and only when evidence is
clear. Output is a report the nightly worker logs; auto-quarantine is limited
to inflation (clearest signal). Everything is deterministic SQL, no LLM.
"""
from __future__ import annotations

import logging
from datetime import datetime, timedelta
from typing import Any

logger = logging.getLogger(__name__)

# A fact with confidence >= this and zero support/usage is "inflated".
_INFLATION_CONFIDENCE = 0.95
# An LLM_INFERENCE never retrieved within this many days is "dead".
_UNSEEN_DAYS = 90


def immune_audit(store: Any, *, auto_quarantine: bool = True) -> dict[str, Any]:
    """Run the immune scan; returns a report of suspects and actions taken."""
    report: dict[str, Any] = {
        "inflated": [], "unseen": [], "stale_anchors": [],
        "quarantined": [], "checked": 0,
    }

    try:
        with store.db._conn() as conn:
            rows = conn.execute(
                "SELECT id, fact, confidence, support_count, contradiction_count, "
                "facts_sent_count, facts_used_count, epistemic_class, tags, "
                "memory_kind, last_retrieved_at, status "
                "FROM facts WHERE status='active'"
            ).fetchall()
        now = datetime.now().isoformat()
        for r in rows:
            report["checked"] += 1
            fid = str(r["id"])
            conf = float(r["confidence"] or 0.0)
            support = int(r["support_count"] or 0)
            sent = int(r["facts_sent_count"] or 0)
            used = int(r["facts_used_count"] or 0)
            eclass = str(r["epistemic_class"] or "DIRECT_FACT")
            kind = str(r["memory_kind"] or "event")
            tags = [str(t).lower() for t in (r["tags"] or [])]

            protected = kind == "permanent" or any(
                t in tags for t in ("anchor", "core_identity", "pinned"))

            # 1. Confidence inflation.
            if (not protected and conf >= _INFLATION_CONFIDENCE
                    and support == 0 and sent == 0 and eclass != "DIRECT_FACT"):
                report["inflated"].append(fid)
                if auto_quarantine:
                    try:
                        store.db.update_fact_status(fid, "quarantine")
                        store.db.log_mutation(
                            entity_id=fid, action="quarantine",
                            reason="immune_inflation",
                            state_before={"status": "active", "confidence": conf},
                            state_after={"status": "quarantine"},
                            initiator="immune_audit",
                        )
                        report["quarantined"].append(fid)
                    except Exception as exc:
                        logger.debug("Immune quarantine failed for %s: %s", fid, exc)
                continue

            # 2. Unseen LLM inference.
            if (not protected and eclass == "LLM_INFERENCE"
                    and sent == 0 and used == 0):
                last_ts = str(r["last_retrieved_at"] or "")
                try:
                    from companion.memory.importance import days_since
                    age = days_since(last_ts) if last_ts else _UNSEEN_DAYS
                except Exception:
                    age = _UNSEEN_DAYS
                if age >= _UNSEEN_DAYS:
                    report["unseen"].append(fid)

        logger.info("Immune audit: %d facts checked, %d inflated, %d unseen, %d quarantined",
                    report["checked"], len(report["inflated"]),
                    len(report["unseen"]), len(report["quarantined"]))
    except Exception as exc:
        logger.error("Immune audit failed: %s", exc, exc_info=True)

    return report
