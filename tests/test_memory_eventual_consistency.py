"""Unit tests for event-driven FAISS consistency and startup recovery (Phase 1.6)."""
from __future__ import annotations

import os
import tempfile
from unittest.mock import MagicMock

from companion.models import Fact
from companion.memory.events.bus import MemoryEventBus
from companion.memory.events.base import FactCreatedEvent, FactUpdatedEvent, FactArchivedEvent
from companion.memory.events.sync import IndexSyncService, recover_index_consistency


class MockVectorIndex:
    def __init__(self):
        self.added = []
        self.deleted = []
        self.content_list = []

    def compute_and_cache(self, text, content_type="fact", fact_id=""):
        if text not in self.content_list:
            self.content_list.append(text)
        self.added.append((text, fact_id))

    def delete_for_content(self, text):
        if text in self.content_list:
            self.content_list.remove(text)
        self.deleted.append(text)

    def delete_for_content_batch(self, texts):
        for t in texts:
            self.delete_for_content(t)


class MockStore:
    def __init__(self, db, vector):
        self.db = db
        self.vector = vector


class MockDB:
    def __init__(self, facts):
        self._facts = facts

    def list_all_facts(self):
        return self._facts

    def get_fact(self, fact_id):
        for f in self._facts:
            if f["id"] == fact_id:
                return f
        return None


def test_index_sync_service_events() -> None:
    event_bus = MemoryEventBus()
    vector = MockVectorIndex()
    db = MockDB([{"id": "f-1", "fact": "Fact 1", "status": "active"}])
    sync = IndexSyncService(event_bus, vector, db)

    # 1. Created event
    event_bus.publish(FactCreatedEvent(fact_id="f-1", fact_text="Fact 1"))
    assert sync.added_count == 1
    assert "Fact 1" in vector.content_list

    # 2. Updated event
    event_bus.publish(FactUpdatedEvent(fact_id="f-1", old_state={"fact": "Fact 1"}, new_state={"fact": "Fact 1 New"}))
    assert sync.updated_count == 1
    assert "Fact 1" not in vector.content_list
    assert "Fact 1 New" in vector.content_list

    # 3. Archived event
    event_bus.publish(FactArchivedEvent(fact_id="f-1", fact_text="Fact 1 New"))
    assert sync.removed_count == 1
    assert "Fact 1 New" not in vector.content_list


def test_recover_index_consistency_orphans_and_missing() -> None:
    db = MockDB([
        {"id": "f-1", "fact": "Active Fact", "status": "active"},
        {"id": "f-2", "fact": "Dormant Fact", "status": "dormant"},
        {"id": "f-3", "fact": "Archived Fact", "status": "archived"},
    ])
    vector = MockVectorIndex()
    # Pre-populate index with an orphan and only one valid fact
    vector.content_list = ["Active Fact", "Orphan Fact"]

    store = MockStore(db, vector)
    stats = recover_index_consistency(store)

    assert stats["missing_computed"] == 1  # "Dormant Fact" added
    assert stats["orphans_removed"] == 1   # "Orphan Fact" removed

    assert "Active Fact" in vector.content_list
    assert "Dormant Fact" in vector.content_list
    assert "Orphan Fact" not in vector.content_list
    assert "Archived Fact" not in vector.content_list
