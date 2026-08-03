"""Tests for Stage 5: Mutation Log."""
import os
import tempfile

import pytest

from companion.storage.sqlite_db import MemoryDatabase


def test_memory_mutation_log() -> None:
    with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as tf:
        db_path = tf.name
    try:
        db = MemoryDatabase(db_path)

        mut_id = db.log_mutation(
            entity_id="fact_123",
            action="DECAY",
            reason="retrieved_but_unused",
            state_before={"importance": 5},
            state_after={"importance": 4},
            initiator="feedback_loop",
        )
        assert mut_id.startswith("mut_")

        mutations = db.list_mutations(entity_id="fact_123")
        assert len(mutations) == 1
        assert mutations[0]["id"] == mut_id
        assert mutations[0]["action"] == "DECAY"
        assert mutations[0]["reason"] == "retrieved_but_unused"
        assert mutations[0]["state_before"] == {"importance": 5}
        assert mutations[0]["state_after"] == {"importance": 4}
        assert mutations[0]["initiator"] == "feedback_loop"

        # Query without entity_id
        all_mutations = db.list_mutations()
        assert len(all_mutations) == 1
    finally:
        if os.path.exists(db_path):
            try:
                os.unlink(db_path)
            except OSError:
                pass


def test_archive_audit_log_detaches_after_failure() -> None:
    """A failed rotation must not leak the ATTACH — otherwise every later call
    dies with 'database archive is already in use' until the process restarts."""
    import sqlite3

    tmp_dir = tempfile.mkdtemp()
    db_path = os.path.join(tmp_dir, "companion.db")
    archive_path = os.path.join(tmp_dir, "audit_archive.db")
    db = MemoryDatabase(db_path)

    # Pre-create an incompatible archive table so the INSERT..SELECT fails.
    bad = sqlite3.connect(archive_path)
    bad.execute("CREATE TABLE audit_log(only_one_column TEXT)")
    bad.commit()
    bad.close()

    db.conn.execute(
        "INSERT INTO audit_log(table_name, record_id, action, timestamp) "
        "VALUES('facts', 'r1', 'INSERT', datetime('now', '-60 days'))"
    )
    db.conn.commit()

    with pytest.raises(sqlite3.OperationalError):
        db.archive_audit_log(30)

    # Nothing was lost: the source rows survive a failed rotation.
    assert db.conn.execute("SELECT COUNT(*) FROM audit_log").fetchone()[0] == 1

    # Remove the cause; the same connection must still be usable.
    fix = sqlite3.connect(archive_path)
    fix.execute("DROP TABLE audit_log")
    fix.commit()
    fix.close()

    assert db.archive_audit_log(30) == 1
    assert db.conn.execute("SELECT COUNT(*) FROM audit_log").fetchone()[0] == 0


def test_archive_audit_log_refuses_inside_atomic_transaction() -> None:
    """ATTACH is illegal inside a transaction; the guard must reject it."""
    import sqlite3

    tmp_dir = tempfile.mkdtemp()
    db = MemoryDatabase(os.path.join(tmp_dir, "companion.db"))

    with pytest.raises(sqlite3.OperationalError, match="atomic_memory_transaction"):
        with db.atomic_memory_transaction():
            db.archive_audit_log(30)


def test_insert_fact_persists_superseded_by() -> None:
    """superseded_by was missing from the INSERT column list, so supersede
    provenance was silently dropped on every insert."""
    tmp_dir = tempfile.mkdtemp()
    db = MemoryDatabase(os.path.join(tmp_dir, "companion.db"))

    db._insert_fact({"id": "f1", "fact": "old wording", "superseded_by": "fact_newer"})
    assert db.get_fact("f1")["superseded_by"] == "fact_newer"

    db._insert_fact({"id": "f2", "fact": "plain fact"})
    assert db.get_fact("f2")["superseded_by"] == ""
