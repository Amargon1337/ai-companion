"""Tests for the migration framework."""
from __future__ import annotations

import sqlite3

import pytest

from companion.migrations.runner import Migration, discover_migrations, run_migrations
from companion.storage.sqlite_db import MemoryDatabase


def test_discover_migrations_finds_001(tmp_path):
    """Migration discovery should find 001_baseline."""
    migrations = discover_migrations()
    assert len(migrations) >= 1
    assert migrations[0].version == 1
    assert "baseline" in migrations[0].description.lower() or "Baseline" in migrations[0].description


def test_run_migrations_applies_pending(tmp_path):
    """run_migrations should apply all pending migrations."""
    db_path = str(tmp_path / "test.db")
    db = MemoryDatabase(db_path)
    try:
        # Set version to 0 to force all migrations to run
        db.set_meta("schema_version", "0")
        
        stats = run_migrations(db)
        assert stats["applied"] >= 1
        assert stats["current_version"] >= 1
        
        # Running again should apply nothing
        stats2 = run_migrations(db)
        assert stats2["applied"] == 0
    finally:
        db.close()


def test_migration_idempotent(tmp_path):
    """Running migrations twice should be safe (idempotent)."""
    db_path = str(tmp_path / "test.db")
    db = MemoryDatabase(db_path)
    try:
        db.set_meta("schema_version", "0")
        
        # Run once
        stats1 = run_migrations(db)
        v1 = stats1["current_version"]
        
        # Run again
        stats2 = run_migrations(db)
        v2 = stats2["current_version"]
        
        # Version should not change
        assert v1 == v2
        assert stats2["applied"] == 0
    finally:
        db.close()


def test_migration_sets_version_in_meta(tmp_path):
    """After migration, schema_version in meta should match."""
    db_path = str(tmp_path / "test.db")
    db = MemoryDatabase(db_path)
    try:
        db.set_meta("schema_version", "0")
        run_migrations(db)
        
        version = db.get_meta("schema_version", "0")
        assert int(version) >= 1
    finally:
        db.close()
