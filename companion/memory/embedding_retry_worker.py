"""Embedding Retry Worker with exponential backoff.

This worker processes facts with status 'pending_embedding' and retries
embedding generation with exponential backoff on failure.

Backoff schedule: 1m, 2m, 5m, 15m, 30m, 1h, 3h, 12h, 24h (max)
Max retries: 10 (after which fact is marked as 'failed_embedding')
"""
from __future__ import annotations

import asyncio
import logging
import time
from datetime import datetime, timedelta
from typing import TYPE_CHECKING

from companion.config import Config

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
    """Background worker that retries embedding generation for pending facts."""

    def __init__(self, memory_store: MemoryStore, check_interval: int = 60) -> None:
        """
        Initialize the retry worker.

        Args:
            memory_store: The memory store instance to use for fact operations.
            check_interval: How often to check for pending facts (seconds).
        """
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
        self._task = asyncio.create_task(self._run_loop(), name="embedding-retry-worker")
        logger.info("EmbeddingRetryWorker started (check_interval=%ds)", self.check_interval)

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
            
            # Wait before next check
            await asyncio.sleep(self.check_interval)

    async def _process_pending_facts(self) -> None:
        """Find and process all facts with pending_embedding status."""
        self._stats["processed"] += 1
        
        # Get all pending embedding facts
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
                logger.exception("Failed to retry embedding for fact %s: %s", fact.id, e)

    async def _retry_fact_embedding(self, fact) -> None:
        """
        Retry embedding generation for a single fact.

        Uses exponential backoff based on retry count stored in metadata.
        """
        fact_id = fact.id
        
        # Get retry metadata
        retry_count = fact.metadata.get("embedding_attempts", 0) if fact.metadata else 0
        last_error = fact.metadata.get("last_embedding_error", "Unknown") if fact.metadata else "Unknown"
        last_retry_at = fact.metadata.get("last_embedding_retry_at") if fact.metadata else None
        
        # Check if we should retry now (exponential backoff)
        if last_retry_at:
            try:
                # Parse ISO format timestamp
                if isinstance(last_retry_at, str):
                    last_retry_dt = datetime.fromisoformat(last_retry_at.replace('Z', '+00:00'))
                else:
                    last_retry_dt = last_retry_at
                
                # Determine delay based on retry count
                delay_index = min(retry_count, len(BACKOFF_DELAYS) - 1)
                required_delay = BACKOFF_DELAYS[delay_index]
                
                elapsed = (datetime.now(last_retry_dt.tzinfo) - last_retry_dt).total_seconds() if last_retry_dt.tzinfo else (datetime.now() - last_retry_dt).total_seconds()
                
                if elapsed < required_delay:
                    logger.debug(
                        "Fact %s: skipping retry (attempt %d, need to wait %.0f more seconds)",
                        fact_id, retry_count + 1, required_delay - elapsed
                    )
                    return
            except Exception as e:
                logger.warning("Failed to parse last_retry_at for fact %s: %s", fact_id, e)
        
        # Check max retries
        if retry_count >= MAX_RETRIES:
            logger.warning(
                "Fact %s: max retries (%d) exceeded, marking as %s. Last error: %s",
                fact_id, MAX_RETRIES, FAILED_EMBEDDING_STATUS, last_error
            )
            self._mark_as_failed(fact_id, retry_count, last_error)
            self._stats["gave_up"] += 1
            return
        
        # Attempt embedding
        logger.info(
            "Fact %s: attempting embedding (attempt %d/%d, last error: %s)",
            fact_id, retry_count + 1, MAX_RETRIES, last_error
        )
        
        try:
            # Generate embedding
            vec = self.memory_store.vector.embed_text_only(fact.fact)
            
            if vec is None:
                raise ValueError("Embedding API returned None")
            
            # Success! Update fact status and save vector
            self._mark_embedding_success(fact_id, vec, retry_count)
            self._stats["succeeded"] += 1
            logger.info("Fact %s: embedding succeeded on attempt %d", fact_id, retry_count + 1)
            
        except Exception as e:
            # Failure - update retry metadata
            error_msg = str(e)
            self._mark_embedding_failure(fact_id, retry_count, error_msg)
            self._stats["failed"] += 1
            
            if retry_count < MAX_RETRIES - 1:
                self._stats["retried"] += 1
                delay_index = min(retry_count, len(BACKOFF_DELAYS) - 1)
                logger.info(
                    "Fact %s: embedding failed (attempt %d), will retry after %ds. Error: %s",
                    fact_id, retry_count + 1, BACKOFF_DELAYS[delay_index], error_msg
                )

    def _mark_embedding_success(self, fact_id: str, vec: list[float], retry_count: int) -> None:
        """Mark fact as successfully embedded."""
        with self.memory_store.db.atomic_memory_transaction():
            # Update status to active
            self.memory_store.db._conn().execute(
                "UPDATE facts SET status = 'active', updated_at = ? WHERE id = ?",
                (datetime.now().isoformat(), fact_id)
            )
            
            # Clear embedding error metadata
            fact = self.memory_store.get_fact(fact_id)
            if fact and fact.metadata:
                new_metadata = fact.metadata.copy()
                new_metadata.pop("embedding_attempts", None)
                new_metadata.pop("last_embedding_error", None)
                new_metadata.pop("last_embedding_retry_at", None)
                self.memory_store.db._update_fact_metadata(fact_id, new_metadata)
            
            # Add vector to FAISS
            self.memory_store.vector.upsert_embedding(
                fact.fact, vec, content_type="fact", fact_id=fact_id
            )
        
        # Publish event
        if self.memory_store.event_bus:
            from companion.memory.events.base import FactUpdatedEvent
            self.memory_store.event_bus.publish(
                FactUpdatedEvent(
                    fact_id=fact_id,
                    old_status="pending_embedding",
                    new_status="active",
                    reason="embedding_retry_success"
                )
            )

    def _mark_embedding_failure(self, fact_id: str, retry_count: int, error_msg: str) -> None:
        """Update fact metadata with retry information."""
        fact = self.memory_store.get_fact(fact_id)
        if not fact:
            return
        
        metadata = fact.metadata.copy() if fact.metadata else {}
        metadata["embedding_attempts"] = retry_count + 1
        metadata["last_embedding_error"] = error_msg
        metadata["last_embedding_retry_at"] = datetime.now().isoformat()
        
        with self.memory_store.db.atomic_memory_transaction():
            self.memory_store.db._update_fact_metadata(fact_id, metadata)

    def _mark_as_failed(self, fact_id: str, retry_count: int, error_msg: str) -> None:
        """Mark fact as permanently failed after max retries."""
        fact = self.memory_store.get_fact(fact_id)
        if not fact:
            return
        
        metadata = fact.metadata.copy() if fact.metadata else {}
        metadata["embedding_attempts"] = retry_count
        metadata["last_embedding_error"] = error_msg
        metadata["embedding_failed_permanently"] = True
        
        with self.memory_store.db.atomic_memory_transaction():
            # Update status to failed_embedding
            self.memory_store.db._conn().execute(
                "UPDATE facts SET status = ?, updated_at = ? WHERE id = ?",
                (FAILED_EMBEDDING_STATUS, datetime.now().isoformat(), fact_id)
            )
            self.memory_store.db._update_fact_metadata(fact_id, metadata)

    def get_stats(self) -> dict[str, int]:
        """Return worker statistics."""
        return self._stats.copy()


# Global instance (will be initialized by main.py)
_embedding_retry_worker: EmbeddingRetryWorker | None = None


def get_embedding_retry_worker() -> EmbeddingRetryWorker | None:
    """Get the global EmbeddingRetryWorker instance."""
    return _embedding_retry_worker


def init_embedding_retry_worker(memory_store: MemoryStore) -> EmbeddingRetryWorker:
    """Initialize and return the global EmbeddingRetryWorker instance."""
    global _embedding_retry_worker
    _embedding_retry_worker = EmbeddingRetryWorker(memory_store)
    return _embedding_retry_worker
