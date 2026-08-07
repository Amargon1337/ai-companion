"""Migration 002: add optimistic-concurrency version to episodes.

Older databases were created before episode updates used the generic OCC
writer.  SQLite supports ADD COLUMN for this compatible change.
"""
from __future__ import annotations

import sqlite3

version = 2
description = "Add version column to episodes for optimistic concurrency"


def up(conn: sqlite3.Connection) -> None:
    columns = {row[1] for row in conn.execute("PRAGMA table_info(episodes)").fetchall()}
    if "version" not in columns:
        conn.execute("ALTER TABLE episodes ADD COLUMN version INTEGER DEFAULT 1")


def down(conn: sqlite3.Connection) -> None:
    # SQLite cannot safely drop a column without rebuilding a live table.
    return None
