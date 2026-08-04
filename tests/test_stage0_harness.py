"""Stage 0 Integration Harness and Baseline Tests.

Tests the full lifecycle persistence of facts, concurrent transaction isolation,
and synchronous EventBus execution guarantees before refactoring core systems.
"""
from __future__ import annotations

import concurrent.futures
import hashlib
import tempfile
from unittest.mock import MagicMock, patch

import pytest

import companion.config as cfg
from companion.memory.events import FactArchivedEvent, FactCreatedEvent, FactUpdatedEvent, IndexSyncService, MemoryEventBus
from companion.memory.store import MemoryStore
from companion.models import Fact
from companion.storage.sqlite_db import MemoryDatabase


def _mock_embed(texts: list[str]) -> list[list[float]]:
    res = []
    for t in texts:
        h = int(hashlib.md5(t.encode("utf-8")).hexdigest(), 16)
        vec = [0.0] * 768
        idx1 = (h ^ 0x55555555) % 768
        vec[idx1] = 1.0
        res.append(vec)
    return res


def test_fact_full_lifecycle_persistence(tmp_path, monkeypatch) -> None:
    """Test full lifecycle: create -> edit -> archive/delete -> search -> reopen -> search."""
    cfg.DATA_DIR = str(tmp_path)
    cfg.SQLITE_PATH = str(tmp_path / "companion_lifecycle.db")

    with patch("companion.memory.vector_index._embed_texts", side_effect=_mock_embed):
        store = MemoryStore()
        store.vector.embeddings_enabled = True
        try:
            # 1. Create Fact
            fact = Fact(
                id="fact-lifecycle-1",
                fact="Пользователь изучает квантовые вычисления и нейросети",
                date="2026-08-02",
                importance=8,
                confidence=0.9,
                source="user",
                status="active",
            )
            store.add_fact(fact)

            # Verify semantic search returns it
            results = store.vector.search("Пользователь изучает квантовые вычисления и нейросети", top_k=5)
            assert any("квантовые" in r.get("content", "") for r in results), "Created fact must be searchable in FAISS"

            # 2. Edit Fact (change wording)
            updated_text = "Пользователь освоил топологические квантовые компьютеры"
            ok = store.update_fact(fact.id, fact=updated_text, importance=9)
            assert ok is True

            # Verify new text is searchable
            results_new = store.vector.search("Пользователь освоил топологические квантовые компьютеры", top_k=5)
            assert any("топологические" in r.get("content", "") for r in results_new), "Updated text must be searchable in FAISS"

            # 3. Archive / Delete Fact
            deleted = store.delete_fact(fact.id)
            assert deleted is True

            # Verify it is removed from DB and no longer searchable
            assert store.get_fact(fact.id) is None
            results_after_delete = store.vector.search("Пользователь освоил топологические квантовые компьютеры", top_k=5)
            assert not any("fact-lifecycle-1" == r.get("id", "") or "fact-lifecycle-1" == r.get("fact_id", "") for r in results_after_delete), "Deleted fact must not appear in FAISS search results"
        finally:
            store.db.close()

        # 4. Reopen Store and verify persistent state
        reopened_store = MemoryStore()
        reopened_store.vector.embeddings_enabled = True
        try:
            assert reopened_store.get_fact("fact-lifecycle-1") is None
            results_reopened = reopened_store.vector.search("Пользователь освоил топологические квантовые компьютеры", top_k=5)
            assert not any("fact-lifecycle-1" == r.get("id", "") or "fact-lifecycle-1" == r.get("fact_id", "") for r in results_reopened), "State must persist across store restart"
        finally:
            reopened_store.db.close()


def test_atomic_tx_thread_isolation(tmp_path) -> None:
    """Test 10 concurrent threads executing atomic_memory_transaction operations on a shared MemoryDatabase."""
    db_path = str(tmp_path / "test_tx_concurrency.db")
    db = MemoryDatabase(db_path)
    try:
        errors: list[Exception] = []

        def worker(thread_idx: int) -> None:
            try:
                for idx in range(10):
                    fid = f"fact-t{thread_idx}-{idx}"
                    with db.atomic_memory_transaction():
                        with db._conn() as conn:
                            conn.execute(
                                "INSERT INTO facts (id, fact, importance, status, version) VALUES (?, ?, ?, ?, ?)",
                                (fid, f"Fact from thread {thread_idx} idx {idx}", 5, "active", 1),
                            )
            except Exception as exc:
                errors.append(exc)

        with concurrent.futures.ThreadPoolExecutor(max_workers=10) as executor:
            futures = [executor.submit(worker, idx) for idx in range(10)]
            concurrent.futures.wait(futures)

        assert not errors, f"Concurrent atomic_memory_transaction raised exceptions: {errors}"

        # Verify all 100 facts were cleanly inserted
        with db._conn() as conn:
            count = conn.execute("SELECT count(*) FROM facts").fetchone()[0]
        assert count == 100
    finally:
        db.close()


