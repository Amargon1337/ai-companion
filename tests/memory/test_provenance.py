"""Tests for Memory OS v3 Phase C1 Provenance and C1.5 Event Integrity Hardening."""
import os
import uuid
from datetime import datetime, timezone, timedelta
import pytest

from companion.memory.events import MemoryEvent, MemoryEventType
from companion.memory.store import MemoryStore
from companion.models import (
    Fact,
    MemoryOrigin,
    IdentityLayer,
    MemoryConfidence,
)


@pytest.fixture
def memory_store(tmp_path):
    old_db = os.environ.get("SQLITE_PATH")
    os.environ["SQLITE_PATH"] = str(tmp_path / "test_provenance.db")

    store = MemoryStore()
    yield store

    if old_db:
        os.environ["SQLITE_PATH"] = old_db
    else:
        os.environ.pop("SQLITE_PATH", None)


class TestPhaseC1Provenance:
    """Tests for Phase C1 provenance and confidence features."""

    def test_fact_provenance_and_confidence_storage(self, memory_store):
        fid = str(uuid.uuid4())
        fact = Fact(
            id=fid,
            fact="User enjoys morning runs",
            date=datetime.now(timezone.utc).strftime("%Y-%m-%d"),
            importance=8,
            confidence=0.9,
            source="test",
            origin=MemoryOrigin.USER_STATEMENT,
            source_message_id=42,
            identity_layer=IdentityLayer.STABLE_HABIT,
            conf_observed=1.0,
            conf_inferred=0.9,
            conf_stability=0.95,
            conf_verification=1.0,
        )

        memory_store.add_fact(fact, actor="USER", log_event=True)

        restored = memory_store.get_fact(fid)
        assert restored is not None
        assert restored.origin == MemoryOrigin.USER_STATEMENT
        assert restored.source_message_id == 42
        assert restored.identity_layer == IdentityLayer.STABLE_HABIT
        assert restored.conf_observed == 1.0
        assert restored.conf_inferred == 0.9
        assert restored.conf_stability == 0.95
        assert restored.conf_verification == 1.0

    def test_memory_confidence_total(self):
        conf = MemoryConfidence(
            observed=1.0,
            inferred=0.8,
            stability=0.5,
            verification=1.0,
        )
        assert abs(conf.total - 0.40) < 1e-6


class TestEventIntegrityHardening:
    """Tests for C1.5 Event Integrity Hardening."""

    def test_revive_dormant_fact_emits_event(self, memory_store):
        fid = str(uuid.uuid4())
        fact = Fact(
            id=fid,
            fact="Old fact to become dormant",
            date="2025-01-01",
            importance=3,
            confidence=0.9,
            source="test",
            status="dormant",
            origin=MemoryOrigin.LLM_EXTRACTION,
            identity_layer=IdentityLayer.PREFERENCE,
        )
        memory_store.db._insert_fact(fact.to_dict())

        # Revive dormant fact
        memory_store.revive_dormant_fact(fid)

        revived = memory_store.get_fact(fid)
        assert revived is not None
        assert revived.status == "active"

        # Verify event was logged to EventStore
        events = memory_store.events.get_events_for_aggregate(fid)
        assert len(events) == 1
        ev = events[0]
        assert ev.event_type == MemoryEventType.FACT_STATUS_CHANGED
        assert ev.payload["old_state"]["status"] == "dormant"
        assert ev.payload["new_state"]["status"] == "active"
        assert "status" in ev.payload["changed_fields"]

    def test_apply_importance_decay_emits_events(self, memory_store):
        fid = str(uuid.uuid4())
        old_date = (datetime.now(timezone.utc) - timedelta(days=100)).strftime("%Y-%m-%d")
        fact = Fact(
            id=fid,
            fact="Very old unimportant fact",
            date=old_date,
            importance=2,
            confidence=0.9,
            source="test",
            status="active",
            origin=MemoryOrigin.LLM_EXTRACTION,
            identity_layer=IdentityLayer.TEMPORARY_STATE,
        )
        memory_store.add_fact(fact, actor="SYSTEM", log_event=True)

        decayed_count = memory_store.apply_importance_decay()
        assert decayed_count >= 1

        decayed = memory_store.get_fact(fid)
        assert decayed is not None
        assert decayed.status == "dormant"

        events = memory_store.events.get_events_for_aggregate(fid)
        status_events = [e for e in events if e.event_type == MemoryEventType.FACT_STATUS_CHANGED]
        assert len(status_events) == 1
        ev = status_events[0]
        assert ev.actor == "GOVERNANCE"
        assert ev.payload["old_state"]["status"] == "active"
        assert ev.payload["new_state"]["status"] == "dormant"
        assert "status" in ev.payload["changed_fields"]

    def test_analyze_retrieval_effectiveness_emits_events(self, memory_store):
        fid = str(uuid.uuid4())
        fact = Fact(
            id=fid,
            fact="Fact with high sent count but zero used count",
            date=datetime.now(timezone.utc).strftime("%Y-%m-%d"),
            importance=7,
            confidence=0.9,
            source="test",
            status="active",
        )
        memory_store.add_fact(fact, actor="SYSTEM", log_event=True)

        with memory_store.db._conn() as conn:
            conn.execute(
                "UPDATE facts SET facts_sent_count = 15, facts_used_count = 0 WHERE id = ?",
                (fid,),
            )

        adjusted = memory_store.analyze_retrieval_effectiveness()
        assert adjusted["lowered"] >= 1

        lowered = memory_store.get_fact(fid)
        assert lowered is not None
        assert lowered.importance == 6

        events = memory_store.events.get_events_for_aggregate(fid)
        updated_events = [e for e in events if e.event_type == MemoryEventType.FACT_UPDATED]
        assert len(updated_events) == 1
        ev = updated_events[0]
        assert ev.actor == "RETRIEVAL_FEEDBACK"
        assert ev.payload["old_state"]["importance"] == 7
        assert ev.payload["new_state"]["importance"] == 6
        assert "importance" in ev.payload["changed_fields"]

    def test_replay_events_reconstructs_c1_and_dormant(self, memory_store):
        fid = str(uuid.uuid4())
        fact = Fact(
            id=fid,
            fact="Fact with full provenance to replay",
            date=datetime.now(timezone.utc).strftime("%Y-%m-%d"),
            importance=6,
            confidence=0.9,
            source="test",
            status="active",
            origin=MemoryOrigin.USER_STATEMENT,
            source_message_id=999,
            identity_layer=IdentityLayer.BIOGRAPHICAL,
            conf_observed=1.0,
            conf_inferred=0.9,
            conf_stability=0.95,
            conf_verification=1.0,
        )
        memory_store.add_fact(fact, actor="USER", log_event=True)

        # Replay after creation
        state = memory_store.events.replay_events(fid)
        assert state.get("status") == "active"
        assert state.get("importance") == 6
        assert state.get("origin") == "user_statement"
        assert state.get("source_message_id") == 999
        assert state.get("identity_layer") == "biographical"
        assert state.get("conf_observed") == 1.0

        # Now decay to dormant and replay
        with memory_store.db._conn() as conn:
            conn.execute("UPDATE facts SET date = '2024-01-01', importance = 3 WHERE id = ?", (fid,))
        memory_store.apply_importance_decay()

        state_dormant = memory_store.events.replay_events(fid)
        assert state_dormant.get("status") == "dormant"

        # Now revive and replay
        memory_store.revive_dormant_fact(fid)
        state_revived = memory_store.events.replay_events(fid)
        assert state_revived.get("status") == "active"
