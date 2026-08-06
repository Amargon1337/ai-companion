"""Tests for Memory OS v3 Phase C0: Event Sourcing Foundation."""
import pytest
import uuid
from datetime import datetime

from companion.memory.events import MemoryEvent, MemoryEventType
from companion.memory.event_store import EventStore
from companion.memory.store import MemoryStore
from companion.models import Fact


class TestMemoryEvent:
    """Tests for MemoryEvent dataclass."""
    
    def test_event_creation(self):
        event = MemoryEvent(
            aggregate_id="fact-123",
            event_type=MemoryEventType.FACT_CREATED,
            actor="LLM_EXTRACTOR",
            payload={"content": "User likes coffee", "origin": "USER_DIRECT"},
            metadata={"model_version": "gemini-2.0"},
        )
        
        assert event.aggregate_id == "fact-123"
        assert event.event_type == MemoryEventType.FACT_CREATED
        assert event.actor == "LLM_EXTRACTOR"
        assert event.payload["content"] == "User likes coffee"
        assert event.metadata["model_version"] == "gemini-2.0"
        assert event.id is not None
        assert event.timestamp is not None
    
    def test_event_serialization(self):
        event = MemoryEvent(
            aggregate_id="fact-456",
            event_type=MemoryEventType.FACT_STATUS_CHANGED,
            actor="GOVERNANCE",
            payload={"old_status": "pending", "new_status": "verified"},
        )
        
        d = event.to_dict()
        assert d["aggregate_id"] == "fact-456"
        assert d["event_type"] == "fact_status_changed"
        assert "old_status" in d["payload"]
        
        # Deserialize
        restored = MemoryEvent.from_row(d)
        assert restored.aggregate_id == event.aggregate_id
        assert restored.event_type == event.event_type


class TestEventStore:
    """Tests for EventStore."""
    
    @pytest.fixture
    def event_store(self, tmp_path):
        db_path = str(tmp_path / "test_events.db")
        store = EventStore(db_path=db_path)
        yield store
    
    def test_append_event(self, event_store):
        event = MemoryEvent(
            aggregate_id="fact-789",
            event_type=MemoryEventType.FACT_CREATED,
            actor="TEST",
            payload={"test": "data"},
        )
        
        event_store.append(event)
        
        # Retrieve
        events = event_store.get_events_for_aggregate("fact-789")
        assert len(events) == 1
        assert events[0].event_type == MemoryEventType.FACT_CREATED
    
    def test_append_batch(self, event_store):
        events = [
            MemoryEvent(
                aggregate_id="fact-batch",
                event_type=MemoryEventType.FACT_CREATED,
                actor="TEST",
                payload={"idx": i},
            )
            for i in range(5)
        ]
        
        event_store.append_batch(events)
        
        retrieved = event_store.get_events_for_aggregate("fact-batch")
        assert len(retrieved) == 5
    
    def test_get_history(self, event_store):
        # Create a sequence of events
        event_store.append(MemoryEvent(
            aggregate_id="fact-life",
            event_type=MemoryEventType.FACT_CREATED,
            actor="TEST",
            payload={"status": "active"},
        ))
        event_store.append(MemoryEvent(
            aggregate_id="fact-life",
            event_type=MemoryEventType.FACT_STATUS_CHANGED,
            actor="TEST",
            payload={"new_status": "quarantined"},
        ))
        
        history = event_store.get_history("fact-life")
        assert len(history) == 2
        assert history[0].event_type == MemoryEventType.FACT_CREATED
        assert history[1].event_type == MemoryEventType.FACT_STATUS_CHANGED
    
    def test_replay_events(self, event_store):
        # Simulate fact lifecycle
        event_store.append(MemoryEvent(
            aggregate_id="fact-replay",
            event_type=MemoryEventType.FACT_CREATED,
            actor="TEST",
            payload={"content": "Test fact", "importance": 5},
        ))
        event_store.append(MemoryEvent(
            aggregate_id="fact-replay",
            event_type=MemoryEventType.FACT_STATUS_CHANGED,
            actor="TEST",
            payload={"new_status": "superseded"},
        ))
        
        state = event_store.replay_events("fact-replay")
        assert state["content"] == "Test fact"
        assert state["status"] == "superseded"


