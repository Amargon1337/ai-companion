# -*- coding: utf-8 -*-
"""R4/R6 tests: Sleep Cycle consolidation + Memory Immune System.

Guards:
  * run_sleep_cycle() compresses dormant episodes, decays importance, updates
    genome survival, refreshes ToM/narrative — never crashes on empty data;
  * immune_audit() flags confidence inflation and auto-quarantines it (kept,
    not deleted — Iron Law #5), logs a mutation entry;
  * unseen LLM inferences (never retrieved, old) are flagged;
  * protected anchors are never flagged;
  * everything is deterministic (no LLM) and idempotent.

Embeddings are an offline stub; only consolidation/immune state is asserted.
"""
from __future__ import annotations

import hashlib
import sys

import pytest

sys.path.insert(0, r"C:\Games")

import companion.config as cfg
import companion.memory.vector_index as vi
from companion.memory.immune import immune_audit
from companion.memory.sleep import run_sleep_cycle
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
    cfg.SQLITE_PATH = str(tmp_path / "r46.db")
    s = MemoryStore()
    yield s
    s.close()


# ── Sleep Cycle ─────────────────────────────────────────────────────────────

def test_sleep_cycle_runs_on_empty(store):
    stats = run_sleep_cycle(store)
    assert stats["compressed"] == 0
    assert stats["decayed"] == 0
    assert stats["genome_updated"] == 0


def test_sleep_cycle_updates_genome_survival(store):
    for i in range(3):
        f = Fact(fact=f"sleep fact {i}", date="2026-08-06", importance=5,
                 confidence=0.8, source="test", source_type="compress")
        f.id = f"f_sleep_{i}"
        store.db._insert_fact(f.to_dict())
    stats = run_sleep_cycle(store)
    assert stats["genome_updated"] == 3
    g = store.db.get_memory_genome("f_sleep_0")
    assert g is not None and 0.0 < g["survival_score"] <= 1.0


# ── Immune system ───────────────────────────────────────────────────────────

def test_immune_flags_confidence_inflation(store):
    # Active LLM_INFERENCE with confidence 0.99 and zero support/usage.
    f = Fact(fact="гипотеза без подтверждений", date="2026-08-06",
             importance=5, confidence=0.99, source="test", source_type="compress")
    f.id = "f_inflated"
    d = f.to_dict()
    d["epistemic_class"] = "LLM_INFERENCE"
    store.db._insert_fact(d)
    report = immune_audit(store)
    assert "f_inflated" in report["inflated"]
    assert "f_inflated" in report["quarantined"], "inflation auto-quarantines"
    row = store.db.get_fact("f_inflated")
    assert row["status"] == "quarantine", "quarantined, never deleted"
    # mutation log entry written by the immune response
    muts = store.db.list_mutations(entity_id="f_inflated")
    assert any(m["action"] == "quarantine" for m in muts)


def test_immune_ignores_direct_facts(store):
    f = Fact(fact="Иван пьёт кофе", date="2026-08-06",
             importance=5, confidence=0.99, source="test", source_type="compress")
    f.id = "f_direct"
    d = f.to_dict()
    d["epistemic_class"] = "DIRECT_FACT"
    store.db._insert_fact(d)
    report = immune_audit(store)
    assert "f_direct" not in report["inflated"]


def test_immune_ignores_protected_anchors(store):
    f = Fact(fact="Морзик — пёс Ивана", date="2026-08-06",
             importance=9, confidence=0.99, source="test", source_type="compress",
             memory_kind="permanent", tags=["anchor"])
    f.id = "f_anchor"
    d = f.to_dict()
    d["epistemic_class"] = "LLM_INFERENCE"
    store.db._insert_fact(d)
    report = immune_audit(store)
    assert "f_anchor" not in report["inflated"]
    assert store.db.get_fact("f_anchor")["status"] == "active"


def test_immune_flags_unseen_inferences(store):
    # LLM inference never retrieved and old.
    f = Fact(fact="старая неиспользованная гипотеза", date="2020-01-01",
             importance=5, confidence=0.6, source="test", source_type="compress")
    f.id = "f_unseen"
    d = f.to_dict()
    d["epistemic_class"] = "LLM_INFERENCE"
    store.db._insert_fact(d)
    report = immune_audit(store)
    assert "f_unseen" in report["unseen"]


def test_immune_report_counts(store):
    f = Fact(fact="обычный факт", date="2026-08-06", importance=5,
             confidence=0.8, source="test", source_type="compress")
    f.id = "f_ok"
    store.db._insert_fact(f.to_dict())
    report = immune_audit(store)
    assert report["checked"] >= 1
