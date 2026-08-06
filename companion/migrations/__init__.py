"""Database migration framework for Amargon's Void.

Replaces the ad-hoc ALTER TABLE approach in sqlite_db.py with a versioned,
ordered, reversible migration system.

Architecture:
    migrations/
        __init__.py          — exports run_migrations()
        runner.py            — migration executor
        001_initial.py       — baseline schema (extracted from _init_schema)
        002_epistemic.py     — R1 cognitive columns
        ...

Each migration module exposes:
    version: int
    description: str
    def up(conn: sqlite3.Connection) -> None
    def down(conn: sqlite3.Connection) -> None   # optional, may be no-op

The runner:
    1. Reads current version from meta table (key='schema_version')
    2. Applies pending migrations in order
    3. Each migration runs inside its own transaction
    4. On failure: transaction rolls back, migration stops, error logged
    5. Version is updated only after successful commit
"""
from companion.migrations.runner import Migration, run_migrations

__all__ = ["Migration", "run_migrations"]
