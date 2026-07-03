import pytest
import os
from companion.config import SQLITE_PATH
from companion.proactive.telemetry import record_ping_sent, record_ping_reply, get_proactive_stats
from companion.storage.sqlite_db import MemoryDatabase

@pytest.fixture
def clean_db():
    # Use a temporary DB for testing
    test_db_path = "test_telemetry.db"
    if os.path.exists(test_db_path):
        os.remove(test_db_path)
        
    db = MemoryDatabase(test_db_path)
    
    # We must patch MemoryDatabase in telemetry to use this test db,
    # or just rely on the fact that MemoryDatabase without args uses SQLITE_PATH
    # So let's patch the SQLITE_PATH in companion.config
    import companion.config
    old_path = companion.config.SQLITE_PATH
    companion.config.SQLITE_PATH = test_db_path
    
    yield db
    
    companion.config.SQLITE_PATH = old_path
    if os.path.exists(test_db_path):
        try:
            os.remove(test_db_path)
        except:
            pass

def test_record_ping_and_stats(clean_db):
    event_id = record_ping_sent("UNFINISHED_GOAL", "neutral", 80, "How is the goal?")
    assert event_id is not None
    
    stats = get_proactive_stats()
    assert stats["total_sent"] == 1
    assert stats["replied"] == 0
    assert stats["reply_rate"] == 0.0
    assert stats["by_reason"]["UNFINISHED_GOAL"]["sent"] == 1
    assert stats["by_reason"]["UNFINISHED_GOAL"]["replies"] == 0

def test_record_ping_reply(clean_db):
    record_ping_sent("EMOTIONAL_CHECKIN", "depressed", 90, "You okay?")
    record_ping_reply(reply_delay_hours=1.5)
    
    stats = get_proactive_stats()
    assert stats["total_sent"] == 1
    assert stats["replied"] == 1
    assert stats["reply_rate"] == 1.0
    assert stats["by_reason"]["EMOTIONAL_CHECKIN"]["replies"] == 1
