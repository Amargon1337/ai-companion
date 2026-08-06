# -*- coding: utf-8 -*-
"""R2 cognitive kernel invariants: epistemic counters, genome parity,
provenance cycle detection, causal-link provenance.

These protect the Phase 0-6 architecture contracts of the OMNI blueprint:
  * confirm/contradict relations drive support/contradiction tallies;
  * every fact has exactly one memory_genome row (1:1 invariant);
  * provenance graphs are acyclic (or the auditor quarantines members);
  * causal links carry derived_from ids (anti-hallucination provenance).

NOTE: embeddings are a deterministic offline stub; only DB/logic state is
asserted. The four pre-existing pending_embedding failures are unrelated.
"""
from __future__ import annotations

import hashlib
import os
import sqlite3
import sys

import pytest

sys.path.insert(0, r"C:\Games")

import companion.config as cfg
import companion.memory.vector_index as vi
from companion.memory.store import MemoryStore
from companion.memory.consolidation import audit_provenance_cycles, reconcile_genome_parity, compute_homeostasis, homeostasis_sleep_due
from companion.models import Fact, FactRelation
from companion.reasoning import CausalLink, reasoning_engine


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
    cfg.SQLITE_PATH = str(tmp_path / "r2.db")
    s = MemoryStore()
    yield s
    s.close()


def _add(store, text, importance=5):
    f = Fact(fact=text, date="2026-08-05", importance=importance,
             confidence=0.8, source="test", source_type="compress")
    return store.add_fact(f)


def _db(store):
    c = sqlite3.connect(store.db.path)
    c.row_factory = sqlite3.Row
    return c


# ── K2: epistemic certainty counters ───────────────────────────────────────

def test_confirms_increments_support_count(store):
    target = _add(store, "Иван предпочитает чай")
    source = _add(store, "Иван снова пил чай")
    store.add_relation(FactRelation(from_id=source.id, to_id=target.id,
                                    relation="confirms", reason="repeated"))
    row = store.db.get_fact(target.id)
    assert row["support_count"] == 1
    assert row["contradiction_count"] == 0


def test_contradicts_increments_counter_and_supersedes(store):
    old = _add(store, "Иван живёт в Москве")
    new = _add(store, "Иван живёт в Санкт-Петербурге")
    store.add_relation(FactRelation(from_id=new.id, to_id=old.id,
                                    relation="contradicts", reason="correction"))
    row = store.db.get_fact(old.id)
    assert row["contradiction_count"] == 1
    assert row["status"] == "superseded"  # resolution semantics preserved


# ── K4: genome parity (1:1 invariant) ───────────────────────────────────────

def test_add_fact_creates_genome_row(store):
    f = _add(store, "Genome invariant fact")
    g = store.db.get_memory_genome(f.id)
    assert g is not None
    assert g["origin"] == "test"
    assert g["generation"] == 0


def test_delete_fact_removes_genome_row(store):
    f = _add(store, "Delete me with genome")
    assert store.db.get_memory_genome(f.id) is not None
    assert store.delete_fact(f.id) is True
    assert store.db.get_memory_genome(f.id) is None
    assert store.db.get_fact(f.id) is None


def test_reconcile_genome_parity_backfills(store):
    # bypass add_fact to simulate a pre-R2 fact without a genome row
    raw = {
        "id": "pre-r2-fact", "fact": "Legacy fact", "date": "2026-01-01",
        "created_at": "2026-01-01T00:00:00", "status": "active",
        "importance": 5, "confidence": 0.8, "source": "legacy",
        "tags": "[]", "evidence": "[]", "meta": "{}",
    }
    store.db._insert_fact(raw)
    assert store.db.get_memory_genome("pre-r2-fact") is None
    res = reconcile_genome_parity(store)
    assert res["backfilled"] >= 1
    assert store.db.get_memory_genome("pre-r2-fact") is not None


# ── K3: provenance cycle detection ──────────────────────────────────────────

def test_audit_provenance_cycles_detects_circular_derivation(store):
    a = _add(store, "A supports B")
    b = _add(store, "B supports A")
    store.add_relation(FactRelation(from_id=a.id, to_id=b.id,
                                    relation="supports", reason="cycle"))
    store.add_relation(FactRelation(from_id=b.id, to_id=a.id,
                                    relation="supports", reason="cycle"))
    cycles = audit_provenance_cycles(store)
    assert cycles, "expected a provenance cycle to be detected"


def test_audit_provenance_cycles_no_false_positive(store):
    a = _add(store, "Root fact")
    b = _add(store, "Derived from root")
    c = _add(store, "Derived from b")
    store.add_relation(FactRelation(from_id=a.id, to_id=b.id, relation="supports", reason="r"))
    store.add_relation(FactRelation(from_id=b.id, to_id=c.id, relation="supports", reason="r"))
    assert audit_provenance_cycles(store) == []


# ── causal provenance ───────────────────────────────────────────────────────

def test_causal_link_derived_from_roundtrip(store):
    link = CausalLink(cause="стресс", effect="усталость", confidence=0.8,
                      derived_from=["f-1", "f-2"], method="llm")
    store.db.upsert_causal_link(link.to_dict())
    rows = store.db.list_causal_links()
    assert any(r["link_id"] == link.link_id and r["derived_from"] == ["f-1", "f-2"]
               for r in rows)


def test_causal_link_default_method(store):
    link = CausalLink(cause="a", effect="b", confidence=0.7)
    store.db.upsert_causal_link(link.to_dict())
    rows = store.db.list_causal_links()
    row = [r for r in rows if r["link_id"] == link.link_id][0]
    assert row["method"] == "llm"
    assert row["derived_from"] == []


# ── K8: homeostasis entropy ─────────────────────────────────────────────────

def test_compute_homeostasis_records_metrics(store):
    _add(store, "healthy active fact 1")
    _add(store, "healthy active fact 2")
    res = compute_homeostasis(store)
    assert "entropy" in res and "ratios" in res
    rows = store.db.list_homeostasis_metrics(limit=1)
    assert len(rows) == 1
    assert 0.0 <= rows[0]["entropy_score"] <= 1.0
    assert rows[0]["measured_at"]


def test_homeostasis_sleep_due_needs_window(store):
    _add(store, "window fact")
    # one sample is never enough to force sleep
    compute_homeostasis(store)
    assert homeostasis_sleep_due(store, window=3) is False


def test_homeostasis_detects_poisoning(store):
    # Pollution mix: 5 superseded via independent contradiction pairs, 2 stale
    # actives (old dates), 2 quarantined. Ratios reflect real cognitive state
    # and must breach the moving-average trigger.
    for pair in range(5):
        old = _add(store, f"старое утверждение {pair}")
        new = _add(store, f"новое утверждение {pair}")
        store.add_relation(FactRelation(from_id=new.id, to_id=old.id,
                                        relation="contradicts", reason="poison"))
    for i in range(2):
        f = _add(store, f"карантин {i}")
        store.db.update_fact_fields(f.id, {"status": "quarantine"})
    stale_a = _add(store, "старый активный факт A")
    stale_b = _add(store, "старый активный факт B")
    for f in (stale_a, stale_b):
        store.db.update_fact_fields(f.id, {"date": "2020-01-01", "last_retrieved_at": "2020-01-01"})

    res = compute_homeostasis(store)
    # 3 consecutive polluted samples must breach the moving-average trigger
    compute_homeostasis(store)
    compute_homeostasis(store)
    compute_homeostasis(store)
    assert homeostasis_sleep_due(store, window=3) is True, (
        f"expected sleep trigger with entropy trend, got {res['entropy']}")
