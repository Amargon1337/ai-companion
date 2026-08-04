"""IdentityVault - Phase 2 Memory Preservation system."""
from __future__ import annotations

import logging
import sqlite3
from collections.abc import Generator
from contextlib import closing, contextmanager
from datetime import datetime
from typing import Any


def _configure_conn(conn: sqlite3.Connection) -> None:
    conn.execute("PRAGMA busy_timeout = 5000;")
    conn.execute("PRAGMA journal_mode = WAL;")
    conn.execute("PRAGMA foreign_keys = ON;")

from companion.memory.text_sim import text_overlap

logger = logging.getLogger(__name__)


class IdentityVault:
    ALLOWED_CATEGORIES = {
        "name", "age", "city", "profession", "pet", "partner",
        "diagnosis", "anchor_reason", "core_value",
        "core_identity", "ambitions", "fears", "values", "hobbies", "roles", "core_traits"
    }

    def __init__(self, db_path: str, db: Any | None = None) -> None:
        # Shared-connection mode: route all SQL through the MemoryDatabase's
        # lock + transaction model so identity writes serialize with everything
        # else touching the file. Path-only mode (standalone tests/tools) keeps
        # the old per-call connection behaviour.
        self.db_path = db_path
        self._db = db
        self._init_schema()

    @contextmanager
    def _conn(self) -> Generator[sqlite3.Connection, None, None]:
        if self._db is not None:
            with self._db._conn() as conn:
                yield conn
            return
        with closing(sqlite3.connect(self.db_path)) as conn:
            _configure_conn(conn)
            conn.row_factory = sqlite3.Row
            try:
                yield conn
                conn.commit()
            except Exception:
                conn.rollback()
                raise

    def _init_schema(self) -> None:
        with self._conn() as conn:
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS identity_facts (
                    id INTEGER PRIMARY KEY,
                    category TEXT NOT NULL UNIQUE,
                    value TEXT NOT NULL,
                    confidence FLOAT DEFAULT 1.0,
                    source TEXT,
                    created_at TIMESTAMP,
                    updated_at TIMESTAMP
                )
                """
            )
            # The audit triggers below insert into audit_log, which is owned by
            # MemoryDatabase._init_schema. In shared-connection mode it already
            # exists; standalone mode (no MemoryDatabase ever opened this file)
            # must create a compatible stub or every identity write fails when
            # the trigger fires.
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS audit_log (
                    audit_id INTEGER PRIMARY KEY AUTOINCREMENT,
                    table_name TEXT NOT NULL,
                    record_id TEXT NOT NULL,
                    action TEXT NOT NULL,
                    old_state TEXT,
                    new_state TEXT,
                    timestamp TEXT DEFAULT (datetime('now', 'utc'))
                );
                """
            )
            # Explicit change attribution: the generic audit triggers only record
            # old/new values, not WHO overrode the lock or WHY. identity_change_log
            # captures actor/reason/override_reason for every accepted write.
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS identity_change_log (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    category TEXT NOT NULL,
                    old_value TEXT,
                    new_value TEXT,
                    result TEXT NOT NULL,
                    actor TEXT,
                    reason TEXT,
                    override_reason TEXT,
                    created_at TEXT
                );
                """
            )
            try:
                conn.execute('''
                    CREATE TRIGGER IF NOT EXISTS audit_identity_insert
                    AFTER INSERT ON identity_facts
                    BEGIN
                        INSERT INTO audit_log (table_name, record_id, action, new_state)
                        VALUES ('identity_facts', NEW.category, 'INSERT', json_object('value', NEW.value));
                    END;
                ''')
                conn.execute('''
                    CREATE TRIGGER IF NOT EXISTS audit_identity_update
                    AFTER UPDATE ON identity_facts
                    BEGIN
                        INSERT INTO audit_log (table_name, record_id, action, old_state, new_state)
                        VALUES ('identity_facts', NEW.category, 'UPDATE',
                                json_object('value', OLD.value), json_object('value', NEW.value));
                    END;
                ''')
                conn.execute('''
                    CREATE TRIGGER IF NOT EXISTS audit_identity_delete
                    AFTER DELETE ON identity_facts
                    BEGIN
                        INSERT INTO audit_log (table_name, record_id, action, old_state)
                        VALUES ('identity_facts', OLD.category, 'DELETE', json_object('value', OLD.value));
                    END;
                ''')
            except sqlite3.OperationalError:
                pass

    def should_lock_update(
        self, old_value: str, new_value: str, confidence: float, source: str = ""
    ) -> bool:
        """
        Check if the update should be locked.
        Lock conditions:
        - Same category but conflicting value (overlap < 0.8)
        - Confidence < 0.8 on new value
        - Source is low reliability (e.g., compress, summary)
        """
        if confidence < 0.8:
            return True

        low_reliability_sources = {"compress", "summary", "low_reliability"}
        if source in low_reliability_sources:
            return True

        overlap = text_overlap(old_value.lower(), new_value.lower())
        if overlap < 0.8:
            return True

        return False

    def update_identity(
        self,
        category: str,
        value: str,
        confidence: float = 1.0,
        source: str = "system",
        explicit_overwrite: bool = False,
        reason: str = "",
    ) -> str:
        from companion.security.sanitizer import sanitize_markup
        value = sanitize_markup(value).strip() if value else ""
        if category not in self.ALLOWED_CATEGORIES:
            raise ValueError(f"Category '{category}' not allowed.")

        # The whole read-check-write now runs inside ONE connection scope, so
        # under shared mode the MemoryDatabase RLock serializes it against all
        # other writers — previously the SELECT and the UPDATE were only
        # related by WAL timing luck on a private connection.
        with self._conn() as conn:
            row = conn.execute(
                "SELECT * FROM identity_facts WHERE category = ?", (category,)
            ).fetchone()

            old_value: str | None = None
            result: str

            if row:
                old_value = row["value"] if isinstance(row, sqlite3.Row) else row[1]

                overlap = text_overlap(old_value.lower(), value.lower())

                # If difference is minor -> reject update
                if 0.5 <= overlap < 1.0 and not explicit_overwrite:
                    logger.info(
                        f"IdentityLock: Minor difference in {category}. "
                        f"Rejecting update. Old: {old_value}, New: {value}"
                    )
                    result = "UPDATE_REJECTED_LOCKED"
                elif overlap == 1.0:
                    result = "NO_CHANGE"
                elif overlap < 0.5 and (
                    self.should_lock_update(old_value, value, confidence, source) and not explicit_overwrite
                ):
                    # If difference is major -> require explicit overwrite flag
                    logger.warning(
                        f"IdentityLock: Locked attempted overwrite of {category}. "
                        f"Old: {old_value}, New: {value}"
                    )
                    result = "UPDATE_REJECTED_LOCKED"
                else:
                    now = datetime.now().isoformat()
                    conn.execute(
                        """
                        UPDATE identity_facts
                        SET value = ?, confidence = ?, source = ?, updated_at = ?
                        WHERE category = ?
                        """,
                        (value, confidence, source, now, category),
                    )
                    result = "UPDATED"
            else:
                now = datetime.now().isoformat()
                conn.execute(
                    """
                    INSERT INTO identity_facts
                    (category, value, confidence, source, created_at, updated_at)
                    VALUES (?, ?, ?, ?, ?, ?)
                    """,
                    (category, value, confidence, source, now, now),
                )
                result = "CREATED"

            if result not in ("NO_CHANGE",):
                conn.execute(
                    """
                    INSERT INTO identity_change_log
                        (category, old_value, new_value, result, actor, reason, override_reason, created_at)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        category,
                        old_value,
                        value if result in ("UPDATED", "CREATED") else old_value,
                        result,
                        source,
                        reason,
                        reason if explicit_overwrite else "",
                        datetime.now().isoformat(),
                    ),
                )
        return result

    def get_all(self) -> list[dict[str, Any]]:
        with self._conn() as conn:
            rows = conn.execute("SELECT * FROM identity_facts ORDER BY category").fetchall()
            if rows and isinstance(rows[0], sqlite3.Row):
                return [dict(r) for r in rows]
            return [
                dict(zip(("id", "category", "value", "confidence", "source", "created_at", "updated_at"), r))
                for r in rows
            ]

    def to_prompt_block(self) -> str:
        """
        Format:
        - deterministic
        - compact
        - always first section in system prompt
        - never truncated unless extreme token pressure
        """
        facts = self.get_all()
        if not facts:
            return ""
        lines = ["[IDENTITY VAULT - CORE FACTS]"]
        for f in facts:
            lines.append(f"- {f['category'].upper()}: {f['value']}")
        return "\n".join(lines)
