"""Tests for Stage 1: Retrieval Analytics (retrieved_count, used_count, timestamps, precision)."""
from datetime import datetime
import os
import tempfile
import pytest

from companion.models import Fact
from companion.storage.sqlite_db import MemoryDatabase


def test_fact_retrieval_analytics_properties() -> None:
    f = Fact(
        id="fact_test_1",
        fact="User likes coffee",
        date="2026-07-28",
        importance=7,
        confidence=0.9,
        source="message",
    )
    assert f.retrieved_count == 0
    assert f.used_count == 0
    assert f.precision == 0.0

    f.retrieved_count = 10
    f.used_count = 3
    f.last_retrieved_at = "2026-07-28T10:00:00"
    f.last_used_at = "2026-07-28T10:05:00"

    assert f.precision == pytest.approx(0.3)
    d = f.to_dict()
    assert d["retrieved_count"] == 10
    assert d["used_count"] == 3
    assert d["precision"] == pytest.approx(0.3)
    assert d["last_retrieved_at"] == "2026-07-28T10:00:00"
    assert d["last_used_at"] == "2026-07-28T10:05:00"

    f2 = Fact.from_dict(d)
    assert f2.retrieved_count == 10
    assert f2.used_count == 3
    assert f2.precision == pytest.approx(0.3)
    assert f2.last_retrieved_at == "2026-07-28T10:00:00"
    assert f2.last_used_at == "2026-07-28T10:05:00"


def test_sqlite_retrieval_analytics_increment() -> None:
    with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as tf:
        db_path = tf.name
    try:
        db = MemoryDatabase(db_path)
        f1 = Fact(
            id="fact_ret_1",
            fact="Fact 1",
            date="2026-07-28",
            importance=5,
            confidence=0.8,
            source="msg",
        )
        f2 = Fact(
            id="fact_ret_2",
            fact="Fact 2",
            date="2026-07-28",
            importance=5,
            confidence=0.8,
            source="msg",
        )
        db.batch_insert_facts([f1.to_dict(), f2.to_dict()])

        # Batch increment: f1 sent and used, f2 sent only
        db.increment_fact_usage_batch(sent_ids=["fact_ret_1", "fact_ret_2"], used_ids=["fact_ret_1"])

        row1 = db.get_fact("fact_ret_1")
        row2 = db.get_fact("fact_ret_2")
        assert row1 is not None and row2 is not None

        assert row1["facts_sent_count"] == 1
        assert row1["facts_used_count"] == 1
        assert row1["last_retrieved_at"] is not None
        assert row1["last_used_at"] is not None

        assert row2["facts_sent_count"] == 1
        assert row2["facts_used_count"] == 0
        assert row2["last_retrieved_at"] is not None
        assert row2["last_used_at"] is None

        # Convert back to Fact object
        fact_obj1 = Fact.from_dict(row1)
        assert fact_obj1.precision == pytest.approx(1.0)
        fact_obj2 = Fact.from_dict(row2)
        assert fact_obj2.precision == pytest.approx(0.0)
    finally:
        if os.path.exists(db_path):
            try:
                os.unlink(db_path)
            except OSError:
                pass
