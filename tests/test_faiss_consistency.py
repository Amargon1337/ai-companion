"""Unit tests for FAISS/SQLite consistency via IndexSyncService."""
from __future__ import annotations

import tempfile
from unittest.mock import MagicMock

from companion.memory.events import FactArchivedEvent, FactSupersededEvent, IndexSyncService, MemoryEventBus
from companion.models import Fact
from companion.storage.sqlite_db import MemoryDatabase


def test_index_sync_service_removes_on_archive_or_supersede() -> None:
    bus = MemoryEventBus()
    mock_vector = MagicMock()
    mock_vector.delete_for_content = MagicMock()

    with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as tf:
        db_path = tf.name

    db = MemoryDatabase(db_path)
    try:
        f1 = Fact(id="f-sync-1", fact="Секретный факт для удаления", date="2026-07-28", importance=5, confidence=0.8, source="msg", status="active")
        db.batch_insert_facts([f1.to_dict()])

        sync_srv = IndexSyncService(bus, mock_vector, db)

        # Publish FactArchivedEvent
        bus.publish(FactArchivedEvent(fact_id="f-sync-1", fact_text="Секретный факт для удаления"))
        mock_vector.delete_for_content.assert_called_with("Секретный факт для удаления")
        assert sync_srv.removed_count == 1

        # Publish FactSupersededEvent without fact_text (lookup from DB)
        bus.publish(FactSupersededEvent(fact_id="f-sync-1", superseded_by="f-sync-2"))
        assert mock_vector.delete_for_content.call_count == 2
        assert sync_srv.removed_count == 2
    finally:
        db.close()
