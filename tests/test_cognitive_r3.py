# -*- coding: utf-8 -*-
"""R3 cognitive kernel invariants: working memory (K5).

Protects the OMNI blueprint contracts for the bounded live-context layer:
  * slot types are constrained (current_goal / active_identity / open_question
    / salient_fact / affective_state);
  * upsert is idempotent per (user, slot_type, ref_id) — no duplicate rows;
  * expired slots FLIP archived (Iron Law #5: nothing is deleted);
  * the per-user live-slot cap (50) is enforced by evicting lowest salience;
  * build_context wiring is safe to fail (guarded, non-fatal).

Embeddings are an offline stub; only DB state is asserted.
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
    cfg.SQLITE_PATH = str(tmp_path / "r3.db")
    s = MemoryStore()
    yield s
    s.close()


def _add(store, text, importance=5, tags=None):
    f = Fact(fact=text, date="2026-08-06", importance=importance,
             confidence=0.8, source="test", source_type="compress", tags=tags or [])
    return store.add_fact(f)


def _snapshot(store, uid=1):
    return store.db.list_live_working_memory_slots(uid, limit=200)


# ── slot derivation & persistence ───────────────────────────────────────────

def test_update_from_turn_writes_goal_and_mood(store):
    store.working_memory.update_from_turn(
        user_id=1,
        mood_state={"anxiety": 0.7, "energy": 0.2},
        captured_goal="пройти собеседование",
    )
    slots = _snapshot(store)
    types = {s["slot_type"] for s in slots}
    assert "current_goal" in types
    assert "affective_state" in types
    goal = [s for s in slots if s["slot_type"] == "current_goal"][0]
    assert goal["payload"] == "пройти собеседование"
    assert goal["salience"] == pytest.approx(0.9)


def test_salient_facts_are_score_gated(store):
    f1 = _add(store, "Факт про морзика который важен")
    f2 = _add(store, "Второй факт про работу")
    # low score fact must be skipped
    store.working_memory.update_from_turn(
        user_id=1,
        top_facts=[(f1, 0.9), (f2, 0.1)],
    )
    slots = _snapshot(store)
    salient = [s for s in slots if s["slot_type"] == "salient_fact"]
    assert any(s["ref_id"] == f1.id for s in salient)
    assert not any(s["ref_id"] == f2.id for s in salient)


def test_active_identity_derived_from_tags(store):
    f = _add(store, "Иван — создатель бота", tags=["core_identity"])
    store.working_memory.update_from_turn(user_id=1, top_facts=[(f, 0.8)])
    slots = _snapshot(store)
    assert any(s["slot_type"] == "active_identity" and s["ref_id"] == f.id for s in slots)


# ── upsert idempotency ──────────────────────────────────────────────────────

def test_upsert_is_idempotent_per_slot(store):
    store.working_memory.update_from_turn(user_id=1, captured_goal="цель одна")
    store.working_memory.update_from_turn(user_id=1, captured_goal="цель одна")
    rows = store.db.list_live_working_memory_slots(1, limit=200)
    goals = [r for r in rows if r["slot_type"] == "current_goal"]
    assert len(goals) == 1, "re-mentioning the same goal must refresh, not duplicate"


def test_upsert_refreshes_freshness(store):
    store.working_memory.update_from_turn(user_id=1, captured_goal="цель с ttl")
    before = _snapshot(store)
    goal_before = [s for s in before if s["slot_type"] == "current_goal"][0]
    # advance the clock past the original expiry by rewriting expires_at manually
    store.db.upsert_working_memory_slot(
        user_id=1, slot_type="current_goal", ref_kind="goal", ref_id="",
        payload="цель с ttl", salience=0.9,
        expires_at=(datetime.now() - timedelta(hours=1)).isoformat(),
    )
    # refresh via the service -> expiry renewed to now + TTL
    store.working_memory.update_from_turn(user_id=1, captured_goal="цель с ttl")
    after = _snapshot(store)
    assert len(after) == 1
    assert after[0]["expires_at"] > goal_before["expires_at"]


# ── Iron Law #5: expiry flips archived, never deletes ───────────────────────

def test_expiry_flips_archived_not_deleted(store):
    store.working_memory.update_from_turn(user_id=1, captured_goal="цель протухнет")
    rows = store.db.list_live_working_memory_slots(1, limit=200)
    slot_id = [r for r in rows if r["slot_type"] == "current_goal"][0]["id"]
    # simulate the slot aging out
    store.db.upsert_working_memory_slot(
        user_id=1, slot_type="current_goal", ref_kind="goal", ref_id="",
        payload="цель протухнет", salience=0.9,
        expires_at=(datetime.now() - timedelta(minutes=1)).isoformat(),
    )
    archived = store.db.archive_expired_working_memory()
    assert archived >= 1
    live = store.db.list_live_working_memory_slots(1, limit=200)
    assert all(r["id"] != slot_id for r in live)
    # row still physically present, just flagged
    with store.db._conn() as conn:
        row = conn.execute(
            "SELECT archived FROM cognitive_working_memory WHERE id=?", (slot_id,)
        ).fetchone()
    assert row is not None and row[0] == 1


# ── bounded working set: cap 50 ─────────────────────────────────────────────

def test_cap_enforces_50_live_slots(store):
    # push 60 distinct salient facts
    for i in range(60):
        f = _add(store, f"Салайентный факт номер {i} про контекст")
        store.working_memory.update_from_turn(
            user_id=7, top_facts=[(f, 0.6 + (i % 5) / 10.0)],
        )
    live = store.db.list_live_working_memory_slots(7, limit=500)
    assert len(live) <= 50, f"cap violated: {len(live)} live slots"
    # highest-salience slots survive eviction
    top = sorted(live, key=lambda s: s["salience"], reverse=True)
    assert top[0]["salience"] >= 0.95, "eviction must keep the most salient"


def test_working_memory_isolated_per_user(store):
    store.working_memory.update_from_turn(user_id=1, captured_goal="у пользователя 1")
    store.working_memory.update_from_turn(user_id=2, captured_goal="у пользователя 2")
    assert len(_snapshot(store, uid=1)) == 1
    assert len(_snapshot(store, uid=2)) == 1


# ── snapshot stability ──────────────────────────────────────────────────────

def test_snapshot_returns_live_slots_only(store):
    store.working_memory.update_from_turn(user_id=1, captured_goal="видимая цель")
    snap = store.working_memory.snapshot(1)
    assert all(s["slot_type"] in
               ("current_goal", "active_identity", "open_question",
                "salient_fact", "affective_state") for s in snap)
    assert any(s["slot_type"] == "current_goal" for s in snap)