def test_event_bus_sync_index_update() -> None:
    """Verify synchronous EventBus index updates before removing imperative CRUD FAISS calls."""
    bus = MemoryEventBus()
    mock_vector = MagicMock()
    mock_vector.delete_for_content = MagicMock()
    mock_vector.compute_and_cache = MagicMock()

    with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as tf:
        db_path = tf.name

    db = MemoryDatabase(db_path)
    try:
        sync_srv = IndexSyncService(bus, mock_vector, db)

        # Publish FactCreatedEvent synchronously
        bus.publish(FactCreatedEvent(
            fact_id="fact-sync-created",
            fact_text="Синхронно добавленный факт в индекс",
            importance=7,
        ))

        # Assert compute_and_cache was called IMMEDIATELY and synchronously
        mock_vector.compute_and_cache.assert_called_with(
            "Синхронно добавленный факт в индекс",
            content_type="fact",
            fact_id="fact-sync-created",
        )
        assert sync_srv.added_count == 1
    finally:
        db.close()


def test_faiss_hnsw_hybrid_tombstone_threshold(tmp_path) -> None:
    """Verify HNSW tombstone threshold: avoids rebuild on small delete, rebuilds when > 10% deleted."""
    cfg.DATA_DIR = str(tmp_path)
    cfg.SQLITE_PATH = str(tmp_path / "companion_tombstones.db")

    with patch("companion.memory.vector_index._embed_texts", side_effect=_mock_embed):
        store = MemoryStore()
        store.vector.embeddings_enabled = True
        try:
            # Add 20 facts
            distinct_facts = [
                "Астрономы открыли новую экзопланету созвездия Лебедя",
                "Биологи изучили структуру митохондриальной редкой бактерии",
                "Физики подтвердили существование бозона коллайдере",
                "Химики синтезировали сверхпрочный полимерный материал",
                "Генетики расшифровали геном древнего неандертальца",
                "Геологи обнаружили месторождение лития пустыне",
                "Археологи раскопали руины античного города",
                "Метеорологи зафиксировали температурный рекорд Арктике",
                "Сейсмологи спрогнозировали смещение тектонических плит",
                "Океанологи исследовали фауну Марианской впадины",
                "Кибернетики создали алгоритм автономной навигации",
                "Лингвисты реконструировали праиндоевропейский древний корень",
                "Экологи оценили влияние вулканического пепла",
                "Фармакологи разработали антибиотик нового поколения",
                "Энергетики запустили термоядерный экспериментальный реактор",
                "Нейробиологи составили карту синапсов мозга",
                "Зоологи описали неизвестный подвид пантеры",
                "Ботаники вырастили реликтовое дерево семян",
                "Палеонтологи идентифицировали кости крупного ящера",
                "Энтомологи изучили миграцию бабочек монархов",
            ]
            for idx, text_val in enumerate(distinct_facts):
                fact = Fact(
                    id=f"fact-tombstone-{idx}",
                    fact=text_val,
                    date="2026-08-02",
                    importance=5,
                    confidence=0.9,
                    source="test",
                    status="active",
                )
                store.add_fact(fact)

            initial_total = store.vector.index.ntotal
            assert initial_total >= 20
            assert len(store.vector._deleted_ids) == 0

            # Delete 1 fact (1/43 ~ 2.3% <= 10% threshold) -> should add tombstone without rebuilding
            with patch.object(store.vector, "_rebuild_index", wraps=store.vector._rebuild_index) as spy_rebuild:
                store.delete_fact("fact-tombstone-0")
                assert len(store.vector._deleted_ids) == 1
                spy_rebuild.assert_not_called()

            # Now delete 4 more facts (total deleted 5/43 ~ 11.6% > 10% threshold) -> should trigger rebuild
            with patch.object(store.vector, "_rebuild_index", wraps=store.vector._rebuild_index) as spy_rebuild:
                for d_idx in range(1, 5):
                    store.delete_fact(f"fact-tombstone-{d_idx}")
                spy_rebuild.assert_called()
                assert len(store.vector._deleted_ids) == 0
        finally:
            store.db.close()


