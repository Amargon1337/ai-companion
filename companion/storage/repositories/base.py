"""Base repository — common SQL helpers shared by all domain repositories."""
from __future__ import annotations

import json
from typing import Any, TYPE_CHECKING

if TYPE_CHECKING:
    from companion.storage.sqlite_db import MemoryDatabase


def _json(value: Any) -> str:
    """Serialize value to JSON, treating None as empty list."""
    return json.dumps(value if value is not None else [], ensure_ascii=False)


def _loads(value: str | None, default: Any = None) -> Any:
    """Deserialize JSON, returning default for None/invalid."""
    if value is None:
        return [] if default is None else default
    try:
        return json.loads(value)
    except (TypeError, json.JSONDecodeError):
        return [] if default is None else default


class BaseRepository:
    """Base class for domain repositories.

    Provides access to the underlying MemoryDatabase connection and
    transaction primitives. Subclasses implement domain-specific CRUD.

    Thread safety: All SQL operations go through MemoryDatabase._lock
    (acquired by _conn() and atomic_memory_transaction()). Repositories
    never hold their own locks.
    """

    def __init__(self, db: MemoryDatabase) -> None:
        self._db = db

    @property
    def db(self) -> MemoryDatabase:
        return self._db
