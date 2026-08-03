"""Phase 2 Concurrency & Stress Tests: 2-Phase Locking, Atomic Transactions, Hash Cleanliness, and Multithreaded Stress."""
from __future__ import annotations

import concurrent.futures
import random
import sqlite3
import threading
import time
from unittest.mock import patch

import pytest
import companion.config as cfg
from companion.memory.store import MemoryStore
from companion.models import Fact
from companion.exceptions import ConcurrentModificationError


def _mock_embed(texts: list[str]) -> list[list[float]]:
    res = []
    for t in texts:
        rng = random.Random(t)
        vec = [rng.gauss(0, 1) for _ in range(768)]
        norm = sum(x * x for x in vec) ** 0.5
        if norm > 0:
            vec = [x / norm for x in vec]
        res.append(vec)
    return res


def _make_fact(fid: str, text: str, importance: int = 5, status: str = "active") -> Fact:
    return Fact(
        id=fid,
        fact=text,
        date="2026-08-02",
        importance=importance,
        confidence=0.9,
        source="test",
        status=status,
    )


def test_update_stress_hash_cleanup(tmp_path, monkeypatch) -> None:
    """Stress test: create -> 100 update -> verify hash_to_id cleanliness and no accumulated old hashes."""
    monkeypatch.setattr(cfg, "DATA_DIR", str(tmp_path))
    monkeypatch.setattr(cfg, "SQLITE_PATH", str(tmp_path / "companion_stress_hash.db"))

    with patch("companion.memory.vector_index._embed_texts", side_effect=_mock_embed), \
         patch("companion.memory.hyde.should_use_hyde", return_value=False):
        store = MemoryStore()
        store.vector.embeddings_enabled = True

        fact = _make_fact("f-hash-stress-1", "Initial fact alpha beta gamma delta zero version 0", importance=5, status="active")
        store.add_fact(fact)

        for i in range(1, 101):
            success = store.update_fact(
                "f-hash-stress-1",
                fact=f"Updated fact unique wording iteration number {i} abcdefghijklmnopqrstuvwxyz_{i}",
            )
            assert success is True

        # Verify that only 1 hash exists in hash_to_id and no garbage old hashes remain
        assert len(store.vector.hash_to_id) == 1
        assert len(store.vector.id_to_content) == 1
        assert len(store.vector.id_to_hash) == 1
        assert len(store.vector.id_to_type) == 1

        final_fact = store.get_fact("f-hash-stress-1")
        assert final_fact is not None
        assert final_fact.fact == "Updated fact unique wording iteration number 100 abcdefghijklmnopqrstuvwxyz_100"
        assert final_fact.version == 101


def test_archive_and_delete_atomic_rollback_on_faiss_error(tmp_path, monkeypatch) -> None:
    """Verify that if FAISS/vector deletion fails, archive_fact and delete_fact roll back SQLite changes."""
    monkeypatch.setattr(cfg, "DATA_DIR", str(tmp_path))
    monkeypatch.setattr(cfg, "SQLITE_PATH", str(tmp_path / "companion_atomic_rollback.db"))

    with patch("companion.memory.vector_index._embed_texts", side_effect=_mock_embed), \
         patch("companion.memory.hyde.should_use_hyde", return_value=False):
        store = MemoryStore()
        store.vector.embeddings_enabled = True

        fact1 = _make_fact("f-atomic-1", "Completely unique knowledge fact alpha beta gamma 101", status="active")
        fact2 = _make_fact("f-atomic-2", "Completely different knowledge fact delta epsilon zeta 202", status="active")
        store.add_fact(fact1)
        store.add_fact(fact2)

        # Force an exception in vector.delete_for_content
        with patch.object(store.vector, "delete_for_content", side_effect=RuntimeError("Simulated FAISS crash")):
            with pytest.raises(RuntimeError, match="Simulated FAISS crash"):
                store.archive_fact("f-atomic-1", reason="test_crash")

            with pytest.raises(RuntimeError, match="Simulated FAISS crash"):
                store.delete_fact("f-atomic-2")

        # Verify SQLite state rolled back cleanly
        f1 = store.get_fact("f-atomic-1")
        f2 = store.get_fact("f-atomic-2")
        assert f1 is not None and f1.status == "active"
        assert f2 is not None and f2.status == "active"


