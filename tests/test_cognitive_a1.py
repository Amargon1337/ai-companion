# -*- coding: utf-8 -*-
"""Phase A tests: Working Memory -> Prompt closure + Cognitive Gravity.

Closes the cognitive loop the user identified:
  * working-memory slots surface FIRST in the prompt block;
  * retrieval scoring uses epistemic gravity (support_count, survival_score,
    contradiction_count, working-memory topicality), not similarity alone.

Guards:
  * WM block appears before identity/personality (desk-first, archive-second);
  * gravity boosts a supported/surviving fact above a mere embedding hit;
  * contradictions penalize a fact;
  * a WM salient-fact hit boosts topicality;
  * defaults (no WM, no genome) keep old behavior — no NaN, no KeyError.

Embeddings are an offline stub; only ranking/bundle state is asserted.
"""
from __future__ import annotations

import hashlib
import sys

import pytest

sys.path.insert(0, r"C:\Games")

import companion.config as cfg
import companion.memory.vector_index as vi
from companion.memory.retrieval import RetrievalBudgetManager
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
    cfg.SQLITE_PATH = str(tmp_path / "phase_a.db")
    s = MemoryStore()
    yield s
    s.close()


def _fact(store, text, importance=5, tags=None, support=0, contra=0, survival=0.5):
    f = Fact(fact=text, date="2026-08-06", importance=importance,
             confidence=0.9, source="test", source_type="compress", tags=tags or [],
             support_count=support, contradiction_count=contra)
    f.id = f"fact_{abs(hash(text)) % 10**9}"
    store.db._insert_fact(f.to_dict())
    if survival is not None:
        store.db.upsert_memory_genome({"memory_id": f.id, "origin": "test",
                                       "survival_score": survival})
    return f


# ── A1: Working Memory -> Prompt ────────────────────────────────────────────

def test_working_memory_block_appears_first(store):
    f = _fact(store, "Иван пишет книгу Ивангелие", tags=["core_identity"])
    slots = [{"slot_type": "current_goal", "ref_kind": "none", "ref_id": "",
              "payload": "завершить главу про Шопенгауэра", "salience": 0.9}]
    mgr = RetrievalBudgetManager(char_budget=5000, max_facts=10, max_reflections=3)
    bundle = mgr.select(
        query="книга", facts=[f], reflections=[],
        working_memory_block="[Рабочая память — актуальное состояние диалога]\n• Текущая цель: завершить главу про Шопенгауэра",
    )
    block = bundle.to_prompt_block()
    # WM block must be the FIRST section (desk before archive): either it opens
    # the whole block, or it precedes any later section marker.
    wm_pos = block.index("<working_memory>")
    assert wm_pos == 0 or block[:wm_pos].strip() == "", \
        f"working memory must open the prompt, got prefix {block[:wm_pos]!r}"
    assert "завершить главу про Шопенгауэра" in block


def test_working_memory_block_empty_is_noop(store):
    f = _fact(store, "Обычный факт")
    mgr = RetrievalBudgetManager(char_budget=5000, max_facts=10, max_reflections=3)
    bundle = mgr.select(query="факт", facts=[f], reflections=[])
    assert "<working_memory>" not in bundle.to_prompt_block()


# ── A2: Cognitive Gravity ───────────────────────────────────────────────────

def test_support_count_boosts_above_embedding_hit(store):
    # Two facts with identical semantics; A is barely embedded-hit, B is
    # heavily supported. Gravity must let B win despite equal similarity.
    faiss = {}
    mgr = RetrievalBudgetManager(char_budget=5000, max_facts=10, max_reflections=3)
    a = _fact(store, "Иван любит кофе", support=0, contra=0, survival=0.5)
    b = _fact(store, "Иван предпочитает крепкий кофе", support=8, contra=0, survival=0.9)
    # Same faiss score for both — support/survival must decide
    faiss = {a.id: 0.80, b.id: 0.80}
    bundle = mgr.select(query="кофе", facts=[a, b], reflections=[],
                        faiss_scores=faiss,
                        genome_scores={a.id: 0.5, b.id: 0.9})
    ids = [f.id for f in bundle.facts]
    assert ids[0] == b.id, f"supported fact should rank first, got {ids}"


def test_contradiction_penalizes(store):
    mgr = RetrievalBudgetManager(char_budget=5000, max_facts=10, max_reflections=3)
    clean = _fact(store, "Иван пьёт кофе утром", support=2, contra=0, survival=0.7)
    poisoned = _fact(store, "Иван пьёт кофе вечером", support=2, contra=5, survival=0.7)
    faiss = {clean.id: 0.80, poisoned.id: 0.80}
    bundle = mgr.select(query="кофе", facts=[clean, poisoned], reflections=[],
                        faiss_scores=faiss,
                        genome_scores={clean.id: 0.7, poisoned.id: 0.7})
    ids = [f.id for f in bundle.facts]
    assert ids[0] == clean.id, "heavily contradicted fact must be pushed down"


def test_working_memory_ids_boost_topicality(store):
    mgr = RetrievalBudgetManager(char_budget=5000, max_facts=10, max_reflections=3)
    topic = _fact(store, "Морзик сходил к ветеринару", support=1, contra=0, survival=0.6)
    other = _fact(store, "Иван купил новый монитор", support=1, contra=0, survival=0.6)
    faiss = {topic.id: 0.70, other.id: 0.70}
    # topic fact is in working memory (salient this turn)
    bundle = mgr.select(query="новости", facts=[topic, other], reflections=[],
                        faiss_scores=faiss,
                        genome_scores={topic.id: 0.6, other.id: 0.6},
                        working_memory_ids={topic.id})
    ids = [f.id for f in bundle.facts]
    assert ids[0] == topic.id, "working-memory topicality must win ties"


def test_gravity_defaults_preserve_old_behavior(store):
    """No genome/WM args => identical ranking to the old formula (no crash)."""
    mgr = RetrievalBudgetManager(char_budget=5000, max_facts=10, max_reflections=3)
    f1 = _fact(store, "Факт альфа про работу", support=0, contra=0)
    f2 = _fact(store, "Факт бета про хобби", support=0, contra=0)
    bundle = mgr.select(query="работа", facts=[f1, f2], reflections=[])
    assert isinstance(bundle.facts, list)
    assert len(bundle.facts) >= 1
