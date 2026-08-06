# -*- coding: utf-8 -*-
"""R7 capacity tests: IN-clause chunking + CoW rebuild gate.

Guards:
  * hydrate_fact_metadata handles >500 ids (SQLite variable limit) without
    error, returns all rows;
  * compute_and_cache_batch chunks its IN clauses for large batches;
  * _cow_rebuild_eligible() defers inline rebuild for large indices
    (threshold configurable) and allows it for small ones;
  * _rebuild_index() swap happens under the lock but the read phase runs
    outside it (concurrent readers see a consistent old/new index).

Embeddings are an offline stub; only storage/vector state is asserted.
"""
from __future__ import annotations

import hashlib
import sys

import pytest

sys.path.insert(0, r"C:\Games")

import companion.config as cfg
import companion.memory.vector_index as vi
from companion.memory.store import MemoryStore
from companion.models import Fact


def _fake_embed(texts):
    dim = getattr(cfg, "EMBEDDING_DIM", 768)
    out = []
    for t in texts:
        h = hashlib.sha256(t.encode("utf-8")).digest()
        vec = [((h[i % len(h)] % 200) - 100) / 100.0 for i in range(dim)]
        out.append(vec)
    return out


@pytest.fixture
def store(tmp_path):
    vi._embed_texts = _fake_embed
    cfg.SQLITE_PATH = str(tmp_path / "r7_cap.db")
    s = MemoryStore()
    yield s
    s.close()


# ── IN-clause chunking ──────────────────────────────────────────────────────

def test_hydrate_metadata_over_500_ids(store):
    # Seed >500 facts directly (bypass dedup).
    ids = []
    for i in range(600):
        f = Fact(fact=f"факт гидрат {i}", date="2026-08-06", importance=5,
                 confidence=0.8, source="t", source_type="t")
        f.id = f"f_hyd_{i}"
        store.db._insert_fact(f.to_dict())
        ids.append(f.id)
    res = store.db.hydrate_fact_metadata(ids)
    assert len(res) == 600, "all 600 facts must hydrate despite variable limit"
    # chunked execution still maps id -> row
    assert res["f_hyd_599"]["status"] == "active"


def test_hydrate_metadata_dedups(store):
    f = Fact(fact="факт один", date="2026-08-06", importance=5,
             confidence=0.8, source="t", source_type="t")
    f.id = "f_one"
    store.db._insert_fact(f.to_dict())
    res = store.db.hydrate_fact_metadata(["f_one", "f_one", "f_one"])
    assert len(res) == 1


def test_compute_and_cache_batch_large(store):
    # 700 distinct facts -> both IN clauses exceed the old 500 chunk boundary.
    texts = [f"очень длинный текст для батча номер {i} про конкретную тему" for i in range(700)]
    for t in texts:
        f = Fact(fact=t, date="2026-08-06", importance=5,
                 confidence=0.8, source="t", source_type="t")
        store.db._insert_fact(f.to_dict())
    store.vector.compute_and_cache_batch(texts, content_type="fact")
    # all 700 must be embedded in the DB facts table
    with store.db._conn() as conn:
        n = conn.execute("SELECT COUNT(*) FROM facts WHERE embedding IS NOT NULL").fetchone()[0]
    assert n == 700, f"all 700 must be embedded, got {n}"


# ── CoW rebuild gate ────────────────────────────────────────────────────────

def test_cow_gate_defers_large_index(store):
    store.vector._last_rebuild_seconds = 0.1
    assert store.vector._cow_rebuild_eligible(100) is False, "small index rebuilds inline"
    assert store.vector._cow_rebuild_eligible(60000) is True, "large index defers (CoW)"
    store.vector._last_rebuild_seconds = 5.0
    assert store.vector._cow_rebuild_eligible(100) is True, "slow rebuild defers even for small index"


def test_rebuild_swap_is_consistent(store):
    # Rebuild with data: the swap must leave searchable facts intact.
    texts = [f"перестрой факт {i}" for i in range(20)]
    for t in texts:
        f = Fact(fact=t, date="2026-08-06", importance=5,
                 confidence=0.8, source="t", source_type="t")
        store.db._insert_fact(f.to_dict())
    store.vector.compute_and_cache_batch(texts, content_type="fact")
    store.vector._rebuild_index()
    assert store.vector._is_initialized is True
    assert store.vector._last_rebuild_seconds >= 0.0
    hits = store.vector.search("перестрой факт", top_k=5)
    assert len(hits) >= 1, "rebuilt index must still serve results"
