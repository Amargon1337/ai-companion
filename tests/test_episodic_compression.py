"""Tests for Episodic Memory Compression (Phase 3)."""
from __future__ import annotations

import os
from unittest.mock import patch
import pytest

import companion.config as cfg
from companion.memory.store import MemoryStore
from companion.models import Fact


def _mock_embed(texts):
    import hashlib
    res = []
    for t in texts:
        h = int(hashlib.md5(t.encode("utf-8")).hexdigest()[:8], 16)
        res.append([float((h >> (i % 32)) & 1) + 0.1 for i in range(768)])
    return res


def test_episodic_memory_compressor(tmp_path):
    """Test that dormant facts are grouped and compressed into summary facts."""
    original_data_dir = cfg.DATA_DIR
    original_sqlite = cfg.SQLITE_PATH
    cfg.DATA_DIR = str(tmp_path)
    cfg.SQLITE_PATH = str(tmp_path / "companion.db")

    try:
        store = MemoryStore()
        store.vector.embeddings_enabled = True
        with patch("companion.memory.vector_index._embed_texts", side_effect=_mock_embed):
            # Create 3 dormant facts in period 2026-05
            f1 = Fact(
                id="dorm_1",
                fact="Иван ездил на выходные на дачу",
                date="2026-05-10",
                importance=3,
                confidence=0.9,
                source="test",
                source_type="test",
                memory_kind="event",
                tags=["weekend"],
                status="dormant",
            )
            f2 = Fact(
                id="dorm_2",
                fact="На даче Иван чинил крышу сарая",
                date="2026-05-11",
                importance=3,
                confidence=0.9,
                source="test",
                source_type="test",
                memory_kind="event",
                tags=["weekend"],
                status="dormant",
            )
            f3 = Fact(
                id="dorm_3",
                fact="Иван жарил шашлыки с друзьями вечером",
                date="2026-05-11",
                importance=3,
                confidence=0.9,
                source="test",
                source_type="test",
                memory_kind="event",
                tags=["weekend"],
                status="dormant",
            )
            store.add_fact(f1)
            store.add_fact(f2)
            store.add_fact(f3)

            # Manually update status to dormant in db because add_fact resets to active if needed
            with store.db._conn() as conn:
                conn.execute("UPDATE facts SET status='dormant' WHERE id IN ('dorm_1', 'dorm_2', 'dorm_3')")

            # Run episodic compression
            summaries = store.compress_dormant_episodes(batch_size=10, min_facts=2)
            assert len(summaries) == 1

            s_fact = summaries[0]
            assert s_fact.memory_kind == "summary"
            assert s_fact.status == "active"
            assert "2026-05" in s_fact.tags
            assert "Эпизод" in s_fact.fact or "Сводка" in s_fact.fact

            # Check that relations were created
            rels = store.db.get_fact_relations(f1.id)
            assert any(r.get("relation") == "summarized_by" and r.get("from_id") == f1.id for r in rels)

            # Check GraphRAG traversal from summary fact to dormant facts
            connected = store.get_connected_facts([s_fact.id], max_hops=1, max_facts=10)
            connected_ids = [f.id for f, _, _ in connected]
            assert "dorm_1" in connected_ids
            assert "dorm_2" in connected_ids
            assert "dorm_3" in connected_ids

            # Second run should not create duplicate summaries for already summarized facts
            summaries_second = store.compress_dormant_episodes(batch_size=10, min_facts=2)
            assert len(summaries_second) == 0
    finally:
        cfg.DATA_DIR = original_data_dir
        cfg.SQLITE_PATH = original_sqlite