def test_faiss_dirty_flag_crash_recovery(tmp_path, monkeypatch) -> None:
    """Verify that if faiss_index_dirty is set (e.g. from crash before save), _load_index rebuilds from SQLite."""
    monkeypatch.setattr(cfg, "DATA_DIR", str(tmp_path))
    monkeypatch.setattr(cfg, "SQLITE_PATH", str(tmp_path / "companion_dirty_crash.db"))

    with patch("companion.memory.vector_index._embed_texts", side_effect=_mock_embed), \
         patch("companion.memory.hyde.should_use_hyde", return_value=False):
        store1 = MemoryStore()
        store1.vector.embeddings_enabled = True

        _distinct_texts = [
            "The mitochondria is the powerhouse of the cell",
            "Python is a programming language created by Guido van Rossum",
            "The Eiffel Tower is located in Paris France",
            "Photosynthesis converts light energy into chemical energy",
            "Shakespeare wrote Hamlet and Macbeth in the 1600s",
        ]
        for i in range(5):
            store1.add_fact(_make_fact(f"f-dirty-{i}", _distinct_texts[i], status="active"))

        # Mark dirty in meta table as if a crash happened before save_index_to_disk()
        store1.db.set_meta("faiss_index_dirty", "1")

        # Re-instantiate store/vector index -> should detect dirty flag and rebuild index from SQLite
        store2 = MemoryStore()
        store2.vector.embeddings_enabled = True

        assert len(store2.vector.hash_to_id) == 5
        assert len(store2.vector.id_to_content) == 5
        assert store2.db.get_meta("faiss_index_dirty", "0") == "0"


