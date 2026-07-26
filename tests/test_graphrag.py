"""Tests for Multi-hop GraphRAG in companion/memory/store.py and retrieval.py."""
from __future__ import annotations

import os
from unittest.mock import patch
import pytest

import companion.config as cfg
from companion.memory.retrieval import RetrievalBudgetManager
from companion.memory.store import MemoryStore
from companion.models import Fact, FactRelation


def _mock_embed(texts):
    import hashlib
    res = []
    for t in texts:
        h = int(hashlib.md5(t.encode("utf-8")).hexdigest()[:8], 16)
        res.append([float((h >> (i % 32)) & 1) + 0.1 for i in range(768)])
    return res


def test_get_connected_facts_bfs(tmp_path):
    """Test that get_connected_facts traverses fact relations via BFS."""
    original_data_dir = cfg.DATA_DIR
    original_sqlite = cfg.SQLITE_PATH
    cfg.DATA_DIR = str(tmp_path)
    cfg.SQLITE_PATH = str(tmp_path / "companion.db")

    try:
        store = MemoryStore()
        store.vector.embeddings_enabled = True
        with patch("companion.memory.vector_index._embed_texts", side_effect=_mock_embed):
            # Anchor fact
            fact1 = Fact(
                id="graph_f1",
                fact="Иван начал учить язык Rust",
                date="2026-07-01",
                importance=8,
                confidence=0.9,
                source="test",
                source_type="test",
                memory_kind="event",
                tags=["coding"],
                status="active",
            )
            # Hop 1 connected fact
            fact2 = Fact(
                id="graph_f2",
                fact="Иван хочет создавать быстрые системные утилиты",
                date="2026-07-02",
                importance=7,
                confidence=0.9,
                source="test",
                source_type="test",
                memory_kind="event",
                tags=["coding"],
                status="active",
            )
            # Hop 2 connected fact
            fact3 = Fact(
                id="graph_f3",
                fact="Иван купил книгу по системному программированию",
                date="2026-07-03",
                importance=6,
                confidence=0.9,
                source="test",
                source_type="test",
                memory_kind="event",
                tags=["books"],
                status="active",
            )
            store.add_fact(fact1)
            store.add_fact(fact2)
            store.add_fact(fact3)

            rel1 = FactRelation(
                from_id=fact1.id,
                to_id=fact2.id,
                relation="causes",
                reason="Rust for utils",
                confidence=0.9,
            )
            rel2 = FactRelation(
                from_id=fact2.id,
                to_id=fact3.id,
                relation="supports",
                reason="book support",
                confidence=0.8,
            )
            store.add_relation(rel1)
            store.add_relation(rel2)

            # Traverse 2 hops from f1
            connected = store.get_connected_facts([fact1.id], max_hops=2, max_facts=10)
            assert len(connected) == 2

            connected_ids = [f.id for f, hop, _ in connected]
            assert "graph_f2" in connected_ids
            assert "graph_f3" in connected_ids

            hops_map = {f.id: hop for f, hop, _ in connected}
            assert hops_map["graph_f2"] == 1
            assert hops_map["graph_f3"] == 2
    finally:
        cfg.DATA_DIR = original_data_dir
        cfg.SQLITE_PATH = original_sqlite


