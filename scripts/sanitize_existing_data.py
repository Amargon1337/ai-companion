"""Migration script to sanitize existing data from markup tag injections."""
from __future__ import annotations

import os
import sqlite3
import sys

# Add root folder to sys.path so we can import companion modules
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from companion.config import SQLITE_PATH
from companion.security.sanitizer import sanitize_markup, _looks_like_injection


def migrate() -> None:
    print(f"Connecting to database at {SQLITE_PATH}...")
    if not os.path.exists(SQLITE_PATH):
        print("Database file does not exist. No migration needed.")
        return

    conn = sqlite3.connect(SQLITE_PATH)
    conn.row_factory = sqlite3.Row
    cursor = conn.cursor()

    # 1. Sanitize messages (text)
    cursor.execute("SELECT id, text FROM messages")
    messages = cursor.fetchall()
    updated_messages = 0
    for row in messages:
        orig = row["text"]
        sanitized = sanitize_markup(orig)
        if sanitized != orig:
            cursor.execute("UPDATE messages SET text = ? WHERE id = ?", (sanitized, row["id"]))
            updated_messages += 1

    # 2. Sanitize facts (fact, status)
    cursor.execute("SELECT id, fact, status FROM facts")
    facts = cursor.fetchall()
    updated_facts = 0
    quarantined_facts = 0
    for row in facts:
        orig = row["fact"]
        orig_status = row["status"]
        sanitized = sanitize_markup(orig)
        is_inj = _looks_like_injection(sanitized)
        new_status = "pending_review" if is_inj else orig_status
        if sanitized != orig or new_status != orig_status:
            cursor.execute("UPDATE facts SET fact = ?, status = ? WHERE id = ?", (sanitized, new_status, row["id"]))
            if sanitized != orig:
                updated_facts += 1
            if new_status == "pending_review" and orig_status != "pending_review":
                quarantined_facts += 1

    # 3. Sanitize reflections (insight, status)
    cursor.execute("SELECT id, insight, status FROM reflections")
    reflections = cursor.fetchall()
    updated_reflections = 0
    quarantined_reflections = 0
    for row in reflections:
        orig = row["insight"]
        orig_status = row["status"]
        sanitized = sanitize_markup(orig)
        is_inj = _looks_like_injection(sanitized)
        new_status = "pending_review" if is_inj else orig_status
        if sanitized != orig or new_status != orig_status:
            cursor.execute("UPDATE reflections SET insight = ?, status = ? WHERE id = ?", (sanitized, new_status, row["id"]))
            if sanitized != orig:
                updated_reflections += 1
            if new_status == "pending_review" and orig_status != "pending_review":
                quarantined_reflections += 1

    # 4. Sanitize beliefs (belief, status)
    cursor.execute("SELECT id, belief, status FROM beliefs")
    beliefs = cursor.fetchall()
    updated_beliefs = 0
    quarantined_beliefs = 0
    for row in beliefs:
        orig = row["belief"]
        orig_status = row["status"]
        sanitized = sanitize_markup(orig)
        is_inj = _looks_like_injection(sanitized)
        new_status = "pending_review" if is_inj else orig_status
        if sanitized != orig or new_status != orig_status:
            cursor.execute("UPDATE beliefs SET belief = ?, status = ? WHERE id = ?", (sanitized, new_status, row["id"]))
            if sanitized != orig:
                updated_beliefs += 1
            if new_status == "pending_review" and orig_status != "pending_review":
                quarantined_beliefs += 1

    conn.commit()
    conn.close()

    print("Migration completed successfully!")
    print(f"Messages updated: {updated_messages}")
    print(f"Facts updated: {updated_facts} (Quarantined: {quarantined_facts})")
    print(f"Reflections updated: {updated_reflections} (Quarantined: {quarantined_reflections})")
    print(f"Beliefs updated: {updated_beliefs} (Quarantined: {quarantined_beliefs})")


if __name__ == "__main__":
    migrate()
