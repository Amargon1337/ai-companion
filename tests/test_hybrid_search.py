"""Tests for Hybrid Search (BM25 + FAISS + RRF) in companion/memory/vector_index.py."""
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


def test_bm25_search_exact_keyword(tmp_path):
    """Test that BM25 search finds facts by exact keyword matches via FTS5."""
    original_data_dir = cfg.DATA_DIR
    original_sqlite = cfg.SQLITE_PATH
    cfg.DATA_DIR = str(tmp_path)
    cfg.SQLITE_PATH = str(tmp_path / "companion.db")

    try:
        store = MemoryStore()
        store.vector.embeddings_enabled = True
        with patch("companion.memory.vector_index._embed_texts", side_effect=_mock_embed):
            fact1 = Fact(
                id="hybrid_fact1",
                fact="У Ивана есть проект Амбиция и цель на пятницу",
                date="2026-07-01",
                importance=8,
                confidence=0.9,
                source="test",
                source_type="test",
                memory_kind="event",
                tags=["work"],
                status="active",
            )
            fact2 = Fact(
                id="hybrid_fact2",
                fact="Иван любит готовить пиццу с сыром и томатами",
                date="2026-07-02",
                importance=5,
                confidence=0.9,
                source="test",
                source_type="test",
                memory_kind="event",
                tags=["hobby"],
                status="active",
            )
            store.add_fact(fact1)
            store.add_fact(fact2)

            bm25_results = store.vector.search_bm25("пиццу", top_k=5, content_type="fact")
            assert len(bm25_results) >= 1
            assert any("пиццу" in r["content"] for r in bm25_results)
            assert "bm25_score" in bm25_results[0]
            assert "bm25_rank" in bm25_results[0]
    finally:
        cfg.DATA_DIR = original_data_dir
        cfg.SQLITE_PATH = original_sqlite


def test_rrf_hybrid_fusion(tmp_path):
    """Test that search_hybrid combines vector search and BM25 search via RRF."""
    original_data_dir = cfg.DATA_DIR
    original_sqlite = cfg.SQLITE_PATH
    cfg.DATA_DIR = str(tmp_path)
    cfg.SQLITE_PATH = str(tmp_path / "companion.db")

    try:
        store = MemoryStore()
        store.vector.embeddings_enabled = True
        with patch("companion.memory.vector_index._embed_texts", side_effect=_mock_embed):
            fact1 = Fact(
                id="hybrid_rrf_fact1",
                fact="СпецифическоеСловоКотороеЕстьТолькоТут и важная информация",
                date="2026-07-01",
                importance=9,
                confidence=0.9,
                source="test",
                source_type="test",
                memory_kind="event",
                tags=["special"],
                status="active",
            )
            fact2 = Fact(
                id="hybrid_rrf_fact2",
                fact="Другая запись о погоде в городе Москве",
                date="2026-07-02",
                importance=5,
                confidence=0.9,
                source="test",
                source_type="test",
                memory_kind="event",
                tags=["weather"],
                status="active",
            )
            store.add_fact(fact1)
            store.add_fact(fact2)

            hybrid_results = store.vector.search_hybrid(
                "СпецифическоеСловоКотороеЕстьТолькоТут", top_k=5, content_type="fact"
            )
            assert len(hybrid_results) >= 1
            top_hit = hybrid_results[0]
            assert "СпецифическоеСловоКотороеЕстьТолькоТут" in top_hit["content"]
            assert "score" in top_hit
            assert "rrf_score" in top_hit
            assert "vector_score" in top_hit
            assert top_hit["bm25_rank"] is not None
    finally:
        cfg.DATA_DIR = original_data_dir
        cfg.SQLITE_PATH = original_sqlite


def test_search_default_hybrid(tmp_path):
    """Test that search() defaults to hybrid=True and supports hybrid=False."""
    original_data_dir = cfg.DATA_DIR
    original_sqlite = cfg.SQLITE_PATH
    cfg.DATA_DIR = str(tmp_path)
    cfg.SQLITE_PATH = str(tmp_path / "companion.db")

    try:
        store = MemoryStore()
        store.vector.embeddings_enabled = True
        with patch("companion.memory.vector_index._embed_texts", side_effect=_mock_embed):
            fact = Fact(
                id="hybrid_default_fact",
                fact="Тестирование флага hybrid в поиске",
                date="2026-07-01",
                importance=7,
                confidence=0.9,
                source="test",
                source_type="test",
                memory_kind="event",
                tags=["test"],
                status="active",
            )
            store.add_fact(fact)

            # hybrid=True
            res_hybrid = store.vector.search("флага hybrid", top_k=5, content_type="fact", hybrid=True)
            assert len(res_hybrid) >= 1
            assert "rrf_score" in res_hybrid[0]

            # hybrid=False
            res_vector_only = store.vector.search("флага hybrid", top_k=5, content_type="fact", hybrid=False)
            assert len(res_vector_only) >= 1
            assert "rrf_score" not in res_vector_only[0]
    finally:
        cfg.DATA_DIR = original_data_dir
        cfg.SQLITE_PATH = original_sqlite


def test_hyde_in_search_facts_vector_search(tmp_path):
    """Test that MemoryStore.search_facts uses HyDE generated fact for vector search when triggered."""
    original_data_dir = cfg.DATA_DIR
    original_sqlite = cfg.SQLITE_PATH
    cfg.DATA_DIR = str(tmp_path)
    cfg.SQLITE_PATH = str(tmp_path / "companion.db")

    try:
        store = MemoryStore()
        store.vector.embeddings_enabled = True
        with patch("companion.memory.vector_index._embed_texts", side_effect=_mock_embed), \
             patch("companion.llm.client.oneshot", return_value="Иван чувствует выгорание из-за работы и проектов") as mock_llm, \
             patch.object(store.vector, "search", wraps=store.vector.search) as mock_vector_search:
            fact = Fact(
                id="hybrid_hyde_fact",
                fact="Иван чувствует выгорание из-за работы и проектов",
                date="2026-07-01",
                importance=8,
                confidence=0.9,
                source="test",
                source_type="test",
                memory_kind="state",
                tags=["work", "state"],
                status="active",
            )
            store.add_fact(fact)

            # 1) Short/emotional query -> should trigger HyDE
            query = "Почему у меня нет сил на проекты?"
            res = store.search_facts(query, limit=5)

            mock_llm.assert_called_once()
            assert mock_vector_search.call_count >= 1
            for call_item in mock_vector_search.call_args_list:
                assert call_item[0][0] == "Иван чувствует выгорание из-за работы и проектов"
            assert len(res) >= 1

            mock_llm.reset_mock()
            mock_vector_search.reset_mock()

            # 2) Long non-emotional query -> should NOT trigger HyDE
            long_query = "Где Иван хранит резервные копии базы данных проектов в системе?"
            _ = store.search_facts(long_query, limit=5)

            mock_llm.assert_not_called()
            assert mock_vector_search.call_count >= 1
            for call_item in mock_vector_search.call_args_list:
                assert call_item[0][0] == long_query

    finally:
        cfg.DATA_DIR = original_data_dir
        cfg.SQLITE_PATH = original_sqlite

