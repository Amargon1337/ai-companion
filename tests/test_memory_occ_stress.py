"""Stress tests for Optimistic Concurrency Control (OCC) across memory tables (Stage 8 / Phase 1.6)."""
from __future__ import annotations

import os
import tempfile
import pytest

from companion.models import Fact
from companion.storage.sqlite_db import MemoryDatabase
from companion.exceptions import ConcurrentModificationError


def test_occ_concurrent_fact_update() -> None:
    with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as tf:
        db_path = tf.name

    db = MemoryDatabase(db_path)
    try:
        f = Fact(id="f-occ-1", fact="Python 3.12", date="2026-07-29", importance=5, confidence=0.9, source="msg", status="active")
        db.batch_insert_facts([f.to_dict()])

        # Thread A reads version 1
        read_a = db.get_fact("f-occ-1")
        assert read_a is not None
        assert read_a["version"] == 1

        # Thread B reads version 1
        read_b = db.get_fact("f-occ-1")
        assert read_b is not None
        assert read_b["version"] == 1

        # Thread A updates successfully (version 1 -> 2)
        db.update_fact_fields("f-occ-1", {"importance": 7}, expected_version=read_a["version"])

        verify_a = db.get_fact("f-occ-1")
        assert verify_a is not None
        assert verify_a["version"] == 2
        assert verify_a["importance"] == 7

        # Thread B attempts to update with stale version 1 -> should fail
        with pytest.raises(ConcurrentModificationError) as exc_info:
            db.update_fact_fields("f-occ-1", {"importance": 9}, expected_version=read_b["version"])

        assert exc_info.value.expected_version == 1
        assert exc_info.value.actual_version == 2
    finally:
        db.close()
        try:
            os.unlink(db_path)
        except OSError:
            pass


def test_occ_concurrent_belief_update() -> None:
    with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as tf:
        db_path = tf.name

    db = MemoryDatabase(db_path)
    try:
        belief_row = {
            "id": "b-occ-1",
            "belief": "AI should be transparent",
            "based_on": ["f-1"],
            "importance": 8,
            "status": "active",
            "created_at": "2026-07-29T00:00:00",
        }
        db.batch_insert_beliefs([belief_row])

        read_a = db.get_belief("b-occ-1")
        assert read_a is not None
        assert read_a["version"] == 1

        read_b = db.get_belief("b-occ-1")
        assert read_b is not None
        assert read_b["version"] == 1

        db.update_belief("b-occ-1", {"importance": 9}, expected_version=read_a["version"])

        with pytest.raises(ConcurrentModificationError):
            db.update_belief("b-occ-1", {"importance": 3}, expected_version=read_b["version"])
    finally:
        db.close()
        try:
            os.unlink(db_path)
        except OSError:
            pass
