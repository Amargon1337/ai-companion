"""Migration runner — applies versioned schema migrations in order.

Usage:
    from companion.migrations import run_migrations
    run_migrations(db)  # db is a MemoryDatabase instance

The runner discovers all migration modules in the migrations/ package,
sorts them by version, and applies any that haven't been applied yet.

Thread safety: The runner acquires db._lock before applying migrations,
so concurrent migration attempts from different threads are serialized.
"""
from __future__ import annotations

import importlib
import logging
import pkgutil
import sqlite3
from dataclasses import dataclass
from typing import Any, Callable

logger = logging.getLogger(__name__)


@dataclass
class Migration:
    """A single schema migration.

    Attributes:
        version: Sequential version number (1, 2, 3, ...).
        description: Human-readable description of what this migration does.
        up: Function that applies the migration (takes sqlite3.Connection).
        down: Function that reverses the migration (optional, may be None).
    """
    version: int
    description: str
    up: Callable[[sqlite3.Connection], None]
    down: Callable[[sqlite3.Connection], None] | None = None


def discover_migrations() -> list[Migration]:
    """Auto-discover all migration modules in this package.

    Migration modules must be named NNN_description.py (e.g., 001_initial.py)
    and expose: version, description, up(conn), and optionally down(conn).
    """
    import companion.migrations as pkg
    migrations: list[Migration] = []

    for importer, modname, ispkg in pkgutil.iter_modules(pkg.__path__):
        if ispkg or not modname[0:3].isdigit():
            continue
        try:
            mod = importlib.import_module(f"companion.migrations.{modname}")
            if hasattr(mod, "version") and hasattr(mod, "up"):
                migrations.append(Migration(
                    version=mod.version,
                    description=getattr(mod, "description", modname),
                    up=mod.up,
                    down=getattr(mod, "down", None),
                ))
        except Exception as exc:
            logger.error("Failed to load migration module %s: %s", modname, exc)

    migrations.sort(key=lambda m: m.version)
    return migrations


def run_migrations(db: Any) -> dict[str, int]:
    """Apply all pending migrations in order.

    Args:
        db: MemoryDatabase instance (must have _conn() and get_meta/set_meta).

    Returns:
        Dict with 'applied' (count of migrations applied) and 'current_version'.
    """
    stats = {"applied": 0, "current_version": 0, "skipped": 0}

    # Read current version
    current_version = int(db.get_meta("schema_version", "0") or "0")
    stats["current_version"] = current_version

    # Discover all migrations
    all_migrations = discover_migrations()
    if not all_migrations:
        logger.info("No migration modules found.")
        return stats

    # Apply pending migrations
    for migration in all_migrations:
        if migration.version <= current_version:
            stats["skipped"] += 1
            continue

        logger.info(
            "Applying migration %d: %s",
            migration.version,
            migration.description,
        )
        try:
            with db.atomic_memory_transaction():
                # The transaction yields a sqlite3.Connection
                # We pass it to the migration's up() function
                conn = db.conn  # type: ignore[attr-defined]
                migration.up(conn)
                # Update version INSIDE the same transaction
                conn.execute(
                    "INSERT INTO meta(key, value) VALUES('schema_version', ?) "
                    "ON CONFLICT(key) DO UPDATE SET value=excluded.value",
                    (str(migration.version),),
                )

            stats["applied"] += 1
            stats["current_version"] = migration.version
            logger.info("Migration %d applied successfully.", migration.version)

        except Exception as exc:
            logger.error(
                "Migration %d FAILED: %s. Database unchanged (transaction rolled back).",
                migration.version,
                exc,
                exc_info=True,
            )
            # Stop applying further migrations — they may depend on this one
            raise

    if stats["applied"] == 0:
        logger.info("Schema is up to date (version %d).", current_version)
    else:
        logger.info(
            "Applied %d migration(s). Schema version: %d",
            stats["applied"],
            stats["current_version"],
        )

    return stats
