"""Tests for Phase C1.6 Replay Verification Layer."""
from __future__ import annotations

import os
import uuid
import pytest
from datetime import datetime, timezone

from companion.memory.store import MemoryStore
from companion.memory.replay import ProjectionRebuilder
from companion.memory.verification import (
    REQUIRED_REPLAY_FIELDS,
    verify_projection_integrity,
)
from companion.models import Fact, FactRelation, MemoryOrigin, IdentityLayer


@pytest.fixture
def memory_store(tmp_path):
    old_db = os.environ.get("SQLITE_PATH")
    os.environ["SQLITE_PATH"] = str(tmp_path / "test_replay_verif.db")

    store = MemoryStore()
    yield store

    if old_db:
        os.environ["SQLITE_PATH"] = old_db
    else:
        os.environ.pop("SQLITE_PATH", None)


class TestPhaseC16ReplayVerification:
    """Tests for Phase C1.6 read-only Replay Verification Layer."""

    def test_fact_creation_parity(self, memory_store):
        """Test 1: Verification parity immediately after FACT_CREATED."""
        fid = str(uuid.uuid4())
        fact = Fact(
            id=fid,
            fact="User loves Italian espresso",
            date=datetime.now(timezone.utc).strftime("%Y-%m-%d"),
            importance=8,
            confidence=0.95,
            source="user_chat",
            origin=MemoryOrigin.USER_STATEMENT,
            identity_layer=IdentityLayer.PREFERENCE,
        )
        memory_store.add_fact(fact, actor="TEST_ACTOR")

        res = verify_projection_integrity(memory_store)
        assert res.passed is True, f"Parity failed: {res.mismatched} {res.missing}"
        assert len(res.missing) == 0
        assert len(res.mismatched) == 0

    def test_fact_lifecycle_parity(self, memory_store):
        """Test 2: Parity across lifecycle (create -> update -> status change)."""
        fid = str(uuid.uuid4())
        fact = Fact(
            id=fid,
            fact="User enjoys morning jogging",
            date=datetime.now(timezone.utc).strftime("%Y-%m-%d"),
            importance=7,
            confidence=0.9,
            source="user_chat",
        )
        memory_store.add_fact(fact, actor="TEST_ACTOR")

        # Lifecycle changes
        memory_store.analyze_retrieval_effectiveness()
        memory_store.apply_importance_decay()
        memory_store.revive_dormant_fact(fid)

        res = verify_projection_integrity(memory_store)
        assert res.passed is True, f"Parity failed after lifecycle changes: {res.mismatched}"

    def test_provenance_and_confidence_parity(self, memory_store):
        """Test 3: Verify C1 provenance and decomposed confidence parity."""
        fid = str(uuid.uuid4())
        fact = Fact(
            id=fid,
            fact="User works as an AI Architect",
            date="2026-07-31",
            importance=9,
            confidence=0.98,
            source="system_inference",
            origin=MemoryOrigin.LLM_INFERENCE,
            identity_layer=IdentityLayer.CORE_VALUE,
            conf_observed=0.99,
            conf_inferred=0.95,
            conf_stability=0.97,
            conf_verification=1.0,
            source_message_id=42,
        )
        memory_store.add_fact(fact, actor="SYSTEM")

        rebuilder = ProjectionRebuilder(memory_store.events)
        snapshot = rebuilder.build_snapshot()
        assert fid in snapshot

        state = snapshot[fid]
        assert state["origin"] == "llm_inference"
        assert state["identity_layer"] == "core_value"
        assert abs(state["conf_observed"] - 0.99) < 1e-5
        assert state["source_message_id"] == 42

        res = verify_projection_integrity(memory_store)
        assert res.passed is True, f"Provenance parity failed: {res.mismatched}"

    def test_fact_superseding_parity(self, memory_store):
        """Test 4: Verify parity when Fact B supersedes Fact A."""
        fid_a = str(uuid.uuid4())
        fact_a = Fact(
            id=fid_a,
            fact="User lives in Boston",
            date="2025-01-01",
            importance=6,
            confidence=0.9,
            source="user",
        )
        memory_store.add_fact(fact_a, actor="TEST_ACTOR")

        fid_b = str(uuid.uuid4())
        fact_b = Fact(
            id=fid_b,
            fact="User moved to Seattle",
            date="2026-07-31",
            importance=8,
            confidence=0.95,
            source="user",
        )
        memory_store.add_fact(fact_b, actor="TEST_ACTOR")

        # Supersede A with B
        rel = FactRelation(
            from_id=fid_b,
            to_id=fid_a,
            relation="supersedes",
            reason="User relocated",
        )
        memory_store.add_relation(rel)

        res = verify_projection_integrity(memory_store)
        assert res.passed is True, f"Supersede parity failed: {res.mismatched}"

        rebuilder = ProjectionRebuilder(memory_store.events)
        snapshot = rebuilder.build_snapshot()
        assert snapshot[fid_a]["status"] == "superseded"
        assert snapshot[fid_a]["superseded_by"] == fid_b

    def test_required_fields_safeguard(self, memory_store, monkeypatch):
        """Test 5: Safeguard check against missing required replay fields."""
        fid = str(uuid.uuid4())
        fact = Fact(
            id=fid,
            fact="User likes testing safeguards",
            date="2026-07-31",
            importance=5,
            confidence=0.9,
            source="test",
        )
        memory_store.add_fact(fact, actor="TEST_ACTOR")

        # Simulate a bug where 'origin' field is forgotten in event payload during replay
        original_apply = memory_store.events._apply_event
        def fake_apply(state, event):
            state = original_apply(state, event)
            state.pop("origin", None)
            return state

        monkeypatch.setattr(memory_store.events, "_apply_event", fake_apply)

        res = verify_projection_integrity(memory_store)
        assert res.passed is False
        assert len(res.mismatched) > 0
        missing_origin_err = any(
            m["field"] == "origin" and m["replay_value"] == "MISSING_FROM_REPLAY_PAYLOAD"
            for m in res.mismatched
        )
        assert missing_origin_err is True, f"Expected origin missing safeguard error, got {res.mismatched}"