def test_gc_routes_through_memory_store(tmp_path) -> None:
    """Verify MemoryGarbageCollector routes through store.archive_fact, keeping FAISS and EventBus in sync."""
    from companion.learning_engine import MemoryGarbageCollector
    from companion.memory.events.base import FactUpdatedEvent

    cfg.DATA_DIR = str(tmp_path)
    cfg.SQLITE_PATH = str(tmp_path / "companion_gc.db")

    with patch("companion.memory.vector_index._embed_texts", side_effect=_mock_embed):
        store = MemoryStore()
        events_received: list[FactUpdatedEvent] = []

        def _on_fact_updated(event: FactUpdatedEvent) -> None:
            events_received.append(event)

        store.event_bus.subscribe(FactUpdatedEvent, _on_fact_updated)
        try:
            fact = Fact(
                id="fact-gc-test",
                fact="Этот факт должен быть удален сборщиком мусора из индекса",
                date="2026-08-02",
                importance=1,
                confidence=0.1,
                source="test",
                status="active",
            )
            store.add_fact(fact)

            # Verify it can be found in semantic search
            found_before = store.vector.search("сборщиком мусора")
            assert any(r["content"] == fact.fact for r in found_before), "Fact should be present in FAISS before GC"

            cand = {
                "id": "fact-gc-test",
                "confidence": 0.1,
                "references_count": 0,
            }
            rep = MemoryGarbageCollector.collect([cand], store=store)
            assert rep.archived_count == 1
            assert "fact-gc-test" in rep.archived_ids

            # 1. DB status should be 'archived'
            db_fact = store.get_fact("fact-gc-test")
            assert db_fact is not None
            assert db_fact.status == "archived"

            # 2. FAISS should no longer return this fact
            found_after = store.vector.search("сборщиком мусора")
            assert not any(r["content"] == fact.fact for r in found_after), "Archived fact must be removed from FAISS"

            # 3. EventBus should have emitted a FactUpdatedEvent
            # (async bus: wait for the worker to drain queued events first)
            store.event_bus.flush(timeout=5.0)
            assert any(
                e.fact_id == "fact-gc-test" and e.new_state.get("status") == "archived"
                for e in events_received
            ), "GC archival must publish FactUpdatedEvent on EventBus"
        finally:
            store.db.close()


def test_index_sync_service_idempotence(tmp_path) -> None:
    """Verify IndexSyncService skips embeddings already present in FAISS (hash_to_id) without duplicate work."""
    from companion.memory.events.base import FactCreatedEvent

    cfg.DATA_DIR = str(tmp_path)
    cfg.SQLITE_PATH = str(tmp_path / "companion_idempotence.db")

    with patch("companion.memory.vector_index._embed_texts", side_effect=_mock_embed):
        store = MemoryStore()
        try:
            # Add a fact directly using compute_and_cache so it is in FAISS
            text_existing = "Этот факт уже в индексе FAISS и не должен дублироваться"
            store.vector.compute_and_cache(text_existing, content_type="fact", fact_id="f-exist")
            initial_count = store.index_sync.added_count

            # Now publish FactCreatedEvent for the same text
            store.event_bus.publish(FactCreatedEvent(
                fact_id="f-exist",
                fact_text=text_existing,
                importance=5,
            ))

            # Verify IndexSyncService did NOT increment added_count
            store.event_bus.flush(timeout=5.0)
            assert store.index_sync.added_count == initial_count, "IndexSyncService must skip existing hash in FAISS"

            # Publish FactCreatedEvent for a new text NOT in FAISS
            text_new = "Новый факт, отсутствующий в индексе FAISS"
            store.event_bus.publish(FactCreatedEvent(
                fact_id="f-new",
                fact_text=text_new,
                importance=5,
            ))

            # Verify IndexSyncService incremented added_count by 1
            store.event_bus.flush(timeout=5.0)
            assert store.index_sync.added_count == initial_count + 1, "IndexSyncService must embed missing facts"
        finally:
            store.db.close()



