"""Unit tests for Memory Lifecycle State Machine (FactStatus)."""
from __future__ import annotations

import pytest

from companion.exceptions import InvalidStateTransitionError
from companion.memory.lifecycle import FactStatus, can_transition, validate_transition


def test_fact_status_transitions() -> None:
    # Valid transitions from DRAFT
    assert can_transition(FactStatus.DRAFT, FactStatus.ACTIVE) is True
    assert can_transition("draft", "active") is True
    assert can_transition("draft", "archived") is False

    # Valid transitions from ACTIVE
    assert can_transition(FactStatus.ACTIVE, FactStatus.ARCHIVED) is True
    assert can_transition(FactStatus.ACTIVE, FactStatus.SUPERSEDED) is True
    assert can_transition(FactStatus.ACTIVE, FactStatus.PINNED) is True

    # Valid transitions from PINNED
    assert can_transition(FactStatus.PINNED, FactStatus.ACTIVE) is True
    assert can_transition(FactStatus.PINNED, FactStatus.SUPERSEDED) is True
    assert can_transition(FactStatus.PINNED, FactStatus.ARCHIVED) is False

    # Valid transitions from ARCHIVED
    assert can_transition(FactStatus.ARCHIVED, FactStatus.ACTIVE) is True
    assert can_transition(FactStatus.ARCHIVED, FactStatus.PURGED) is True

    # Terminal state PURGED
    assert can_transition(FactStatus.PURGED, FactStatus.ACTIVE) is False
    assert can_transition(FactStatus.PURGED, FactStatus.ARCHIVED) is False
    assert can_transition(FactStatus.PURGED, FactStatus.PURGED) is True  # No-op


def test_validate_transition_raises() -> None:
    validate_transition(FactStatus.ACTIVE, FactStatus.ARCHIVED)

    with pytest.raises(InvalidStateTransitionError) as exc_info:
        validate_transition(FactStatus.PURGED, FactStatus.ACTIVE)

    assert exc_info.value.old_state == "purged"
    assert exc_info.value.new_state == "active"
