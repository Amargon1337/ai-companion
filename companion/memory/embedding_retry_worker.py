"""Embedding Retry Worker with exponential backoff.

This worker processes facts with status 'pending_embedding' and retries
embedding generation with exponential backoff on failure.

Backoff schedule: 1m, 2m, 5m, 15m, 30m, 1h, 3h, 12h, 24h (max)
Max retries: 10 (after which fact is marked as 'failed_embedding')

Architecture note:
  All retry metadata is stored in the Fact.meta dict field (NOT a separate
  'metadata' field). The fact's meta column is a JSON blob that tracks:
    - embedding_attempts: int
    - last_embedding_error: str
    - last_embedding_retry_at: ISO timestamp
    - embedding_failed_permanently: bool

  The worker runs OUTSIDE the main write path. Embedding API calls are
  performed before any SQLite transaction — matching the same principle
  as add_fact() (P0-6 fix).
"""
from __future__ import annotations

import asyncio
import json
import logging
from datetime import datetime
from typing import TYPE_CHECKING

logger = logging.getLogger(__name__)

if TYPE_CHECKING:
    from companion.memory.store import MemoryStore


# Exponential backoff delays in seconds
BACKOFF_DELAYS = [
    60,       # 1 min
    120,      # 2 min
    300,      # 5 min
    900,      # 15 min
    1800,     # 30 min
    3600,     # 1 hour
    10800,    # 3 hours
    43200,    # 12 hours
    86400,    # 24 hours
]

MAX_RETRIES = 10
FAILED_EMBEDDING_STATUS = "failed_embedding"