def test_graphrag_retrieval_select_integration(tmp_path):
    """Test that RetrievalBudgetManager.select includes connected facts."""
    original_data_dir = cfg.DATA_DIR
    original_sqlite = cfg.SQLITE_PATH
    cfg.DATA_DIR = str(tmp_path)
    cfg.SQLITE_PATH = str(tmp_path / "companion.db")

    try:
        store = MemoryStore()
        store.vector.embeddings_enabled = True
        with patch("companion.memory.vector_index._embed_texts", side_effect=_mock_embed):
            anchor_fact = Fact(
                id="graphrag_anchor",
                fact="Иван готовит доклад на конференцию разработчиков",
                date="2026-07-01",
                importance=9,
                confidence=0.9,
                source="test",
                source_type="test",
                memory_kind="event",
                tags=["anchor", "work"],
                status="active",
            )
            connected_fact = Fact(
                id="graphrag_connected",
                fact="Тема доклада: асинхронные архитектуры в Python",
                date="2026-07-02",
                importance=7,
                confidence=0.9,
                source="test",
                source_type="test",
                memory_kind="event",
                tags=["work"],
                status="active",
            )
            store.add_fact(anchor_fact)
            store.add_fact(connected_fact)

            rel = FactRelation(
                from_id=anchor_fact.id,
                to_id=connected_fact.id,
                relation="related_to",
                reason="details",
                confidence=0.9,
            )
            store.add_relation(rel)

            retrieval_mgr = RetrievalBudgetManager(store=store)
            bundle = retrieval_mgr.select(
                query="конференцию",
                facts=[anchor_fact, connected_fact],
                reflections=[],
                faiss_scores={anchor_fact.id: 0.9, connected_fact.id: 0.1},
            )

            bundle_ids = [f.id for f in bundle.facts]
            assert "graphrag_anchor" in bundle_ids
            assert "graphrag_connected" in bundle_ids
    finally:
        cfg.DATA_DIR = original_data_dir
        cfg.SQLITE_PATH = original_sqlite


def test_parent_child_unpacking_integration(tmp_path):
    """Test that Parent Summary facts unpack relevant dormant Child Facts via 'summarizes'."""
    original_data_dir = cfg.DATA_DIR
    original_sqlite = cfg.SQLITE_PATH
    cfg.DATA_DIR = str(tmp_path)
    cfg.SQLITE_PATH = str(tmp_path / "companion.db")

    try:
        store = MemoryStore()
        store.vector.embeddings_enabled = True
        with patch("companion.memory.vector_index._embed_texts", side_effect=_mock_embed):
            parent_summary = Fact(
                id="graphrag_summary",
                fact="[Сводка за 2026-07] Иван изучал программирование на Rust",
                date="2026-07",
                importance=8,
                confidence=0.9,
                source="episodic_compression",
                source_type="system",
                memory_kind="summary",
                tags=["episodic_summary", "coding"],
                status="active",
            )
            child_relevant = Fact(
                id="graphrag_child_relevant",
                fact="Иван написал первую программу на языке Rust в июле",
                date="2026-07-05",
                importance=6,
                confidence=0.9,
                source="test",
                source_type="test",
                memory_kind="event",
                tags=["coding", "rust"],
                status="dormant",
            )
            child_irrelevant = Fact(
                id="graphrag_child_irrelevant",
                fact="Иван купил зеленый чай и печенье в магазине",
                date="2026-07-06",
                importance=5,
                confidence=0.9,
                source="test",
                source_type="test",
                memory_kind="event",
                tags=["food"],
                status="dormant",
            )
            store.add_fact(parent_summary)
            store.add_fact(child_relevant)
            store.add_fact(child_irrelevant)

            rel1 = FactRelation(
                from_id=parent_summary.id,
                to_id=child_relevant.id,
                relation="summarizes",
                reason="Episodic compression",
                confidence=0.9,
            )
            rel2 = FactRelation(
                from_id=parent_summary.id,
                to_id=child_irrelevant.id,
                relation="summarizes",
                reason="Episodic compression",
                confidence=0.9,
            )
            store.add_relation(rel1)
            store.add_relation(rel2)

            retrieval_mgr = RetrievalBudgetManager(store=store)
            bundle = retrieval_mgr.select(
                query="программирование на Rust",
                facts=[parent_summary],
                reflections=[],
                faiss_scores={parent_summary.id: 0.9, child_relevant.id: 0.85},
            )

            bundle_ids = [f.id for f in bundle.facts]
            assert "graphrag_summary" in bundle_ids
            assert "graphrag_child_relevant" in bundle_ids
            assert "graphrag_child_irrelevant" not in bundle_ids
    finally:
        cfg.DATA_DIR = original_data_dir
        cfg.SQLITE_PATH = original_sqlite

