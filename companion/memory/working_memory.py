"""Cognitive Working Memory (K5) — bounded live context for the current turn.

Cognitive function: a small, TTL-expiring set of per-user slots that keeps
"what matters right now" (current goal, active identity, open questions,
salient facts, affective state) out of long-term tables. The prompt compiler
reads slots instead of re-scanning memory each turn — the RAM/query savings the
blueprint (OMNI Phase 2, S5) requires on an 8GB box.

Iron Law #5: nothing here ever DELETEs. Expired/evicted slots flip `archived`.

Failure modes are handled by the caller with try/except — working memory is an
optimization, never a correctness dependency of the conversation path.
"""
from __future__ import annotations

import logging
from datetime import datetime, timedelta
from typing import Any

logger = logging.getLogger(__name__)

# TTL per slot type (hours). Goals and identity persist across a session;
# affective state and open questions are short-lived by design.
_SLOT_TTL_HOURS = {
    "current_goal": 24,
    "active_identity": 24 * 7,
    "open_question": 4,
    "salient_fact": 24,
    "affective_state": 1,
}
_MAX_LIVE_SLOTS_PER_USER = 50


class WorkingMemoryService:
    """Derives and persists working-memory slots from a single turn."""

    def __init__(self, db: Any) -> None:
        self.db = db

    def _slot_ttl(self, slot_type: str) -> timedelta:
        return timedelta(hours=_SLOT_TTL_HOURS.get(slot_type, 1))

    def _upsert(self, *, user_id: int, slot_type: str, ref_kind: str,
                ref_id: str, payload: str, salience: float) -> None:
        expires_at = (datetime.now() + self._slot_ttl(slot_type)).isoformat()
        try:
            self.db.upsert_working_memory_slot(
                user_id=user_id, slot_type=slot_type, ref_kind=ref_kind,
                ref_id=ref_id or "", payload=payload[:512],
                salience=round(max(0.0, min(1.0, salience)), 3),
                expires_at=expires_at,
            )
        except Exception as exc:
            logger.debug("working memory slot upsert failed (non-fatal): %s", exc)

    def update_from_turn(
        self,
        *,
        user_id: int,
        mood_state: dict[str, float] | None = None,
        needs_clarification: str = "",
        captured_goal: str = "",
        active_goals: list[Any] | None = None,
        top_facts: list[tuple[Any, float]] | None = None,
    ) -> dict[str, int]:
        """Write this turn's salient slots; enforce expiry + cap.

        Inputs mirror what build_context already computed — no extra LLM calls.
        Returns counts of slots written by type (for telemetry/tests).
        """
        written: dict[str, int] = {}
        try:
            self.db.archive_expired_working_memory()
        except Exception as exc:
            logger.debug("working memory expiry sweep failed (non-fatal): %s", exc)

        # 1. Current goal (from analyzer captured_goal or active goal list)
        goal_text = (captured_goal or "").strip()
        if not goal_text and active_goals:
            g = active_goals[0]
            goal_text = str(getattr(g, "title", "") or "")
        if goal_text:
            self._upsert(user_id=user_id, slot_type="current_goal",
                         ref_kind="goal", ref_id="", payload=goal_text, salience=0.9)
            written["current_goal"] = written.get("current_goal", 0) + 1

        # 2. Open question (clarification gap)
        if needs_clarification.strip():
            self._upsert(user_id=user_id, slot_type="open_question",
                         ref_kind="none", ref_id="",
                         payload=needs_clarification.strip()[:256], salience=0.7)
            written["open_question"] = written.get("open_question", 0) + 1

        # 3. Affective state (from analyzer mood; no LLM)
        if mood_state:
            top_mood = max(mood_state.items(), key=lambda kv: kv[1] or 0.0)
            if top_mood[1] and float(top_mood[1] or 0.0) > 0.4:
                self._upsert(user_id=user_id, slot_type="affective_state",
                             ref_kind="none", ref_id="",
                             payload=f"{top_mood[0]}:{float(top_mood[1]):.2f}",
                             salience=0.6)
                written["affective_state"] = written.get("affective_state", 0) + 1

        # 4. Salient facts (top retrieved, score-gated)
        for fact, score in (top_facts or [])[:5]:
            if score and float(score) >= 0.55:
                self._upsert(user_id=user_id, slot_type="salient_fact",
                             ref_kind="fact", ref_id=getattr(fact, "id", ""),
                             payload=str(getattr(fact, "fact", ""))[:256],
                             salience=min(1.0, float(score)))
                written["salient_fact"] = written.get("salient_fact", 0) + 1

        # 5. Active identity: identity-tagged facts among the retrieved window
        for fact, score in (top_facts or [])[:10]:
            tags = [str(t).lower() for t in (getattr(fact, "tags", None) or [])]
            if any(t in tags for t in ("anchor", "core_identity", "pinned")) \
                    or getattr(fact, "memory_kind", "") == "permanent":
                self._upsert(user_id=user_id, slot_type="active_identity",
                             ref_kind="fact", ref_id=getattr(fact, "id", ""),
                             payload=str(getattr(fact, "fact", ""))[:256],
                             salience=0.8)
                written["active_identity"] = written.get("active_identity", 0) + 1

        # Cap enforcement (bounded working set)
        try:
            evicted = self.db.evict_working_memory_slots(
                user_id, keep=_MAX_LIVE_SLOTS_PER_USER)
            if evicted:
                logger.debug("working memory evicted %d low-salience slot(s)", evicted)
        except Exception as exc:
            logger.debug("working memory eviction failed (non-fatal): %s", exc)

        return written

    def snapshot(self, user_id: int, limit: int = 50) -> list[dict[str, Any]]:
        """Live slots for prompt assembly, highest salience first."""
        try:
            return self.db.list_live_working_memory_slots(user_id, limit=limit)
        except Exception as exc:
            logger.debug("working memory snapshot failed: %s", exc)
            return []