class EmbeddingRetryWorker:
    """Background worker that retries embedding generation for pending facts.

    Lifecycle:
      1. start() — launches the async loop
      2. _run_loop() — every check_interval seconds, scans for pending_embedding facts
      3. _retry_fact_embedding() — per-fact retry with backoff
      4. stop() — cancels the loop gracefully
    """

    def __init__(self, memory_store: MemoryStore, check_interval: int = 60) -> None:
        self.memory_store = memory_store
        self.check_interval = check_interval
        self._running = False
        self._task: asyncio.Task | None = None
        self._stats = {
            "processed": 0,
            "succeeded": 0,
            "failed": 0,
            "retried": 0,
            "gave_up": 0,
        }

    async def start(self) -> None:
        """Start the background worker."""
        if self._running:
            logger.warning("EmbeddingRetryWorker already running")
            return
        self._running = True
        self._task = asyncio.create_task(
            self._run_loop(), name="embedding-retry-worker"
        )
        logger.info(
            "EmbeddingRetryWorker started (check_interval=%ds)", self.check_interval
        )

    async def stop(self) -> None:
        """Stop the background worker gracefully."""
        self._running = False
        if self._task:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
            self._task = None
        logger.info("EmbeddingRetryWorker stopped. Stats: %s", self._stats)

    async def _run_loop(self) -> None:
        """Main loop: check for pending facts and retry embeddings."""
        while self._running:
            try:
                await self._process_pending_facts()
            except asyncio.CancelledError:
                raise
            except Exception as e:
                logger.exception("Error in EmbeddingRetryWorker loop: %s", e)
            await asyncio.sleep(self.check_interval)

    async def _process_pending_facts(self) -> None:
        """Find and process all facts with pending_embedding status."""
        self._stats["processed"] += 1
        pending_facts = self.memory_store.list_facts("pending_embedding")
        if not pending_facts:
            return
        logger.debug("Found %d facts with pending_embedding status", len(pending_facts))
        for fact in pending_facts:
            if not self._running:
                break
            try:
                await self._retry_fact_embedding(fact)
            except Exception as e:
                logger.exception(
                    "Failed to retry embedding for fact %s: %s", fact.id, e
                )

    # ------------------------------------------------------------------ #
    #  Retry logic
    # ------------------------------------------------------------------ #

    async def _retry_fact_embedding(self, fact) -> None:
        """Retry embedding generation for a single fact.

        Uses exponential backoff based on retry count stored in fact.meta.
        """
        fact_id = fact.id
        meta = fact.meta if isinstance(fact.meta, dict) else {}

        retry_count = int(meta.get("embedding_attempts", 0))
        last_error = str(meta.get("last_embedding_error", "Unknown"))
        last_retry_at = meta.get("last_embedding_retry_at")

        # --- Backoff check ---
        if last_retry_at:
            try:
                if isinstance(last_retry_at, str):
                    last_retry_dt = datetime.fromisoformat(
                        last_retry_at.replace("Z", "+00:00")
                    )
                else:
                    last_retry_dt = last_retry_at

                delay_index = min(retry_count, len(BACKOFF_DELAYS) - 1)
                required_delay = BACKOFF_DELAYS[delay_index]
                elapsed = (datetime.now(last_retry_dt.tzinfo) - last_retry_dt).total_seconds() if last_retry_dt.tzinfo else (datetime.now() - last_retry_dt).total_seconds()

                if elapsed < required_delay:
                    logger.debug(
                        "Fact %s: skipping retry (attempt %d, need to wait %.0f more seconds)",
                        fact_id,
                        retry_count + 1,
                        required_delay - elapsed,
                    )
                    return
            except Exception as e:
                logger.warning("Failed to parse last_retry_at for fact %s: %s", fact_id, e)

        # --- Max retries check ---
        if retry_count >= MAX_RETRIES:
            logger.warning(
                "Fact %s: max retries (%d) exceeded, marking as %s. Last error: %s",
                fact_id,
                MAX_RETRIES,
                FAILED_EMBEDDING_STATUS,
                last_error,
            )
            self._mark_as_failed(fact_id, meta, last_error)
            self._stats["gave_up"] += 1
            return

        # --- Attempt embedding (OUTSIDE any transaction) ---
        logger.info(
            "Fact %s: attempting embedding (attempt %d/%d, last error: %s)",
            fact_id,
            retry_count + 1,
            MAX_RETRIES,
            last_error,
        )
        try:
            vec = self.memory_store.vector.embed_text_only(fact.fact)
            if vec is None:
                raise ValueError("Embedding API returned None")

            # Success: activate the fact and persist the vector
            self._activate_with_embedding(fact_id, fact.fact, vec, meta)
            self._stats["succeeded"] += 1
            logger.info("Fact %s: embedding succeeded on attempt %d", fact_id, retry_count + 1)

        except Exception as e:
            error_msg = str(e)
            self._record_failure(fact_id, meta, retry_count, error_msg)
            self._stats["failed"] += 1
            if retry_count < MAX_RETRIES - 1:
                self._stats["retried"] += 1
                delay_index = min(retry_count, len(BACKOFF_DELAYS) - 1)
                logger.info(
                    "Fact %s: embedding failed (attempt %d), will retry after %ds. Error: %s",
                    fact_id,
                    retry_count + 1,
                    BACKOFF_DELAYS[delay_index],
                    error_msg,
                )

    # ------------------------------------------------------------------ #
    #  State mutations
    # ------------------------------------------------------------------ #

    def _activate_with_embedding(
        self, fact_id: str, fact_text: str, vec: list[float], old_meta: dict
    ) -> None:
        """Mark fact as active and persist its embedding vector.

        Order: SQLite transaction (status update + meta cleanup) first,
        then FAISS upsert (outside transaction, matching add_fact pattern).
        """
        # Clean retry metadata
        new_meta = {k: v for k, v in old_meta.items()
                    if k not in ("embedding_attempts", "last_embedding_error", "last_embedding_retry_at")}

        # 1. SQLite transaction: status → active, meta cleanup
        with self.memory_store.db.atomic_memory_transaction():
            self.memory_store.db.update_fact_fields(
                fact_id,
                {"status": "active", "meta": new_meta},
            )

        # 2. FAISS upsert (outside transaction — no API calls inside)
        try:
            self.memory_store.vector.upsert_embedding(
                fact_text, vec, content_type="fact", fact_id=fact_id
            )
        except Exception as exc:
            logger.warning(
                "FAISS upsert failed for reactivated fact %s (will recover on next rebuild): %s",
                fact_id,
                exc,
            )

        # 3. Publish event (outside transaction)
        if self.memory_store.event_bus:
            from companion.memory.events.base import FactUpdatedEvent
            self.memory_store.event_bus.publish(
                FactUpdatedEvent(
                    fact_id=fact_id,
                    old_state={"status": "pending_embedding"},
                    new_state={"status": "active"},
                    reason="embedding_retry_success",
                )
            )

    def _record_failure(
        self, fact_id: str, old_meta: dict, retry_count: int, error_msg: str
    ) -> None:
        """Update fact meta with retry information after a failed attempt."""
        new_meta = dict(old_meta)
        new_meta["embedding_attempts"] = retry_count + 1
        new_meta["last_embedding_error"] = error_msg
        new_meta["last_embedding_retry_at"] = datetime.now().isoformat()
        self.memory_store.db.update_fact_fields(fact_id, {"meta": new_meta})

    def _mark_as_failed(
        self, fact_id: str, old_meta: dict, error_msg: str
    ) -> None:
        """Mark fact as permanently failed after max retries exhausted."""
        new_meta = dict(old_meta)
        new_meta["embedding_attempts"] = MAX_RETRIES
        new_meta["last_embedding_error"] = error_msg
        new_meta["embedding_failed_permanently"] = True
        try:
            self.memory_store.db.update_fact_fields(
                fact_id,
                {"status": FAILED_EMBEDDING_STATUS, "meta": new_meta},
            )
        except Exception as exc:
            # Legacy databases may still have the pre-status-enum CHECK
            # constraint. Pending-review is a supported terminal review state
            # and prevents a permanent 60-second retry loop until migration.
            logger.warning("Unable to persist failed_embedding for %s; moving to pending_review: %s", fact_id, exc)
            new_meta["embedding_failed_permanently"] = True
            self.memory_store.db.update_fact_fields(
                fact_id,
                {"status": "pending_review", "meta": new_meta},
            )

    def get_stats(self) -> dict[str, int]:
        """Return worker statistics."""
        return self._stats.copy()


# Global instance (initialized by main.py via init_embedding_retry_worker)
_embedding_retry_worker: EmbeddingRetryWorker | None = None


def get_embedding_retry_worker() -> EmbeddingRetryWorker | None:
    """Get the global EmbeddingRetryWorker instance."""
    return _embedding_retry_worker


def init_embedding_retry_worker(memory_store: MemoryStore) -> EmbeddingRetryWorker:
    """Initialize and return the global EmbeddingRetryWorker instance."""
    global _embedding_retry_worker
    _embedding_retry_worker = EmbeddingRetryWorker(memory_store)
    return _embedding_retry_worker