class TestMemoryStoreWithEvents:
    """Integration tests for MemoryStore with event logging."""
    
    @pytest.fixture
    def memory_store(self, tmp_path):
        import os
        old_db = os.environ.get('SQLITE_PATH')
        os.environ['SQLITE_PATH'] = str(tmp_path / "test_memory.db")
        
        store = MemoryStore()
        yield store
        
        if old_db:
            os.environ['SQLITE_PATH'] = old_db
    
    def test_add_fact_logs_event(self, memory_store):
        fact = Fact(
            id=str(uuid.uuid4()),
            fact="User lives in Berlin",
            date=datetime.utcnow().isoformat(),
            created_at=datetime.utcnow().isoformat(),
            importance=8,
            confidence=0.9,
            source="TEST_USER",
        )
        
        result = memory_store.add_fact(fact, actor="TEST_USER", log_event=True)
        
        assert result.id == fact.id
        
        # Check event was logged
        events = memory_store.events.get_events_for_aggregate(fact.id)
        assert len(events) == 1
        assert events[0].event_type == MemoryEventType.FACT_CREATED
        assert events[0].actor == "TEST_USER"
    
    def test_add_fact_without_logging(self, memory_store):
        fact = Fact(
            id=str(uuid.uuid4()),
            fact="Temporary fact",
            date=datetime.utcnow().isoformat(),
            created_at=datetime.utcnow().isoformat(),
            importance=5,
            confidence=0.7,
            source="TEST",
        )
        
        memory_store.add_fact(fact, log_event=False)
        
        # No events should be logged
        events = memory_store.events.get_events_for_aggregate(fact.id)
        assert len(events) == 0
    
    def test_add_relation_logs_events(self, memory_store):
        # Create two facts
        fact1 = Fact(
            id=str(uuid.uuid4()),
            fact="Old preference",
            date=datetime.utcnow().isoformat(),
            created_at=datetime.utcnow().isoformat(),
            importance=7,
            confidence=0.8,
            source="TEST",
        )
        fact2 = Fact(
            id=str(uuid.uuid4()),
            fact="New preference",
            date=datetime.utcnow().isoformat(),
            created_at=datetime.utcnow().isoformat(),
            importance=8,
            confidence=0.9,
            source="TEST",
        )
        
        memory_store.add_fact(fact1, log_event=True)
        memory_store.add_fact(fact2, log_event=True)
        
        # Create supersedes relation
        from companion.models import FactRelation
        relation = FactRelation(
            id=str(uuid.uuid4()),
            from_id=fact2.id,
            to_id=fact1.id,
            relation="supersedes",
            created_at=datetime.utcnow().isoformat(),
        )
        
        memory_store.add_relation(relation, actor="TEST_ARBITER", log_event=True)
        
        # Check events
        rel_events = memory_store.events.get_events_for_aggregate(relation.id)
        assert len(rel_events) >= 1
        
        status_events = memory_store.events.get_events_for_aggregate(fact1.id)
        assert any(e.event_type == MemoryEventType.FACT_STATUS_CHANGED for e in status_events)
    
    def test_add_reflection_logs_event(self, memory_store):
        from companion.models import Reflection
        
        reflection = Reflection(
            id=str(uuid.uuid4()),
            insight="User tends to be more productive in mornings",
            based_on=[],
            period="week",
            importance=7,
            confidence=0.85,
            created_at=datetime.utcnow().isoformat(),
        )
        
        memory_store.add_reflection(reflection, actor="LLM_PIPELINE", log_event=True)
        
        events = memory_store.events.get_events_for_aggregate(reflection.id)
        assert len(events) == 1
        assert events[0].event_type == MemoryEventType.PATTERN_FORMED


class TestEventSourcingAudit:
    """Test audit trail capabilities."""
    
    @pytest.fixture
    def store_with_history(self, tmp_path):
        import os
        old_db = os.environ.get('SQLITE_PATH')
        os.environ['SQLITE_PATH'] = str(tmp_path / "test_audit.db")
        
        store = MemoryStore()
        
        # Create a fact with full lifecycle
        fact = Fact(
            id="audit-fact-1",
            fact="User started learning Python",
            date=datetime.utcnow().isoformat(),
            created_at=datetime.utcnow().isoformat(),
            importance=8,
            confidence=0.9,
            source="USER_DIRECT",
        )
        store.add_fact(fact, actor="USER_DIRECT", log_event=True)
        
        # Update status
        from companion.models import FactRelation
        new_fact = Fact(
            id="audit-fact-2",
            fact="User completed Python course",
            date=datetime.utcnow().isoformat(),
            created_at=datetime.utcnow().isoformat(),
            importance=9,
            confidence=0.95,
            source="USER_DIRECT",
        )
        store.add_fact(new_fact, actor="USER_DIRECT", log_event=True)
        
        relation = FactRelation(
            id=str(uuid.uuid4()),
            from_id=new_fact.id,
            to_id=fact.id,
            relation="supersedes",
            created_at=datetime.utcnow().isoformat(),
        )
        store.add_relation(relation, actor="CONSOLIDATOR", log_event=True)
        
        yield store, fact.id, new_fact.id
        
        if old_db:
            os.environ['SQLITE_PATH'] = old_db
    
    def test_full_audit_trail(self, store_with_history):
        store, old_fact_id, new_fact_id = store_with_history
        
        # Get complete history of old fact
        history = store.events.get_history(old_fact_id)
        
        # Should have: creation + status change
        event_types = [e.event_type for e in history]
        assert MemoryEventType.FACT_CREATED in event_types
        assert MemoryEventType.FACT_STATUS_CHANGED in event_types
        
        # Verify actors are recorded
        actors = [e.actor for e in history]
        assert "USER_DIRECT" in actors
        assert "CONSOLIDATOR" in actors
    
    def test_event_count(self, store_with_history):
        store, _, _ = store_with_history
        
        total_events = store.events.count_events()
        assert total_events >= 3  # At least 2 creations + 1 relation
