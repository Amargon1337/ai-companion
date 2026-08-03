"""Memory Lifecycle State Machine (FactStatus & transitions)."""
from __future__ import annotations

from enum import Enum
from typing import Any

from companion.exceptions import InvalidStateTransitionError


class FactStatus(str, Enum):
    """Authoritative memory lifecycle statuses for facts.

    Kept in sync with `models.FactStatus` and with what production actually
    writes (store/policies). `aging`/`stale` are NOT here: those describe
    HumanModel insight freshness (see consolidation._items), not fact status.
    """
    QUARANTINE = "quarantine"
    ACTIVE = "active"
    DORMANT = "dormant"
    PENDING_REVIEW = "pending_review"
    ARCHIVED = "archived"
    SUPERSEDED = "superseded"
    PURGED = "purged"

    @classmethod
    def from_str(cls, value: str | FactStatus) -> FactStatus:
        if isinstance(value, cls):
            return value
        val = str(value).lower().strip()
        for member in cls:
            if member.value == val:
                return member
        raise ValueError(f"Unknown FactStatus: {value}")


# Authoritative transition matrix
VALID_TRANSITIONS: dict[FactStatus, set[FactStatus]] = {
    FactStatus.QUARANTINE: {
        FactStatus.ACTIVE,
        FactStatus.PENDING_REVIEW,
        FactStatus.ARCHIVED,
        FactStatus.PURGED,
    },
    FactStatus.PENDING_REVIEW: {
        FactStatus.ACTIVE,
        FactStatus.QUARANTINE,
        FactStatus.ARCHIVED,
        FactStatus.PURGED,
    },
    FactStatus.ACTIVE: {
        FactStatus.QUARANTINE,
        FactStatus.PENDING_REVIEW,
        FactStatus.DORMANT,
        FactStatus.ARCHIVED,
        FactStatus.SUPERSEDED,
        FactStatus.PURGED,
    },
    FactStatus.DORMANT: {
        FactStatus.ACTIVE,
        FactStatus.ARCHIVED,
        FactStatus.SUPERSEDED,
        FactStatus.PURGED,
    },
    FactStatus.ARCHIVED: {
        FactStatus.ACTIVE,
        FactStatus.PURGED,
    },
    FactStatus.SUPERSEDED: {
        FactStatus.ACTIVE,
        FactStatus.PURGED,
    },
    FactStatus.PURGED: set(),  # Terminal state
}


def can_transition(old_state: str | FactStatus, new_state: str | FactStatus) -> bool:
    """Check if transitioning from old_state to new_state is allowed."""
    try:
        old_s = FactStatus.from_str(old_state)
        new_s = FactStatus.from_str(new_state)
    except ValueError:
        return False

    if old_s == new_s:
        return True  # No-op transitions are always allowed

    return new_s in VALID_TRANSITIONS.get(old_s, set())


def validate_transition(old_state: str | FactStatus, new_state: str | FactStatus) -> None:
    """Raise InvalidStateTransitionError if transition is not allowed."""
    if not can_transition(old_state, new_state):
        old_val = old_state.value if isinstance(old_state, FactStatus) else str(old_state)
        new_val = new_state.value if isinstance(new_state, FactStatus) else str(new_state)
        raise InvalidStateTransitionError(old_val, new_val)
