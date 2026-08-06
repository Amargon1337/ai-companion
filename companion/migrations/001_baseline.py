"""Migration 001: Baseline schema marker.

This migration is a no-op that marks the existing schema as version 1.
The actual schema creation still happens in MemoryDatabase._init_schema()
(which handles all existing tables, indexes, triggers, and ALTER TABLE
backward-compatibility).

Future migrations (002+) will use this framework for incremental changes.
"""
from __future__ import annotations

import sqlite3

version = 1
description = "Baseline schema marker (existing schema from _init_schema)"


def up(conn: sqlite3.Connection) -> None:
    """No-op: schema already exists from _init_schema()."""
    pass


def down(conn: sqlite3.Connection) -> None:
    """No-op: cannot reverse initial schema creation."""
    pass
