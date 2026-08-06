# -*- coding: utf-8 -*-
"""R6 tests: Theory of Mind (layered read-model) + Narrative Identity Engine.

Guards:
  * ToM L1 derives from facts mentioning an entity (via entity_mentions);
  * ToM L2 derives from active patterns;
  * ToM L3 is ONLY produced from an explicit reflected statement (never
    auto-derived — no mind-reading);
  * refresh() supersedes stale claims, keeps them (Iron Law #5: no delete);
  * TTL: aged claims are excluded from active_for();
  * Narrative arcs cluster episodes/transitions/patterns by keyword, derived
    each read (no authoritative narrative storage);
  * arcs carry importance + event_count and sort by them.

Embeddings are an offline stub; only derived-model state is asserted.
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
from companion.models import Fact, Episode, Pattern


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
    cfg.SQLITE_PATH = str(tmp_path / "r6.db")
    s = MemoryStore()
    yield s
    s.close()


# ── Theory of Mind ──────────────────────────────────────────────────────────

def test_tom_level1_from_fact_mention(store):
    # Add an entity + a fact mentioning it, then refresh L1 for that entity.
    ent_id = "ent_ivan"
    store.db.upsert_world_entity({"entity_id": ent_id, "name": "Иван",
                                  "type": "person", "importance": 1.0})
    f = Fact(fact="Иван написал трек в Reaper", date="2026-08-06",
             importance=7, confidence=0.9, source="test", source_type="compress")
    f.id = "f_tom_l1"
    store.db._insert_fact(f.to_dict())
    store.db.add_entity_mention({"entity_id": ent_id, "fact_id": f.id,
                                 "context_snippet": f.fact[:100]})

    stats = store.tom.refresh(ent_id)
    assert stats["inserted"] >= 1
    claims = store.tom.active_for(ent_id, level=1)
    assert any(c["claim"] == f.fact for c in claims)
    assert all(c["level"] == 1 for c in claims)


def test_tom_level2_from_patterns(store):
    store.db.upsert_world_entity({"entity_id": "ent_ivan", "name": "Иван",
                                  "type": "person", "importance": 1.0})
    store.add_pattern(Pattern(pattern="Иван справляется со стрессом через музыку",
                              category="coping", confidence=0.8, importance=7))
    stats = store.tom.refresh("ent_ivan")
    assert stats["inserted"] >= 1
    claims = store.tom.active_for("ent_ivan", level=2)
    assert any("музык" in c["claim"] for c in claims)


def test_tom_level3_requires_explicit_input(store):
    # No auto-derived L3 — must pass a reflected statement.
    assert store.tom.build_level3("ent_ivan", "") is None
    claim = store.tom.build_level3("ent_ivan", "Иван думает, что я не до конца понимаю его проект",
                                   confidence=0.4)
    assert claim is not None and claim["level"] == 3
    assert claim["confidence"] <= 0.7  # meta-perception stays low-confidence


def test_tom_refresh_supersedes_stale(store):
    ent = "ent_ivan"
    store.db.upsert_world_entity({"entity_id": ent, "name": "Иван",
                                  "type": "person", "importance": 1.0})
    store.db.insert_tom_claim({"subject_entity_id": ent, "level": 1,
                               "claim": "старое наблюдение", "confidence": 0.8,
                               "basis_ids": ["f-x"], "created_at": "2026-01-01T00:00:00"})
    # refresh with no fresh L1/L2 for this entity -> stale claim superseded
    stats = store.tom.refresh(ent)
    assert stats["superseded"] >= 1
    active = store.tom.active_for(ent)
    assert all(c["claim"] != "старое наблюдение" for c in active)
    # row still exists (superseded, not deleted)
    all_rows = store.db.list_tom_claims(ent, status="superseded")
    assert any(c["claim"] == "старое наблюдение" for c in all_rows)


def test_tom_ttl_excludes_aged_claims(store):
    ent = "ent_aged"
    store.db.upsert_world_entity({"entity_id": ent, "name": "Старая сущность",
                                  "type": "concept", "importance": 0.5})
    store.db.insert_tom_claim({"subject_entity_id": ent, "level": 3,
                               "claim": "старое мета-восприятие", "confidence": 0.5,
                               "basis_ids": [], "created_at": "2025-01-01T00:00:00"})
    # L3 TTL = 30 days; 2025-01-01 is way past -> excluded from active_for
    active = store.tom.active_for(ent)
    assert all(c["claim"] != "старое мета-восприятие" for c in active)


def test_tom_prompt_block_sections(store):
    ent = "ent_ivan"
    store.db.upsert_world_entity({"entity_id": ent, "name": "Иван",
                                  "type": "person", "importance": 1.0})
    f = Fact(fact="Иван играет на гитаре", date="2026-08-06",
             importance=7, confidence=0.9, source="test", source_type="compress")
    f.id = "f_tom_pb"
    store.db._insert_fact(f.to_dict())
    store.db.add_entity_mention({"entity_id": ent, "fact_id": f.id,
                                 "context_snippet": f.fact[:100]})
    store.tom.refresh(ent)
    block = store.tom.to_prompt_block(ent)
    assert "[ToM L1" in block


# ── Narrative Identity Engine ───────────────────────────────────────────────

def test_narrative_clusters_episodes(store):
    store.db.upsert_episode({
        "id": "ep_music", "title": "Написал трек в Reaper",
        "narrative": "Иван весь вечер сводил новый бит", "date": "2026-08-06",
        "participants": [], "emotions": {}, "lesson": "",
        "fact_ids": [], "fact_id": "", "importance": 8, "confidence": 0.9,
        "created_at": "2026-08-06T00:00:00",
    })
    arcs = store.narrative.build_arcs()
    music = [a for a in arcs if a["arc"] == "музыка"]
    assert music, "episode about Reaper must cluster into the 'музыка' arc"
    assert music[0]["event_count"] == 1
    assert music[0]["importance"] == 8


def test_narrative_prompt_block(store):
    store.db.upsert_episode({
        "id": "ep_work", "title": "Прошёл собеседование",
        "narrative": "Иван прошёл собеседование на QA", "date": "2026-08-06",
        "participants": [], "emotions": {}, "lesson": "",
        "fact_ids": [], "fact_id": "", "importance": 9, "confidence": 0.9,
        "created_at": "2026-08-06T00:00:00",
    })
    block = store.narrative.to_prompt_block()
    assert "[Нарративные арки" in block
    assert "карьера" in block.lower() or "карьера" in block


def test_narrative_empty_when_no_data(store):
    assert store.narrative.to_prompt_block() == ""
