"""IdentityVault - Phase 2 Memory Preservation system."""
from __future__ import annotations

import logging
import sqlite3
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

    def __init__(self, db_path: str) -> None:
        self.db_path = db_path
        self._init_schema()

    def _init_schema(self) -> None:
        with sqlite3.connect(self.db_path) as conn:
            _configure_conn(conn)
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
            conn.commit()

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
    ) -> str:
        from companion.security.sanitizer import sanitize_markup
        value = sanitize_markup(value).strip() if value else ""
        if category not in self.ALLOWED_CATEGORIES:
            raise ValueError(f"Category '{category}' not allowed.")

        with sqlite3.connect(self.db_path) as conn:
            _configure_conn(conn)
            conn.row_factory = sqlite3.Row
            row = conn.execute(
                "SELECT * FROM identity_facts WHERE category = ?", (category,)
            ).fetchone()

            if row:
                old_value = row["value"]

                overlap = text_overlap(old_value.lower(), value.lower())

                # If difference is minor -> reject update
                if 0.5 <= overlap < 1.0 and not explicit_overwrite:
                    logger.info(
                        f"IdentityLock: Minor difference in {category}. "
                        f"Rejecting update. Old: {old_value}, New: {value}"
                    )
                    return "UPDATE_REJECTED_LOCKED"
                    
                if overlap == 1.0:
                    return "NO_CHANGE"

                # If difference is major -> require explicit overwrite flag
                if overlap < 0.5:
                    if self.should_lock_update(old_value, value, confidence, source) and not explicit_overwrite:
                        logger.warning(
                            f"IdentityLock: Locked attempted overwrite of {category}. "
                            f"Old: {old_value}, New: {value}"
                        )
                        return "UPDATE_REJECTED_LOCKED"

                now = datetime.now().isoformat()
                conn.execute(
                    """
                    UPDATE identity_facts 
                    SET value = ?, confidence = ?, source = ?, updated_at = ? 
                    WHERE category = ?
                    """,
                    (value, confidence, source, now, category),
                )
                return "UPDATED"
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
                return "CREATED"

    def get_all(self) -> list[dict[str, Any]]:
        with sqlite3.connect(self.db_path) as conn:
            _configure_conn(conn)
            conn.row_factory = sqlite3.Row
            rows = conn.execute("SELECT * FROM identity_facts ORDER BY category").fetchall()
            return [dict(r) for r in rows]

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
