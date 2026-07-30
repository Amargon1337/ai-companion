"""Custom exceptions for Amargon's Void Companion Memory OS."""
from __future__ import annotations


class MemoryError(Exception):
    """Base class for memory system errors."""


class ConcurrentModificationError(MemoryError):
    """Raised when an optimistic concurrency check fails during a memory UPDATE operation.

    Scenario:
    - Process A reads record with version=5
    - Process B modifies record (version becomes 6)
    - Process A attempts to save expected version=5 -> ConcurrentModificationError raised.
    """
    def __init__(self, message: str, record_id: str | None = None, expected_version: int | None = None, actual_version: int | None = None):
        super().__init__(message)
        self.record_id = record_id
        self.expected_version = expected_version
        self.actual_version = actual_version


class InvalidStateTransitionError(MemoryError):
    """Raised when an invalid memory lifecycle state transition is attempted."""
    def __init__(self, old_state: str, new_state: str, message: str | None = None):
        msg = message or f"Invalid memory state transition from '{old_state}' to '{new_state}'"
        super().__init__(msg)
        self.old_state = old_state
        self.new_state = new_state

