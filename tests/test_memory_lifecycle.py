"""Unit tests for Memory Lifecycle State Machine (FactStatus)."""
from __future__ import annotations

import pytest

from companion.exceptions import InvalidStateTransitionError
from companion.memory.lifecycle import FactStatus, can_transition, validate_transition
from companion.storage.sqlite_db import MemoryDatabase


def test_fact_status_transitions() -> None:
    # Valid transitions from QUARANTINE (governor/validation_policy entry point)
    assert can_transition(FactStatus.QUARANTINE, FactStatus.ACTIVE) is True
    assert can_transition("quarantine", "active") is True

    # Valid transitions from ACTIVE
    assert can_transition(FactStatus.ACTIVE, FactStatus.ARCHIVED) is True
    assert can_transition(FactStatus.ACTIVE, FactStatus.SUPERSEDED) is True
    assert can_transition(FactStatus.ACTIVE, FactStatus.DORMANT) is True
    assert can_transition(FactStatus.ACTIVE, FactStatus.PENDING_REVIEW) is True

    # Dormant facts can be revived (store.revive_dormant_fact)
    assert can_transition(FactStatus.DORMANT, FactStatus.ACTIVE) is True

    # Valid transitions from ARCHIVED
    assert can_transition(FactStatus.ARCHIVED, FactStatus.ACTIVE) is True
    assert can_transition(FactStatus.ARCHIVED, FactStatus.PURGED) is True

    # Terminal state PURGED
    assert can_transition(FactStatus.PURGED, FactStatus.ACTIVE) is False
    assert can_transition(FactStatus.PURGED, FactStatus.ARCHIVED) is False
    assert can_transition(FactStatus.PURGED, FactStatus.PURGED) is True  # No-op

    # Unknown statuses are rejected, not silently accepted
    assert can_transition("bogus", "active") is False


def test_lifecycle_vocabulary_matches_models() -> None:
    """The state machine and models.FactStatus must not drift apart.

    They previously disagreed: lifecycle declared draft/pinned/aging/stale
    (never written anywhere) while missing dormant/pending_review/quarantine
    that production actually writes.
    """
    import typing

    from companion.models import FactStatus as ModelFactStatus

    declared = set(typing.get_args(ModelFactStatus))
    machine = {s.value for s in FactStatus}

    # Every status a Fact can carry must be known to the state machine.
    assert declared <= machine, f"unknown to state machine: {sorted(declared - machine)}"
    # purged is the only machine-internal terminal state.
    assert machine - declared == {"purged"}


def test_real_production_transitions_are_valid() -> None:
    """Transitions production performs today must all be legal, so enabling
    enforcement later cannot break working paths."""
    real_transitions = [
        ("active", "superseded"),      # store.py contradiction handling
        ("active", "archived"),        # archive_policy
        ("active", "dormant"),         # store.py:936
        ("active", "quarantine"),      # validation_policy:61
        ("active", "pending_review"),  # llm/pipeline.py injection guard
        ("quarantine", "active"),      # validation_policy:69
        ("dormant", "active"),         # store.revive_dormant_fact
        ("pending_review", "active"),  # manual review approval
    ]
    invalid = [(a, b) for a, b in real_transitions if not can_transition(a, b)]
    assert not invalid, f"production performs transitions the machine rejects: {invalid}"


def test_validate_transition_raises() -> None:
    validate_transition(FactStatus.ACTIVE, FactStatus.ARCHIVED)

    with pytest.raises(InvalidStateTransitionError) as exc_info:
        validate_transition(FactStatus.PURGED, FactStatus.ACTIVE)

    assert exc_info.value.old_state == "purged"
    assert exc_info.value.new_state == "active"


def test_database_rejects_invalid_fact_transition(tmp_path) -> None:
    db = MemoryDatabase(str(tmp_path / "lifecycle.db"))
    try:
        with db._conn() as conn:
            conn.execute(
                "INSERT INTO facts (id, fact, status, version) VALUES (?, ?, ?, ?)",
                ("f-purged", "terminal", "purged", 1),
            )

        with pytest.raises(InvalidStateTransitionError):
            db.update_fact_status("f-purged", "active", expected_version=1)

        row = db.get_fact("f-purged")
        assert row is not None
        assert row["status"] == "purged"
        assert row["version"] == 1
    finally:
        db.close()
