"""Regression test for archive_fact OCC."""
import os
os.environ.setdefault('API_TOKEN', 'test:token')
os.environ.setdefault('GOOGLE_API_KEY', 'test_key')
os.environ.setdefault('ADMIN_IDS', '12345')
os.environ.setdefault('LLM_TIMEOUT', '5')
os.environ.setdefault('LLM_RETRIES', '1')

import pytest
import companion.config as cfg

from companion.memory.store import MemoryStore
from companion.models import Fact
from companion.exceptions import ConcurrentModificationError


def test_archive_fact_occ_blocks_concurrent_modification(tmp_path, monkeypatch):
    """Verify that archive_fact passes expected_version to update_fact_fields.
    
    If another operation changes the fact between archive_fact's read and
    update_fact_fields, the CAS should fail with ConcurrentModificationError.
    """
    monkeypatch.setattr(cfg, "DATA_DIR", str(tmp_path))
    monkeypatch.setattr(cfg, "SQLITE_PATH", str(tmp_path / "test_archive_occ.db"))
    
    store = MemoryStore()
    store.vector.embeddings_enabled = False
    
    store.add_fact(Fact(
        id="f-occ-1", fact="Test fact for OCC archive",
        date="2026-08-02", importance=5, confidence=0.9,
        source="test", status="active",
    ))
    
    # Read the fact to get stale version
    stale = store.get_fact("f-occ-1")
    stale_version = stale.version
    
    # Simulate another thread updating the fact (incrementing version)
    store.db.update_fact_fields("f-occ-1", {"importance": 8}, expected_version=stale_version)
    
    # Now call update_fact_fields directly with the stale version to verify OCC kicks in
    with pytest.raises(ConcurrentModificationError):
        store.db.update_fact_fields("f-occ-1", {"status": "archived"}, expected_version=stale_version)
    
    store.db.close()


def test_archive_fact_passes_expected_version(tmp_path, monkeypatch):
    """Verify archive_fact implementation passes expected_version to update_fact_fields.
    
    Uses a mock to intercept the update_fact_fields call and check expected_version.
    """
    import unittest.mock
    
    monkeypatch.setattr(cfg, "DATA_DIR", str(tmp_path))
    monkeypatch.setattr(cfg, "SQLITE_PATH", str(tmp_path / "test_archive_occ_pass.db"))
    
    store = MemoryStore()
    store.vector.embeddings_enabled = False
    
    store.add_fact(Fact(
        id="f-occ-2", fact="Test fact for OCC archive 2",
        date="2026-08-02", importance=5, confidence=0.9,
        source="test", status="active",
    ))
    
    # Mock update_fact_fields to capture expected_version
    original = store.db.update_fact_fields
    captured = {}
    
    def mock_update(fact_id, fields, expected_version=None):
        captured["expected_version"] = expected_version
        return original(fact_id, fields, expected_version=expected_version)
    
    with unittest.mock.patch.object(store.db, "update_fact_fields", side_effect=mock_update):
        store.archive_fact("f-occ-2", reason="test")
    
    # Verify expected_version was passed (not None)
    assert "expected_version" in captured, "archive_fact did not pass expected_version to update_fact_fields"
    assert captured["expected_version"] is not None, "expected_version was None"
    
    store.db.close()
