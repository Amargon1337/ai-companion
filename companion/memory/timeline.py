"""Cognitive timeline (R7) — materialized read-model over the event journal.

Cognitive function: the journal is the durable record of memory events
(commit -> append -> drain -> applied). The timeline is the *temporal view*
over that record: each applied journal event becomes a tick on the
consciousness timeline, tagged with the cognitive phase it represents.

This is a pure READ-MODEL:
  * source of truth = event_journal (append-only, never deleted);
  * timeline rows are derived and can be rebuilt from the journal at any
    time (re-materialization is idempotent via journal-id watermark);
  * rows older than the retention window are flagged `archived` (Iron Law
    #5: never deleted), keeping the table bounded.

Phase mapping (event type -> cognitive phase):
  * FactRetrievedEvent   -> interpretation  (context was read)
  * MutationAppliedEvent -> decision        (governor decided)
  * FactCreated/Updated/Archived/Superseded -> memory_update (memory changed)
  * everything else      -> perception      (raw input observed)
"""
from __future__ import annotations

import hashlib
import json
import logging
from datetime import datetime, timedelta
from typing import Any

logger = logging.getLogger(__name__)

_WATERMARK_KEY = "timeline_journal_watermark"
_RETENTION_DAYS = 90

# event type -> phase; unknown types fall back to "perception"
_PHASE_BY_EVENT = {
    "FactRetrievedEvent": "interpretation",
    "MutationAppliedEvent": "decision",
    "FactCreatedEvent": "memory_update",
    "FactUpdatedEvent": "memory_update",
    "FactArchivedEvent": "memory_update",
    "FactSupersededEvent": "memory_update",
}


class CognitiveTimeline:
    """Materializes the cognitive_timeline table from event_journal rows."""

    def __init__(self, db: Any) -> None:
        self.db = db

    # ── materialization ────────────────────────────────────────────────────

    def materialize(self, limit: int = 2000) -> int:
        """Append timeline ticks for journal events above the watermark.

        Idempotent: the watermark is the last journal id materialized.
        Returns the number of ticks written this call.
        """
        try:
            watermark = int(self.db.get_meta(_WATERMARK_KEY, "0") or "0")
        except (TypeError, ValueError):
            watermark = 0

        try:
            rows = self.db.list_journal_after(watermark, limit=limit)
        except Exception as exc:
            logger.error("Timeline: journal read failed: %s", exc)
            return 0

        written = 0
        max_id = watermark
        for row in rows:
            jid = int(row["id"])
            if jid <= watermark:
                continue
            try:
                self._write_tick(jid, row)
                written += 1
                max_id = max(max_id, jid)
            except Exception as exc:
                logger.debug("Timeline: tick write failed for journal %s: %s", jid, exc)

        if written:
            try:
                self.db.set_meta(_WATERMARK_KEY, str(max_id))
            except Exception as exc:
                logger.error("Timeline: watermark persist failed: %s", exc)
        return written

    def _write_tick(self, journal_id: int, row: dict[str, Any]) -> None:
        event_type = str(row.get("event_type", ""))
        phase = _PHASE_BY_EVENT.get(event_type, "perception")

        payload = str(row.get("payload", "") or "{}")
        # entity/fact id extracted from the payload for the tick reference
        try:
            data = json.loads(payload) if payload.strip() else {}
        except (ValueError, TypeError):
            data = {}
        ref_id = str(data.get("fact_id") or data.get("entity_id")
                     or data.get("mutation_id") or f"j{journal_id}")
        user_id = int(data.get("user_id", 0) or 0)

        payload_hash = hashlib.sha256(payload.encode("utf-8")).hexdigest()[:16]
        created_at = str(row.get("created_at", "") or datetime.now().isoformat())

        self.db.insert_timeline_tick(
            turn_id=f"j{journal_id}",
            user_id=user_id,
            phase=phase,
            payload_hash=payload_hash,
            payload=payload[:1024],
            created_at=created_at,
        )

    # ── maintenance ────────────────────────────────────────────────────────

    def archive_old(self, retention_days: int = _RETENTION_DAYS) -> int:
        """Flag timeline ticks older than the retention window as archived.

        Keeps the table bounded; never deletes (Iron Law #5).
        """
        try:
            cutoff = (datetime.now() - timedelta(days=max(1, retention_days))).isoformat()
            return self.db.archive_timeline_before(cutoff)
        except Exception as exc:
            logger.error("Timeline: archive sweep failed: %s", exc)
            return 0

    def recent(self, limit: int = 50, phase: str | None = None) -> list[dict[str, Any]]:
        """Recent (unarchived) ticks for observability, newest first."""
        return self.db.list_timeline_ticks(limit=limit, phase=phase)

    def to_prompt_block(self, limit: int = 15) -> str:
        """Compact recent-ticks block for the prompt (observability)."""
        ticks = self.recent(limit=limit)
        if not ticks:
            return ""
        lines = ["[Когнитивный таймлайн — последние такты]"]
        for t in ticks:
            created = str(t.get("created_at", ""))[11:19]
            lines.append(f"• {created} [{t.get('phase', '?')}] {str(t.get('payload', ''))[:80]}")
        return "\n".join(lines)
