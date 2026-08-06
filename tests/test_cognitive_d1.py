# -*- coding: utf-8 -*-
"""R7 tests: cognitive timeline read-model over the event journal.

Guards:
  * applied journal events materialize into timeline ticks;
  * event type -> phase mapping (retrieval=interpretation, mutation=decision,
    lifecycle=memory_update);
  * materialize() is idempotent (watermark) — re-running appends nothing;
  * only APPLIED journal rows are materialized;
  * archive_old() flags old ticks, never deletes (Iron Law #5);
  * recent() returns unarchived ticks, newest first.

Embeddings are an offline stub; only journal/timeline state is asserted.
"""
from __future__ import annotations

import hashlib
import sys
from datetime import datetime, timedelta

import pytest

sys.path.insert(0, r"C:\Games")

import companion.config as cfg
import companion.memory.vector_index as vi
from companion.memory.store import MemoryStore


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
    cfg.SQLITE_PATH = str(tmp_path / "r7_tl.db")
    s = MemoryStore()
    yield s
    s.close()


def _seed_journal(store, event_type, payload, applied=True):
    jid = store.db.insert_event_journal(event_type, payload)
    if applied:
        store.db.mark_event_journal_applied(jid)
    return jid


def test_materialize_creates_ticks_with_phase_mapping(store):
    _seed_journal(store, "FactCreatedEvent", '{"fact_id": "f1", "fact_text": "x"}')
    _seed_journal(store, "FactRetrievedEvent", '{"fact_id": "f2"}')
    _seed_journal(store, "MutationAppliedEvent", '{"mutation_id": "m1"}')
    n = store.timeline.materialize()
    assert n == 3
    ticks = store.timeline.recent(limit=10)
    phases = {t["phase"] for t in ticks}
    assert "memory_update" in phases      # FactCreatedEvent
    assert "interpretation" in phases     # FactRetrievedEvent
    assert "decision" in phases           # MutationAppliedEvent
    assert len(ticks) == 3


def test_materialize_idempotent_via_watermark(store):
    _seed_journal(store, "FactCreatedEvent", '{"fact_id": "f1"}')
    assert store.timeline.materialize() == 1
    # second run: watermark advanced -> nothing new
    assert store.timeline.materialize() == 0
    assert len(store.timeline.recent(limit=10)) == 1


def test_materialize_skips_unapplied_events(store):
    _seed_journal(store, "FactCreatedEvent", '{"fact_id": "f1"}', applied=False)
    assert store.timeline.materialize() == 0
    assert store.timeline.recent(limit=10) == []


def test_materialize_unknown_event_falls_back_to_perception(store):
    _seed_journal(store, "WeirdEvent", '{"x": 1}')
    assert store.timeline.materialize() == 1
    ticks = store.timeline.recent(limit=10)
    assert ticks[0]["phase"] == "perception"


def test_archive_old_flags_not_deletes(store):
    _seed_journal(store, "FactCreatedEvent", '{"fact_id": "f_old"}')
    store.timeline.materialize()
    # backdate the tick beyond the retention window
    old = (datetime.now() - timedelta(days=200)).isoformat()
    with store.db._conn() as conn:
        conn.execute("UPDATE cognitive_timeline SET created_at=? WHERE phase='memory_update'", (old,))
    archived = store.timeline.archive_old(retention_days=90)
    assert archived >= 1
    assert store.timeline.recent(limit=10) == []  # excluded from recent
    # row still physically present, just flagged
    with store.db._conn() as conn:
        n = conn.execute("SELECT COUNT(*) FROM cognitive_timeline WHERE archived=1").fetchone()[0]
    assert n >= 1


def test_timeline_ticks_include_payload_hash_and_ref(store):
    _seed_journal(store, "FactCreatedEvent", '{"fact_id": "f_ref", "fact_text": "t"}')
    store.timeline.materialize()
    tick = store.timeline.recent(limit=1)[0]
    assert tick["payload_hash"]
    assert "f_ref" in tick["payload"] or tick["turn_id"].startswith("j")
