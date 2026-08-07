"""IndexSyncService for synchronizing FAISS vector index with SQLite facts via MemoryEventBus."""
from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Any

from companion.memory.events.base import (
    FactArchivedEvent,
    FactCreatedEvent,
    FactSupersededEvent,
    FactUpdatedEvent,
    MemoryEvent,
)

if TYPE_CHECKING:
    from companion.memory.events.bus import MemoryEventBus
    from companion.storage.sqlite_db import MemoryDatabase

logger = logging.getLogger(__name__)


class IndexSyncService:
    """Subscribes to MemoryEventBus to maintain consistency between SQLite and FAISS VectorIndex."""

    def __init__(
        self,
        event_bus: MemoryEventBus,
        vector_index: Any,
        db: MemoryDatabase | None = None,
    ) -> None:
        self.event_bus = event_bus
        self.vector_index = vector_index
        self.db = db
        self.removed_count = 0
        self.added_count = 0
        self.updated_count = 0
        self.error_count = 0

        self.event_bus.subscribe(FactCreatedEvent, self.on_fact_created)
        self.event_bus.subscribe(FactUpdatedEvent, self.on_fact_updated)
        self.event_bus.subscribe(FactArchivedEvent, self.on_fact_removed)
        self.event_bus.subscribe(FactSupersededEvent, self.on_fact_removed)
        logger.info(
            "IndexSyncService subscribed to FactCreatedEvent, FactUpdatedEvent, "
            "FactArchivedEvent and FactSupersededEvent"
        )

    def on_fact_created(self, event: MemoryEvent) -> None:
        """Handler for when a fact is created."""
        fact_id = getattr(event, "fact_id", "")
        fact_text = getattr(event, "fact_text", "")
        if not fact_text and self.db and fact_id:
            fact = self.db.get_fact(fact_id)
            if fact:
                fact_text = str(fact.get("fact", ""))

        if not fact_text:
            return

        try:
            if hasattr(self.vector_index, "compute_and_cache"):
                if hasattr(self.vector_index, "_content_hash") and isinstance(getattr(self.vector_index, "hash_to_id", None), dict):
                    h = self.vector_index._content_hash(fact_text)
                    if h in self.vector_index.hash_to_id:
                        logger.debug("Fact %s already indexed in FAISS; IndexSyncService skipping.", fact_id)
                        return
                self.vector_index.compute_and_cache(fact_text, content_type="fact", fact_id=fact_id)
                self.added_count += 1
                logger.info(
                    "IndexSyncService added embedding for fact_id=%s (event=%s)",
                    fact_id,
                    event.__class__.__name__,
                )
        except Exception:
            self.error_count += 1
            logger.exception("Error adding embedding to VectorIndex for fact_id=%s", fact_id)

    def on_fact_updated(self, event: MemoryEvent) -> None:
        """Handler for when a fact is updated."""
        fact_id = getattr(event, "fact_id", "")
        old_state = getattr(event, "old_state", {}) or {}
        new_state = getattr(event, "new_state", {}) or {}

        old_text = str(old_state.get("fact", ""))
        new_text = str(new_state.get("fact", ""))

        if not new_text and self.db and fact_id:
            fact = self.db.get_fact(fact_id)
            if fact:
                new_text = str(fact.get("fact", ""))

        if not new_text or not old_text or old_text.strip() == new_text.strip():
            return

        try:
            if hasattr(self.vector_index, "_content_hash") and isinstance(getattr(self.vector_index, "hash_to_id", None), dict):
                new_h = self.vector_index._content_hash(new_text)
                if new_h in self.vector_index.hash_to_id:
                    logger.debug("Updated fact %s already indexed in FAISS; IndexSyncService skipping.", fact_id)
                    return
            if hasattr(self.vector_index, "delete_for_content"):
                self.vector_index.delete_for_content(old_text)
            if hasattr(self.vector_index, "compute_and_cache"):
                self.vector_index.compute_and_cache(new_text, content_type="fact", fact_id=fact_id)
            self.updated_count += 1
            logger.info(
                "IndexSyncService updated embedding for fact_id=%s (event=%s)",
                fact_id,
                event.__class__.__name__,
            )
        except Exception:
            self.error_count += 1
            logger.exception("Error updating embedding in VectorIndex for fact_id=%s", fact_id)

    def on_fact_removed(self, event: MemoryEvent) -> None:
        """Handler for when a fact is archived or superseded."""
        fact_id = getattr(event, "fact_id", "")
        fact_text = getattr(event, "fact_text", "")

        if not fact_text and self.db and fact_id:
            fact = self.db.get_fact(fact_id)
            if fact:
                fact_text = str(fact.get("fact", ""))

        if not fact_text:
            logger.warning("IndexSyncService could not determine fact_text for event=%s", event)
            return

        try:
            if hasattr(self.vector_index, "_content_hash") and isinstance(getattr(self.vector_index, "hash_to_id", None), dict):
                h = self.vector_index._content_hash(fact_text)
                if h not in self.vector_index.hash_to_id:
                    logger.debug("Fact %s already removed from FAISS; IndexSyncService skipping.", fact_id)
                    return
            if hasattr(self.vector_index, "delete_for_content"):
                self.vector_index.delete_for_content(fact_text)
                self.removed_count += 1
                logger.info(
                    "IndexSyncService removed embedding for fact_id=%s (event=%s)",
                    fact_id,
                    event.__class__.__name__,
                )
        except Exception:
            self.error_count += 1
            logger.exception("Error removing embedding from VectorIndex for fact_id=%s", fact_id)


def recover_index_consistency(store: Any) -> dict[str, int]:
    """Startup consistency scan and repair between SQLite facts and FAISS vector index."""
    stats = {"missing_computed": 0, "orphans_removed": 0}

    all_facts = store.db.list_all_facts()
    valid_texts: set[str] = set()
    fact_map: dict[str, str] = {}
    for f in all_facts:
        st = f.get("status", "active")
        text = str(f.get("fact", "")).strip()
        if st in ("active", "dormant") and text:
            valid_texts.add(text)
            fact_map[text] = str(f.get("id", ""))

    current_texts = set(store.vector.content_list)
    missing_texts = valid_texts - current_texts
    orphan_texts = current_texts - valid_texts

    for text in missing_texts:
        try:
            fid = fact_map.get(text, "")
            store.vector.compute_and_cache(text, content_type="fact", fact_id=fid)
            stats["missing_computed"] += 1
        except Exception:
            logger.exception("Failed to recover missing embedding for text: %r", text)

    if orphan_texts:
        try:
            store.vector.delete_for_content_batch(list(orphan_texts))
            stats["orphans_removed"] += len(orphan_texts)
        except Exception:
            logger.exception("Failed to remove orphan embeddings: %r", orphan_texts)

    logger.info("recover_index_consistency complete: %s", stats)
    return stats