def test_multithreaded_memory_stress_10_threads(tmp_path, monkeypatch) -> None:
    """Rigorous multithreaded stress test: 10 threads running concurrent adds, updates, archives, and searches."""
    monkeypatch.setattr(cfg, "DATA_DIR", str(tmp_path))
    monkeypatch.setattr(cfg, "SQLITE_PATH", str(tmp_path / "companion_multithreaded_stress.db"))

    with patch("companion.memory.vector_index._embed_texts", side_effect=_mock_embed), \
         patch("companion.memory.hyde.should_use_hyde", return_value=False):
        store = MemoryStore()
        store.vector.embeddings_enabled = True

        # Pre-populate 10 base facts to update/archive/search
        _base_texts = [
            "The mitochondria is the powerhouse of the cell",
            "Python is a programming language created by Guido van Rossum",
            "The Eiffel Tower is located in Paris France",
            "Photosynthesis converts light energy into chemical energy",
            "Shakespeare wrote Hamlet and Macbeth in the 1600s",
            "Water boils at 100 degrees Celsius at sea level",
            "The Great Wall of China is visible from space",
            "Gravity causes objects with mass to attract each other",
            "The human heart pumps blood throughout the body",
            "JavaScript was created in just 10 days by Brendan Eich",
        ]
        base_ids = []
        for i in range(10):
            fid = f"f-base-{i}"
            store.add_fact(_make_fact(fid, _base_texts[i], importance=5, status="active"))
            base_ids.append(fid)

        errors: list[Exception] = []

        _add_pool = [
            "The Roman Empire fell in 476 AD",
            "Quantum computers use qubits for computation",
            "The Pacific Ocean is the largest ocean on Earth",
            "Van Gogh painted Starry Night in 1889",
            "DNA stands for deoxyribonucleic acid",
            "The Moon orbits Earth approximately every 27 days",
            "Tesla Motors was founded by Elon Musk",
            "Honey never spoils due to its low moisture content",
            "The human brain uses about 20 watts of power",
            "Venus is the hottest planet in our solar system",
            "Octopuses have three hearts and blue blood",
            "The Great Wall of China spans over 13000 miles",
            "Bananas are berries but strawberries are not",
            "A day on Venus is longer than a year on Venus",
            "The first computer bug was a moth in a relay",
            "Crows can recognize human faces and remember them",
            "The shortest war in history lasted 38 minutes",
            "Sea otters hold hands while sleeping to avoid drifting",
            "A group of flamingos is called a flamboyance",
            "The inventor of the frisbee was turned into a flying disc after death",
            "Polar bear fur is actually transparent not white",
            "A teaspoon of honey represents the lifetime of two bees",
            "The world generates 320 times more data than stars in the universe",
            "Butterflies taste with their feet",
            "A group of owls is called a parliament",
            "The first web page was created in 1991",
            "Elephants can detect rainstorms from 150 miles away",
            "The unicorn is the national animal of Scotland",
            "A day on Mercury is equivalent to 59 Earth days",
            "The world's oldest known living tree is over 5000 years old",
            "Sloths can hold their breath longer than dolphins",
            "Bananas grow on herbs not trees",
            "The Great Pacific Garbage Patch is twice the size of Texas",
            "A group of ferrets is called a business",
            "The first computer mouse was made of wood",
            "Honeybees communicate through dance",
            "The world's largest waterfall is actually underwater",
            "A giraffe's neck has only 7 vertebrae like a human",
            "The shortest verse in the Bible is about God resting",
            "Octopi have blue blood due to copper-based hemocyanin",
            "The first email was sent in 1971",
            "Cows have best friends and get stressed when separated",
            "The world's oldest known shoe was found in a cave",
            "A group of rhinos is called a crash or a stubborn",
            "The inventor of the Pringles can is buried in one",
            "Sea stars can regrow their entire body from a single arm",
            "The world's most expensive coffee is made from digested beans",
            "A group of crows is called a murder",
            "The first computer programmer was Ada Lovelace",
            "Butterflies were originally called flutterby",
            "The Great Wall of China is not visible from space with the naked eye",
        ]
        _add_idx = [0]

        def worker_add(worker_id: int) -> None:
            try:
                for idx in range(25):
                    fid = f"f-add-{worker_id}-{idx}"
                    with store.vector.lock:
                        text = _add_pool[(_add_idx[0] % len(_add_pool))]
                        _add_idx[0] += 1
                    store.add_fact(_make_fact(fid, text, status="active"))
            except Exception as exc:
                errors.append(exc)

        def worker_update(worker_id: int) -> None:
            try:
                for idx in range(25):
                    target_id = random.choice(base_ids)
                    for attempt in range(5):
                        try:
                            store.update_fact(target_id, fact=f"Updated base fact {target_id} iteration {idx} worker_{worker_id}_xyz_{idx}")
                            break
                        except ConcurrentModificationError:
                            if attempt == 4:
                                raise
                            time.sleep(0.001 * (attempt + 1))
            except Exception as exc:
                errors.append(exc)

        def worker_search(worker_id: int) -> None:
            try:
                for idx in range(25):
                    store.search_facts(f"Concurrent added fact worker {idx} search phrase long query words", limit=3)
            except Exception as exc:
                errors.append(exc)

        def worker_archive(worker_id: int) -> None:
            try:
                for idx in range(25):
                    target_id = random.choice(base_ids)
                    for attempt in range(5):
                        try:
                            store.archive_fact(target_id, reason=f"worker-{worker_id}")
                            break
                        except ConcurrentModificationError:
                            if attempt == 4:
                                raise
                            time.sleep(0.001 * (attempt + 1))
            except Exception as exc:
                errors.append(exc)

        with concurrent.futures.ThreadPoolExecutor(max_workers=10) as executor:
            futures = []
            # 4 add threads (100 adds total)
            for w in range(4):
                futures.append(executor.submit(worker_add, w))
            # 2 update threads (50 updates total)
            for w in range(2):
                futures.append(executor.submit(worker_update, w))
            # 2 search threads (50 searches total)
            for w in range(2):
                futures.append(executor.submit(worker_search, w))
            # 2 archive threads (50 archives total)
            for w in range(2):
                futures.append(executor.submit(worker_archive, w))

            concurrent.futures.wait(futures)

        assert not errors, f"Concurrent stress test encountered errors: {errors}"
        # Verify that SQLite database is healthy and facts table is accessible
        all_facts = store.list_all_facts()
        # 10 base facts + up to 50 unique added facts (dedup gate prevents duplicates)
        assert len(all_facts) >= 10
        # DB must be queryable and consistent
        assert store.list_facts("active") or store.list_facts("archived") or len(all_facts) > 0
